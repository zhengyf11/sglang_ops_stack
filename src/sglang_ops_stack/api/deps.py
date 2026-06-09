from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from sglang_ops_stack.core.auth import decode_access_token
from sglang_ops_stack.db.models.user import User
from sglang_ops_stack.db.session import get_db
from sglang_ops_stack.services import audit_service, user_service

DbSession = Annotated[Session, Depends(get_db)]
_bearer = HTTPBearer(auto_error=False)
RoleDependency = Callable[[Request, DbSession, HTTPAuthorizationCredentials | None], User]


ROLE_LEVELS = {"viewer": 1, "operator": 2, "admin": 3}


def current_user(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    raw_user_id = payload.get("sub")
    if not isinstance(raw_user_id, str):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    try:
        user_id = int(raw_user_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from None
    user = user_service.get_user(db, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")
    request.state.current_user = user
    return user


def optional_user(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User | None:
    if credentials is None:
        return None
    try:
        return current_user(request, db, credentials)
    except HTTPException:
        return None


def require_role(
    min_role: str,
) -> Callable[[Request, DbSession, HTTPAuthorizationCredentials | None], User]:
    min_level = ROLE_LEVELS[min_role]

    def dependency(
        request: Request,
        db: DbSession,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
    ) -> User:
        user = current_user(request, db, credentials)
        if ROLE_LEVELS.get(user.role, 0) < min_level:
            audit_service.record_event(
                db,
                event_type="auth.permission_denied",
                actor=user,
                target_type="route",
                target_id=request.url.path,
                summary={
                    "method": request.method,
                    "path": request.url.path,
                    "required_role": min_role,
                },
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return dependency
