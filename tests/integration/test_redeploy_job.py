from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.deployment import DeploymentCreate, DockerConfig, DockerVolume
from sglang_ops_stack.db.models.host import Host
from sglang_ops_stack.jobs.redeploy_jobs import RedeployJobRunner
from sglang_ops_stack.remote.command_spec import CommandSpec
from sglang_ops_stack.remote.result import CommandResult
from sglang_ops_stack.services import deployment_service, redeploy_service


class FakeExecutor:
    def __init__(self, fail_on: str | None = None, failure_text: str = "") -> None:
        self.command_ids: list[str] = []
        self.fail_on = fail_on
        self.failure_text = failure_text

    def execute_spec(self, **kwargs: Any) -> CommandResult:
        spec = kwargs["spec"]
        assert isinstance(spec, CommandSpec)
        self.command_ids.append(spec.id)
        failed = spec.id == self.fail_on
        return CommandResult(
            exit_code=42 if failed else 0,
            stdout=self.failure_text if failed else "ok",
            stderr=self.failure_text if failed else "",
            timed_out=False,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )


class FakeHealthService:
    def __init__(self, status: str = "OK") -> None:
        self.status = status
        self.calls: list[dict[str, Any]] = []

    def check_deployment(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"status": self.status, "checked_at": "now", "layers": []}


def _deployment(db_session: Session):
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
            image="lmsysorg/sglang:old",
            model_path="/models/old-secret",
            port=30000,
        ),
    )
    deployment.status = "running"
    db_session.add(deployment)
    db_session.commit()
    return host, deployment


def test_redeploy_plan_masks_diff_and_requires_high_risk_confirmation(db_session: Session) -> None:
    _host, deployment = _deployment(db_session)
    new_payload = DeploymentCreate(
        host_id=deployment.host_id,
        name="demo",
        container_name="sglang_demo",
        image="lmsysorg/sglang:new",
        model_path="/models/new-secret",
        port=30001,
        docker_config=DockerConfig(
            privileged=True,
            volumes=[DockerVolume(host_path="/secret/host", container_path="/models", mode="ro")],
        ),
    )

    plan = redeploy_service.plan_redeploy(deployment, new_payload)

    assert plan.high_risk is True
    assert any(item.field == "docker_config.volumes" and item.risk for item in plan.diff)
    assert "/models/old-secret" not in str(plan.model_dump())
    assert "/models/new-secret" not in str(plan.model_dump())
    try:
        redeploy_service.create_redeploy_job(
            db_session, deployment, new_payload, confirm_high_risk=False
        )
    except redeploy_service.RedeployError as exc:
        assert "confirm_high_risk" in str(exc)
    else:
        raise AssertionError("high-risk redeploy should require confirmation")


def test_redeploy_success_creates_new_revision_after_health_check(db_session: Session) -> None:
    host, deployment = _deployment(db_session)
    old_revision_id = deployment.current_revision_id
    payload = DeploymentCreate(
        host_id=deployment.host_id,
        name="demo2",
        container_name="sglang_demo",
        image="lmsysorg/sglang:new",
        model_path="/models/new-secret",
        port=30001,
    )
    job, _plan = redeploy_service.create_redeploy_job(
        db_session,
        deployment,
        payload,
        confirm_high_risk=True,
    )
    db_session.refresh(deployment)
    assert deployment.status == "redeploying"
    result_text = str(job.result)
    assert job.result is not None
    assert job.result["previous_status"] == "running"
    assert "/models/new-secret" not in result_text
    executor = FakeExecutor()
    health_service = FakeHealthService()

    RedeployJobRunner(
        db=db_session,
        executor=executor,  # type: ignore[arg-type]
        health_service=health_service,  # type: ignore[arg-type]
    ).run(
        deployment=deployment,
        host=host,
        job_id=job.id,
        password="pw",
        payload=payload,
        confirm_high_risk=True,
    )

    db_session.refresh(deployment)
    db_session.refresh(job)
    assert job.status == "succeeded"
    assert health_service.calls[0]["port"] == 30001
    assert health_service.calls[0]["service_url"] == "http://127.0.0.1:30001"
    assert deployment.status == "running"
    assert deployment.current_version == 2
    assert deployment.current_revision_id != old_revision_id
    assert deployment.image == "lmsysorg/sglang:new"
    assert executor.command_ids == [
        "deployment.docker_stop",
        "deployment.docker_rm_existing",
        "deployment.docker_pull",
        "deployment.docker_run_idle",
        "deployment.sglang_exec_start",
    ]


def test_redeploy_health_failure_preserves_old_revision_and_config(db_session: Session) -> None:
    host, deployment = _deployment(db_session)
    old_revision_id = deployment.current_revision_id
    old_version = deployment.current_version
    old_image = deployment.image
    old_port = deployment.port
    payload = DeploymentCreate(
        host_id=deployment.host_id,
        name="demo2",
        container_name="sglang_demo",
        image="lmsysorg/sglang:new",
        model_path="/models/new-secret",
        port=30001,
    )
    job, _plan = redeploy_service.create_redeploy_job(
        db_session,
        deployment,
        payload,
        confirm_high_risk=True,
    )
    health_service = FakeHealthService(status="ERROR")

    RedeployJobRunner(
        db=db_session,
        executor=FakeExecutor(),  # type: ignore[arg-type]
        health_service=health_service,  # type: ignore[arg-type]
    ).run(
        deployment=deployment,
        host=host,
        job_id=job.id,
        password="pw",
        payload=payload,
        confirm_high_risk=True,
    )

    db_session.refresh(deployment)
    db_session.refresh(job)
    assert job.status == "failed"
    assert health_service.calls[0]["port"] == 30001
    assert health_service.calls[0]["service_url"] == "http://127.0.0.1:30001"
    assert deployment.current_revision_id == old_revision_id
    assert deployment.current_version == old_version
    assert deployment.image == old_image
    assert deployment.port == old_port
    assert deployment.status == "running"


def test_redeploy_failure_preserves_old_revision_and_config(db_session: Session) -> None:
    host, deployment = _deployment(db_session)
    old_revision_id = deployment.current_revision_id
    old_version = deployment.current_version
    old_image = deployment.image
    payload = DeploymentCreate(
        host_id=deployment.host_id,
        name="demo2",
        container_name="sglang_demo",
        image="lmsysorg/sglang:new",
        model_path="/models/new-secret",
    )
    job, _plan = redeploy_service.create_redeploy_job(
        db_session,
        deployment,
        payload,
        confirm_high_risk=True,
    )

    RedeployJobRunner(
        db=db_session,
        executor=FakeExecutor(fail_on="deployment.docker_run_idle", failure_text="token=secret"),  # type: ignore[arg-type]
        health_service=FakeHealthService(),  # type: ignore[arg-type]
    ).run(
        deployment=deployment,
        host=host,
        job_id=job.id,
        password="pw",
        payload=payload,
        confirm_high_risk=True,
    )

    db_session.refresh(deployment)
    db_session.refresh(job)
    assert job.status == "failed"
    assert deployment.current_revision_id == old_revision_id
    assert deployment.current_version == old_version
    assert deployment.image == old_image
    assert deployment.status == "running"
