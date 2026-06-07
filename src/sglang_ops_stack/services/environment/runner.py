from typing import Protocol

from sqlalchemy.orm import Session

from sglang_ops_stack.db.models.host import Host
from sglang_ops_stack.db.models.job import Job
from sglang_ops_stack.remote.command_spec import CommandSpec
from sglang_ops_stack.remote.executor import (
    SSHAuthError,
    SSHCommandTimeoutError,
    SSHExecutionError,
    SSHExecutor,
)
from sglang_ops_stack.remote.result import CommandResult
from sglang_ops_stack.services import job_service
from sglang_ops_stack.services.environment.commands import HostCheckCommandBuilder
from sglang_ops_stack.utils.masking import mask_secret


class EnvironmentExecutor(Protocol):
    def execute_spec(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        spec: CommandSpec,
    ) -> CommandResult: ...


STEP_NAMES = {
    "ssh": "SSH",
    "os": "OS / package manager",
    "gpu": "GPU visibility",
    "driver": "NVIDIA driver",
    "docker": "Docker",
    "nvidia_runtime": "NVIDIA Container Toolkit / Docker runtime",
    "container_test": "Container GPU test",
}


def _step(
    step_id: str,
    status: str,
    summary: str,
    error_code: str | None = None,
) -> dict[str, object]:
    data: dict[str, object] = {
        "id": step_id,
        "name": STEP_NAMES[step_id],
        "status": status,
        "summary": summary,
    }
    if error_code:
        data["error_code"] = error_code
    return data


