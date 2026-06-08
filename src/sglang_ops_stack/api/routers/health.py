from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from sglang_ops_stack.config import get_settings
from sglang_ops_stack.db.session import get_db

router = APIRouter(prefix="/api/health", tags=["health"])
DbSession = Annotated[Session, Depends(get_db)]


class AppHealth(BaseModel):
    name: str
    environment: str
    status: Literal["ok"]


class DatabaseHealth(BaseModel):
    status: Literal["ok", "error"]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    app: AppHealth
    database: DatabaseHealth


@router.get("", response_model=HealthResponse)
def api_health(db: DbSession) -> HealthResponse:
    settings = get_settings()
    database_status: Literal["ok", "error"] = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        database_status = "error"

    return HealthResponse(
        status="ok" if database_status == "ok" else "degraded",
        app=AppHealth(
            name=settings.app_name,
            environment=settings.environment,
            status="ok",
        ),
        database=DatabaseHealth(status=database_status),
    )
