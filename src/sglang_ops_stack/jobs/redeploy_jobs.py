from typing import Any

from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.deployment import DeploymentCreate
from sglang_ops_stack.commands.docker import DockerCommandBuilder
from sglang_ops_stack.commands.sglang import build_sglang_argv
from sglang_ops_stack.db.models.deployment import Deployment
from sglang_ops_stack.db.models.host import Host
from sglang_ops_stack.db.models.job import Job
from sglang_ops_stack.db.session import SessionLocal
from sglang_ops_stack.remote.command_spec import CommandSpec
from sglang_ops_stack.remote.executor import SSHCommandTimeoutError, SSHExecutionError, SSHExecutor
from sglang_ops_stack.remote.result import CommandResult
from sglang_ops_stack.services import deployment_service, job_service, operation_service
from sglang_ops_stack.services.deployment_service import DeploymentErrorCode
from sglang_ops_stack.services.health_service import HealthService
from sglang_ops_stack.services.redeploy_service import RedeployError, validate_redeploy


def run_redeploy_task(
    job_id: int,
    password: str,
    requested_config: dict[str, Any],
    confirm_high_risk: bool = False,
) -> None:
    with SessionLocal() as db:
        job = job_service.get_job(db, job_id)
        if job is None:
            return
        deployment = deployment_service.get_deployment(db, job.target_id)
        if deployment is None:
            job_service.mark_failed(
                db,
                job,
                error_code=DeploymentErrorCode.validation_error,
                error_message="Deployment not found",
            )
            return
        host = db.get(Host, deployment.host_id)
        if host is None:
            _fail(db, deployment, job, DeploymentErrorCode.validation_error, "Host not found")
            return
        payload = DeploymentCreate.model_validate(requested_config)
        runner = RedeployJobRunner(db=db, executor=SSHExecutor(), health_service=HealthService())
        runner.run(
            deployment=deployment,
            host=host,
            job_id=job_id,
            password=password,
            payload=payload,
            confirm_high_risk=confirm_high_risk,
        )


