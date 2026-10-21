"""One sentence in, one action out, with a line for Siri to say (SPEC: POST /quick).

Understood, in this order:

* "stop" / "stop the timer"                       → stop the timer
* "start timer for Acme" / "working on the audit"  → start a timer
* "log 2 hours on Acme yesterday afternoon"        → log time
* "idea: farm sensor newsletter"                   → capture an idea
* "what's running" / "hours this week"             → answer, change nothing
* anything else                                    → a task, via quick add
  ("Call Laura Thursday 2pm #acme")

A name that could mean several things returns ``needs_confirmation`` with candidates
and a ``speech`` question rather than a guess.
"""

import re
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from tahgul import lookup, quickadd
from tahgul import timekeeping as tk
from tahgul.crud import get_live, resolve_workspace
from tahgul.db import get_db
from tahgul.deps import require_scope
from tahgul.models import Project, Workspace
from tahgul.principal import Principal
from tahgul.routes import ideas, reports, tasks
from tahgul.routes import time as time_routes

router = APIRouter(prefix="/api/v1", tags=["agent"])

Action = Literal[
    "task_created",
    "timer_started",
    "timer_stopped",
    "time_logged",
    "idea_captured",
    "status",
    "summary",
    "needs_confirmation",
    "nothing",
]


class QuickIn(BaseModel):
    text: str = Field(min_length=1, max_length=500, examples=["Call Laura Thursday 2pm #acme"])
    workspace_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = Field(
        None, description="Use this project (e.g. after a needs_confirmation answer)"
    )
    project: str | None = Field(None, description="Same, by project or client name")
    dry_run: bool = False


class QuickResult(BaseModel):
    action: Action
    speech: str = Field(description="A short sentence for Siri or a notification")
    needs_confirmation: bool = False
    candidates: list[dict[str, Any]] = []
    task: dict | None = None
    entry: dict | None = None
    idea: dict | None = None
    summary: dict | None = None


# --- Small parsers --------------------------------------------------------------------

STOP = re.compile(r"^(?:stop|end|finish|pause)(?:\s+(?:the\s+|my\s+)?timer)?[.!]?$", re.I)
START = re.compile(
    r"^(?:start(?:\s+(?:a|the|my))?\s+timer|start\s+tracking|track|"
    r"start\s+working\s+on|start\s+work\s+on|working\s+on|start)\b\s*(?:for|on)?\s*(?P<rest>.*)$",
    re.I,
)
IDEA = re.compile(r"^(?:idea|new idea|note to self)\s*[:,\-]?\s*(?P<rest>.+)$", re.I)
STATUS = re.compile(r"^(?:what'?s|what is)\s+(?:running|the timer)|^(?:timer|status)\??$", re.I)
SUMMARY = re.compile(
    r"^(?:how much|how many|hours|summary|total|what did i (?:bill|log|do))\b.*"
    r"\b(?P<when>today|yesterday|this week|last week|this month)\b",
    re.I,
)
NUM_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "half an": 0.5,
    "half a": 0.5,
}
DURATION = re.compile(
    r"(?P<n>\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|half an|half a)\s*"
    r"(?P<unit>h|hrs?|hours?|m|mins?|minutes?)\b"
    r"(?:\s*(?:and\s*)?(?P<m2>\d+)\s*(?:m|mins?|minutes?)\b)?",
    re.I,
)
LOG = re.compile(r"^(?:log|logged|add|track|record|spent|i spent|bill)\b\s*(?P<rest>.*)$", re.I)
PARTS_OF_DAY = {"morning": time(9), "afternoon": time(13), "evening": time(18), "tonight": time(19)}


def _hours_words(seconds: int) -> str:
    if seconds < 60:
        return "less than a minute"
    m = round(seconds / 60)
    h, m = divmod(m, 60)
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h != 1 else ''}")
    if m or not h:
        parts.append(f"{m} minute{'s' if m != 1 else ''}")
    return " ".join(parts)


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _day_words(d: date, today: date) -> str:
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    if d == today - timedelta(days=1):
        return "yesterday"
    if 0 < (d - today).days < 7:
        return d.strftime("%A")
    return d.strftime("%B %-d")


def _time_words(t: time) -> str:
    return t.strftime("%-I %p") if t.minute == 0 else t.strftime("%-I:%M %p")


def _money(amount: Decimal | None, currency: str) -> str:
    if amount is None:
        return ""
    sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency, "")
    return f"{sym}{amount:,.0f}" if sym else f"{amount:,.0f} {currency}"


