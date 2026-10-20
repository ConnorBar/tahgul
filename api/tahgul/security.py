"""Password hashing, opaque tokens and TOTP."""

import hashlib
import secrets
import time
from datetime import UTC, datetime

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()

TOKEN_PREFIX = "thg"
MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# A dummy hash so a login for an unknown email takes as long as a real one.
DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_api_token() -> tuple[str, str]:
    """Return (full token, public prefix). The full token is shown to the owner once."""
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    return f"{TOKEN_PREFIX}_{prefix}_{secret}", prefix


def parse_api_token(token: str) -> str | None:
    """Return the prefix of a well-formed token, else None."""
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX or len(parts[1]) != 8:
        return None
    return parts[1]


# --- TOTP -------------------------------------------------------------------

TOTP_ISSUER = "Tahgul"


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=TOTP_ISSUER)


def verify_totp(
    secret: str, code: str, last_step: int | None, now: float | None = None
) -> int | None:
    """Check a 6-digit code against the current step +/- 1.

    Returns the matched time step (store it to block replays), or None if the code is
    invalid or was already used.
    """
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return None
    totp = pyotp.TOTP(secret)
    now = time.time() if now is None else now
    current = totp.timecode(datetime.fromtimestamp(now, UTC))
    for step in (current - 1, current, current + 1):
        if last_step is not None and step <= last_step:
            continue
        expected = totp.generate_otp(step)
        if secrets.compare_digest(expected, code):
            return step
    return None
