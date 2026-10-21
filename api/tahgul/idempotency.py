"""Idempotency-Key support for every write (SPEC: agent API design rules).

A retried voice command must never create two tasks. Any POST/PUT/PATCH/DELETE under
/api/ that carries ``Idempotency-Key`` is recorded per caller (token or session) for
24 hours:

* same key, same request  → the stored response is replayed (``Idempotent-Replayed: true``)
* same key, different body → 422 ``idempotency_key_reused``
* same key while the first request is still running → 409 ``request_in_progress``

Server errors (5xx) aren't stored, so a retry after a crash runs again.
"""

import hashlib
import uuid
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tahgul.db import get_sessionmaker
from tahgul.models import IdempotencyKey
from tahgul.timeutil import as_utc, utcnow

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
TTL = timedelta(hours=24)
STALE = timedelta(seconds=60)  # an in-progress claim older than this was abandoned
HEADER = b"idempotency-key"


def _sha(*parts: bytes) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p)
        h.update(b"\0")
    return h.hexdigest()


def _owner(headers: dict[bytes, bytes]) -> str | None:
    """Who the key belongs to: the bearer token, else the session cookie."""
    auth = headers.get(b"authorization", b"")
    if auth.lower().startswith(b"bearer "):
        return _sha(b"token", auth[7:].strip())
    cookie = headers.get(b"cookie", b"")
    for part in cookie.split(b";"):
        name, _, value = part.strip().partition(b"=")
        if name.endswith(b"_session") and value:
            return _sha(b"session", value)
    return None


def _claim(owner: str, key: str, req_hash: str, method: str, path: str):
    """Returns ("run", id) | ("replay", row) | ("mismatch", None) | ("busy", None)."""
    with get_sessionmaker()() as db:
        now = utcnow()
        db.execute(delete(IdempotencyKey).where(IdempotencyKey.created_at < now - TTL))
        row = IdempotencyKey(
            owner_hash=owner, key=key, request_hash=req_hash, method=method, path=path
        )
        db.add(row)
        try:
            db.commit()
            return "run", row.id
        except IntegrityError:
            db.rollback()
        existing = db.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.owner_hash == owner, IdempotencyKey.key == key
            )
        )
        if existing is None:  # expired and removed between the two statements
            return _claim(owner, key, req_hash, method, path)
        if existing.request_hash != req_hash:
            return "mismatch", None
        if existing.status_code is None:
            if now - as_utc(existing.created_at) > STALE:
                existing.created_at = now
                db.commit()
                return "run", existing.id
            return "busy", None
        db.expunge(existing)
        return "replay", existing


def _finish(row_id: uuid.UUID, status: int, content_type: str | None, body: bytes) -> None:
    with get_sessionmaker()() as db:
        row = db.get(IdempotencyKey, row_id)
        if row is None:
            return
        if status >= 500:
            db.delete(row)  # let a retry run for real
        else:
            row.status_code, row.content_type, row.response_body = status, content_type, body
        db.commit()


async def _send_json(send: Send, status: int, body: bytes, extra: list | None = None) -> None:
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ] + (extra or [])
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class IdempotencyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in WRITE_METHODS
            or not scope["path"].startswith("/api/")
        ):
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope["headers"]}
        raw_key = headers.get(HEADER)
        owner = _owner(headers)
        if not raw_key or owner is None:
            return await self.app(scope, receive, send)
        # Uploads can be hundreds of MB; they de-duplicate on their own (voice notes by
        # yog_id) rather than being buffered here.
        if headers.get(b"content-type", b"").startswith(b"multipart/"):
            return await self.app(scope, receive, send)
        key = raw_key.decode("latin-1").strip()[:255]

        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        method, path = scope["method"], scope["path"]
        req_hash = _sha(method.encode(), path.encode(), scope.get("query_string", b""), body)

        outcome, payload = await run_in_threadpool(_claim, owner, key, req_hash, method, path)
        if outcome == "mismatch":
            return await _send_json(send, 422, b'{"detail":"idempotency_key_reused"}')
        if outcome == "busy":
            return await _send_json(send, 409, b'{"detail":"request_in_progress"}')
        if outcome == "replay":
            row = payload
            resp = row.response_body or b""
            hdrs = [(b"idempotent-replayed", b"true"), (b"content-length", str(len(resp)).encode())]
            if row.content_type:
                hdrs.append((b"content-type", row.content_type.encode()))
            await send({"type": "http.response.start", "status": row.status_code, "headers": hdrs})
            await send({"type": "http.response.body", "body": resp})
            return

        row_id = payload
        sent_body = False

        async def replay_receive() -> Message:
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        status_code = 500
        content_type = None
        chunks: list[bytes] = []

        async def capture_send(message: Message) -> None:
            nonlocal status_code, content_type
            if message["type"] == "http.response.start":
                status_code = message["status"]
                for k, v in message.get("headers", []):
                    if k.lower() == b"content-type":
                        content_type = v.decode("latin-1")
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, replay_receive, capture_send)
        finally:
            await run_in_threadpool(_finish, row_id, status_code, content_type, b"".join(chunks))
