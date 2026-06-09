from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from sglang_ops_stack.api.deps import current_user, require_role
from sglang_ops_stack.api.schemas.deployment import (
    DeploymentCreate,
    DeploymentPreview,
    DeploymentRead,
    DeployRequest,
)
from sglang_ops_stack.config import get_settings
from sglang_ops_stack.db.models.user import User
from sglang_ops_stack.db.session import get_db
from sglang_ops_stack.domain_enums import JobType
from sglang_ops_stack.jobs.deployment_jobs import run_deployment_task
from sglang_ops_stack.services import audit_service, deployment_service
from sglang_ops_stack.worker.queue import enqueue_job

router = APIRouter(prefix="/api/deployments", tags=["deployments"])
DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(current_user)]
OperatorUser = Annotated[User, Depends(require_role("operator"))]


@router.get("", response_model=list[DeploymentRead])
def list_deployments(db: DbSession, _user: CurrentUser) -> list[DeploymentRead]:
    return [DeploymentRead.model_validate(item) for item in deployment_service.list_deployments(db)]


@router.post("/preview", response_model=DeploymentPreview)
def preview_deployment(payload: DeploymentCreate, _user: OperatorUser) -> DeploymentPreview:
    command_preview, warnings = deployment_service.preview_commands(payload)
    return DeploymentPreview(command_preview=command_preview, risk_warnings=warnings)


@router.post("", response_model=DeploymentRead, status_code=status.HTTP_201_CREATED)
def create_deployment(
    payload: DeploymentCreate, db: DbSession, user: OperatorUser
) -> DeploymentRead:
    try:
        deployment = deployment_service.create_deployment(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    audit_service.record_event(
        db,
        event_type="deployment.create",
        actor=user,
        target_type="deployment",
        target_id=deployment.id,
        summary={"name": deployment.name, "host_id": deployment.host_id},
    )
    return DeploymentRead.model_validate(deployment)


@router.get("/{deployment_id}", response_model=DeploymentRead)
def get_deployment(deployment_id: int, db: DbSession, _user: CurrentUser) -> DeploymentRead:
    deployment = deployment_service.get_deployment(db, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    return DeploymentRead.model_validate(deployment)


@router.post("/{deployment_id}/deploy")
def deploy(
    deployment_id: int,
    payload: DeployRequest,
    background_tasks: BackgroundTasks,
    db: DbSession,
    user: OperatorUser,
) -> dict[str, int | str]:
    deployment = deployment_service.get_deployment(db, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    job = deployment_service.create_deployment_job(db, deployment)
    if not enqueue_job(
        get_settings(),
        JobType.deployment.value,
        job.id,
        payload.password,
        confirm_remove_existing=payload.confirm_remove_existing,
    ):
        background_tasks.add_task(
            run_deployment_task,
            job.id,
            payload.password,
            payload.confirm_remove_existing,
        )
    audit_service.record_event(
        db,
        event_type="deployment.deploy",
        actor=user,
        target_type="deployment",
        target_id=deployment_id,
        summary={"job_id": job.id, "confirm_remove_existing": payload.confirm_remove_existing},
    )
    return {"job_id": job.id, "status": job.status}