def _parse_duration(text: str) -> tuple[int | None, str]:
    m = DURATION.search(text)
    if not m:
        return None, text
    n = m["n"].lower()
    value = NUM_WORDS.get(n) if n in NUM_WORDS else float(n)
    unit = m["unit"].lower()
    minutes = value * 60 if unit.startswith("h") else value
    if m["m2"]:
        minutes += int(m["m2"])
    rest = (text[: m.start()] + " " + text[m.end() :]).strip()
    return int(round(minutes)), rest


def _confirm(field: str, query: str, candidates: list[dict]) -> QuickResult:
    names = ", ".join(c["name"] for c in candidates[:3])
    return QuickResult(
        action="needs_confirmation",
        needs_confirmation=True,
        candidates=candidates,
        speech=f"Which {field} did you mean: {names}?"
        if candidates
        else f"I couldn't find a {field} called {query}.",
    )


def _resolve_project_from(
    db: Session, ws: Workspace, text: str, today: date, forced: Project | None
) -> tuple[Project | None, str, quickadd.Parsed, QuickResult | None]:
    """Find a project named in free text (for/@/#name, or the whole text)."""
    parsed = quickadd.parse(text, today, tasks._targets(db, ws.id))
    if forced is not None:
        return forced, parsed.title, parsed, None
    if parsed.project_id:
        return db.get(Project, uuid.UUID(parsed.project_id)), parsed.title, parsed, None
    if parsed.client_id and parsed.candidates:
        rows = [db.get(Project, uuid.UUID(c)) for c in parsed.candidates]
        return (
            None,
            parsed.title,
            parsed,
            _confirm(
                "project",
                parsed.matched or text,
                [{"id": str(p.id), "name": p.name} for p in rows if p],
            ),
        )
    # The words left over might just be the project's name ("start timer network audit").
    words = parsed.title.strip()
    if words and not parsed.matched:
        try:
            return lookup.find_project(db, ws.id, words), "", parsed, None
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            if detail.get("code") == "needs_confirmation":
                return None, words, parsed, _confirm("project", words, detail["candidates"])
    return None, words, parsed, None


# --- Endpoint ----------------------------------------------------------------------------


