"""Friendly lookups: a project, client or activity by id or by name (SPEC: agent API).

Exact matches (id, name, normalised name, client code) resolve. A client name used
for a project resolves to its only open project. Anything fuzzier comes back as
409 ``needs_confirmation`` with candidates instead of a guess.
"""

import difflib
import re
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from tahgul.models import Activity, Client, Project

OPEN = ("active", "paused", "idea")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _as_uuid(ref: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(ref))
    except ValueError:
        return None


def needs_confirmation(field: str, query: str, candidates: list[dict]) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "code": "needs_confirmation",
            "field": field,
            "query": query,
            "candidates": candidates,
            "message": f"Which {field} did you mean by “{query}”?",
        },
    )


def _fuzzy(query: str, names: dict[str, str]) -> list[str]:
    """ids whose name contains the query or is close to it, best first."""
    q = _norm(query)
    if not q:
        return []
    scored = []
    for id_, name in names.items():
        n = _norm(name)
        if q in n or n in q:
            scored.append((0.0, id_))
        else:
            ratio = difflib.SequenceMatcher(None, q, n).ratio()
            if ratio >= 0.6:
                scored.append((1 - ratio, id_))
    return [i for _, i in sorted(scored)][:5]


def _project_card(p: Project) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "client": p.client.name if p.client else None,
        "status": p.status,
    }


def find_client(db: Session, workspace_id: uuid.UUID, ref: str) -> Client:
    clients = db.scalars(
        select(Client).where(Client.workspace_id == workspace_id, Client.deleted_at.is_(None))
    ).all()
    hit = _exact(clients, ref, codes=True)
    if hit:
        return hit
    ids = _fuzzy(
        ref, {str(c.id): c.name for c in clients} | {str(c.id): c.code for c in clients if c.code}
    )
    if ids:
        by_id = {str(c.id): c for c in clients}
        raise needs_confirmation("client", ref, [{"id": i, "name": by_id[i].name} for i in ids])
    raise HTTPException(404, detail={"code": "client_not_found", "query": ref})


def find_project(db: Session, workspace_id: uuid.UUID, ref: str) -> Project:
    projects = db.scalars(
        select(Project).where(Project.workspace_id == workspace_id, Project.deleted_at.is_(None))
    ).all()
    hit = _exact(projects, ref)
    if hit:
        return hit
    # A client's name stands for its only open project.
    clients = db.scalars(
        select(Client).where(Client.workspace_id == workspace_id, Client.deleted_at.is_(None))
    ).all()
    client = _exact(clients, ref, codes=True)
    if client:
        theirs = [p for p in projects if p.client_id == client.id and p.status in OPEN]
        if len(theirs) == 1:
            return theirs[0]
        if theirs:
            raise needs_confirmation("project", ref, [_project_card(p) for p in theirs])
    live = [p for p in projects if p.status in OPEN]
    ids = _fuzzy(ref, {str(p.id): p.name for p in live})
    if ids:
        by_id = {str(p.id): p for p in projects}
        raise needs_confirmation("project", ref, [_project_card(by_id[i]) for i in ids])
    raise HTTPException(404, detail={"code": "project_not_found", "query": ref})


def find_activity(db: Session, workspace_id: uuid.UUID, ref: str) -> Activity:
    acts = db.scalars(
        select(Activity).where(Activity.workspace_id == workspace_id, Activity.deleted_at.is_(None))
    ).all()
    hit = _exact(acts, ref)
    if hit:
        return hit
    ids = _fuzzy(ref, {str(a.id): a.name for a in acts})
    if ids:
        by_id = {str(a.id): a for a in acts}
        raise needs_confirmation("activity", ref, [{"id": i, "name": by_id[i].name} for i in ids])
    raise HTTPException(404, detail={"code": "activity_not_found", "query": ref})


def _exact(rows, ref: str, codes: bool = False):
    ref = str(ref).strip()
    as_id = _as_uuid(ref)
    if as_id is not None:
        return next((r for r in rows if r.id == as_id), None)
    low, n = ref.lower(), _norm(ref)
    for test in (
        lambda r: r.name.lower() == low,
        lambda r: _norm(r.name) == n,
        lambda r: codes and r.code and _norm(r.code) == n,
    ):
        matches = [r for r in rows if test(r)]
        if len(matches) == 1:
            return matches[0]
    return None
