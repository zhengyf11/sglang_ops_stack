from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from sglang_ops_stack.api.routers import (
    deployments,
    health,
    hosts,
    jobs,
    monitoring,
    operations,
    pages,
)
from sglang_ops_stack.config import get_settings
from sglang_ops_stack.core.logging import configure_logging
from sglang_ops_stack.core.security import build_security_config
from sglang_ops_stack.db.base import Base
from sglang_ops_stack.db.models import (  # noqa: F401
    Deployment,
    DeploymentRevision,
    Host,
    Job,
    JobLog,
    MonitoringConfig,
)
from sglang_ops_stack.db.session import engine

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


def _sanitize_validation_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for error in errors:
        item = dict(error)
        item.pop("input", None)
        sanitized.append(item)
    return sanitized


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _ = app
    settings = get_settings()
    configure_logging(settings)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    docs_url = "/docs" if settings.docs_enabled else None
    redoc_url = "/redoc" if settings.docs_enabled else None
    openapi_url = "/openapi.json" if settings.docs_enabled else None
    app = FastAPI(
        title=settings.app_name,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )
    security_config = build_security_config(settings)
    app.state.security = security_config
    Base.metadata.create_all(bind=engine)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(health.router)
    app.include_router(hosts.router)
    app.include_router(jobs.router)
    app.include_router(deployments.router)
    app.include_router(operations.router)
    app.include_router(monitoring.router)
    app.include_router(pages.router)

    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next: Any) -> Any:
        _ = request
        response = await call_next(request)
        for name, value in security_config.headers.items():
            response.headers[name] = value
        return response

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
    def health_legacy() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
