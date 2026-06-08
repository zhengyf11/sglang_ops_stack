from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.deployment import DeploymentCreate
from sglang_ops_stack.api.schemas.host import HostCreate, HostUpdate
from sglang_ops_stack.db.session import get_db
from sglang_ops_stack.domain_enums import JobStatus, JobType
from sglang_ops_stack.jobs.deployment_jobs import run_deployment_task
from sglang_ops_stack.services import deployment_service, host_service, job_service
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


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "dashboard.html", {})


@router.get("/deployments", response_class=HTMLResponse)
def deployments_page(request: Request, db: DbSession) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "deployments/list.html",
        {"deployments": deployment_service.list_deployments(db)},
    )


@router.get("/deployments/new", response_class=HTMLResponse)
def new_deployment_page(request: Request, db: DbSession) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "deployments/create.html",
        {"hosts": host_service.list_hosts(db)},
    )


@router.post("/deployments")
def create_deployment_page(
    db: DbSession,
    host_id: Annotated[int, Form()],
    name: Annotated[str, Form()],
    container_name: Annotated[str, Form()],
    image: Annotated[str, Form()],
    model_path: Annotated[str, Form()],
    port: Annotated[int, Form()] = 30000,
    served_model_name: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    deployment = deployment_service.create_deployment(
        db,
        DeploymentCreate(
            host_id=host_id,
            name=name,
            container_name=container_name,
            image=image,
            model_path=model_path,
            served_model_name=served_model_name,
            port=port,
        ),
    )
    return _redirect(f"/deployments/{deployment.id}")


@router.get("/deployments/{deployment_id}", response_class=HTMLResponse)
def deployment_detail_page(deployment_id: int, request: Request, db: DbSession) -> HTMLResponse:
    deployment = deployment_service.get_deployment(db, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    job = job_service.get_job(db, deployment.last_job_id) if deployment.last_job_id else None
    logs = job_service.list_logs(db, job.id) if job else []
    return templates.TemplateResponse(
        request,
        "deployments/detail.html",
        {"deployment": deployment, "job": job, "logs": logs},
    )


@router.post("/deployments/{deployment_id}/deploy")
def deploy_deployment_page(
    deployment_id: int,
    background_tasks: BackgroundTasks,
    db: DbSession,
    password: Annotated[str, Form()],
    confirm_remove_existing: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    deployment = deployment_service.get_deployment(db, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    job = deployment_service.create_deployment_job(db, deployment)
    background_tasks.add_task(
        run_deployment_task,
        job.id,
        password,
        confirm_remove_existing == "yes",
    )
    return _redirect(f"/jobs/{job.id}")


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
    if job.type != JobType.environment_check.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job is not an environment check",
        )
    if job.status != JobStatus.waiting_confirmation.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job is not waiting for confirmation",
        )
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
    if job.type != JobType.environment_check.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only environment check jobs can be retried",
        )
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
