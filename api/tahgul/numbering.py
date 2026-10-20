"""Invoice number patterns (SPEC: "Pattern like {Y}-{seq:3}, stored, gap-free").

Tokens:
    {Y}       four-digit year        2026
    {y}       two-digit year         26
    {M}       two-digit month        10
    {CLIENT}  the client's code      ACME
    {seq}     the counter            7
    {seq:N}   counter, zero-padded   007 (N = 1-9)

The counter restarts whenever the most specific period in the pattern changes:
monthly if {M} is present, yearly if {Y}/{y} is, otherwise never. With {CLIENT}
each client has its own counter. Numbers are assigned in milestone 7.
"""

import re
from dataclasses import dataclass
from datetime import date

TOKEN_RE = re.compile(r"\{(Y|y|M|CLIENT|seq(?::([1-9]))?)\}")
MAX_LENGTH = 50
ALLOWED_LITERAL = re.compile(r"^[A-Za-z0-9 ._/#-]*$")


@dataclass(frozen=True)
class Preset:
    pattern: str
    label: str
    resets: str


PRESETS = (
    Preset("{Y}-{seq:3}", "Year and number", "yearly"),
    Preset("INV-{seq:4}", "Running number", "never"),
    Preset("{Y}{M}-{seq:3}", "Year, month and number", "monthly"),
    Preset("{CLIENT}-{Y}-{seq:3}", "Client code, year and number", "yearly, per client"),
)


def validate_pattern(pattern: str) -> list[str]:
    """Return a list of problems; empty means the pattern is usable."""
    errors: list[str] = []
    if not pattern or not pattern.strip():
        return ["Pattern can't be empty."]
    if len(pattern) > MAX_LENGTH:
        errors.append(f"Keep it under {MAX_LENGTH} characters.")
    seq_count = sum(1 for m in TOKEN_RE.finditer(pattern) if m.group(1).startswith("seq"))
    if seq_count != 1:
        errors.append("Include the counter exactly once: {seq} or {seq:3}.")
    literal = TOKEN_RE.sub("", pattern)
    if "{" in literal or "}" in literal:
        errors.append("Unknown token. Use {Y}, {y}, {M}, {CLIENT} and {seq} or {seq:N}.")
    elif not ALLOWED_LITERAL.match(literal):
        errors.append("Use letters, digits, spaces and - _ . / # outside the tokens.")
    return errors


def uses_client_code(pattern: str) -> bool:
    return "{CLIENT}" in pattern


def format_number(pattern: str, *, on: date, seq: int, client_code: str | None = None) -> str:
    if validate_pattern(pattern):
        raise ValueError(f"invalid invoice number pattern: {pattern!r}")

    def sub(m: re.Match) -> str:
        token, width = m.group(1), m.group(2)
        if token == "Y":
            return f"{on.year:04d}"
        if token == "y":
            return f"{on.year % 100:02d}"
        if token == "M":
            return f"{on.month:02d}"
        if token == "CLIENT":
            return (client_code or "CLIENT").upper()
        return str(seq).zfill(int(width or 1))

    return TOKEN_RE.sub(sub, pattern)


def sequence_key(pattern: str, *, on: date, client_id: str | None = None) -> str:
    """The counter bucket a number belongs to; a new key starts again at 1."""
    parts = [pattern]
    if "{M}" in pattern:
        parts.append(f"{on.year:04d}-{on.month:02d}")
    elif "{Y}" in pattern or "{y}" in pattern:
        parts.append(f"{on.year:04d}")
    if uses_client_code(pattern):
        parts.append(f"client:{client_id}")
    return "|".join(parts)


def examples(pattern: str, *, on: date, client_code: str = "ACME") -> list[str]:
    return [format_number(pattern, on=on, seq=n, client_code=client_code) for n in (1, 2, 42)]
