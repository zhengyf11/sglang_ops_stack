from datetime import UTC, datetime

from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.host import HostCreate
from sglang_ops_stack.remote.executor import SSHExecutionError
from sglang_ops_stack.remote.result import CommandResult
from sglang_ops_stack.services import host_service, job_service
from sglang_ops_stack.services.ssh_connect_check import run_ssh_connect_check


class FailingExecutor:
    def run(self, **kwargs: object) -> CommandResult:
        raise SSHExecutionError("failed with password=super-secret")


def test_job_failure_sets_finished_at_and_masks_error(db_session: Session) -> None:
    host = host_service.create_host(db_session, HostCreate(name="gpu", ip="127.0.0.1"))
    job = job_service.create_job(db_session, target_id=host.id)

    run_ssh_connect_check(
        db_session,
        job_id=job.id,
        password="super-secret",
        executor=FailingExecutor(),
    )

    refreshed = job_service.get_job(db_session, job.id)
    assert refreshed is not None
    assert refreshed.status == "failed"
    assert refreshed.finished_at is not None
    assert "super-secret" not in (refreshed.error_message or "")


def make_result(stdout: str = "", exit_code: int = 0) -> CommandResult:
    now = datetime.now(UTC)
    return CommandResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
        timed_out=False,
        started_at=now,
        finished_at=now,
    )
