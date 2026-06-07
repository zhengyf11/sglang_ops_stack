from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.host import HostCreate, HostUpdate
from sglang_ops_stack.db.session import get_db
from sglang_ops_stack.domain_enums import JobType
from sglang_ops_stack.services import host_service, job_service
from sglang_ops_stack.services.environment.runner import (
    confirm_environment_install_task,
    run_environment_check_task,
)
from sglang_ops_stack.services.ssh_connect_check import run_ssh_connect_check_task

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "web" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
router = APIRouter(tags=["pages"])
DbSession = Annotated[Session, Depends(get_db)]


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return _redirect("/hosts")


@router.get("/hosts", response_class=HTMLResponse)
def hosts_page(request: Request, db: DbSession) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "hosts/list.html",
        {"hosts": host_service.list_hosts(db)},
    )


@router.get("/hosts/new", response_class=HTMLResponse)
def new_host_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "hosts/create.html", {})


@router.post("/hosts")
def create_host_page(
    db: DbSession,
    name: Annotated[str, Form()],
    ip: Annotated[str, Form()],
    ssh_port: Annotated[int, Form()] = 22,
    ssh_user: Annotated[str, Form()] = "root",
    tags: Annotated[str | None, Form()] = None,
    note: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    host = host_service.create_host(
        db,
        HostCreate(
            name=name,
            ip=ip,
            ssh_port=ssh_port,
            ssh_user=ssh_user,
            tags=tags,
            note=note,
        ),
    )
    return _redirect(f"/hosts/{host.id}")


@router.get("/hosts/{host_id}", response_class=HTMLResponse)
def host_detail_page(host_id: int, request: Request, db: DbSession) -> HTMLResponse:
    host = host_service.get_host(db, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    return templates.TemplateResponse(request, "hosts/detail.html", {"host": host})


@router.get("/hosts/{host_id}/edit", response_class=HTMLResponse)
def edit_host_page(host_id: int, request: Request, db: DbSession) -> HTMLResponse:
    host = host_service.get_host(db, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    return templates.TemplateResponse(request, "hosts/edit.html", {"host": host})


@router.post("/hosts/{host_id}")
def update_host_page(
    host_id: int,
    db: DbSession,
    name: Annotated[str, Form()],
    ip: Annotated[str, Form()],
    ssh_port: Annotated[int, Form()],
    ssh_user: Annotated[str, Form()],
    tags: Annotated[str | None, Form()] = None,
    note: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    host = host_service.get_host(db, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    host_service.update_host(
        db,
        host,
        HostUpdate(
            name=name,
            ip=ip,
            ssh_port=ssh_port,
            ssh_user=ssh_user,
            tags=tags,
            note=note,
        ),
    )
    return _redirect(f"/hosts/{host_id}")


@router.post("/hosts/{host_id}/delete")
def delete_host_page(host_id: int, db: DbSession) -> RedirectResponse:
    host = host_service.get_host(db, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    host_service.delete_host(db, host)
    return _redirect("/hosts")


@router.post("/hosts/{host_id}/ssh-check")
def ssh_check_page(
    host_id: int,
    background_tasks: BackgroundTasks,
    db: DbSession,
    password: Annotated[str, Form()],
) -> RedirectResponse:
    host = host_service.get_host(db, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    job = job_service.create_job(db, target_id=host_id)
    background_tasks.add_task(run_ssh_connect_check_task, job.id, password)
    return _redirect(f"/jobs/{job.id}")


@router.post("/hosts/{host_id}/environment/check")
def environment_check_page(
    host_id: int,
    background_tasks: BackgroundTasks,
    db: DbSession,
    password: Annotated[str, Form()],
) -> RedirectResponse:
    host = host_service.get_host(db, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    job = job_service.create_job(db, target_id=host_id, job_type=JobType.environment_check)
    background_tasks.add_task(run_environment_check_task, job.id, password)
    return _redirect(f"/jobs/{job.id}")


@router.post("/jobs/{job_id}/confirm-install")
def confirm_install_page(
    job_id: int,
    background_tasks: BackgroundTasks,
    db: DbSession,
    password: Annotated[str, Form()],
    confirmed: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    job = job_service.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if confirmed != "yes":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confirmation is required",
        )
    background_tasks.add_task(confirm_environment_install_task, job.id, password, True)
    return _redirect(f"/jobs/{job.id}")


@router.post("/jobs/{job_id}/retry")
def retry_job_page(
    job_id: int,
    background_tasks: BackgroundTasks,
    db: DbSession,
    password: Annotated[str, Form()],
) -> RedirectResponse:
    job = job_service.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    job_service.reset_for_retry(db, job)
    background_tasks.add_task(run_environment_check_task, job.id, password)
    return _redirect(f"/jobs/{job.id}")


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail_page(job_id: int, request: Request, db: DbSession) -> HTMLResponse:
    job = job_service.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    logs = job_service.list_logs(db, job_id)
    return templates.TemplateResponse(request, "jobs/detail.html", {"job": job, "logs": logs})
