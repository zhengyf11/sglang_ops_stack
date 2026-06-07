from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.job import (
    ConfirmInstallRequest,
    JobLogRead,
    JobPasswordRequest,
    JobRead,
)
from sglang_ops_stack.db.session import get_db
from sglang_ops_stack.domain_enums import JobStatus, JobType
from sglang_ops_stack.services import job_service
from sglang_ops_stack.services.environment.runner import (
    confirm_environment_install_task,
    run_environment_check_task,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: int, db: DbSession) -> JobRead:
    job = job_service.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return JobRead.model_validate(job)


@router.get("/{job_id}/logs", response_model=list[JobLogRead])
def get_job_logs(job_id: int, db: DbSession) -> list[JobLogRead]:
    job = job_service.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return [JobLogRead.model_validate(log) for log in job_service.list_logs(db, job_id)]


@router.post("/{job_id}/confirm-install")
def confirm_install(
    job_id: int,
    payload: ConfirmInstallRequest,
    background_tasks: BackgroundTasks,
    db: DbSession,
) -> dict[str, int | str]:
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
    if not payload.confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Installation confirmation is required",
        )
    background_tasks.add_task(
        confirm_environment_install_task,
        job.id,
        payload.password,
        payload.confirmed,
    )
    return {"job_id": job.id, "status": job.status}


@router.post("/{job_id}/retry")
def retry_job(
    job_id: int,
    payload: JobPasswordRequest,
    background_tasks: BackgroundTasks,
    db: DbSession,
) -> dict[str, int | str]:
    job = job_service.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.type != JobType.environment_check.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only environment check jobs can be retried",
        )
    job_service.reset_for_retry(db, job)
    background_tasks.add_task(run_environment_check_task, job.id, payload.password)
    return {"job_id": job.id, "status": job.status}
