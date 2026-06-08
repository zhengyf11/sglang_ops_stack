from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.deployment import DeploymentCreate, DockerConfig, SGLangConfig
from sglang_ops_stack.commands.docker import DockerCommandBuilder
from sglang_ops_stack.commands.sglang import build_sglang_argv
from sglang_ops_stack.db.models.deployment import Deployment
from sglang_ops_stack.db.models.host import Host
from sglang_ops_stack.db.models.job import Job
from sglang_ops_stack.db.session import SessionLocal
from sglang_ops_stack.remote.command_spec import CommandSpec
from sglang_ops_stack.remote.executor import SSHCommandTimeoutError, SSHExecutionError, SSHExecutor
from sglang_ops_stack.remote.result import CommandResult
from sglang_ops_stack.services import deployment_service, job_service
from sglang_ops_stack.services.deployment_service import DeploymentErrorCode
from sglang_ops_stack.services.health_service import HealthService
from sglang_ops_stack.utils.masking import mask_secret


def run_deployment_task(job_id: int, password: str, confirm_remove_existing: bool = False) -> None:
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
        runner = DeploymentJobRunner(db=db, executor=SSHExecutor(), health_service=HealthService())
        runner.run(
            deployment=deployment,
            host=host,
            job_id=job_id,
            password=password,
            confirm_remove_existing=confirm_remove_existing,
        )


class DeploymentJobRunner:
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
        confirm_remove_existing: bool = False,
    ) -> None:
        job = job_service.get_job(self.db, job_id)
        if job is None:
            return
        job_service.mark_running(self.db, job)
        _add_log(
            self.db,
            job_id=job.id,
            level="INFO",
            message=f"deployment job starting deployment_id={deployment.id}",
            deployment=deployment,
        )
        payload = _payload_from_deployment(deployment)
        docker = DockerCommandBuilder(
            image=deployment.image,
            container_name=deployment.container_name,
            port=deployment.port,
            config=payload.docker_config,
        )
        try:
            if host.environment_status not in (None, "READY", "ready"):
                raise DeploymentJobError(
                    DeploymentErrorCode.validation_error,
                    (
                        "Host environment is not READY; run environment check or "
                        "confirm readiness first"
                    ),
                )
            self._ensure_port_available(job, deployment, host, password, deployment.port)
            self._run_required(
                job,
                deployment,
                host,
                password,
                docker.pull(),
                DeploymentErrorCode.image_pull_failed,
            )
            if payload.docker_config.remove_existing:
                if not confirm_remove_existing:
                    raise DeploymentJobError(
                        DeploymentErrorCode.validation_error,
                        "Container removal requires confirm_remove_existing=true",
                    )
                self._run_required(
                    job,
                    deployment,
                    host,
                    password,
                    docker.rm_existing(),
                    DeploymentErrorCode.container_start_failed,
                )
            self._run_required(
                job,
                deployment,
                host,
                password,
                docker.run_detached_idle(),
                DeploymentErrorCode.container_start_failed,
            )
            self._run_required(
                job,
                deployment,
                host,
                password,
                docker.exec_detached(build_sglang_argv(payload.sglang_config)),
                DeploymentErrorCode.sglang_start_failed,
            )
            self._run_required(
                job,
                deployment,
                host,
                password,
                docker.inspect_running(),
                DeploymentErrorCode.container_start_failed,
            )
            health = self.health_service.check_deployment(
                host=host,
                deployment=deployment,
                password=password,
                docker_builder=docker,
            )
            deployment.last_health_status = health
            deployment.last_checked_at = deployment_service.utc_now()
            _add_log(
                self.db,
                job_id=job.id,
                level="INFO",
                message=f"health aggregate status={health['status']}",
                deployment=deployment,
            )
            for layer in health.get("layers", []):
                if isinstance(layer, dict):
                    _add_log(
                        self.db,
                        job_id=job.id,
                        level="INFO" if layer.get("status") != "ERROR" else "ERROR",
                        message=(
                            f"health layer {layer.get('name')} status={layer.get('status')} "
                            f"message={layer.get('message')}"
                        ),
                        deployment=deployment,
                    )
            if health["status"] == "ERROR":
                raise DeploymentJobError(
                    DeploymentErrorCode.health_check_failed,
                    "Layered health check failed",
                )
            deployment.status = "running" if health["status"] == "OK" else "degraded"
            deployment.last_error_code = None
            deployment.last_error_message = None
            self.db.add(deployment)
            job_service.save_result(
                self.db,
                job,
                {"deployment_id": deployment.id, "health": health},
            )
            _add_log(
                self.db,
                job_id=job.id,
                level="INFO",
                message=f"deployment job succeeded status={deployment.status}",
                deployment=deployment,
            )
            job_service.mark_succeeded(self.db, job)
        except DeploymentJobError as exc:
            _fail(self.db, deployment, job, exc.error_code, exc.message)
        except SSHCommandTimeoutError as exc:
            _fail(self.db, deployment, job, DeploymentErrorCode.command_timeout, str(exc))
        except SSHExecutionError as exc:
            _fail(self.db, deployment, job, DeploymentErrorCode.docker_not_running, str(exc))

    def _ensure_port_available(
        self, job: Job, deployment: Deployment, host: Host, password: str, port: int
    ) -> None:
        spec = deployment_service.port_check_spec(port)
        _add_log(
            self.db,
            job_id=job.id,
            level="INFO",
            message=f"{spec.id} starting port={port}",
            deployment=deployment,
        )
        result = self.executor.execute_spec(
            host=host.ip,
            port=host.ssh_port,
            username=host.ssh_user,
            password=password,
            spec=spec,
        )
        if result.exit_code == 1:
            _add_log(
                self.db,
                job_id=job.id,
                level="ERROR",
                message=f"{spec.id} failed {_command_excerpt(result)}",
                deployment=deployment,
            )
            raise DeploymentJobError(
                DeploymentErrorCode.validation_error,
                f"Target host port {port} is already listening",
            )
        _add_log(
            self.db,
            job_id=job.id,
            level="INFO",
            message=f"{spec.id} completed {_command_excerpt(result)}",
            deployment=deployment,
        )

    def _run_required(
        self,
        job: Job,
        deployment: Deployment,
        host: Host,
        password: str,
        spec: CommandSpec,
        error_code: str,
    ) -> CommandResult:
        _add_log(
            self.db,
            job_id=job.id,
            level="INFO",
            message=f"{spec.id} starting",
            deployment=deployment,
        )
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
                job_id=job.id,
                level="ERROR",
                message=f"{spec.id} failed {error_code} {_command_excerpt(result)}",
                deployment=deployment,
            )
            raise DeploymentJobError(error_code, message)
        _add_log(
            self.db,
            job_id=job.id,
            level="INFO",
            message=f"{spec.id} completed {_command_excerpt(result)}",
            deployment=deployment,
        )
        return result


