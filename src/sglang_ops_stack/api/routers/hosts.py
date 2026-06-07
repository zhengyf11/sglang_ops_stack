from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.host import HostCreate, HostRead, HostUpdate, SSHCheckRequest
from sglang_ops_stack.db.session import get_db
from sglang_ops_stack.services import host_service, job_service
from sglang_ops_stack.services.ssh_connect_check import run_ssh_connect_check_task

router = APIRouter(prefix="/api/hosts", tags=["hosts"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[HostRead])
def list_hosts(db: DbSession) -> list[HostRead]:
    return [HostRead.model_validate(host) for host in host_service.list_hosts(db)]


@router.post("", response_model=HostRead, status_code=status.HTTP_201_CREATED)
def create_host(payload: HostCreate, db: DbSession) -> HostRead:
    return HostRead.model_validate(host_service.create_host(db, payload))


@router.get("/{host_id}", response_model=HostRead)
def get_host(host_id: int, db: DbSession) -> HostRead:
    host = host_service.get_host(db, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    return HostRead.model_validate(host)


@router.patch("/{host_id}", response_model=HostRead)
def update_host(host_id: int, payload: HostUpdate, db: DbSession) -> HostRead:
    host = host_service.get_host(db, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    return HostRead.model_validate(host_service.update_host(db, host, payload))


@router.delete("/{host_id}")
def delete_host(host_id: int, db: DbSession) -> dict[str, bool]:
    host = host_service.get_host(db, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    host_service.delete_host(db, host)
    return {"deleted": True}


@router.post("/{host_id}/ssh-check")
def start_ssh_check(
    host_id: int,
    payload: SSHCheckRequest,
    background_tasks: BackgroundTasks,
    db: DbSession,
) -> dict[str, int | str]:
    host = host_service.get_host(db, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Host not found")
    job = job_service.create_job(db, target_id=host_id)
    background_tasks.add_task(run_ssh_connect_check_task, job.id, payload.password)
    return {"job_id": job.id, "status": job.status}
