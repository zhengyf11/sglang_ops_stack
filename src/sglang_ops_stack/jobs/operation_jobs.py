from typing import Literal

from sqlalchemy.orm import Session

from sglang_ops_stack.commands.docker import DockerCommandBuilder
from sglang_ops_stack.db.models.deployment import Deployment
from sglang_ops_stack.db.models.host import Host
from sglang_ops_stack.db.models.job import Job
from sglang_ops_stack.db.session import SessionLocal
from sglang_ops_stack.remote.command_spec import CommandSpec
from sglang_ops_stack.remote.executor import SSHCommandTimeoutError, SSHExecutionError, SSHExecutor
from sglang_ops_stack.remote.result import CommandResult
from sglang_ops_stack.services import deployment_service, job_service, operation_service
from sglang_ops_stack.services.health_service import HealthService

OperationName = Literal["restart", "stop", "start"]


def run_operation_task(job_id: int, operation: OperationName, password: str) -> None:
    with SessionLocal() as db:
        job = job_service.get_job(db, job_id)
        if job is None:
            return
        deployment = deployment_service.get_deployment(db, job.target_id)
        if deployment is None:
            job_service.mark_failed(
                db,
                job,
                error_code="VALIDATION_ERROR",
                error_message="Deployment not found",
            )
            return
        host = db.get(Host, deployment.host_id)
        if host is None:
            _fail(db, deployment, job, "VALIDATION_ERROR", "Host not found")
            return
        runner = OperationJobRunner(db=db, executor=SSHExecutor(), health_service=HealthService())
        runner.run(
            deployment=deployment, host=host, job_id=job_id, operation=operation, password=password
        )


class OperationJobRunner:
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
        operation: OperationName,
        password: str,
    ) -> None:
        job = job_service.get_job(self.db, job_id)
        if job is None:
            return
        secrets = operation_service.deployment_secrets(deployment)
        try:
            job_service.mark_running(self.db, job)
            _add_log(self.db, job, "INFO", f"operation {operation} starting", secrets)
            builder = DockerCommandBuilder(
                image=deployment.image,
                container_name=deployment.container_name,
                port=deployment.port,
                config=deployment_service.payload_from_deployment(deployment).docker_config,
            )
            spec = self._spec_for(operation, builder)
            self._run_required(job, host, password, spec, secrets)
            if operation == "stop":
                deployment.status = "stopped"
                deployment.last_error_code = None
                deployment.last_error_message = None
                self.db.add(deployment)
                job_service.save_result(self.db, job, {"operation": operation, "status": "stopped"})
                _add_log(self.db, job, "INFO", "operation stop succeeded status=stopped", secrets)
                job_service.mark_succeeded(self.db, job)
                return

            health = self.health_service.check_deployment(
                host=host,
                deployment=deployment,
                password=password,
                docker_builder=builder,
            )
            deployment.last_health_status = health
            deployment.last_checked_at = deployment_service.utc_now()
            _add_log(self.db, job, "INFO", f"health aggregate status={health['status']}", secrets)
            if health["status"] == "ERROR":
                raise OperationJobError("HEALTH_CHECK_FAILED", "Layered health check failed")
            deployment.status = "running" if health["status"] == "OK" else "degraded"
            deployment.last_error_code = None
            deployment.last_error_message = None
            self.db.add(deployment)
            job_service.save_result(
                self.db,
                job,
                {"operation": operation, "status": deployment.status, "health": health},
            )
            _add_log(
                self.db,
                job,
                "INFO",
                f"operation {operation} succeeded status={deployment.status}",
                secrets,
            )
            job_service.mark_succeeded(self.db, job)
        except OperationJobError as exc:
            _fail(self.db, deployment, job, exc.error_code, exc.message)
        except SSHCommandTimeoutError as exc:
            _fail(self.db, deployment, job, "COMMAND_TIMEOUT", str(exc))
        except SSHExecutionError as exc:
            _fail(self.db, deployment, job, "DOCKER_NOT_RUNNING", str(exc))

    def _spec_for(self, operation: OperationName, builder: DockerCommandBuilder) -> CommandSpec:
        if operation == "restart":
            return builder.restart()
        if operation == "stop":
            return builder.stop()
        return builder.start()

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
            raise OperationJobError("CONTAINER_OPERATION_FAILED", message)
        _add_log(
            self.db,
            job,
            "INFO",
            f"{spec.id} completed {operation_service.result_excerpt(result, secrets)}",
            secrets,
        )
        return result


class OperationJobError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def _add_log(db: Session, job: Job, level: str, message: str, secrets: list[str]) -> None:
    job_service.add_log(db, job_id=job.id, level=level, message=message[:1200], secrets=secrets)


def _fail(db: Session, deployment: Deployment, job: Job, error_code: str, message: str) -> None:
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
    deployment.status = "failed"
    deployment.last_error_code = error_code
    deployment.last_error_message = safe_message
    db.add(deployment)
    db.commit()
    job_service.mark_failed(
        db, job, error_code=error_code, error_message=safe_message, secrets=secrets
    )