class DeploymentJobError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def _payload_from_deployment(deployment: Deployment) -> DeploymentCreate:
    return DeploymentCreate(
        host_id=deployment.host_id,
        name=deployment.name,
        container_name=deployment.container_name,
        image=deployment.image,
        model_path=deployment.model_path,
        served_model_name=deployment.served_model_name,
        port=deployment.port,
        tp_size=deployment.tp_size,
        dp_size=deployment.dp_size,
        pp_size=deployment.pp_size,
        mem_fraction_static=deployment.mem_fraction_static,
        docker_config=DockerConfig.model_validate(deployment.docker_config),
        sglang_config=SGLangConfig.model_validate(deployment.sglang_config),
    )


def _deployment_secrets(deployment: Deployment) -> list[str]:
    payload = _payload_from_deployment(deployment)
    secrets = [deployment.model_path, payload.model_path]
    secrets.extend(payload.docker_config.env.values())
    for volume in payload.docker_config.volumes:
        secrets.append(volume.host_path)
        secrets.append(volume.container_path)
    return [secret for secret in secrets if secret]


def _truncate(text: str, limit: int = 500) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated>"


def _command_excerpt(result: CommandResult) -> str:
    stdout = _truncate(result.stdout.strip())
    stderr = _truncate(result.stderr.strip())
    return f"exit_code={result.exit_code} stdout={stdout!r} stderr={stderr!r}"


def _add_log(
    db: Session,
    *,
    job_id: int,
    level: str,
    message: str,
    deployment: Deployment,
) -> None:
    job_service.add_log(
        db,
        job_id=job_id,
        level=level,
        message=_truncate(message, 1200),
        secrets=_deployment_secrets(deployment),
    )


def _fail(db: Session, deployment: Deployment, job: Job, error_code: str, message: str) -> None:
    secrets = _deployment_secrets(deployment)
    safe_message = mask_secret(_truncate(message), secrets)
    _add_log(
        db,
        job_id=job.id,
        level="ERROR",
        message=f"{error_code}: {safe_message}",
        deployment=deployment,
    )
    deployment.status = "failed"
    deployment.last_error_code = error_code
    deployment.last_error_message = safe_message
    db.add(deployment)
    db.commit()
    job_service.mark_failed(
        db,
        job,
        error_code=error_code,
        error_message=safe_message,
        secrets=secrets,
    )
