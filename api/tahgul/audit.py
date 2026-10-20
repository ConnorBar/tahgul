from typing import Any

from sqlalchemy.orm import Session

from tahgul.models import AuditLog
from tahgul.principal import Principal


def record(
    db: Session,
    actor: Principal | None,
    action: str,
    entity: str,
    entity_id: Any = None,
    changes: dict[str, Any] | None = None,
) -> None:
    """Add an audit row to the current transaction. The caller commits."""
    db.add(
        AuditLog(
            actor_type=actor.kind if actor else "system",
            actor_name=actor.name if actor else "system",
            action=action,
            entity=entity,
            entity_id=str(entity_id) if entity_id is not None else None,
            changes=changes,
        )
    )