@router.post("/quick", response_model=QuickResult)
def quick(
    body: QuickIn,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Natural-language-lite: tasks, timers, time, ideas and quick answers from one sentence."""
    forced = get_live(db, Project, body.project_id, "project") if body.project_id else None
    ws = resolve_workspace(db, body.workspace_id or (forced and forced.workspace_id))
    if forced is None and body.project:
        forced = lookup.find_project(db, ws.id, body.project)
    zone = tk.tz(ws)
    now_local = datetime.now(zone)
    today = now_local.date()
    text = body.text.strip().rstrip(".")

    # Stop
    if STOP.match(text):
        running = time_routes.running_entry(db)
        if running is None:
            return QuickResult(action="nothing", speech="No timer is running.")
        if body.dry_run:
            return QuickResult(action="timer_stopped", speech="I'd stop the timer.")
        e = time_routes.stop_timer(None, principal, db)
        where = f" on {e.project_name}" if e.project_name else ""
        return QuickResult(
            action="timer_stopped",
            entry=e.model_dump(mode="json"),
            speech=f"Stopped. {_hours_words(e.duration_seconds)}{where}.",
        )

    # What's running
    if STATUS.match(text):
        t = time_routes._timer_out(db)
        if t.running is None:
            return QuickResult(action="status", speech="Nothing is running.")
        where = t.running.project_name or "no project"
        return QuickResult(
            action="status",
            entry=t.running.model_dump(mode="json"),
            speech=f"{_hours_words(t.elapsed_seconds)} on {where}.",
        )

    # Summary
    if m := SUMMARY.search(text):
        when = m["when"].lower()
        first, last = {
            "today": (today, today),
            "yesterday": (today - timedelta(days=1), today - timedelta(days=1)),
            "this week": (tk.week_start(today), tk.week_start(today) + timedelta(days=6)),
            "last week": (
                tk.week_start(today) - timedelta(days=7),
                tk.week_start(today) - timedelta(days=1),
            ),
            "this month": (today.replace(day=1), today),
        }[when]
        s = reports.summary(
            workspace_id=ws.id,
            client_ref=None,
            project_ref=None,
            from_=first,
            to=last,
            _=principal,
            db=db,
        )
        hours = s.totals.hours
        speech = f"{when.capitalize()}: {hours.normalize():f} hours"
        if s.totals.amount:
            speech += f", {_money(s.totals.amount, ws.currency)} billable"
        return QuickResult(
            action="summary", summary=s.model_dump(mode="json", by_alias=True), speech=speech + "."
        )

    # Idea
    if m := IDEA.match(text):
        if body.dry_run:
            return QuickResult(action="idea_captured", speech=f"I'd save the idea {m['rest']}.")
        idea = ideas.create_idea(ideas.IdeaIn(title=m["rest"], workspace_id=ws.id), principal, db)
        return QuickResult(
            action="idea_captured",
            idea=idea.model_dump(mode="json"),
            speech=f"Saved the idea “{idea.name}”.",
        )

    # Start a timer
    if m := START.match(text):
        rest = m["rest"].strip()
        project, notes, _, ask = (
            _resolve_project_from(db, ws, rest, today, forced) if rest else (forced, "", None, None)
        )
        if ask:
            return ask
        if body.dry_run:
            return QuickResult(
                action="timer_started",
                speech=f"I'd start a timer{' for ' + project.name if project else ''}.",
            )
        t = time_routes.start_timer(
            time_routes.TimerStart(
                workspace_id=ws.id, project_id=project.id if project else None, notes=notes or None
            ),
            principal,
            db,
        )
        speech = f"Timer started for {project.name}." if project else "Timer started."
        if t.stopped:
            speech += f" Stopped {t.stopped.project_name or 'the previous timer'} first."
        return QuickResult(
            action="timer_started", entry=t.running.model_dump(mode="json"), speech=speech
        )

    # Log time: "log 2 hours on Acme yesterday afternoon", "2h on audit"
    lm = LOG.match(text)
    candidate = lm["rest"] if lm else text
    minutes, rest = _parse_duration(candidate)
    if minutes and (lm or re.match(r"^\s*(on|for)\b", rest, re.I) or rest == ""):
        rest = re.sub(r"^\s*(?:on|for)\s+", "", rest, flags=re.I)
        part = next((p for p in PARTS_OF_DAY if re.search(rf"\b{p}\b", rest, re.I)), None)
        rest = re.sub(rf"\b{part}\b", "", rest, flags=re.I).strip() if part else rest
        rest = re.sub(r"\b(?:this|in the)\b", "", rest, flags=re.I).strip()
        project, notes, parsed, ask = (
            _resolve_project_from(db, ws, rest, today, forced) if rest else (forced, "", None, None)
        )
        if ask:
            return ask
        day = parsed.due_date if parsed and parsed.due_date else today
        at = (parsed.due_time if parsed and parsed.due_time else None) or (
            PARTS_OF_DAY[part] if part else None
        )
        if at is not None:
            start = datetime.combine(day, at, zone)
        elif day == today:
            start = now_local - timedelta(minutes=minutes)
        else:
            start = datetime.combine(day, time(9), zone)
        if start + timedelta(minutes=minutes) > now_local + timedelta(minutes=1):
            start = now_local - timedelta(minutes=minutes)
        where = f" on {project.name}" if project else ""
        when = _day_words(day, today)
        if body.dry_run:
            return QuickResult(
                action="time_logged", speech=f"I'd log {_hours_words(minutes * 60)}{where} {when}."
            )
        e = time_routes.create_entry(
            time_routes.TimeEntryIn(
                workspace_id=ws.id,
                project_id=project.id if project else None,
                start_at=start,
                duration_minutes=minutes,
                notes=notes or None,
            ),
            principal,
            db,
        )
        speech = f"Logged {_hours_words(e.duration_seconds)}{where} {when}."
        if e.amount and e.billable:
            speech += f" That's {_money(e.amount, ws.currency)}."
        return QuickResult(action="time_logged", entry=e.model_dump(mode="json"), speech=speech)

    # Default: a task
    r = tasks.quick_task(db, principal, text, ws.id, forced, body.dry_run)
    if r.needs_confirmation:
        res = _confirm("project", r.matched or r.title, r.candidates)
        if not r.candidates:
            res.speech = f"Which project should “{r.title}” go in?"
        return res
    due = ""
    if r.due_date:
        due = f", due {_day_words(r.due_date, today)}"
        if r.due_time:
            due += f" at {_time_words(r.due_time)}"
    if body.dry_run or r.task is None:
        return QuickResult(
            action="task_created", speech=f"I'd add “{r.title}” to {r.project_name}{due}."
        )
    return QuickResult(
        action="task_created",
        task=r.task.model_dump(mode="json"),
        speech=f"Added “{r.title}” to {r.project_name}{due}.",
    )
