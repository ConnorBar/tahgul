"""MCP server wrapping Tahgul's REST API (SPEC: agent API, "MCP server").

Every tool calls the same REST endpoints the web app uses, with this server's own API
token, so the same scopes, agent limits and audit log apply ("created by agent").

    TAHGUL_URL=https://tahgul.<tailnet>.ts.net TAHGUL_TOKEN=thg_… tahgul-mcp
    tahgul-mcp --http --port 8765      # streamable HTTP instead of stdio

Tools return plain dictionaries. When a name is ambiguous the result says so and lists
candidates, so the model can ask the person instead of guessing.
"""

import argparse
import logging
import os
import uuid
from datetime import date
from typing import Any, Literal

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

INSTRUCTIONS = """\
Tools for Tahgul, a solo consultant's ops app: tasks on project boards, a running timer,
logged time, calendar events, ideas, reports and invoice drafts. Projects and clients can
be named in plain words ("Acme", "network audit"). If a result has needs_confirmation,
ask the person which candidate they meant and call again with that exact name; never
pick one yourself.
For anything said in one sentence ("log 2 hours on Acme yesterday afternoon") the
`quick` tool is usually simplest. Deleting things and changing rates aren't available here.
"""


class TahgulAPI:
    """A thin client: one place for auth, idempotency keys and error shaping."""

    def __init__(self, http: httpx.Client):
        self.http = http

    def call(
        self, method: str, path: str, *, json: Any = None, params: dict | None = None
    ) -> dict | list:
        headers = {}
        if method != "GET":
            # One key per tool call; a transport retry of the same call reuses it.
            headers["Idempotency-Key"] = f"mcp-{uuid.uuid4()}"
        params = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(2):
            try:
                r = self.http.request(
                    method, f"/api/v1{path}", json=json, params=params, headers=headers
                )
                break
            except httpx.TransportError:
                if attempt:
                    return {"error": "Tahgul is unreachable. Is Tailscale up?"}
        if r.status_code == 204:
            return {"ok": True}
        body = (
            r.json()
            if r.headers.get("content-type", "").startswith("application/json")
            else {"detail": r.text}
        )
        if r.is_success:
            return body
        detail = body.get("detail") if isinstance(body, dict) else body
        if isinstance(detail, dict) and detail.get("code") == "needs_confirmation":
            return {
                "needs_confirmation": True,
                "question": detail.get("message"),
                "field": detail.get("field"),
                "candidates": [c.get("name") for c in detail.get("candidates", [])],
            }
        return {"error": detail, "status": r.status_code}


def _task_brief(t: dict) -> dict:
    return {
        k: t.get(k)
        for k in (
            "ref",
            "id",
            "title",
            "project_name",
            "client_name",
            "status",
            "done",
            "priority",
            "due_date",
            "due_time",
            "labels",
            "blocked",
            "overdue",
        )
    }


def _entry_brief(e: dict) -> dict:
    return {
        k: e.get(k)
        for k in (
            "id",
            "project_name",
            "client_name",
            "activity_name",
            "task_ref",
            "start_at",
            "end_at",
            "hours",
            "amount",
            "billable",
            "notes",
            "running",
        )
    }


