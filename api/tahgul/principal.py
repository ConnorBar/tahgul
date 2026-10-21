import uuid
from dataclasses import dataclass, field
from typing import Literal

# Scopes an API token can hold (SPEC: agent API design rules). "admin" is needed to
# delete records or change rates, which agent tokens can't do by default
# (SPEC: security, agent limits).
TOKEN_SCOPES = ("read", "write", "invoice", "admin")


@dataclass(frozen=True)
class Principal:
    """Who is making a request: the owner in a browser session, or a named API token."""

    kind: Literal["owner", "token", "sync"]
    name: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    user_id: uuid.UUID | None = None
    token_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None

    @property
    def is_owner(self) -> bool:
        return self.kind == "owner"

    def has(self, scope: str) -> bool:
        return self.is_owner or scope in self.scopes
