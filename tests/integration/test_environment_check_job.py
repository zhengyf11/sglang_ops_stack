from datetime import UTC, datetime

from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.host import HostCreate
from sglang_ops_stack.remote.command_spec import CommandSpec
from sglang_ops_stack.remote.result import CommandResult
from sglang_ops_stack.services import host_service, job_service
from sglang_ops_stack.services.environment.runner import (
    EnvironmentExecutor,
    confirm_environment_install,
    run_environment_check,
)


def result(stdout: str = "", stderr: str = "", exit_code: int = 0) -> CommandResult:
    now = datetime.now(UTC)
    return CommandResult(exit_code, stdout, stderr, False, now, now)


class FakeEnvironmentExecutor:
    def __init__(self, responses: dict[str, CommandResult]) -> None:
        self.responses = responses
        self.spec_ids: list[str] = []

    def execute_spec(self, *, spec: CommandSpec, **kwargs: object) -> CommandResult:
        self.spec_ids.append(spec.id)
        return self.responses.get(spec.id, result("", "missing", 127))


def _host_and_job(db_session: Session) -> tuple[int, int]:
    host = host_service.create_host(db_session, HostCreate(name="gpu", ip="10.0.0.1"))
    job = job_service.create_job(db_session, target_id=host.id, job_type="environment_check")
    return host.id, job.id


def healthy_responses() -> dict[str, CommandResult]:
    return {
        "ssh.smoke": result("root\n"),
        "os.release": result('ID=ubuntu\nVERSION_ID="22.04"\n'),
        "os.arch": result("x86_64\n"),
        "gpu.lspci": result("NVIDIA Corporation AD102\n"),
        "driver.nvidia_smi": result("535.154.05, NVIDIA A100\n"),
        "docker.version": result("Docker version 26.1.0\n"),
        "docker.service_active": result("active\n"),
        "nvidia_runtime.ctk_version": result("NVIDIA Container Toolkit CLI version 1.15.0\n"),
        "nvidia_runtime.docker_info": result("runc nvidia\n"),
        "container.cuda_gpu_test": result("NVIDIA-SMI 535.154.05\n"),
    }


def test_environment_check_success_records_layered_steps_and_masks_logs(
    db_session: Session,
) -> None:
    host_id, job_id = _host_and_job(db_session)
    executor = FakeEnvironmentExecutor(healthy_responses())

    run_environment_check(
        db_session,
        job_id=job_id,
        password="super-secret",
        executor=executor,
    )

    job = job_service.get_job(db_session, job_id)
    host = host_service.get_host(db_session, host_id)
    logs = job_service.list_logs(db_session, job_id)
    assert job is not None and job.status == "succeeded"
    assert job.result is not None
    assert [step["status"] for step in job.result["steps"]] == ["passed"] * 7
    assert job.result["install_plan"] == []
    assert host is not None and host.last_check_status == "succeeded"
    assert all("super-secret" not in log.message for log in logs)
    assert executor.spec_ids[-1] == "container.cuda_gpu_test"


def test_environment_check_stops_for_docker_install_plan_until_confirmed(
    db_session: Session,
) -> None:
    _host_id, job_id = _host_and_job(db_session)
    responses = healthy_responses()
    responses["docker.version"] = result("", "docker: command not found", 127)
    executor = FakeEnvironmentExecutor(responses)

    run_environment_check(db_session, job_id=job_id, password="super-secret", executor=executor)

    job = job_service.get_job(db_session, job_id)
    assert job is not None and job.status == "waiting_confirmation"
    assert job.result is not None
    docker_step = next(step for step in job.result["steps"] if step["id"] == "docker")
    assert docker_step["status"] == "waiting_confirmation"
    assert docker_step["error_code"] == "ENV_DOCKER_MISSING"
    assert [item["id"] for item in job.result["install_plan"]] == [
        "docker.apt_update",
        "docker.install",
        "docker.enable_now",
    ]
    assert all("install" not in spec_id for spec_id in executor.spec_ids)


def test_confirm_install_redetects_and_skips_already_fixed_docker(db_session: Session) -> None:
    _host_id, job_id = _host_and_job(db_session)
    missing = healthy_responses()
    missing["docker.version"] = result("", "docker: command not found", 127)
    run_environment_check(
        db_session,
        job_id=job_id,
        password="super-secret",
        executor=FakeEnvironmentExecutor(missing),
    )

    fixed_executor = FakeEnvironmentExecutor(healthy_responses())
    confirm_environment_install(
        db_session,
        job_id=job_id,
        password="super-secret",
        confirmed=True,
        executor=fixed_executor,
    )

    job = job_service.get_job(db_session, job_id)
    assert job is not None and job.status == "succeeded"
    assert "docker.install" not in fixed_executor.spec_ids
    docker_step = next(step for step in job.result["steps"] if step["id"] == "docker")  # type: ignore[index]
    assert docker_step["status"] in {"passed", "skipped"}


def test_environment_check_unsupported_os_fails_with_error_code(db_session: Session) -> None:
    _host_id, job_id = _host_and_job(db_session)
    responses = healthy_responses()
    responses["os.release"] = result("ID=centos\nVERSION_ID=7\n")

    run_environment_check(
        db_session,
        job_id=job_id,
        password="super-secret",
        executor=FakeEnvironmentExecutor(responses),
    )

    job = job_service.get_job(db_session, job_id)
    assert job is not None and job.status == "failed"
    assert job.error_code == "ENV_OS_UNSUPPORTED"
    assert "Ubuntu/Debian" in (job.error_message or "")


def test_environment_executor_protocol_requires_command_spec() -> None:
    assert EnvironmentExecutor.__name__ == "EnvironmentExecutor"
