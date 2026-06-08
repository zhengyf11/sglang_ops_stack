from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.deployment import (
    DeploymentCreate,
    DeploymentDiffItem,
    RedeployPlan,
)
from sglang_ops_stack.commands.docker import docker_risk_warnings
from sglang_ops_stack.db.models.deployment import Deployment
from sglang_ops_stack.domain_enums import JobType, TargetType
from sglang_ops_stack.services import deployment_service, job_service
from sglang_ops_stack.services.operation_service import deployment_secrets
from sglang_ops_stack.utils.masking import mask_secret

_BLOCKING_STATUSES = {"deploying", "redeploying", "restarting", "stopping", "starting"}
_REDEPLOY_ALLOWED = {"running", "degraded", "failed", "stopped"}
_HIGH_RISK_FIELDS = {
    "docker_config.privileged",
    "docker_config.network",
    "docker_config.user",
    "docker_config.ipc",
}


class RedeployError(ValueError):
    pass


def plan_redeploy(deployment: Deployment, payload: DeploymentCreate) -> RedeployPlan:
    command_preview, warnings = deployment_service.preview_commands(payload)
    diff = build_config_diff(deployment_service.payload_from_deployment(deployment), payload)
    high_risk = any(item.risk for item in diff)
    secrets = deployment_secrets(deployment) + _payload_secrets(payload)
    return RedeployPlan(
        command_preview=mask_secret(command_preview, secrets),
        risk_warnings=[mask_secret(warning, secrets) for warning in warnings],
        diff=[
            DeploymentDiffItem(
                field=item.field,
                old_value=_mask_value(item.old_value, secrets),
                new_value=_mask_value(item.new_value, secrets),
                risk=item.risk,
            )
            for item in diff
        ],
        high_risk=high_risk,
        requires_confirmation=high_risk,
    )


def build_config_diff(old: DeploymentCreate, new: DeploymentCreate) -> list[DeploymentDiffItem]:
    old_data = old.model_dump()
    new_data = new.model_dump()
    items: list[DeploymentDiffItem] = []
    for field in sorted(set(old_data) | set(new_data)):
        _append_diff(items, field, old_data.get(field), new_data.get(field))
    return items


def _append_diff(items: list[DeploymentDiffItem], field: str, old: Any, new: Any) -> None:
    if old == new:
        return
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            _append_diff(items, f"{field}.{key}", old.get(key), new.get(key))
        return
    risk = _risk_for(field, old, new)
    items.append(DeploymentDiffItem(field=field, old_value=old, new_value=new, risk=risk))


def _risk_for(field: str, old: Any, new: Any) -> str | None:
    if field == "docker_config.volumes":
        return "High risk: Docker volume changes can expose or replace host paths."
    if field in _HIGH_RISK_FIELDS:
        if field == "docker_config.privileged" and new is True:
            return "High risk: --privileged grants broad host capabilities."
        if field == "docker_config.network" and new == "host":
            return "High risk: --network host exposes host networking."
        if field == "docker_config.user" and new in (None, "", "root", "0"):
            return "High risk: container runs as root user."
        if field == "docker_config.ipc" and new == "host":
            return "High risk: --ipc host shares host IPC namespace."
    if field.startswith("docker_config.env"):
        return "Sensitive: environment variable changed."
    return None


def validate_redeploy(
    deployment: Deployment,
    payload: DeploymentCreate,
    confirm_high_risk: bool,
    *,
    check_status: bool = True,
) -> RedeployPlan:
    if check_status:
        if deployment.status in _BLOCKING_STATUSES:
            raise RedeployError(f"deployment is busy: {deployment.status}")
        if deployment.status not in _REDEPLOY_ALLOWED:
            raise RedeployError(f"redeploy is not allowed when status={deployment.status}")
    plan = plan_redeploy(deployment, payload)
    if plan.high_risk and not confirm_high_risk:
        raise RedeployError("High-risk redeploy requires confirm_high_risk=true")
    return plan


def create_redeploy_job(
    db: Session,
    deployment: Deployment,
    payload: DeploymentCreate,
    *,
    confirm_high_risk: bool,
) -> tuple[Any, RedeployPlan]:
    previous_status = deployment.status
    plan = validate_redeploy(deployment, payload, confirm_high_risk)
    job = job_service.create_job(
        db,
        target_id=deployment.id,
        job_type=JobType.redeployment,
        target_type=TargetType.deployment,
    )
    secrets = deployment_secrets(deployment) + _payload_secrets(payload)
    job.result = {
        "requested_config": _mask_value(payload.model_dump(), secrets),
        "plan": plan.model_dump(),
        "previous_status": previous_status,
    }
    deployment.last_job_id = job.id
    deployment.status = "redeploying"
    db.add_all([job, deployment])
    db.commit()
    db.refresh(job)
    db.refresh(deployment)
    return job, plan


def redact_plan(plan: RedeployPlan, secrets: Iterable[str]) -> RedeployPlan:
    secret_list = list(secrets)
    return RedeployPlan(
        command_preview=mask_secret(plan.command_preview, secret_list),
        risk_warnings=[mask_secret(item, secret_list) for item in plan.risk_warnings],
        diff=[
            DeploymentDiffItem(
                field=item.field,
                old_value=_mask_value(item.old_value, secret_list),
                new_value=_mask_value(item.new_value, secret_list),
                risk=mask_secret(item.risk, secret_list) if item.risk else None,
            )
            for item in plan.diff
        ],
        high_risk=plan.high_risk,
        requires_confirmation=plan.requires_confirmation,
    )


def _payload_secrets(payload: DeploymentCreate) -> list[str]:
    secrets = [payload.model_path]
    secrets.extend(payload.docker_config.env.values())
    for volume in payload.docker_config.volumes:
        secrets.extend([volume.host_path, volume.container_path])
    return [secret for secret in secrets if secret]


def _mask_value(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        return mask_secret(value, secrets)
    if isinstance(value, list):
        return [_mask_value(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: _mask_value(item, secrets) for key, item in value.items()}
    return value


def has_docker_risk(payload: DeploymentCreate) -> bool:
    return bool(docker_risk_warnings(payload.docker_config))
