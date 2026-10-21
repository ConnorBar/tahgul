"""Light parsing for the quick-add bar (SPEC: "Fix VPN for Acme tomorrow !high #network").

What it understands:

* ``!urgent`` ``!high`` ``!medium`` ``!low``, or "high priority"  → priority
* ``#word`` → a project or client when the word matches one, otherwise a label
* ``for Acme`` / ``@Acme`` → a project or client, only when the name matches
* dates: today, tonight, tomorrow, yesterday, weekday names ("fri", "next monday"), "in 3 days",
  "next week", "oct 30", "30 oct", "10/30", "2026-10-30"
* times: "2pm", "2:30pm", "14:00", "at 3", "noon"

Anything not understood stays in the title. A client with several open projects is
returned as candidates rather than guessed (SPEC: needs_confirmation).
"""

import re
from dataclasses import dataclass, field
from datetime import date, time, timedelta

PRIORITY_WORDS = {
    "urgent": "urgent",
    "u": "urgent",
    "1": "urgent",
    "high": "high",
    "h": "high",
    "2": "high",
    "medium": "medium",
    "med": "medium",
    "m": "medium",
    "3": "medium",
    "low": "low",
    "l": "low",
    "4": "low",
}
SPOKEN_PRIORITY = {
    "urgent": "urgent",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "top": "urgent",
    "normal": "medium",
}
WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}
MONTHS = {
    m: i + 1
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ]
    )
    for m in names
}


@dataclass
class Target:
    """Something in the workspace the text can point at."""

    kind: str  # "project" | "client"
    id: str
    name: str
    code: str | None = None
    project_ids: list[str] = field(default_factory=list)  # a client's open projects


@dataclass
class Parsed:
    title: str
    priority: str | None = None
    labels: list[str] = field(default_factory=list)
    due_date: date | None = None
    due_time: time | None = None
    project_id: str | None = None
    client_id: str | None = None
    # Set when a client matched but has several projects to choose from.
    candidates: list[str] = field(default_factory=list)
    matched: str | None = None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _match(text: str, targets: list[Target]) -> Target | None:
    key = _norm(text)
    if not key:
        return None
    # Projects first, then clients; exact name or code beats nothing.
    for kind in ("project", "client"):
        for t in targets:
            if t.kind == kind and (_norm(t.name) == key or (t.code and _norm(t.code) == key)):
                return t
    return None


def _next_weekday(today: date, wd: int, force_next: bool) -> date:
    days = (wd - today.weekday()) % 7
    if days == 0 and force_next:
        days = 7
    return today + timedelta(days=days)


def _year_for(month: int, day: int, today: date) -> date | None:
    try:
        d = date(today.year, month, day)
    except ValueError:
        return None
    # A date already well past this year means next year ("jan 5" in December).
    if d < today - timedelta(days=60):
        d = date(today.year + 1, month, day)
    return d


TIME_RE = re.compile(r"^(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$")


def _parse_time(tok: str, nxt: str | None) -> tuple[time | None, int]:
    """Returns (time, tokens consumed)."""
    t = tok.lower()
    if t in ("noon", "midday"):
        return time(12, 0), 1
    if t == "midnight":
        return time(0, 0), 1
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?(am|pm)$", t)
    if m:
        h, mi, ap = int(m[1]), int(m[2] or 0), m[3]
    else:
        m = re.match(r"^(\d{1,2}):(\d{2})$", t)
        if m:
            h, mi, ap = int(m[1]), int(m[2]), None
        elif re.match(r"^\d{1,2}$", t) and nxt and nxt.lower() in ("am", "pm"):
            h, mi, ap = int(t), 0, nxt.lower()
            if not 1 <= h <= 12:
                return None, 0
            h = h % 12 + (12 if ap == "pm" else 0)
            return time(h, mi), 2
        else:
            return None, 0
    if ap:
        if not 1 <= h <= 12:
            return None, 0
        h = h % 12 + (12 if ap == "pm" else 0)
    if h > 23 or mi > 59:
        return None, 0
    return time(h, mi), 1


