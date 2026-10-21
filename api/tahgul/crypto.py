"""Encrypting stored credentials (SPEC: "Secrets ... encrypted at rest with a key from an
environment variable, never in the repo")."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status

from tahgul.config import get_settings


def _fernet() -> Fernet:
    key = get_settings().secret_key
    if not key or len(key) < 16:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="secret_key_missing",
        )
    # Any long random string works; derive a Fernet key from it.
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="secret_key_changed"
        ) from exc
