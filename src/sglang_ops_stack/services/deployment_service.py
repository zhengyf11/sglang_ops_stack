from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.deployment import DeploymentCreate
from sglang_ops_stack.commands.docker import DockerCommandBuilder, docker_risk_warnings
from sglang_ops_stack.commands.sglang import build_sglang_argv
from sglang_ops_stack.db.models.deployment import Deployment, DeploymentRevision
from sglang_ops_stack.db.models.host import Host
from sglang_ops_stack.domain_enums import JobType, TargetType
from sglang_ops_stack.remote.command_spec import CommandRisk, CommandSpec
from sglang_ops_stack.services import job_service
from sglang_ops_stack.utils.masking import mask_secret


class DeploymentErrorCode:
    validation_error = "VALIDATION_ERROR"
    docker_not_running = "DOCKER_NOT_RUNNING"
    image_pull_failed = "IMAGE_PULL_FAILED"
    container_start_failed = "CONTAINER_START_FAILED"
    sglang_start_failed = "SGLANG_START_FAILED"
    health_check_failed = "HEALTH_CHECK_FAILED"
    command_timeout = "COMMAND_TIMEOUT"


def utc_now() -> datetime:
    return datetime.now(UTC)


def list_deployments(db: Session) -> Sequence[Deployment]:
    return db.scalars(select(Deployment).order_by(Deployment.id.desc())).all()


def get_deployment(db: Session, deployment_id: int) -> Deployment | None:
    return db.get(Deployment, deployment_id)


def build_docker_builder(payload: DeploymentCreate) -> DockerCommandBuilder:
    return DockerCommandBuilder(
        image=payload.image,
        container_name=payload.container_name,
        port=payload.port,
        config=payload.docker_config,
    )


def build_command_specs(payload: DeploymentCreate) -> list[CommandSpec]:
    docker = build_docker_builder(payload)
    specs = [docker.pull()]
    if payload.docker_config.remove_existing:
        specs.append(docker.rm_existing())
    specs.append(docker.run_detached_idle())
    specs.append(docker.exec_detached(build_sglang_argv(payload.sglang_config)))
    return specs


def preview_commands(payload: DeploymentCreate) -> tuple[str, list[str]]:
    specs = build_command_specs(payload)
    secrets = _preview_secrets(payload)
    lines = [mask_secret(spec.command_line(), secrets) for spec in specs]
    return "\n".join(lines), docker_risk_warnings(payload.docker_config)


def _preview_secrets(payload: DeploymentCreate) -> list[str]:
    secrets = [payload.model_path]
    secrets.extend(payload.docker_config.env.values())
    for volume in payload.docker_config.volumes:
        secrets.append(volume.host_path)
        secrets.append(volume.container_path)
    return [secret for secret in secrets if secret]


def create_deployment(db: Session, payload: DeploymentCreate) -> Deployment:
    host = db.get(Host, payload.host_id)
    if host is None:
        raise ValueError("host not found")
    command_preview, _warnings = preview_commands(payload)
    deployment = Deployment(
        host_id=payload.host_id,
        name=payload.name,
        container_name=payload.container_name,
        image=payload.image,
        model_path=payload.model_path,
        served_model_name=payload.served_model_name,
        bind_host=payload.sglang_config.host,
        port=payload.port,
        tp_size=payload.tp_size,
        dp_size=payload.dp_size,
        pp_size=payload.pp_size,
        mem_fraction_static=payload.mem_fraction_static,
        docker_config=payload.docker_config.model_dump(),
        sglang_config=payload.sglang_config.model_dump(),
        status="draft",
        service_url=f"http://{host.ip}:{payload.port}",
        metrics_url=f"http://{host.ip}:{payload.port}/metrics"
        if payload.sglang_config.enable_metrics
        else None,
        last_command_preview=command_preview,
        current_version=0,
    )
    db.add(deployment)
    db.commit()
    db.refresh(deployment)
    revision = create_revision(db, deployment, payload, command_preview)
    deployment.current_revision_id = revision.id
    deployment.current_version = revision.revision_no
    db.add(deployment)
    db.commit()
    db.refresh(deployment)
    return deployment


def create_revision(
    db: Session,
    deployment: Deployment,
    payload: DeploymentCreate,
    command_preview: str,
) -> DeploymentRevision:
    revision_no = (
        db.scalar(
            select(func.max(DeploymentRevision.revision_no)).where(
                DeploymentRevision.deployment_id == deployment.id
            )
        )
        or 0
    ) + 1
    revision = DeploymentRevision(
        deployment_id=deployment.id,
        revision_no=revision_no,
        config_snapshot=payload.model_dump(),
        command_preview=command_preview,
        change_summary=payload.change_summary,
        created_by=payload.created_by,
    )
    db.add(revision)
    db.commit()
    db.refresh(revision)
    return revision


def create_deployment_job(db: Session, deployment: Deployment) -> Any:
    job = job_service.create_job(
        db,
        target_id=deployment.id,
        job_type=JobType.deployment,
        target_type=TargetType.deployment,
    )
    deployment.last_job_id = job.id
    deployment.status = "deploying"
    db.add(deployment)
    db.commit()
    db.refresh(deployment)
    return job


def port_check_spec(port: int) -> CommandSpec:
    script = (
        "import socket,sys;"
        "s=socket.socket();s.settimeout(2);"
        f"sys.exit(0 if s.connect_ex(('127.0.0.1',{port})) else 1)"
    )
    return CommandSpec(
        id="deployment.port_check",
        executable="python3",
        args=("-c", script),
        timeout_seconds=10,
        allowed_exit_codes=(0, 1),
        risk=CommandRisk.read_only,
        description="Check whether target host port is already listening",
    )
