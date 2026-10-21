"""A small CalDAV client: just what two-way sync needs.

* discovery: well-known → current-user-principal → calendar-home-set → calendars
* incremental pull: sync-collection REPORT (RFC 6578) + calendar-multiget
* writes: PUT with If-Match / If-None-Match and DELETE with If-Match, so a change made
  on the phone is never silently overwritten (412 comes back instead)

Works with Radicale and iCloud. Kept separate from the ``caldav`` package so the exact
headers (etags, preconditions) are under our control; see docs/decisions.md.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

NS = {
    "d": "DAV:",
    "c": "urn:ietf:params:xml:ns:caldav",
    "cs": "http://calendarserver.org/ns/",
    "ic": "http://apple.com/ns/ical/",
}
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)


class CalDAVError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class PreconditionFailed(CalDAVError):
    """The resource changed on the server since we last saw it (412)."""


@dataclass
class RemoteCalendar:
    url: str
    name: str
    colour: str | None
    writable: bool
    sync_token: str | None
    supports_events: bool = True
    supports_todos: bool = True


@dataclass
class Changes:
    """What changed since a sync token."""

    sync_token: str | None
    changed: dict[str, str] = field(default_factory=dict)  # href → etag
    deleted: list[str] = field(default_factory=list)  # hrefs
    full: bool = False  # True when the server ignored/expired our token


def _q(tag: str) -> str:
    p, local = tag.split(":")
    return f"{{{NS[p]}}}{local}"


def _xml(body: str) -> bytes:
    return ('<?xml version="1.0" encoding="utf-8"?>\n' + body).encode()


class CalDAVClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20,
    ):
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.http = httpx.Client(
            auth=(username, password),
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
            headers={"User-Agent": "Tahgul/0.1"},
        )

    def close(self) -> None:
        self.http.close()

    # --- plumbing ---------------------------------------------------------------------

    def _abs(self, href: str) -> str:
        return urljoin(self.base_url, href)

    def _request(
        self, method: str, url: str, *, body: bytes | str | None = None, headers: dict | None = None
    ) -> httpx.Response:
        h = {"Content-Type": "application/xml; charset=utf-8"} if body is not None else {}
        h.update(headers or {})
        try:
            r = self.http.request(method, url, content=body, headers=h)
        except httpx.HTTPError as exc:
            raise CalDAVError(f"Can't reach the calendar server: {exc}") from exc
        if r.status_code == 401:
            raise CalDAVError("The calendar server rejected the username or password.", 401)
        if r.status_code == 412:
            raise PreconditionFailed("Changed on the server since last sync.", 412)
        return r

    def _multistatus(self, method: str, url: str, body: str, depth: str) -> ET.Element:
        r = self._request(method, url, body=_xml(body), headers={"Depth": depth})
        if r.status_code != 207:
            raise CalDAVError(
                f"{method} {urlparse(url).path} failed ({r.status_code}).", r.status_code
            )
        return ET.fromstring(r.content)

    @staticmethod
    def _props(resp: ET.Element) -> dict[str, ET.Element]:
        """Props with 200 status from a <response>."""
        out: dict[str, ET.Element] = {}
        for ps in resp.findall("d:propstat", NS):
            status = ps.findtext("d:status", "", NS)
            if " 200 " not in status:
                continue
            prop = ps.find("d:prop", NS)
            if prop is not None:
                for child in prop:
                    out[child.tag] = child
        return out

    def _href_prop(self, url: str, prop: str) -> str | None:
        root = self._multistatus(
            "PROPFIND",
            url,
            f'<d:propfind xmlns:d="DAV:" '
            f'xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><{prop}/>'
            f"</d:prop></d:propfind>",
            "0",
        )
        for resp in root.findall("d:response", NS):
            el = self._props(resp).get(_q(prop))
            if el is not None:
                href = el.findtext("d:href", None, NS)
                if href:
                    return self._abs(href)
        return None

    # --- discovery ------------------------------------------------------------------

    def home(self) -> str:
        """The calendar-home-set URL, where new calendars go."""
        principal = self._principal()
        home = self._href_prop(principal, "c:calendar-home-set")
        if not home:
            raise CalDAVError("The server didn't say where your calendars are.")
        return home

    def _principal(self) -> str:
        principal = None
        for start in (self.base_url, urljoin(self.base_url, "/.well-known/caldav")):
            try:
                principal = self._href_prop(start, "d:current-user-principal")
            except CalDAVError as exc:
                if exc.status == 401:
                    raise
                continue
            if principal:
                return principal
        raise CalDAVError("This doesn't look like a CalDAV server (no principal found).")

    def calendars(self) -> list[RemoteCalendar]:
        home = self.home()
        root = self._multistatus(
            "PROPFIND",
            home,
            """
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"
            xmlns:cs="http://calendarserver.org/ns/" xmlns:ic="http://apple.com/ns/ical/">
  <d:prop>
    <d:resourcetype/><d:displayname/><ic:calendar-color/><d:sync-token/>
    <d:current-user-privilege-set/><c:supported-calendar-component-set/>
  </d:prop>
