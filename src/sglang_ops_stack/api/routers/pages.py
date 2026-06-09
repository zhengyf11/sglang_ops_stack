from pathlib import Path
from typing import Annotated, Literal, cast

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.deployment import (
    DeploymentCreate,
    DeploymentRevisionRead,
    DockerConfig,
)
from sglang_ops_stack.api.schemas.host import HostCreate, HostUpdate
from sglang_ops_stack.api.schemas.monitoring import MonitoringConfigUpsert
from sglang_ops_stack.db.models.host import Host
from sglang_ops_stack.db.session import get_db
from sglang_ops_stack.domain_enums import JobStatus, JobType
from sglang_ops_stack.jobs.deployment_jobs import run_deployment_task
from sglang_ops_stack.jobs.operation_jobs import run_operation_task
from sglang_ops_stack.jobs.redeploy_jobs import run_redeploy_task
from sglang_ops_stack.remote.executor import SSHExecutor
from sglang_ops_stack.services import (
    deployment_service,
    host_service,
    job_service,
    monitoring_service,
    operation_service,
    redeploy_service,
)
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
def index(request: Request, db: DbSession) -> HTMLResponse:
    hosts = host_service.list_hosts(db)
    deployments = deployment_service.list_deployments(db)
    summary = monitoring_service.dashboard_summary(db, hosts=hosts, deployments=deployments)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"summary": summary, "deployments": deployments},
    )


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
    host = db.get(Host, deployment.host_id)
    config = monitoring_service.get_config(db)
    monitoring = None
    if host is not None:
        monitoring = {
            "target": monitoring_service.build_metrics_target(host, deployment),
            "scrape_config": monitoring_service.build_prometheus_scrape_config(host, deployment),
            "links": monitoring_service.build_monitoring_links(config, host, deployment),
            "metrics_status": monitoring_service.metrics_status_from_health(deployment),
        }
    can_restart = deployment.status in {"running", "degraded", "failed"}
    can_stop = deployment.status in {"running", "degraded", "failed"}
    can_start = deployment.status in {"stopped", "failed"}
    return templates.TemplateResponse(
        request,
        "deployments/detail.html",
        {
            "deployment": deployment,
            "job": job,
            "logs": logs,
            "monitoring": monitoring,
            "can_restart": can_restart,
            "can_stop": can_stop,
            "can_start": can_start,
        },
    )


