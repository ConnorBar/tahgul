"""Background transcription with a local Whisper server (SPEC: audio, milestone 8b).

Any server with the OpenAI speech-to-text API works: speaches (faster-whisper) in the
stack by default, or whisper.cpp's server, or a hosted one. Tahgul posts the stored
file to ``{TAHGUL_WHISPER_URL}/v1/audio/transcriptions`` and keeps the text.

Notes waiting for a transcript have ``transcript_status = "pending"``. They're picked up
right after upload (a background task) and by a sweep every minute, so a restart or a
Whisper outage only delays them. A failure marks the note ``failed`` with the reason;
"Transcribe" in the app queues it again.
"""

from __future__ import annotations

import logging
import threading

import httpx
from sqlalchemy import select

from tahgul import audio
from tahgul.config import get_settings
from tahgul.db import get_sessionmaker
from tahgul.models import VoiceNote

log = logging.getLogger("tahgul.transcribe")

# Tests swap in an httpx.MockTransport here.
_transport: httpx.BaseTransport | None = None
# One worker at a time: Whisper on a homelab CPU is the bottleneck anyway.
_lock = threading.Lock()

TIMEOUT = httpx.Timeout(connect=10, read=1800, write=300, pool=10)
BATCH = 10


class TranscriptionError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(get_settings().whisper_url)


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=get_settings().whisper_url.rstrip("/"), timeout=TIMEOUT, transport=_transport
    )


def _post(http: httpx.Client, note: VoiceNote) -> httpx.Response:
    s = get_settings()
    data = {"model": s.whisper_model, "response_format": "json"}
    if s.whisper_language:
        data["language"] = s.whisper_language
    ext = note.path.rsplit(".", 1)[-1]
    with audio.data_path(note.path).open("rb") as f:
        return http.post(
            "/v1/audio/transcriptions",
            files={"file": (f"note.{ext}", f, note.mime_type)},
            data=data,
        )


def transcribe_file(note: VoiceNote) -> str:
    """Send one note's audio to Whisper and return the text."""
    if not audio.data_path(note.path).exists():
        raise TranscriptionError("The audio file is missing.")
    model = get_settings().whisper_model
    try:
        with _client() as http:
            r = _post(http, note)
            if r.status_code in (400, 404) and "model" in r.text.lower():
                # speaches only serves models it has downloaded: fetch it once, then retry.
                log.info("Asking the Whisper server to download %s", model)
                http.post(f"/v1/models/{model}", timeout=httpx.Timeout(1800, connect=10))
                r = _post(http, note)
    except httpx.HTTPError as e:
        raise TranscriptionError(f"Couldn't reach the Whisper server: {e}") from e
    if not r.is_success:
        raise TranscriptionError(f"Whisper server said {r.status_code}: {r.text[:300]}")
    try:
        body = r.json()
    except ValueError:
        return r.text.strip()
    text = body.get("text") if isinstance(body, dict) else None
    if text is None:
        raise TranscriptionError("The Whisper server's reply had no text.")
    return text.strip()


def run_pending(limit: int = BATCH) -> int:
    """Transcribe waiting notes, oldest first. Returns how many were done."""
    if not enabled() or not _lock.acquire(blocking=False):
        return 0
    done = 0
    try:
        with get_sessionmaker()() as db:
            ids = db.scalars(
                select(VoiceNote.id)
                .where(VoiceNote.transcript_status == "pending", VoiceNote.deleted_at.is_(None))
                .order_by(VoiceNote.created_at)
                .limit(limit)
            ).all()
            for note_id in ids:
                note = db.get(VoiceNote, note_id)
                if note is None or note.transcript_status != "pending":
                    continue
                try:
                    text = transcribe_file(note)
                except TranscriptionError as e:
                    db.refresh(note)
                    if note.transcript_status == "pending":
                        note.transcript_status, note.transcript_error = "failed", str(e)[:500]
                        db.commit()
                    log.warning("Transcription failed for %s: %s", note_id, e)
                    continue
                db.refresh(note)
                # Someone typed a transcript meanwhile, or deleted the note: theirs wins.
                if note.transcript_status != "pending" or note.deleted_at is not None:
                    continue
                note.transcript = text or None
                note.transcript_source = "whisper"
                note.transcript_status = "done"
                note.transcript_error = None
                db.commit()
                done += 1
    except Exception:  # never take the scheduler down
        log.exception("Transcription sweep failed")
    finally:
        _lock.release()
    return done