</d:propfind>""",
            "1",
        )
        out = []
        for resp in root.findall("d:response", NS):
            props = self._props(resp)
            rtype = props.get(_q("d:resourcetype"))
            if rtype is None or rtype.find("c:calendar", NS) is None:
                continue
            href = self._abs(resp.findtext("d:href", "", NS))
            comps = props.get(_q("c:supported-calendar-component-set"))
            names = (
                {c.get("name") for c in comps.findall("c:comp", NS)} if comps is not None else None
            )
            supports_events = names is None or "VEVENT" in names
            supports_todos = names is None or "VTODO" in names
            privs = props.get(_q("d:current-user-privilege-set"))
            writable = privs is None or any(
                p.find(t, NS) is not None
                for p in privs.findall("d:privilege", NS)
                for t in ("d:write", "d:write-content", "d:all")
            )
            colour = props.get(_q("ic:calendar-color"))
            name = props.get(_q("d:displayname"))
            token = props.get(_q("d:sync-token"))
            out.append(
                RemoteCalendar(
                    url=href,
                    name=(
                        name.text
                        if name is not None and name.text
                        else urlparse(href).path.rstrip("/").rsplit("/", 1)[-1]
                    ),
                    colour=(colour.text or "")[:7] or None if colour is not None else None,
                    writable=writable,
                    sync_token=token.text if token is not None else None,
                    supports_events=supports_events,
                    supports_todos=supports_todos,
                )
            )
        return out

    def make_calendar(
        self,
        home_url: str,
        slug: str,
        name: str,
        colour: str | None = None,
        component: str = "VEVENT",
    ) -> str:
        """MKCALENDAR in the calendar home; returns the new calendar's URL."""
        url = urljoin(home_url if home_url.endswith("/") else home_url + "/", slug + "/")
        colour_xml = f"<ic:calendar-color>{colour}</ic:calendar-color>" if colour else ""
        body = f"""<c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"
  xmlns:ic="http://apple.com/ns/ical/"><d:set><d:prop><d:displayname>{name}</d:displayname>
  {colour_xml}<c:supported-calendar-component-set><c:comp name="{component}"/>
  </c:supported-calendar-component-set></d:prop></d:set></c:mkcalendar>"""
        r = self._request("MKCALENDAR", url, body=_xml(body))
        if r.status_code not in (201, 200):
            raise CalDAVError(f"Couldn't create the calendar ({r.status_code}).", r.status_code)
        return url

    # --- sync -----------------------------------------------------------------------

    def changes(self, calendar_url: str, sync_token: str | None) -> Changes:
        body = f"""<d:sync-collection xmlns:d="DAV:">
  <d:sync-token>{sync_token or ""}</d:sync-token><d:sync-level>1</d:sync-level>
  <d:prop><d:getetag/></d:prop></d:sync-collection>"""
        try:
            root = self._multistatus("REPORT", calendar_url, body, "1")
        except CalDAVError as exc:
            # 403/409 with valid-sync-token means the token expired: start over.
            if sync_token and exc.status in (403, 409, 400):
                return self.changes(calendar_url, None)
            raise
        out = Changes(sync_token=root.findtext("d:sync-token", None, NS), full=not sync_token)
        for resp in root.findall("d:response", NS):
            href = self._abs(resp.findtext("d:href", "", NS))
            if href.rstrip("/") == calendar_url.rstrip("/"):
                continue
            status = resp.findtext("d:status", "", NS)
            if " 404 " in status:
                out.deleted.append(href)
                continue
            etag = self._props(resp).get(_q("d:getetag"))
            if etag is not None and etag.text:
                out.changed[href] = etag.text
        return out

    def fetch(self, calendar_url: str, hrefs: list[str]) -> dict[str, tuple[str, str]]:
        """href → (etag, iCalendar text)."""
        out: dict[str, tuple[str, str]] = {}
        for i in range(0, len(hrefs), 100):
            chunk = hrefs[i : i + 100]
            refs = "".join(f"<d:href>{urlparse(h).path}</d:href>" for h in chunk)
            root = self._multistatus(
                "REPORT",
                calendar_url,
                f"""
<c:calendar-multiget xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>{refs}</c:calendar-multiget>""",
                "1",
            )
            for resp in root.findall("d:response", NS):
                props = self._props(resp)
                data = props.get(_q("c:calendar-data"))
                etag = props.get(_q("d:getetag"))
                if data is not None and data.text:
                    out[self._abs(resp.findtext("d:href", "", NS))] = (
                        etag.text if etag is not None else "",
                        data.text,
                    )
        return out

    def put(self, url: str, ics: str, etag: str | None) -> str | None:
        """Create (etag None) or update (If-Match). Returns the new etag when the server says."""
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        headers["If-Match" if etag else "If-None-Match"] = etag or "*"
        r = self._request("PUT", url, body=ics.encode(), headers=headers)
        if r.status_code not in (200, 201, 204):
            raise CalDAVError(f"Saving the event failed ({r.status_code}).", r.status_code)
        return r.headers.get("ETag")

    def delete(self, url: str, etag: str | None) -> None:
        r = self._request("DELETE", url, headers={"If-Match": etag} if etag else {})
        if r.status_code not in (200, 204, 404):
            raise CalDAVError(f"Deleting the event failed ({r.status_code}).", r.status_code)

    def etag(self, url: str) -> str | None:
        root = self._multistatus(
            "PROPFIND",
            url,
            '<d:propfind xmlns:d="DAV:"><d:prop><d:getetag/></d:prop></d:propfind>',
            "0",
        )
        for resp in root.findall("d:response", NS):
            el = self._props(resp).get(_q("d:getetag"))
            if el is not None:
                return el.text
        return None
