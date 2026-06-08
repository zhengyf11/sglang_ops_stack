from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.deployment import DeploymentCreate, DockerConfig, DockerVolume
from sglang_ops_stack.db.models.host import Host
from sglang_ops_stack.domain_enums import JobType, TargetType
from sglang_ops_stack.jobs.deployment_jobs import DeploymentJobRunner
from sglang_ops_stack.remote.command_spec import CommandSpec
from sglang_ops_stack.remote.result import CommandResult
from sglang_ops_stack.services import deployment_service, job_service


class FakeExecutor:
    def __init__(self, *, fail_on: str | None = None, failure_text: str = "") -> None:
        self.command_ids: list[str] = []
        self.fail_on = fail_on
        self.failure_text = failure_text

    def execute_spec(self, **kwargs: Any) -> CommandResult:
        spec = kwargs["spec"]
        assert isinstance(spec, CommandSpec)
        self.command_ids.append(spec.id)
        if spec.id == self.fail_on:
            return CommandResult(
                exit_code=42,
                stdout=self.failure_text,
                stderr=self.failure_text,
                timed_out=False,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )
        stdout = "true" if spec.id == "deployment.docker_inspect_running" else ""
        return CommandResult(
            exit_code=0,
            stdout=stdout,
            stderr="",
            timed_out=False,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )


class FakeHealthService:
    def check_deployment(self, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "OK", "checked_at": "now", "layers": []}


def test_deployment_job_runner_transitions_to_running(db_session: Session) -> None:
    host = Host(
        name="gpu-1",
        ip="127.0.0.1",
        ssh_port=22,
        ssh_user="root",
        environment_status="READY",
    )
    db_session.add(host)
    db_session.commit()
    db_session.refresh(host)
    deployment = deployment_service.create_deployment(
        db_session,
        DeploymentCreate(
            host_id=host.id,
            name="demo",
            container_name="sglang_demo",
            image="lmsysorg/sglang:latest",
            model_path="/models/secret",
        ),
    )
    job = job_service.create_job(
        db_session,
        target_id=deployment.id,
        job_type=JobType.deployment,
        target_type=TargetType.deployment,
    )
    fake_executor = FakeExecutor()

    DeploymentJobRunner(
        db=db_session,
        executor=fake_executor,  # type: ignore[arg-type]
        health_service=FakeHealthService(),  # type: ignore[arg-type]
    ).run(deployment=deployment, host=host, job_id=job.id, password="secret")

    db_session.refresh(deployment)
    db_session.refresh(job)
    assert job.status == "succeeded"
    assert deployment.status == "running"
    assert deployment.last_health_status == {"status": "OK", "checked_at": "now", "layers": []}
    assert fake_executor.command_ids == [
        "deployment.port_check",
        "deployment.docker_pull",
        "deployment.docker_run_idle",
        "deployment.sglang_exec_start",
        "deployment.docker_inspect_running",
    ]
    logs = job_service.list_logs(db_session, job.id)
    assert any("deployment.docker_pull starting" in log.message for log in logs)
    assert any("health aggregate status=OK" in log.message for log in logs)


def test_deployment_job_failure_writes_masked_job_logs(db_session: Session) -> None:
    host = Host(
        name="gpu-1",
        ip="127.0.0.1",
        ssh_port=22,
        ssh_user="root",
        environment_status="READY",
    )
    db_session.add(host)
    db_session.commit()
    db_session.refresh(host)
    deployment = deployment_service.create_deployment(
        db_session,
        DeploymentCreate(
            host_id=host.id,
            name="demo",
            container_name="sglang_demo",
            image="lmsysorg/sglang:latest",
            model_path="/models/private-secret-model",
            docker_config=DockerConfig(
                env={"HF_TOKEN": "secret-token"},
                volumes=[
                    DockerVolume(host_path="/host/private", container_path="/models", mode="ro")
                ],
            ),
        ),
    )
    job = job_service.create_job(
        db_session,
        target_id=deployment.id,
        job_type=JobType.deployment,
        target_type=TargetType.deployment,
    )
    failure_text = (
        "pull failed for /models/private-secret-model token=secret-token "
        "volume=/host/private:/models"
    )
    fake_executor = FakeExecutor(fail_on="deployment.docker_pull", failure_text=failure_text)

    DeploymentJobRunner(
        db=db_session,
        executor=fake_executor,  # type: ignore[arg-type]
        health_service=FakeHealthService(),  # type: ignore[arg-type]
    ).run(deployment=deployment, host=host, job_id=job.id, password="ssh-password")

    db_session.refresh(deployment)
    db_session.refresh(job)
    logs = job_service.list_logs(db_session, job.id)
    log_text = "\n".join(log.message for log in logs)

    assert job.status == "failed"
    assert job.error_code == "IMAGE_PULL_FAILED"
    assert deployment.status == "failed"
    assert deployment.last_error_code == "IMAGE_PULL_FAILED"
    assert logs
    assert "deployment.docker_pull failed IMAGE_PULL_FAILED" in log_text
    assert "IMAGE_PULL_FAILED:" in log_text
    assert "/models/private-secret-model" not in log_text
    assert "secret-token" not in log_text
    assert "/host/private" not in log_text
    assert "/models/private-secret-model" not in (job.error_message or "")
    assert "secret-token" not in (deployment.last_error_message or "")