def parse(text: str, today: date, targets: list[Target]) -> Parsed:
    tokens = text.split()
    keep: list[str] = []
    out = Parsed(title="")
    target: Target | None = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        low = tok.lower().rstrip(",.;")
        nxt = tokens[i + 1].lower().rstrip(",.;") if i + 1 < len(tokens) else None
        nxt2 = tokens[i + 2].lower().rstrip(",.;") if i + 2 < len(tokens) else None

        # Priority: "!high", or spoken "high priority" / "priority high"
        if low.startswith("!") and low[1:] in PRIORITY_WORDS:
            out.priority = PRIORITY_WORDS[low[1:]]
            i += 1
            continue
        if low in SPOKEN_PRIORITY and nxt == "priority":
            out.priority = SPOKEN_PRIORITY[low]
            i += 2
            continue
        if low == "priority" and nxt in SPOKEN_PRIORITY:
            out.priority = SPOKEN_PRIORITY[nxt]
            i += 2
            continue
        # #label or #project
        if low.startswith("#") and len(low) > 1:
            t = _match(low[1:], targets)
            if t and target is None:
                target = t
            else:
                label = re.sub(r"[^a-z0-9_-]", "", low[1:])
                if label and label not in out.labels:
                    out.labels.append(label)
            i += 1
            continue
        # @Acme
        if low.startswith("@") and len(low) > 1:
            t = _match(low[1:], targets)
            if t and target is None:
                target = t
                i += 1
                continue
        # "for Acme Farms": try the longest run of up to 4 words that names something.
        if low == "for" and target is None:
            for n in range(min(4, len(tokens) - i - 1), 0, -1):
                t = _match(" ".join(tokens[i + 1 : i + 1 + n]), targets)
                if t:
                    target = t
                    i += 1 + n
                    break
            else:
                keep.append(tok)
                i += 1
            continue

        # Dates
        d, used = None, 0
        if low in ("today", "tonight", "eod"):
            d, used = today, 1
            if low == "tonight" and out.due_time is None:
                out.due_time = time(20, 0)
        elif low in ("tomorrow", "tmrw", "tmr", "tom"):
            d, used = today + timedelta(days=1), 1
        elif low == "yesterday":
            d, used = today - timedelta(days=1), 1
        elif low in WEEKDAYS:
            d, used = _next_weekday(today, WEEKDAYS[low], False), 1
        elif low == "next" and nxt in WEEKDAYS:
            d, used = _next_weekday(today, WEEKDAYS[nxt], True), 2
        elif low == "next" and nxt == "week":
            d, used = _next_weekday(today, 0, True), 2
        elif low == "in" and nxt and nxt.isdigit() and nxt2 in ("day", "days", "week", "weeks"):
            n = int(nxt) * (7 if nxt2.startswith("week") else 1)
            d, used = today + timedelta(days=n), 3
        elif low in MONTHS and nxt and nxt.rstrip("stndrh").isdigit():
            d, used = _year_for(MONTHS[low], int(nxt.rstrip("stndrh")), today), 2
        elif low.rstrip("stndrh").isdigit() and nxt in MONTHS:
            d, used = _year_for(MONTHS[nxt], int(low.rstrip("stndrh")), today), 2
        elif re.match(r"^\d{4}-\d{2}-\d{2}$", low):
            try:
                d, used = date.fromisoformat(low), 1
            except ValueError:
                pass
        elif re.match(r"^\d{1,2}/\d{1,2}$", low):
            mo, da = (int(x) for x in low.split("/"))
            d, used = (_year_for(mo, da, today), 1) if 1 <= mo <= 12 else (None, 0)
        if d is not None and used:
            out.due_date = d
            i += used
            continue

        # Times ("at 3" needs the "at"; a bare number stays in the title)
        if low == "at" and nxt:
            t, n = _parse_time(nxt, nxt2)
            if t:
                out.due_time = t
                i += 1 + n
                continue
        t, n = _parse_time(low, nxt)
        explicit = ":" in low or low.endswith(("am", "pm")) or n == 2
        if t and (explicit or low in ("noon", "midday", "midnight")):
            out.due_time = t
            i += n
            continue

        keep.append(tok)
        i += 1

    if out.due_time is not None and out.due_date is None:
        out.due_date = today
    if target is not None:
        out.matched = target.name
        if target.kind == "project":
            out.project_id = target.id
        else:
            out.client_id = target.id
            if len(target.project_ids) == 1:
                out.project_id = target.project_ids[0]
            else:
                out.candidates = list(target.project_ids)
    out.title = " ".join(keep).strip(" ,-") or text.strip()
    return out
