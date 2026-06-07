from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from sglang_ops_stack.api.routers import hosts, jobs, pages
from sglang_ops_stack.config import get_settings
from sglang_ops_stack.db.base import Base
from sglang_ops_stack.db.models import Host, Job, JobLog  # noqa: F401
from sglang_ops_stack.db.session import engine

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


def _sanitize_validation_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for error in errors:
        item = dict(error)
        item.pop("input", None)
        sanitized.append(item)
    return sanitized


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)
    Base.metadata.create_all(bind=engine)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(hosts.router)
    app.include_router(jobs.router)
    app.include_router(pages.router)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        _ = request
        return JSONResponse(
            status_code=422,
            content={"detail": _sanitize_validation_errors(exc.errors())},
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
