from sqlalchemy import select
from sqlalchemy.orm import Session

from sglang_ops_stack.core.auth import hash_password, verify_password
from sglang_ops_stack.db.models.user import User

VALID_ROLES = {"admin", "operator", "viewer"}


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    role: str = "viewer",
    is_active: bool = True,
) -> User:
    if role not in VALID_ROLES:
        raise ValueError("invalid role")
    existing = get_user_by_username(db, username)
    if existing is not None:
        return existing
    user = User(
        username=username,
        password_hash=hash_password(password),
        role=role,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = get_user_by_username(db, username)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user
