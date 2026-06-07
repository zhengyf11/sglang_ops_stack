from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.job import JobLogRead, JobRead
from sglang_ops_stack.db.session import get_db
from sglang_ops_stack.services import job_service

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
