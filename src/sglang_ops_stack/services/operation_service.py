from collections.abc import Sequence
from typing import Any, Literal

from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.deployment import DockerConfig
from sglang_ops_stack.commands.docker import DockerCommandBuilder
from sglang_ops_stack.db.models.deployment import Deployment
from sglang_ops_stack.db.models.host import Host
from sglang_ops_stack.domain_enums import JobType, TargetType
from sglang_ops_stack.remote.executor import SSHExecutor
from sglang_ops_stack.remote.result import CommandResult
from sglang_ops_stack.services import deployment_service, job_service
from sglang_ops_stack.utils.masking import mask_secret

MAX_LOG_TAIL = 500
OperationName = Literal["restart", "stop", "start"]

_ALLOWED_STATUSES: dict[OperationName, set[str]] = {
    "restart": {"running", "degraded", "failed"},
    "stop": {"running", "degraded", "failed"},
    "start": {"stopped", "failed"},
}
_TRANSITION_STATUS: dict[OperationName, str] = {
    "restart": "restarting",
    "stop": "stopping",
    "start": "starting",
}
_BLOCKING_STATUSES = {"deploying", "redeploying", "restarting", "stopping", "starting"}


class OperationError(ValueError):
    pass


def deployment_secrets(deployment: Deployment) -> list[str]:
    payload = deployment_service.payload_from_deployment(deployment)
    secrets = [deployment.model_path, payload.model_path]
    secrets.extend(payload.docker_config.env.values())
    for key, value in payload.docker_config.env.items():
        if any(
            token in key.lower()
            for token in ("password", "passwd", "pwd", "token", "key", "secret")
        ):
            secrets.append(value)
    for volume in payload.docker_config.volumes:
        secrets.append(volume.host_path)
        secrets.append(volume.container_path)
    return [secret for secret in secrets if secret]


def validate_operation(deployment: Deployment, operation: OperationName) -> None:
    if deployment.status in _BLOCKING_STATUSES:
        raise OperationError(f"deployment is busy: {deployment.status}")
    if deployment.status not in _ALLOWED_STATUSES[operation]:
        raise OperationError(
            f"operation {operation} is not allowed when status={deployment.status}"
        )


def create_operation_job(db: Session, deployment: Deployment, operation: OperationName) -> Any:
    validate_operation(deployment, operation)
    job = job_service.create_job(
        db,
        target_id=deployment.id,
        job_type=JobType.deployment_operation,
        target_type=TargetType.deployment,
    )
    deployment.last_job_id = job.id
    deployment.status = _TRANSITION_STATUS[operation]
    db.add(deployment)
    db.commit()
    db.refresh(deployment)
    return job


def clamp_tail(tail: int) -> int:
    if tail < 1:
        return 1
    return min(tail, MAX_LOG_TAIL)


def _docker_builder(deployment: Deployment) -> DockerCommandBuilder:
    return DockerCommandBuilder(
        image=deployment.image,
        container_name=deployment.container_name,
        port=deployment.port,
        config=DockerConfig.model_validate(deployment.docker_config),
    )


def read_container_logs(
    db: Session,
    *,
    deployment: Deployment,
    host: Host,
    password: str,
    tail: int,
    executor: SSHExecutor,
) -> tuple[int, str]:
    safe_tail = clamp_tail(tail)
    result = executor.execute_spec(
        host=host.ip,
        port=host.ssh_port,
        username=host.ssh_user,
        password=password,
        spec=_docker_builder(deployment).logs_tail(safe_tail),
    )
    combined = result.stdout if result.exit_code == 0 else f"{result.stdout}\n{result.stderr}"
    logs = mask_secret(combined, deployment_secrets(deployment))
    audit = job_service.create_job(
        db,
        target_id=deployment.id,
        job_type=JobType.deployment_operation,
        target_type=TargetType.deployment,
    )
    job_service.add_log(
        db,
        job_id=audit.id,
        level="INFO",
        message=f"read container logs tail={safe_tail} exit_code={result.exit_code}",
        secrets=deployment_secrets(deployment),
    )
    job_service.mark_succeeded(db, audit)
    return safe_tail, logs


def result_excerpt(result: CommandResult, secrets: Sequence[str]) -> str:
    text = (
        f"exit_code={result.exit_code} stdout={result.stdout.strip()!r} "
        f"stderr={result.stderr.strip()!r}"
    )
    if len(text) > 600:
        text = f"{text[:600]}...<truncated>"
    return mask_secret(text, secrets)
