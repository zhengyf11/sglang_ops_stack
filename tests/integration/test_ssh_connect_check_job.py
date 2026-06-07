from datetime import UTC, datetime

from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.host import HostCreate
from sglang_ops_stack.db.models.host import Host
from sglang_ops_stack.remote.executor import SSHAuthError
from sglang_ops_stack.remote.result import CommandResult
from sglang_ops_stack.services import host_service, job_service
from sglang_ops_stack.services.ssh_connect_check import (
    GPU_QUERY_COMMAND,
    UNAME_COMMAND,
    WHOAMI_COMMAND,
    run_ssh_connect_check,
)


def result(stdout: str = "", stderr: str = "", exit_code: int = 0) -> CommandResult:
    now = datetime.now(UTC)
    return CommandResult(exit_code, stdout, stderr, False, now, now)


class FakeSuccessExecutor:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(self, **kwargs: object) -> CommandResult:
        command = str(kwargs["command"])
        self.commands.append(command)
        if command == WHOAMI_COMMAND:
            return result("root\n")
        if command == UNAME_COMMAND:
            return result("Linux gpu-host 6.1\n")
        if command == GPU_QUERY_COMMAND:
            return result("NVIDIA A100, 535.1\n")
        raise AssertionError(f"unexpected command {command}")


class FakeAuthFailureExecutor:
    def run(self, **kwargs: object) -> CommandResult:
        raise SSHAuthError("auth failed for password=super-secret")


class FakeGpuFailureExecutor(FakeSuccessExecutor):
    def run(self, **kwargs: object) -> CommandResult:
        command = str(kwargs["command"])
        if command == GPU_QUERY_COMMAND:
            self.commands.append(command)
            return result("", "nvidia-smi missing", 127)
        return super().run(**kwargs)


def _host_and_job(db_session: Session) -> tuple[int, int]:
    host = host_service.create_host(db_session, HostCreate(name="gpu", ip="10.0.0.1"))
    job = job_service.create_job(db_session, target_id=host.id)
    return host.id, job.id


def test_ssh_connect_check_success_updates_job_host_and_logs(db_session: Session) -> None:
    host_id, job_id = _host_and_job(db_session)
    executor = FakeSuccessExecutor()

    run_ssh_connect_check(
        db_session,
        job_id=job_id,
        password="super-secret",
        executor=executor,
    )

    host = host_service.get_host(db_session, host_id)
    job = job_service.get_job(db_session, job_id)
    logs = job_service.list_logs(db_session, job_id)
    assert host is not None and host.last_check_status == "succeeded"
    assert host.os_info == "Linux gpu-host 6.1"
    assert host.gpu_info == "NVIDIA A100, 535.1"
    assert job is not None and job.status == "succeeded"
    assert executor.commands == [WHOAMI_COMMAND, UNAME_COMMAND, GPU_QUERY_COMMAND]
    assert all("super-secret" not in log.message for log in logs)


def test_ssh_connect_check_auth_failure_masks_secret(db_session: Session) -> None:
    host_id, job_id = _host_and_job(db_session)

    run_ssh_connect_check(
        db_session,
        job_id=job_id,
        password="super-secret",
        executor=FakeAuthFailureExecutor(),
    )

    host = host_service.get_host(db_session, host_id)
    job = job_service.get_job(db_session, job_id)
    logs = job_service.list_logs(db_session, job_id)
    assert host is not None and host.last_check_status == "failed"
    assert job is not None and job.status == "failed"
    assert job.error_code == "auth_failed"
    assert "super-secret" not in (job.error_message or "")
    assert all("super-secret" not in log.message for log in logs)


def test_gpu_probe_failure_does_not_fail_job(db_session: Session) -> None:
    host_id, job_id = _host_and_job(db_session)

    run_ssh_connect_check(
        db_session,
        job_id=job_id,
        password="super-secret",
        executor=FakeGpuFailureExecutor(),
    )

    host = host_service.get_host(db_session, host_id)
    job = job_service.get_job(db_session, job_id)
    logs = job_service.list_logs(db_session, job_id)
    assert host is not None and host.last_check_status == "succeeded"
    assert job is not None and job.status == "succeeded"
    assert any(log.level == "warning" for log in logs)


def test_missing_host_marks_job_failed_and_masks_secret(db_session: Session) -> None:
    host_id, job_id = _host_and_job(db_session)
    host = db_session.get(Host, host_id)
    assert host is not None
    db_session.delete(host)
    db_session.commit()

    run_ssh_connect_check(
        db_session,
        job_id=job_id,
        password="super-secret",
        executor=FakeSuccessExecutor(),
    )

    job = job_service.get_job(db_session, job_id)
    logs = job_service.list_logs(db_session, job_id)
    assert job is not None
    assert job.status == "failed"
    assert job.error_code == "host_not_found"
    assert "super-secret" not in (job.error_message or "")
    assert any("host" in log.message and "not found" in log.message for log in logs)
    assert all("super-secret" not in log.message for log in logs)


def test_missing_job_does_not_raise_or_create_logs(db_session: Session) -> None:
    run_ssh_connect_check(
        db_session,
        job_id=999,
        password="super-secret",
        executor=FakeSuccessExecutor(),
    )

    assert job_service.get_job(db_session, 999) is None
    assert list(job_service.list_logs(db_session, 999)) == []
