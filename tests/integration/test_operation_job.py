from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.deployment import DeploymentCreate, DockerConfig, DockerVolume
from sglang_ops_stack.db.models.host import Host
from sglang_ops_stack.domain_enums import JobType, TargetType
from sglang_ops_stack.jobs.operation_jobs import OperationJobRunner
from sglang_ops_stack.remote.command_spec import CommandSpec
from sglang_ops_stack.remote.result import CommandResult
from sglang_ops_stack.services import deployment_service, job_service, operation_service


class FakeExecutor:
    def __init__(self, stdout: str = "", fail_on: str | None = None) -> None:
        self.command_ids: list[str] = []
        self.stdout = stdout
        self.fail_on = fail_on

    def execute_spec(self, **kwargs: Any) -> CommandResult:
        spec = kwargs["spec"]
        assert isinstance(spec, CommandSpec)
        self.command_ids.append(spec.id)
        exit_code = 42 if spec.id == self.fail_on else 0
        return CommandResult(
            exit_code=exit_code,
            stdout=self.stdout,
            stderr=self.stdout if exit_code else "",
            timed_out=False,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )


class FakeHealthService:
    def check_deployment(self, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "OK", "checked_at": "now", "layers": []}


def _deployment(db_session: Session, status: str = "running"):
    host = Host(
        name="gpu-1", ip="127.0.0.1", ssh_port=22, ssh_user="root", environment_status="READY"
    )
    db_session.add(host)
    db_session.commit()
    deployment = deployment_service.create_deployment(
        db_session,
        DeploymentCreate(
            host_id=host.id,
            name="demo",
            container_name="sglang_demo",
            image="lmsysorg/sglang:latest",
            model_path="/models/private",
            docker_config=DockerConfig(
                env={"HF_TOKEN": "secret-token"},
                volumes=[
                    DockerVolume(host_path="/host/private", container_path="/models", mode="ro")
                ],
            ),
        ),
    )
    deployment.status = status
    db_session.add(deployment)
    db_session.commit()
    return host, deployment


def test_operation_service_blocks_invalid_status_and_transitions(db_session: Session) -> None:
    _host, deployment = _deployment(db_session, status="stopped")

    try:
        operation_service.create_operation_job(db_session, deployment, "stop")
    except operation_service.OperationError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("stop should be blocked for stopped deployments")

    job = operation_service.create_operation_job(db_session, deployment, "start")
    db_session.refresh(deployment)
    assert job.type == JobType.deployment_operation.value
    assert deployment.status == "starting"


def test_restart_job_runs_controlled_command_health_check_and_masks_logs(
    db_session: Session,
) -> None:
    host, deployment = _deployment(db_session, status="running")
    job = job_service.create_job(
        db_session,
        target_id=deployment.id,
        job_type=JobType.deployment_operation,
        target_type=TargetType.deployment,
    )
    executor = FakeExecutor(stdout="token=secret-token path=/host/private model=/models/private")

    OperationJobRunner(
        db=db_session,
        executor=executor,  # type: ignore[arg-type]
        health_service=FakeHealthService(),  # type: ignore[arg-type]
    ).run(deployment=deployment, host=host, job_id=job.id, operation="restart", password="pw")

    db_session.refresh(deployment)
    db_session.refresh(job)
    log_text = "\n".join(log.message for log in job_service.list_logs(db_session, job.id))
    assert executor.command_ids == ["deployment.docker_restart"]
    assert job.status == "succeeded"
    assert deployment.status == "running"
    assert "health aggregate status=OK" in log_text
    assert "secret-token" not in log_text
    assert "/host/private" not in log_text


def test_read_container_logs_clamps_tail_and_redacts(db_session: Session) -> None:
    host, deployment = _deployment(db_session, status="running")
    executor = FakeExecutor(
        stdout="password=hunter2 token=secret-token /host/private /models/private"
    )

    tail, logs = operation_service.read_container_logs(
        db_session,
        deployment=deployment,
        host=host,
        password="pw",
        tail=999,
        executor=executor,  # type: ignore[arg-type]
    )

    assert tail == 500
    assert executor.command_ids == ["deployment.docker_logs"]
    assert "hunter2" not in logs
    assert "secret-token" not in logs
    assert "/host/private" not in logs
