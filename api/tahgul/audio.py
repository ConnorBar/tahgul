"""Voice-note files: type checks, storage on disk, durations (SPEC: audio and Yog).

Files live under the data dir as audio/{yyyy}/{mm}/{uuid}.{ext}; the database keeps the
relative path, size, checksum and metadata. Point TAHGUL_DATA_DIR at a NAS mount to keep
audio off the server's disk.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from tahgul.config import get_settings

MAX_BYTES = 200 * 1024 * 1024
CHUNK = 1024 * 1024

# extension → served media type
TYPES = {
    "m4a": "audio/mp4",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "webm": "audio/webm",
    "ogg": "audio/ogg",
}


class AudioError(ValueError):
    pass


def sniff(head: bytes) -> str | None:
    """The container format from the first bytes, or None if it isn't audio we take."""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "m4a"
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "mp3"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "wav"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if head.startswith(b"OggS"):
        return "ogg"
    return None


def data_path(rel: str) -> Path:
    return get_settings().data_dir / rel


@dataclass
class Stored:
    path: str  # relative to the data dir
    ext: str
    mime_type: str
    size: int
    sha256: str


def store(src: BinaryIO, when: datetime) -> Stored:
    """Copy an upload into place, checking type (by its bytes) and size as it goes."""
    head = src.read(64)
    ext = sniff(head)
    if ext is None:
        raise AudioError("unsupported_audio")
    rel = f"audio/{when.year:04d}/{when.month:02d}/{uuid.uuid4()}.{ext}"
    dest = data_path(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256(head)
    size = len(head)
    try:
        with tmp.open("wb") as out:
            out.write(head)
            while chunk := src.read(CHUNK):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise AudioError("audio_too_large")
                digest.update(chunk)
                out.write(chunk)
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return Stored(path=rel, ext=ext, mime_type=TYPES[ext], size=size, sha256=digest.hexdigest())


def duration(rel: str) -> float | None:
    """Length in seconds when the container says (not for WebM); None if unknown."""
    try:
        import mutagen

        f = mutagen.File(data_path(rel))
        if f is not None and f.info and getattr(f.info, "length", 0):
            return round(float(f.info.length), 2)
    except Exception:  # noqa: BLE001 - a missing duration is never fatal
        return None
    return None


def remove(rel: str) -> None:
    p = data_path(rel)
    p.unlink(missing_ok=True)
    # Tidy empty month/year folders.
    for parent in (p.parent, p.parent.parent):
        try:
            parent.rmdir()
        except OSError:
            break
