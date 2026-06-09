from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from sglang_ops_stack.api.deps import require_role
from sglang_ops_stack.api.schemas.auth import LoginRequest, TokenResponse, UserCreate, UserRead
from sglang_ops_stack.core.auth import create_access_token
from sglang_ops_stack.db.models.user import User
from sglang_ops_stack.db.session import get_db
from sglang_ops_stack.services import audit_service, user_service

router = APIRouter(prefix="/api/auth", tags=["auth"])
DbSession = Annotated[Session, Depends(get_db)]
AdminUser = Annotated[User, Depends(require_role("admin"))]


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: DbSession) -> TokenResponse:
    user = user_service.authenticate_user(db, payload.username, payload.password)
    if user is None:
        audit_service.record_event(
            db,
            event_type="auth.login_failed",
            target_type="user",
            target_id=payload.username,
            summary={"username": payload.username},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    audit_service.record_event(
        db,
        event_type="auth.login_succeeded",
        actor=user,
        target_type="user",
        target_id=user.id,
        summary={"username": user.username},
    )
    return TokenResponse(access_token=create_access_token(user), user=UserRead.model_validate(user))


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, db: DbSession, admin: AdminUser) -> UserRead:
    try:
        user = user_service.create_user(
            db,
            username=payload.username,
            password=payload.password,
            role=payload.role,
            is_active=payload.is_active,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    audit_service.record_event(
        db,
        event_type="auth.user_create",
        actor=admin,
        target_type="user",
        target_id=user.id,
        summary={"username": user.username, "role": user.role, "is_active": user.is_active},
    )
    return UserRead.model_validate(user)
