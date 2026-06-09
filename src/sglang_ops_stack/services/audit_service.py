from typing import Any

from sqlalchemy.orm import Session

from sglang_ops_stack.db.models.audit import AuditLog
from sglang_ops_stack.db.models.user import User
from sglang_ops_stack.utils.masking import mask_sensitive_data


def record_event(
    db: Session,
    *,
    event_type: str,
    actor: User | None = None,
    target_type: str | None = None,
    target_id: int | str | None = None,
    summary: dict[str, Any] | None = None,
) -> AuditLog:
    audit = AuditLog(
        event_type=event_type,
        actor_id=actor.id if actor else None,
        actor_username=actor.username if actor else None,
        actor_role=actor.role if actor else None,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        summary=mask_sensitive_data(summary or {}),
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)
    return audit
