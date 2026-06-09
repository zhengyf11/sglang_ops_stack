from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.deployment import (
    DeploymentLogsResponse,
    DeploymentRevisionRead,
    OperationRequest,
    OperationResponse,
    RedeployPlan,
    RedeployRequest,
    RedeployResponse,
)
from sglang_ops_stack.db.models.host import Host
from sglang_ops_stack.db.session import get_db
from sglang_ops_stack.jobs.operation_jobs import run_operation_task
from sglang_ops_stack.jobs.redeploy_jobs import run_redeploy_task
from sglang_ops_stack.remote.executor import SSHExecutor
from sglang_ops_stack.services import deployment_service, operation_service, redeploy_service

router = APIRouter(prefix="/api/deployments", tags=["deployment-operations"])
DbSession = Annotated[Session, Depends(get_db)]
OperationName = Literal["restart", "stop", "start"]


def _get_deployment_or_404(db: Session, deployment_id: int):  # type: ignore[no-untyped-def]
    deployment = deployment_service.get_deployment(db, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    return deployment


@router.post("/{deployment_id}/{operation}", response_model=OperationResponse)
def create_operation(
    deployment_id: int,
    operation: OperationName,
    payload: OperationRequest,
    background_tasks: BackgroundTasks,
    db: DbSession,
) -> OperationResponse:
    deployment = _get_deployment_or_404(db, deployment_id)
    try:
        job = operation_service.create_operation_job(db, deployment, operation)
    except operation_service.OperationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    background_tasks.add_task(run_operation_task, job.id, operation, payload.password)
    return OperationResponse(job_id=job.id, status=job.status, operation=operation)


@router.post("/{deployment_id}/logs", response_model=DeploymentLogsResponse)
def read_logs(
    deployment_id: int,
    payload: OperationRequest,
    db: DbSession,
    tail: Annotated[int, Query(ge=1)] = 100,
) -> DeploymentLogsResponse:
    deployment = _get_deployment_or_404(db, deployment_id)
    host = db.get(Host, deployment.host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Host not found")
    safe_tail, logs = operation_service.read_container_logs(
        db,
        deployment=deployment,
        host=host,
        password=payload.password,
        tail=tail,
        executor=SSHExecutor(),
    )
    return DeploymentLogsResponse(deployment_id=deployment.id, tail=safe_tail, logs=logs)


@router.post("/{deployment_id}/redeploy/plan", response_model=RedeployPlan)
def plan_redeploy(deployment_id: int, payload: RedeployRequest, db: DbSession) -> RedeployPlan:
    deployment = _get_deployment_or_404(db, deployment_id)
    return redeploy_service.plan_redeploy(deployment, payload)


@router.post("/{deployment_id}/redeploy", response_model=RedeployResponse)
def redeploy(
    deployment_id: int,
    payload: RedeployRequest,
    background_tasks: BackgroundTasks,
    db: DbSession,
) -> RedeployResponse:
    deployment = _get_deployment_or_404(db, deployment_id)
    try:
        job, plan = redeploy_service.create_redeploy_job(
            db,
            deployment,
            payload,
            confirm_high_risk=payload.confirm_high_risk,
        )
    except redeploy_service.RedeployError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    background_tasks.add_task(
        run_redeploy_task,
        job.id,
        payload.password,
        payload.model_dump(exclude={"password", "confirm_high_risk"}),
        payload.confirm_high_risk,
    )
    return RedeployResponse(job_id=job.id, status=job.status, high_risk=plan.high_risk)


@router.get("/{deployment_id}/revisions", response_model=list[DeploymentRevisionRead])
def list_revisions(deployment_id: int, db: DbSession) -> list[DeploymentRevisionRead]:
    _get_deployment_or_404(db, deployment_id)
    return [
        DeploymentRevisionRead.model_validate(item)
        for item in deployment_service.list_revisions(db, deployment_id)
    ]


@router.get("/{deployment_id}/revisions/{revision_id}", response_model=DeploymentRevisionRead)
def get_revision(deployment_id: int, revision_id: int, db: DbSession) -> DeploymentRevisionRead:
    _get_deployment_or_404(db, deployment_id)
    revision = deployment_service.get_revision(db, deployment_id, revision_id)
    if revision is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Revision not found")
    return DeploymentRevisionRead.model_validate(revision)