def build_server(api: TahgulAPI) -> MCPServer:
    mcp = MCPServer("tahgul", instructions=INSTRUCTIONS, version="0.1.0")

    @mcp.tool(annotations=WRITE)
    def quick(text: str, project: str | None = None) -> dict:
        """Do one thing from a sentence: add a task ("Call Laura Thursday 2pm #acme"),
        start or stop the timer, log time ("2 hours on Acme yesterday afternoon"),
        save an idea ("idea: drone surveys") or answer "how many hours this week".
        Returns `speech`, a one-line confirmation to relay."""
        return api.call("POST", "/quick", json={"text": text, "project": project})

    @mcp.tool(annotations=WRITE)
    def create_task(
        title: str,
        project: str,
        due_date: date | None = None,
        due_time: str | None = None,
        priority: Literal["urgent", "high", "medium", "low", "none"] | None = None,
        labels: list[str] | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> dict:
        """Create a task on a project's board. `project` is a project or client name.
        `due_time` is HH:MM. `status` is a board column name (default: first column)."""
        r = api.call(
            "POST",
            "/tasks",
            json={
                "project": project,
                "title": title,
                "due_date": due_date and str(due_date),
                "due_time": due_time,
                "priority": priority,
                "labels": labels,
                "description": description,
                "status": status,
            },
        )
        return _task_brief(r) if "ref" in r else r

    @mcp.tool(annotations=READ)
    def list_tasks(
        project: str | None = None,
        client: str | None = None,
        status: str | None = None,
        open_only: bool = True,
        due_before: date | None = None,
        overdue: bool | None = None,
        search: str | None = None,
        limit: int = 25,
    ) -> dict:
        """Find tasks. Defaults to open tasks, soonest due first."""
        r = api.call(
            "GET",
            "/tasks",
            params={
                "project": project,
                "client": client,
                "status": status,
                "open": "true" if open_only else None,
                "due_before": due_before,
                "overdue": "true" if overdue else None,
                "q": search,
                "sort": "due",
                "limit": max(1, min(limit, 100)),
            },
        )
        if isinstance(r, dict):
            return r
        return {"count": len(r), "tasks": [_task_brief(t) for t in r]}

    @mcp.tool(annotations=WRITE)
    def update_task(
        task: str,
        status: str | None = None,
        done: bool | None = None,
        title: str | None = None,
        due_date: date | None = None,
        priority: Literal["urgent", "high", "medium", "low", "none"] | None = None,
        comment: str | None = None,
    ) -> dict:
        """Change a task. `task` is its reference like "#12" or "12". Use `done` to tick it
        off, `status` to move it to a named column, `comment` to add a note."""
        t = _task_lookup(api, task)
        if "error" in t:
            return t
        changes = {
            k: v
            for k, v in {
                "status": status,
                "done": done,
                "title": title,
                "due_date": due_date and str(due_date),
                "priority": priority,
            }.items()
            if v is not None
        }
        result = t
        if changes:
            result = api.call("PATCH", f"/tasks/{t['id']}", json=changes)
            if "error" in result:
                return result
        if comment:
            c = api.call("POST", f"/tasks/{t['id']}/comments", json={"body": comment})
            if "error" in c:
                return c
        return _task_brief(result)

    @mcp.tool(annotations=WRITE)
    def start_timer(
        project: str | None = None,
        task: str | None = None,
        notes: str | None = None,
        activity: str | None = None,
    ) -> dict:
        """Start the timer (stops any running one). Give a project name or a task like "#12"."""
        body: dict[str, Any] = {"project": project, "notes": notes, "activity": activity}
        if task:
            t = _task_lookup(api, task)
            if "error" in t:
                return t
            body = {"task_id": t["id"], "notes": notes, "activity": activity}
        r = api.call("POST", "/timer/start", json=body)
        if "running" not in r:
            return r
        out = {"running": _entry_brief(r["running"])}
        if r.get("stopped"):
            out["stopped_previous"] = _entry_brief(r["stopped"])
        return out

    @mcp.tool(annotations=WRITE)
    def stop_timer() -> dict:
        """Stop the running timer and return the logged entry."""
        r = api.call("POST", "/timer/stop", json={})
        return _entry_brief(r) if "id" in r else r

    @mcp.tool(annotations=READ)
    def timer_status() -> dict:
        """What's running, and for how long."""
        r = api.call("GET", "/timer")
        if "running" not in r:
            return r
        if r["running"] is None:
            return {"running": None}
        return {
            "running": _entry_brief(r["running"]),
            "minutes": round(r["elapsed_seconds"] / 60),
            "long_running": r["long_running"],
        }

    @mcp.tool(annotations=WRITE)
    def log_time(
        project: str,
        minutes: int,
        day: date | None = None,
        start_time: str | None = None,
        notes: str | None = None,
        activity: str | None = None,
    ) -> dict:
        """Log time after the fact. `day` defaults to today and `start_time` (HH:MM, local)
        to 09:00. For phrasing like "yesterday afternoon", prefer `quick`."""
        start = f"{(day or date.today()).isoformat()}T{start_time or '09:00'}:00"
        r = api.call(
            "POST",
            "/time-entries",
            json={
                "project": project,
                "activity": activity,
                "start_at": start,
                "duration_minutes": minutes,
                "notes": notes,
            },
        )
        if "id" not in r:
            return r
        out = _entry_brief(r)
        if r.get("overlaps"):
            out["warning"] = f"Overlaps {len(r['overlaps'])} other entr(y/ies)."
        return out

    @mcp.tool(annotations=READ)
    def summary(
        date_from: date | None = None,
        date_to: date | None = None,
        client: str | None = None,
    ) -> dict:
        """Hours and money for a date range (default: this week), by client and project,
        with budget left and the effective hourly rate."""
        r = api.call(
            "GET", "/reports/summary", params={"from": date_from, "to": date_to, "client": client}
        )
        if "totals" not in r:
            return r
        return {
            "from": r["from"],
            "to": r["to"],
            "currency": r["currency"],
            "totals": r["totals"],
            "by_client": [{k: c[k] for k in ("name", "hours", "amount")} for c in r["by_client"]],
            "by_project": [
                {
                    k: p[k]
                    for k in (
                        "name",
                        "client_name",
                        "hours",
                        "amount",
                        "budget_hours_left",
                        "budget_amount_left",
                    )
                }
                for p in r["by_project"]
            ],
        }

    @mcp.tool(annotations=WRITE)
    def create_event(
        title: str,
        start: str,
        minutes: int = 60,
        project: str | None = None,
        location: str | None = None,
        all_day: bool = False,
    ) -> dict:
        """Put an event on your default calendar; it appears on the phone right away.
        `start` is local time like "2026-10-23T14:00" (or a date for all-day)."""
        r = api.call(
            "POST",
            "/events",
            json={
                "title": title,
                "start_at": start if "T" in start else f"{start}T00:00:00",
                "duration_minutes": minutes,
                "project": project,
                "location": location,
                "all_day": all_day,
            },
        )
        return _event_brief(r) if "id" in r else r

    @mcp.tool(annotations=READ)
    def list_events(date_from: date, date_to: date | None = None) -> dict:
        """Calendar events between two dates (inclusive), recurring ones expanded."""
        r = api.call("GET", "/events", params={"from": date_from, "to": date_to or date_from})
        if isinstance(r, dict):
            return r
        return {"count": len(r), "events": [_event_brief(e) for e in r]}

    @mcp.tool(annotations=READ)
    def events_to_log() -> dict:
        """Meetings that ended recently, are linked to a project, and have no time logged:
        ask the person "log this as time?"."""
        r = api.call("GET", "/events/pending-log")
        if isinstance(r, dict):
            return r
        return {"count": len(r), "events": [_event_brief(e) for e in r]}

    @mcp.tool(annotations=WRITE)
    def log_event_as_time(event_id: str, notes: str | None = None) -> dict:
        """Turn a calendar event into a time entry ("I was in that meeting, bill it")."""
        r = api.call("POST", f"/events/{event_id}/log-time", json={"notes": notes})
        return _entry_brief(r["entry"]) if "entry" in r else r

    @mcp.tool(annotations=WRITE)
    def capture_idea(title: str, description: str | None = None) -> dict:
        """Save an idea to the ideas inbox (an idea project that can grow into real work)."""
        r = api.call("POST", "/ideas", json={"title": title, "description": description})
        return {"id": r["id"], "name": r["name"], "status": r["status"]} if "id" in r else r

    @mcp.tool(annotations=READ)
    def budget_left(project: str) -> dict:
        """What's left on a project: its budget and each agreed scope (hours and money used,
        budget, warning at 80%/100%), plus work logged outside every scope. "What's left on
        the Acme audit?" """
        found = api.call("GET", "/projects", params={"q": project})
        if isinstance(found, dict):
            return found
        exact = [p for p in found if p["name"].lower() == project.lower()]
        hits = exact or found
        if len(hits) != 1:
            return {
                "needs_confirmation": bool(hits),
                "question": f"Which project did you mean by “{project}”?",
                "candidates": [p["name"] for p in hits[:8]],
            }
        p = hits[0]
        rep = api.call("GET", f"/projects/{p['id']}/scopes")
        if "scopes" not in rep:
            return rep
        scopes = []
        for s in rep["scopes"]:
            b = s.get("burn") or {}
            left = (
                f"{float(s['budget_hours']) - float(b.get('hours', 0)):.2f}"
                if s.get("budget_hours")
                else None
            )
            scopes.append(
                {
                    "title": s["title"],
                    "type": s["type"],
                    "status": s["status"],
                    "hours_used": b.get("hours"),
                    "budget_hours": s.get("budget_hours"),
                    "hours_left": left,
                    "amount_used": b.get("amount"),
                    "budget_amount": s.get("budget_amount"),
                    "warning": b.get("warning"),
                }
            )
        return {
            "project": p["name"],
            "client": p.get("client_name"),
            "project_budget_hours": p.get("budget_hours"),
            "scopes": scopes,
            "outside_scope_hours": rep["outside_scope_hours"],
            "scope_creep": rep["creep"],
        }

    @mcp.tool(annotations=READ)
    def voice_notes(project: str | None = None, query: str | None = None) -> dict:
        """Voice notes with their transcripts, newest first: on one project or idea (by
        name), or matching words in the title or transcript. Use it to pull to-dos out of
        a recorded idea, then create_task for each."""
        params: dict = {"q": query, "limit": 20}
        if project:
            found = api.call("GET", "/projects", params={"q": project})
            if isinstance(found, dict):
                return found
            exact = [p for p in found if p["name"].lower() == project.lower()]
            hits = exact or found
            if len(hits) != 1:
                return {
                    "needs_confirmation": bool(hits),
                    "question": f"Which project did you mean by “{project}”?",
                    "candidates": [p["name"] for p in hits[:8]],
                }
            params["project_id"] = hits[0]["id"]
        r = api.call("GET", "/voice-notes", params=params)
        if isinstance(r, dict):
            return r
        return {
            "count": len(r),
            "notes": [
                {
                    k: n.get(k)
                    for k in (
                        "id",
                        "title",
                        "recorded_at",
                        "duration_seconds",
                        "project_name",
                        "task_ref",
                        "transcript",
                        "transcript_status",
                    )
                }
                for n in r
            ],
        }

    @mcp.tool(annotations=READ)
    def unbilled_work() -> dict:
        """Billable time not yet invoiced, per client: "who should I bill?"."""
        r = api.call("GET", "/invoices/unbilled")
        if isinstance(r, dict):
            return r
        return {"count": len(r), "clients": r}

    @mcp.tool(annotations=WRITE)
    def draft_invoice(
        client: str,
        period_start: date | None = None,
        period_end: date | None = None,
        group_by: Literal["project", "activity", "scope", "day", "entry"] = "project",
    ) -> dict:
        """Draft an invoice for a client from their unbilled time (default: everything up to
        today). Needs a token with the invoice scope. It stays a draft: the person reviews
        and issues it in the app."""
        r = api.call(
            "POST",
            "/invoices",
            json={
                "client": client,
                "period_start": period_start and period_start.isoformat(),
                "period_end": period_end and period_end.isoformat(),
                "group_by": group_by,
            },
        )
        if "id" not in r:
            return r
        return {
            "id": r["id"],
            "client": r["client_name"],
            "period": [r["period_start"], r["period_end"]],
            "lines": [
                {
                    "description": ln["description"],
                    "quantity": ln["quantity"],
                    "amount": ln["amount"],
                }
                for ln in r["lines"]
            ],
            "total": r["total"],
            "currency": r["currency"],
            "warnings": r["warnings"],
            "speech": f"Drafted an invoice for {r['client_name']}: {r['currency']} {r['total']}.",
        }

    return mcp


def _event_brief(e: dict) -> dict:
    return {
        k: e.get(k)
        for k in (
            "id",
            "title",
            "start_at",
            "end_at",
            "all_day",
            "calendar_name",
            "project_name",
            "location",
            "time_entry_id",
            "sync_pending",
        )
    }


def _task_lookup(api: TahgulAPI, ref: str) -> dict:
    n = ref.strip().lstrip("#")
    if n.isdigit():
        return api.call("GET", f"/tasks/by-number/{n}")
    try:
        return api.call("GET", f"/tasks/{uuid.UUID(ref)}")
    except ValueError:
        return {"error": f"Use a task reference like #12, not {ref!r}."}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tahgul-mcp")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP, not stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    logging.getLogger("httpx").setLevel(logging.WARNING)  # keep stdio's stderr quiet
    url = os.environ.get("TAHGUL_URL", "http://127.0.0.1:8000")
    token = os.environ.get("TAHGUL_TOKEN")
    if not token:
        raise SystemExit("Set TAHGUL_TOKEN to an API token (Settings → API tokens).")
    http = httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    server = build_server(TahgulAPI(http))
    if args.http:
        server.run("streamable-http", host=args.host, port=args.port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
