from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.orm import Session

from sglang_ops_stack.config import get_settings
from sglang_ops_stack.db.models.host import Host
from sglang_ops_stack.db.models.job import Job
from sglang_ops_stack.db.session import SessionLocal
from sglang_ops_stack.remote.executor import SSHExecutionError, SSHExecutor
from sglang_ops_stack.remote.result import CommandResult
from sglang_ops_stack.services import job_service
from sglang_ops_stack.utils.masking import mask_secret

WHOAMI_COMMAND = "whoami"
UNAME_COMMAND = "uname -a"
GPU_QUERY_COMMAND = "nvidia-smi --query-gpu=name,driver_version --format=csv,noheader"
ALLOWED_COMMANDS = {WHOAMI_COMMAND, UNAME_COMMAND, GPU_QUERY_COMMAND}


class RemoteExecutor(Protocol):
    def run(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        command: str,
        timeout: float | None = None,
    ) -> CommandResult: ...


def _run_command(
    executor: RemoteExecutor,
    host: Host,
    password: str,
    command: str,
    command_timeout: float,
) -> CommandResult:
    if command not in ALLOWED_COMMANDS:
        raise ValueError("command is not allowed")
    return executor.run(
        host=host.ip,
        port=host.ssh_port,
        username=host.ssh_user,
        password=password,
        command=command,
        timeout=command_timeout,
    )


def _log_result(
    db: Session,
    job_id: int,
    command: str,
    result: CommandResult,
    password: str,
) -> None:
    stdout = mask_secret(result.stdout.strip(), [password])
    stderr = mask_secret(result.stderr.strip(), [password])
    level = "info" if result.exit_code == 0 and not result.timed_out else "warning"
    message = f"{command}: exit_code={result.exit_code} timed_out={result.timed_out}"
    if stdout:
        message += f" stdout={stdout}"
    if stderr:
        message += f" stderr={stderr}"
    job_service.add_log(db, job_id=job_id, level=level, message=message, secrets=[password])


def _mark_host_failed(db: Session, host: Host | None) -> None:
    if host is None:
        return
    host.last_check_status = "failed"
    host.last_check_at = datetime.now(UTC)
    db.add(host)
    db.commit()


def _fail_job(
    db: Session,
    job: Job,
    *,
    error_code: str,
    error_message: str,
    secrets: list[str],
) -> None:
    message = mask_secret(error_message, secrets)
    job_service.add_log(db, job_id=job.id, level="error", message=message, secrets=secrets)
    job_service.mark_failed(
        db,
        job,
        error_code=error_code,
        error_message=message,
        secrets=secrets,
    )


def run_ssh_connect_check_task(job_id: int, password: str) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        try:
            run_ssh_connect_check(
                db,
                job_id=job_id,
                password=password,
                connect_timeout=settings.ssh_connect_timeout,
                command_timeout=settings.ssh_command_timeout,
            )
        except Exception:
            rollback = getattr(db, "rollback", None)
            if rollback is not None:
                rollback()


def run_ssh_connect_check(
    db: Session,
    *,
    job_id: int,
    password: str,
    executor: RemoteExecutor | None = None,
    connect_timeout: float = 10.0,
    command_timeout: float = 30.0,
) -> None:
    secrets = [password]
    job = job_service.get_job(db, job_id)
    if job is None:
        return
    host = db.get(Host, job.target_id)
    if host is None:
        _fail_job(
            db,
            job,
            error_code="host_not_found",
            error_message=f"host {job.target_id} not found",
            secrets=secrets,
        )
        return

    executor = executor or SSHExecutor(
        connect_timeout=connect_timeout,
        command_timeout=command_timeout,
    )
    job_service.mark_running(db, job)
    job_service.add_log(
        db,
        job_id=job.id,
        level="info",
        message=f"Starting SSH connectivity check for host {host.name} ({host.ip})",
        secrets=secrets,
    )

    try:
        whoami = _run_command(executor, host, password, WHOAMI_COMMAND, command_timeout)
        _log_result(db, job.id, WHOAMI_COMMAND, whoami, password)
        if whoami.exit_code != 0 or whoami.timed_out:
            raise SSHExecutionError("whoami command failed")

        uname = _run_command(executor, host, password, UNAME_COMMAND, command_timeout)
        _log_result(db, job.id, UNAME_COMMAND, uname, password)
        if uname.exit_code != 0 or uname.timed_out:
            raise SSHExecutionError("uname command failed")

        gpu = _run_command(executor, host, password, GPU_QUERY_COMMAND, command_timeout)
        _log_result(db, job.id, GPU_QUERY_COMMAND, gpu, password)
        if gpu.exit_code != 0 or gpu.timed_out:
            job_service.add_log(
                db,
                job_id=job.id,
                level="warning",
                message="GPU probe failed or timed out; SSH connectivity still succeeded",
                secrets=secrets,
            )
        else:
            host.gpu_info = mask_secret(gpu.stdout.strip(), secrets) or None

        host.os_info = mask_secret(uname.stdout.strip(), secrets) or None
        host.last_check_status = "succeeded"
        host.last_check_at = datetime.now(UTC)
        db.add(host)
        db.commit()
        job_service.add_log(
            db,
            job_id=job.id,
            level="info",
            message="SSH connectivity check succeeded",
        )
        job_service.mark_succeeded(db, job)
    except SSHExecutionError as exc:
        _mark_host_failed(db, host)
        error_code = getattr(exc, "error_code", "ssh_error")
        message = str(exc) or "SSH connectivity check failed"
        _fail_job(db, job, error_code=error_code, error_message=message, secrets=secrets)
    except Exception as exc:
        _mark_host_failed(db, host)
        message = str(exc) or "Unexpected SSH connectivity check failure"
        _fail_job(db, job, error_code="ssh_error", error_message=message, secrets=secrets)