class EnvironmentCheckRunner:
    def __init__(
        self,
        db: Session,
        job: Job,
        host: Host,
        password: str,
        executor: EnvironmentExecutor,
    ) -> None:
        self.db = db
        self.job = job
        self.host = host
        self.password = password
        self.secrets = [password]
        self.executor = executor
        self.builder = HostCheckCommandBuilder()
        self.steps: list[dict[str, object]] = []
        self.install_plan: list[dict[str, object]] = []

    def check(self) -> None:
        job_service.mark_running(self.db, self.job)
        self._log("info", f"Starting environment check for host {self.host.name} ({self.host.ip})")
        try:
            self._check_ssh()
            self._check_os()
            self._check_gpu()
            self._check_driver()
            if not self._check_docker():
                self._wait_for_confirmation()
                return
            if not self._check_nvidia_runtime():
                self._wait_for_confirmation()
                return
            self._check_container_gpu()
        except SSHAuthError as exc:
            self._fail("ENV_SSH_AUTH_FAILED", str(exc) or "SSH authentication failed")
            return
        except SSHCommandTimeoutError as exc:
            self._fail("ENV_COMMAND_TIMEOUT", str(exc) or "Remote command timed out")
            return
        except SSHExecutionError as exc:
            self._fail("ENV_SSH_UNREACHABLE", str(exc) or "SSH execution failed")
            return
        except RuntimeError as exc:
            code = getattr(exc, "error_code", "ENV_INTERNAL")
            self._fail(str(code), str(exc))
            return

        self.host.last_check_status = "succeeded"
        self.db.add(self.host)
        self.db.commit()
        self._save_result()
        self._log("info", "Environment check succeeded")
        job_service.mark_succeeded(self.db, self.job)

    def confirm_install(self) -> None:
        job_service.mark_running(self.db, self.job)
        self._log("info", "User confirmed environment installation risk plan")
        try:
            self._check_ssh()
            self._check_os()
            self._check_gpu()
            self._check_driver()
            if not self._check_docker(install=True):
                return
            if not self._check_nvidia_runtime(install=True):
                return
            self._check_container_gpu()
        except SSHAuthError as exc:
            self._fail("ENV_SSH_AUTH_FAILED", str(exc) or "SSH authentication failed")
            return
        except SSHCommandTimeoutError as exc:
            self._fail("ENV_COMMAND_TIMEOUT", str(exc) or "Remote command timed out")
            return
        except SSHExecutionError as exc:
            self._fail("ENV_SSH_UNREACHABLE", str(exc) or "SSH execution failed")
            return
        except RuntimeError as exc:
            code = getattr(exc, "error_code", "ENV_INTERNAL")
            self._fail(str(code), str(exc))
            return
        self._save_result()
        self._log("info", "Environment installation and verification succeeded")
        job_service.mark_succeeded(self.db, self.job)

    def _execute(self, spec: CommandSpec) -> CommandResult:
        self._log("info", f"Executing {spec.id}: {spec.description}")
        result = self.executor.execute_spec(
            host=self.host.ip,
            port=self.host.ssh_port,
            username=self.host.ssh_user,
            password=self.password,
            spec=spec,
        )
        self._log_result(spec, result)
        return result

    def _log_result(self, spec: CommandSpec, result: CommandResult) -> None:
        level = (
            "info"
            if result.exit_code in spec.allowed_exit_codes and not result.timed_out
            else "warning"
        )
        message = f"{spec.id}: exit_code={result.exit_code} timed_out={result.timed_out}"
        stdout = mask_secret(result.stdout.strip(), self.secrets)
        stderr = mask_secret(result.stderr.strip(), self.secrets)
        if stdout:
            message += f" stdout={stdout}"
        if stderr:
            message += f" stderr={stderr}"
        self._log(level, message)

    def _ok(self, result: CommandResult, spec: CommandSpec) -> bool:
        return not result.timed_out and result.exit_code in spec.allowed_exit_codes

    def _check_ssh(self) -> None:
        spec = self.builder.ssh_smoke()
        result = self._execute(spec)
        if not self._ok(result, spec):
            self.steps.append(
                _step("ssh", "failed", "SSH command execution failed", "ENV_SSH_UNREACHABLE")
            )
            raise _EnvFailure("ENV_SSH_UNREACHABLE", "Unable to execute SSH smoke command")
        self.steps.append(_step("ssh", "passed", "SSH command execution succeeded"))

    def _check_os(self) -> None:
        release_spec = self.builder.os_release()
        release = self._execute(release_spec)
        arch_spec = self.builder.os_arch()
        arch = self._execute(arch_spec)
        text = release.stdout.lower()
        if not self._ok(release, release_spec) or not self._ok(arch, arch_spec):
            self.steps.append(
                _step("os", "failed", "Unable to read OS information", "ENV_OS_UNSUPPORTED")
            )
            raise _EnvFailure("ENV_OS_UNSUPPORTED", "Unable to detect supported OS")
        if "id=ubuntu" not in text and "id=debian" not in text and "id_like=debian" not in text:
            self.steps.append(
                _step(
                    "os", "failed", "Only Ubuntu/Debian hosts are supported", "ENV_OS_UNSUPPORTED"
                )
            )
            raise _EnvFailure("ENV_OS_UNSUPPORTED", "Only Ubuntu/Debian hosts are supported")
        self.host.os_info = mask_secret(release.stdout.strip(), self.secrets) or None
        self.steps.append(_step("os", "passed", "Supported Ubuntu/Debian apt host detected"))

    def _check_gpu(self) -> None:
        spec = self.builder.gpu_lspci()
        result = self._execute(spec)
        if not self._ok(result, spec) or "nvidia" not in (result.stdout + result.stderr).lower():
            self.steps.append(_step("gpu", "failed", "No NVIDIA GPU found", "ENV_GPU_NOT_FOUND"))
            raise _EnvFailure("ENV_GPU_NOT_FOUND", "No NVIDIA GPU found")
        self.steps.append(_step("gpu", "passed", "NVIDIA GPU is visible on PCI bus"))

    def _check_driver(self) -> None:
        spec = self.builder.driver_query()
        result = self._execute(spec)
        if not self._ok(result, spec):
            self.steps.append(
                _step(
                    "driver",
                    "failed",
                    "NVIDIA driver is unavailable",
                    "ENV_NVIDIA_DRIVER_MISSING",
                )
            )
            raise _EnvFailure("ENV_NVIDIA_DRIVER_MISSING", "NVIDIA driver is unavailable")
        self.host.gpu_info = mask_secret(result.stdout.strip(), self.secrets) or None
        self.steps.append(
            _step("driver", "passed", "nvidia-smi reported driver and GPU information")
        )

    def _check_docker(self, install: bool = False) -> bool:
        version_spec = self.builder.docker_version()
        version = self._execute(version_spec)
        service_spec = self.builder.docker_service_active()
        service = self._execute(service_spec) if self._ok(version, version_spec) else None
        if self._ok(version, version_spec) and service is not None and "active" in service.stdout:
            status = "skipped" if install else "passed"
            self.steps.append(_step("docker", status, "Docker is installed and running"))
            return True
        if install:
            for spec in self.builder.install_docker():
                result = self._execute(spec)
                if not self._ok(result, spec):
                    self.steps.append(
                        _step(
                            "docker", "failed", "Docker installation failed", "ENV_DOCKER_MISSING"
                        )
                    )
                    self._fail("ENV_DOCKER_MISSING", "Docker installation failed")
                    return False
            return self._check_docker(install=False)
        self.install_plan.extend(spec.safe_summary() for spec in self.builder.install_docker())
        self.steps.append(
            _step(
                "docker",
                "waiting_confirmation",
                "Docker must be installed or started",
                "ENV_DOCKER_MISSING",
            )
        )
        return False

    def _check_nvidia_runtime(self, install: bool = False) -> bool:
        ctk_spec = self.builder.nvidia_ctk_version()
        ctk = self._execute(ctk_spec)
        runtime_spec = self.builder.docker_runtime_info()
        runtime = self._execute(runtime_spec) if self._ok(ctk, ctk_spec) else None
        if self._ok(ctk, ctk_spec) and runtime is not None and "nvidia" in runtime.stdout.lower():
            status = "skipped" if install else "passed"
            self.steps.append(
                _step("nvidia_runtime", status, "NVIDIA Docker runtime is configured")
            )
            return True
        if install:
            for spec in self.builder.configure_nvidia_runtime():
                result = self._execute(spec)
                if not self._ok(result, spec):
                    self.steps.append(
                        _step(
                            "nvidia_runtime",
                            "failed",
                            "NVIDIA runtime configuration failed",
                            "ENV_NVIDIA_RUNTIME_NOT_CONFIGURED",
                        )
                    )
                    self._fail(
                        "ENV_NVIDIA_RUNTIME_NOT_CONFIGURED",
                        "NVIDIA runtime configuration failed",
                    )
                    return False
            return self._check_nvidia_runtime(install=False)
        self.install_plan.extend(
            spec.safe_summary() for spec in self.builder.configure_nvidia_runtime()
        )
        self.steps.append(
            _step(
                "nvidia_runtime",
                "waiting_confirmation",
                "NVIDIA Container Toolkit/runtime must be configured",
                "ENV_NVIDIA_RUNTIME_NOT_CONFIGURED",
            )
        )
        return False

    def _check_container_gpu(self) -> None:
        spec = self.builder.container_gpu_test()
        result = self._execute(spec)
        if not self._ok(result, spec):
            self.steps.append(
                _step(
                    "container_test",
                    "failed",
                    "CUDA test container cannot access GPU",
                    "ENV_CONTAINER_GPU_TEST_FAILED",
                )
            )
            raise _EnvFailure(
                "ENV_CONTAINER_GPU_TEST_FAILED",
                "CUDA test container cannot access GPU",
            )
        self.steps.append(_step("container_test", "passed", "CUDA test container can access GPU"))

    def _wait_for_confirmation(self) -> None:
        self._save_result()
        self._log("warning", "Environment check requires explicit installation confirmation")
        job_service.mark_waiting_confirmation(self.db, self.job, result=self._result())

    def _save_result(self) -> None:
        job_service.save_result(self.db, self.job, self._result())

    def _result(self) -> dict[str, object]:
        return {"steps": self.steps, "install_plan": self.install_plan}

    def _fail(self, error_code: str, message: str) -> None:
        self.host.last_check_status = "failed"
        self.db.add(self.host)
        self.db.commit()
        self._save_result()
        safe_message = mask_secret(message, self.secrets)
        self._log("error", safe_message)
        job_service.mark_failed(
            self.db,
            self.job,
            error_code=error_code,
            error_message=safe_message,
            secrets=self.secrets,
        )

    def _log(self, level: str, message: str) -> None:
        job_service.add_log(
            self.db,
            job_id=self.job.id,
            level=level,
            message=message,
            secrets=self.secrets,
        )