@router.post("/deployments/{deployment_id}/operations/{operation}")
def deployment_operation_page(
    deployment_id: int,
    operation: str,
    background_tasks: BackgroundTasks,
    db: DbSession,
    password: Annotated[str, Form()],
) -> RedirectResponse:
    if operation not in {"restart", "stop", "start"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    typed_operation = cast(Literal["restart", "stop", "start"], operation)
    deployment = deployment_service.get_deployment(db, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    try:
        job = operation_service.create_operation_job(db, deployment, typed_operation)
    except operation_service.OperationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    background_tasks.add_task(run_operation_task, job.id, typed_operation, password)
    return _redirect(f"/jobs/{job.id}")


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


@router.get("/deployments/{deployment_id}/logs", response_class=HTMLResponse)
def deployment_logs_page(deployment_id: int, request: Request, db: DbSession) -> HTMLResponse:
    deployment = deployment_service.get_deployment(db, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    return templates.TemplateResponse(request, "deployments/logs.html", {"deployment": deployment})


@router.post("/deployments/{deployment_id}/logs", response_class=HTMLResponse)
def deployment_logs_submit_page(
    deployment_id: int,
    request: Request,
    db: DbSession,
    password: Annotated[str, Form()],
    tail: Annotated[int, Form()] = 100,
) -> HTMLResponse:
    deployment = deployment_service.get_deployment(db, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    host = db.get(Host, deployment.host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Host not found")
    safe_tail, log_text = operation_service.read_container_logs(
        db,
        deployment=deployment,
        host=host,
        password=password,
        tail=tail,
        executor=SSHExecutor(),
    )
    return templates.TemplateResponse(
        request,
        "deployments/logs.html",
        {"deployment": deployment, "tail": safe_tail, "log_text": log_text},
    )


@router.get("/deployments/{deployment_id}/redeploy", response_class=HTMLResponse)
def redeploy_page(deployment_id: int, request: Request, db: DbSession) -> HTMLResponse:
    deployment = deployment_service.get_deployment(db, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    payload = deployment_service.payload_from_deployment(deployment)
    plan = redeploy_service.plan_redeploy(deployment, payload)
    return templates.TemplateResponse(
        request,
        "deployments/redeploy.html",
        {"deployment": deployment, "plan": plan, "payload": payload},
    )


@router.post("/deployments/{deployment_id}/redeploy")
def redeploy_submit_page(
    deployment_id: int,
    background_tasks: BackgroundTasks,
    db: DbSession,
    password: Annotated[str, Form()],
    name: Annotated[str, Form()],
    container_name: Annotated[str, Form()],
    image: Annotated[str, Form()],
    port: Annotated[int, Form()],
    model_path: Annotated[str | None, Form()] = None,
    privileged: Annotated[str | None, Form()] = None,
    confirm_high_risk: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    deployment = deployment_service.get_deployment(db, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    docker_config = DockerConfig(privileged=privileged == "yes")
    payload = DeploymentCreate(
        host_id=deployment.host_id,
        name=name,
        container_name=container_name,
        image=image,
        model_path=model_path if model_path else deployment.model_path,
        port=port,
        docker_config=docker_config,
    )
    confirmed = confirm_high_risk == "yes"
    try:
        job, _plan = redeploy_service.create_redeploy_job(
            db,
            deployment,
            payload,
            confirm_high_risk=confirmed,
        )
    except redeploy_service.RedeployError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    background_tasks.add_task(run_redeploy_task, job.id, password, payload.model_dump(), confirmed)
    return _redirect(f"/jobs/{job.id}")


@router.get("/deployments/{deployment_id}/revisions", response_class=HTMLResponse)
def revisions_page(deployment_id: int, request: Request, db: DbSession) -> HTMLResponse:
    deployment = deployment_service.get_deployment(db, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    return templates.TemplateResponse(
        request,
        "deployments/revisions.html",
        {
            "deployment": deployment,
            "revisions": deployment_service.list_revisions(db, deployment_id),
        },
    )


@router.get("/deployments/{deployment_id}/revisions/{revision_id}", response_class=HTMLResponse)
def revision_detail_page(
    deployment_id: int, revision_id: int, request: Request, db: DbSession
) -> HTMLResponse:
    deployment = deployment_service.get_deployment(db, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    revision = deployment_service.get_revision(db, deployment_id, revision_id)
    if revision is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Revision not found")
    safe_revision = DeploymentRevisionRead.model_validate(revision)
    return templates.TemplateResponse(
        request,
        "deployments/revision_detail.html",
        {"deployment": deployment, "revision": safe_revision},
    )


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


@router.get("/monitoring", response_class=HTMLResponse)
def monitoring_page(request: Request, db: DbSession) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "monitoring/config.html",
        {"config": monitoring_service.get_config(db), "error": None},
    )


@router.post("/monitoring", response_class=HTMLResponse, response_model=None)
def monitoring_submit_page(
    request: Request,
    db: DbSession,
    prometheus_base_url: Annotated[str | None, Form()] = None,
    grafana_base_url: Annotated[str | None, Form()] = None,
    default_dashboard_path: Annotated[str | None, Form()] = None,
) -> HTMLResponse | RedirectResponse:
    try:
        monitoring_service.upsert_config(
            db,
            MonitoringConfigUpsert(
                prometheus_base_url=prometheus_base_url,
                grafana_base_url=grafana_base_url,
                default_dashboard_path=default_dashboard_path,
            ),
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "monitoring/config.html",
            {"config": monitoring_service.get_config(db), "error": str(exc)},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return _redirect("/monitoring")


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