class RedeployJobRunner:
    def __init__(
        self, *, db: Session, executor: SSHExecutor, health_service: HealthService
    ) -> None:
        self.db = db
        self.executor = executor
        self.health_service = health_service

    def run(
        self,
        *,
        deployment: Deployment,
        host: Host,
        job_id: int,
        password: str,
        payload: DeploymentCreate,
        confirm_high_risk: bool,
    ) -> None:
        job = job_service.get_job(self.db, job_id)
        if job is None:
            return
        old_secrets = operation_service.deployment_secrets(deployment)
        new_secrets = _payload_secrets(payload)
        secrets = old_secrets + new_secrets
        job_previous_status = None
        if isinstance(job.result, dict):
            value = job.result.get("previous_status")
            if isinstance(value, str):
                job_previous_status = value
        old_status = job_previous_status or deployment.status
        if old_status == "redeploying":
            old_status = "failed"
        old_config = deployment_service.payload_from_deployment(deployment)
        try:
            job_service.mark_running(self.db, job)
            plan = validate_redeploy(
                deployment,
                payload,
                confirm_high_risk,
                check_status=False,
            )
            _add_log(
                self.db,
                job,
                "INFO",
                (
                    f"redeploy starting high_risk={plan.high_risk} "
                    f"confirm_high_risk={confirm_high_risk}"
                ),
                secrets,
            )
            old_builder = _builder_from_payload(
                deployment.container_name, deployment.port, old_config
            )
            new_builder = _builder_from_payload(payload.container_name, payload.port, payload)
            self._run_required(job, host, password, old_builder.stop(), secrets)
            self._run_required(job, host, password, old_builder.rm_existing(), secrets)
            self._run_required(job, host, password, new_builder.pull(), secrets)
            self._run_required(job, host, password, new_builder.run_detached_idle(), secrets)
            self._run_required(
                job,
                host,
                password,
                new_builder.exec_detached(build_sglang_argv(payload.sglang_config)),
                secrets,
            )
            candidate_service_url = f"http://{host.ip}:{payload.port}"
            health = self.health_service.check_deployment(
                host=host,
                deployment=deployment,
                password=password,
                docker_builder=new_builder,
                service_url=candidate_service_url,
                port=payload.port,
            )
            _add_log(self.db, job, "INFO", f"health aggregate status={health['status']}", secrets)
            if health["status"] == "ERROR":
                raise RedeployJobError(
                    DeploymentErrorCode.health_check_failed,
                    "Layered health check failed during redeploy",
                )
            deployment_service.apply_payload_to_deployment(self.db, deployment, payload)
            self.db.refresh(deployment)
            deployment.last_health_status = health
            deployment.last_checked_at = deployment_service.utc_now()
            deployment.status = "running" if health["status"] == "OK" else "degraded"
            deployment.last_error_code = None
            deployment.last_error_message = None
            self.db.add(deployment)
            job_service.save_result(
                self.db,
                job,
                {
                    "deployment_id": deployment.id,
                    "current_revision_id": deployment.current_revision_id,
                    "current_version": deployment.current_version,
                    "health": health,
                },
            )
            _add_log(
                self.db,
                job,
                "INFO",
                (
                    f"redeploy succeeded revision={deployment.current_version} "
                    f"status={deployment.status}"
                ),
                secrets,
            )
            job_service.mark_succeeded(self.db, job)
        except (RedeployError, RedeployJobError) as exc:
            code = (
                exc.error_code
                if isinstance(exc, RedeployJobError)
                else DeploymentErrorCode.validation_error
            )
            _fail(self.db, deployment, job, code, str(exc), restore_status=old_status)
        except SSHCommandTimeoutError as exc:
            _fail(
                self.db,
                deployment,
                job,
                DeploymentErrorCode.command_timeout,
                str(exc),
                restore_status=old_status,
            )
        except SSHExecutionError as exc:
            _fail(
                self.db,
                deployment,
                job,
                DeploymentErrorCode.docker_not_running,
                str(exc),
                restore_status=old_status,
            )

    def _run_required(
        self,
        job: Job,
        host: Host,
        password: str,
        spec: CommandSpec,
        secrets: list[str],
    ) -> CommandResult:
        _add_log(self.db, job, "INFO", f"{spec.id} starting", secrets)
        result = self.executor.execute_spec(
            host=host.ip,
            port=host.ssh_port,
            username=host.ssh_user,
            password=password,
            spec=spec,
        )
        if result.exit_code not in spec.allowed_exit_codes:
            message = result.stderr.strip() or result.stdout.strip() or "command failed"
            _add_log(
                self.db,
                job,
                "ERROR",
                f"{spec.id} failed {operation_service.result_excerpt(result, secrets)}",
                secrets,
            )
            raise RedeployJobError(DeploymentErrorCode.container_start_failed, message)
        _add_log(
            self.db,
            job,
            "INFO",
            f"{spec.id} completed {operation_service.result_excerpt(result, secrets)}",
            secrets,
        )
        return result


class RedeployJobError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def _builder_from_payload(
    container_name: str, port: int, payload: DeploymentCreate
) -> DockerCommandBuilder:
    return DockerCommandBuilder(
        image=payload.image,
        container_name=container_name,
        port=port,
        config=payload.docker_config,
    )


def _payload_secrets(payload: DeploymentCreate) -> list[str]:
    secrets = [payload.model_path]
    secrets.extend(payload.docker_config.env.values())
    for volume in payload.docker_config.volumes:
        secrets.extend([volume.host_path, volume.container_path])
    return [secret for secret in secrets if secret]


def _add_log(db: Session, job: Job, level: str, message: str, secrets: list[str]) -> None:
    job_service.add_log(db, job_id=job.id, level=level, message=message[:1200], secrets=secrets)


def _fail(
    db: Session,
    deployment: Deployment,
    job: Job,
    error_code: str,
    message: str,
    *,
    restore_status: str | None = None,
) -> None:
    secrets = operation_service.deployment_secrets(deployment)
    safe_message = operation_service.result_excerpt(
        CommandResult(
            exit_code=1,
            stdout=message,
            stderr="",
            timed_out=False,
            started_at=deployment_service.utc_now(),
            finished_at=deployment_service.utc_now(),
        ),
        secrets,
    )
    _add_log(db, job, "ERROR", f"{error_code}: {safe_message}", secrets)
    deployment.status = "failed" if restore_status is None else restore_status
    deployment.last_error_code = error_code
    deployment.last_error_message = safe_message
    db.add(deployment)
    db.commit()
    job_service.mark_failed(
        db, job, error_code=error_code, error_message=safe_message, secrets=secrets
    )
