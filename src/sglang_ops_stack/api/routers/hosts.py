from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from sglang_ops_stack.api.deps import current_user, require_role
from sglang_ops_stack.api.schemas.host import HostCreate, HostRead, HostUpdate, SSHCheckRequest
from sglang_ops_stack.api.schemas.job import JobPasswordRequest
from sglang_ops_stack.config import get_settings
from sglang_ops_stack.db.models.user import User
from sglang_ops_stack.db.session import get_db
from sglang_ops_stack.domain_enums import JobType
from sglang_ops_stack.services import audit_service, host_service, job_service
from sglang_ops_stack.services.environment.runner import run_environment_check_task
from sglang_ops_stack.services.ssh_connect_check import run_ssh_connect_check_task
from sglang_ops_stack.worker.queue import enqueue_job

router = APIRouter(prefix="/api/hosts", tags=["hosts"])
DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(current_user)]
OperatorUser = Annotated[User, Depends(require_role("operator"))]


@router.get("", response_model=list[HostRead])
def list_hosts(db: DbSession, _user: CurrentUser) -> list[HostRead]:
    return [HostRead.model_validate(host) for host in host_service.list_hosts(db)]


@router.post("", response_model=HostRead, status_code=status.HTTP_201_CREATED)
def create_host(
    payload: HostCreate,
    db: DbSession,
    user: OperatorUser,
) -> HostRead:
    host = host_service.create_host(db, payload)
    audit_service.record_event(
        db,
        event_type="host.create",
        actor=user,
        target_type="host",
        target_id=host.id,
        summary={"name": host.name, "ip": host.ip, "ssh_user": host.ssh_user},
    )
    return HostRead.model_validate(host)


@router.get("/{host_id}", response_model=HostRead)
def get_host(host_id: int, db: DbSession, _user: CurrentUser) -> HostRead:
    host = host_service.get_host(db, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    return HostRead.model_validate(host)


@router.patch("/{host_id}", response_model=HostRead)
def update_host(
    host_id: int,
    payload: HostUpdate,
    db: DbSession,
    user: OperatorUser,
) -> HostRead:
    host = host_service.get_host(db, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    updated = host_service.update_host(db, host, payload)
    audit_service.record_event(
        db,
        event_type="host.update",
        actor=user,
        target_type="host",
        target_id=updated.id,
        summary=payload.model_dump(exclude_unset=True),
    )
    return HostRead.model_validate(updated)


@router.delete("/{host_id}")
def delete_host(host_id: int, db: DbSession, user: OperatorUser) -> dict[str, bool]:
    host = host_service.get_host(db, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    audit_service.record_event(
        db,
        event_type="host.delete",
        actor=user,
        target_type="host",
        target_id=host.id,
        summary={"name": host.name, "ip": host.ip},
    )
    host_service.delete_host(db, host)
    return {"deleted": True}


@router.post("/{host_id}/ssh-check")
def start_ssh_check(
    host_id: int,
    payload: SSHCheckRequest,
    background_tasks: BackgroundTasks,
    db: DbSession,
    user: OperatorUser,
) -> dict[str, int | str]:
    host = host_service.get_host(db, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    job = job_service.create_job(db, target_id=host_id)
    if not enqueue_job(get_settings(), JobType.ssh_connect_check.value, job.id, payload.password):
        background_tasks.add_task(run_ssh_connect_check_task, job.id, payload.password)
    audit_service.record_event(
        db,
        event_type="host.ssh_check",
        actor=user,
        target_type="host",
        target_id=host_id,
        summary={"job_id": job.id},
    )
    return {"job_id": job.id, "status": job.status}


@router.post("/{host_id}/environment/check")
def start_environment_check(
    host_id: int,
    payload: JobPasswordRequest,
    background_tasks: BackgroundTasks,
    db: DbSession,
    user: OperatorUser,
) -> dict[str, int | str]:
    host = host_service.get_host(db, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    job = job_service.create_job(db, target_id=host_id, job_type=JobType.environment_check)
    if not enqueue_job(get_settings(), JobType.environment_check.value, job.id, payload.password):
        background_tasks.add_task(run_environment_check_task, job.id, payload.password)
    audit_service.record_event(
        db,
        event_type="host.environment_check",
        actor=user,
        target_type="host",
        target_id=host_id,
        summary={"job_id": job.id},
    )
    return {"job_id": job.id, "status": job.status}