class _EnvFailure(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def _load_job_host(db: Session, job_id: int, secrets: list[str]) -> tuple[Job, Host] | None:
    job = job_service.get_job(db, job_id)
    if job is None:
        return None
    host = db.get(Host, job.target_id)
    if host is None:
        job_service.mark_failed(
            db,
            job,
            error_code="host_not_found",
            error_message=f"host {job.target_id} not found",
            secrets=secrets,
        )
        return None
    return job, host


def run_environment_check(
    db: Session,
    *,
    job_id: int,
    password: str,
    executor: EnvironmentExecutor | None = None,
) -> None:
    loaded = _load_job_host(db, job_id, [password])
    if loaded is None:
        return
    job, host = loaded
    runner = EnvironmentCheckRunner(
        db,
        job,
        host,
        password,
        executor or SSHExecutor(),
    )
    runner.check()


def confirm_environment_install(
    db: Session,
    *,
    job_id: int,
    password: str,
    confirmed: bool,
    executor: EnvironmentExecutor | None = None,
) -> None:
    if not confirmed:
        raise ValueError("installation confirmation is required")
    loaded = _load_job_host(db, job_id, [password])
    if loaded is None:
        return
    job, host = loaded
    runner = EnvironmentCheckRunner(
        db,
        job,
        host,
        password,
        executor or SSHExecutor(),
    )
    runner.confirm_install()


def run_environment_check_task(job_id: int, password: str) -> None:
    from sglang_ops_stack.db.session import SessionLocal

    with SessionLocal() as db:
        try:
            run_environment_check(db, job_id=job_id, password=password)
        except Exception:
            rollback = getattr(db, "rollback", None)
            if rollback is not None:
                rollback()


def confirm_environment_install_task(job_id: int, password: str, confirmed: bool) -> None:
    from sglang_ops_stack.db.session import SessionLocal

    with SessionLocal() as db:
        try:
            confirm_environment_install(
                db,
                job_id=job_id,
                password=password,
                confirmed=confirmed,
            )
        except Exception:
            rollback = getattr(db, "rollback", None)
            if rollback is not None:
                rollback()
