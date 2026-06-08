from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from sglang_ops_stack.db.models.job import Job
from sglang_ops_stack.db.models.log import JobLog
from sglang_ops_stack.domain_enums import JobStatus, JobType, TargetType
from sglang_ops_stack.utils.masking import mask_secret


def utc_now() -> datetime:
    return datetime.now(UTC)


def create_job(
    db: Session,
    *,
    target_id: int,
    job_type: str | JobType = JobType.ssh_connect_check,
    target_type: str | TargetType = TargetType.host,
) -> Job:
    type_value = job_type.value if isinstance(job_type, JobType) else job_type
    target_type_value = target_type.value if isinstance(target_type, TargetType) else target_type
    job = Job(
        type=type_value,
        status=JobStatus.pending.value,
        target_type=target_type_value,
        target_id=target_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_job(db: Session, job_id: int) -> Job | None:
    return db.get(Job, job_id)


def save_result(db: Session, job: Job, result: dict[str, object] | None) -> Job:
    job.result = result
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def list_logs(db: Session, job_id: int) -> Sequence[JobLog]:
    return db.scalars(select(JobLog).where(JobLog.job_id == job_id).order_by(JobLog.id)).all()


def add_log(
    db: Session,
    *,
    job_id: int,
    level: str,
    message: str,
    secrets: Sequence[str] = (),
) -> JobLog:
    log = JobLog(job_id=job_id, level=level, message=mask_secret(message, secrets))
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def mark_running(db: Session, job: Job) -> Job:
    job.status = JobStatus.running.value
    job.started_at = utc_now()
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def mark_succeeded(db: Session, job: Job) -> Job:
    job.status = JobStatus.succeeded.value
    job.finished_at = utc_now()
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def mark_waiting_confirmation(
    db: Session,
    job: Job,
    *,
    result: dict[str, object] | None = None,
) -> Job:
    job.status = JobStatus.waiting_confirmation.value
    job.result = result
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def reset_for_retry(db: Session, job: Job) -> Job:
    job.status = JobStatus.pending.value
    job.error_code = None
    job.error_message = None
    job.finished_at = None
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def mark_failed(
    db: Session,
    job: Job,
    *,
    error_code: str,
    error_message: str,
    secrets: Sequence[str] = (),
) -> Job:
    job.status = JobStatus.failed.value
    job.error_code = error_code
    job.error_message = mask_secret(error_message, secrets)
    job.finished_at = utc_now()
    db.add(job)
    db.commit()
    db.refresh(job)
    return job
