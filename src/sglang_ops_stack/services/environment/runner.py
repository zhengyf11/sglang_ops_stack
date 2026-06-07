from typing import Protocol

from sqlalchemy.orm import Session

from sglang_ops_stack.config import get_settings
from sglang_ops_stack.db.models.host import Host
from sglang_ops_stack.db.models.job import Job
from sglang_ops_stack.db.session import SessionLocal
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
from sglang_ops_stack.services.environment.error_codes import EnvironmentErrorCode
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
SUPPORTED_UBUNTU_LTS_VERSION_IDS = {"20.04", "22.04", "24.04"}
SUPPORTED_UBUNTU_LTS_NAMES = {
    "20.04": "focal",
    "22.04": "jammy",
    "24.04": "noble",
}


def _step(
    step_id: str,
    status: str,
    summary: str,
    error_code: str | EnvironmentErrorCode | None = None,
    commands: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    data: dict[str, object] = {
        "id": step_id,
        "name": STEP_NAMES[step_id],
        "status": status,
        "summary": summary,
        "commands": commands or [],
    }
    if error_code:
        data["error_code"] = str(error_code)
    return data


def parse_os_release(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.lower()] = value.strip().strip('"').strip("'")
    return values


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
        self.reboot_required = False
        self.resume_step: str | None = None
        self.risk_notice: str | None = None
        self._step_commands: dict[str, list[dict[str, object]]] = {}

    def check(self) -> None:
        job_service.mark_running(self.db, self.job)
        self._log("info", f"Starting environment check for host {self.host.name} ({self.host.ip})")
        try:
            self._check_ssh()
            self._check_os()
            self._check_gpu()
            if not self._check_driver():
                self._wait_for_confirmation(
                    "Environment check requires NVIDIA driver remediation before continuing"
                )
                return
            if not self._check_docker():
                self._wait_for_confirmation()
                return
            if not self._check_nvidia_runtime():
                self._wait_for_confirmation()
                return
            self._check_container_gpu()
        except SSHAuthError as exc:
            self._fail(EnvironmentErrorCode.SSH_CONNECT_FAILED, str(exc) or "SSH authentication failed")
            return
        except SSHCommandTimeoutError as exc:
            self._fail(EnvironmentErrorCode.COMMAND_TIMEOUT, str(exc) or "Remote command timed out")
            return
        except SSHExecutionError as exc:
            self._fail(EnvironmentErrorCode.SSH_CONNECT_FAILED, str(exc) or "SSH execution failed")
            return
        except RuntimeError as exc:
            code = getattr(exc, "error_code", EnvironmentErrorCode.SSH_CONNECT_FAILED)
            self._fail(code, str(exc))
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
            if not self._check_driver():
                self._wait_for_confirmation(
                    "NVIDIA driver still requires manual remediation and reboot before continuing"
                )
                return
            if not self._check_docker(install=True):
                return
            if not self._check_nvidia_runtime(install=True):
                return
            self._check_container_gpu()
        except SSHAuthError as exc:
            self._fail(EnvironmentErrorCode.SSH_CONNECT_FAILED, str(exc) or "SSH authentication failed")
            return
        except SSHCommandTimeoutError as exc:
            self._fail(EnvironmentErrorCode.COMMAND_TIMEOUT, str(exc) or "Remote command timed out")
            return
        except SSHExecutionError as exc:
            self._fail(EnvironmentErrorCode.SSH_CONNECT_FAILED, str(exc) or "SSH execution failed")
            return
        except RuntimeError as exc:
            code = getattr(exc, "error_code", EnvironmentErrorCode.SSH_CONNECT_FAILED)
            self._fail(code, str(exc))
            return
        self._save_result()
        self._log("info", "Environment installation and verification succeeded")
        job_service.mark_succeeded(self.db, self.job)

    def _execute(self, spec: CommandSpec, step_id: str) -> CommandResult:
        self._log("info", f"Executing {spec.id}: {spec.description}")
        result = self.executor.execute_spec(
            host=self.host.ip,
            port=self.host.ssh_port,
            username=self.host.ssh_user,
            password=self.password,
            spec=spec,
        )
        command = self._command_result(spec, result)
        self._step_commands.setdefault(step_id, []).append(command)
        self._log_result(spec, result)
        if result.timed_out:
            raise _EnvFailure(EnvironmentErrorCode.COMMAND_TIMEOUT, f"Command timed out: {spec.id}")
        return result

    def _command_result(self, spec: CommandSpec, result: CommandResult) -> dict[str, object]:
        return {
            "id": spec.id,
            "summary": spec.description,
            "command": spec.command_line(),
            "risk": spec.risk.value,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "stdout": mask_secret(result.stdout.strip(), self.secrets),
            "stderr": mask_secret(result.stderr.strip(), self.secrets),
            "started_at": result.started_at.isoformat(),
            "finished_at": result.finished_at.isoformat(),
        }

    def _commands(self, step_id: str) -> list[dict[str, object]]:
        return self._step_commands.get(step_id, [])

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
        step_id = "ssh"
        spec = self.builder.ssh_smoke()
        result = self._execute(spec, step_id)
        if not self._ok(result, spec):
            self.steps.append(
                _step(step_id, "failed", "SSH command execution failed", EnvironmentErrorCode.SSH_CONNECT_FAILED, self._commands(step_id))
            )
            raise _EnvFailure(EnvironmentErrorCode.SSH_CONNECT_FAILED, "Unable to execute SSH smoke command")
        self.steps.append(_step(step_id, "passed", "SSH command execution succeeded", commands=self._commands(step_id)))

    def _check_os(self) -> None:
        step_id = "os"
        release_spec = self.builder.os_release()
        release = self._execute(release_spec, step_id)
        arch_spec = self.builder.os_arch()
        arch = self._execute(arch_spec, step_id)
        if not self._ok(release, release_spec) or not self._ok(arch, arch_spec):
            self.steps.append(
                _step(step_id, "failed", "Unable to read OS information", EnvironmentErrorCode.UNSUPPORTED_OS, self._commands(step_id))
            )
            raise _EnvFailure(EnvironmentErrorCode.UNSUPPORTED_OS, "Unable to detect supported Ubuntu LTS OS")
        os_info = parse_os_release(release.stdout)
        os_id = os_info.get("id", "")
        version_id = os_info.get("version_id", "")
        if os_id != "ubuntu" or version_id not in SUPPORTED_UBUNTU_LTS_VERSION_IDS:
            self.steps.append(
                _step(
                    step_id,
                    "failed",
                    f"Only supported Ubuntu LTS hosts are allowed; detected ID={os_id or 'unknown'} VERSION_ID={version_id or 'unknown'}",
                    EnvironmentErrorCode.UNSUPPORTED_OS,
                    self._commands(step_id),
                )
            )
            raise _EnvFailure(
                EnvironmentErrorCode.UNSUPPORTED_OS,
                "Only Ubuntu LTS 20.04, 22.04, or 24.04 hosts are supported",
            )
        self.host.os_info = mask_secret(release.stdout.strip(), self.secrets) or None
        codename = SUPPORTED_UBUNTU_LTS_NAMES[version_id]
        self.steps.append(
            _step(
                step_id,
                "passed",
                f"Ubuntu {version_id} LTS ({codename}) detected on {arch.stdout.strip()}",
                commands=self._commands(step_id),
            )
        )

    def _check_gpu(self) -> None:
        step_id = "gpu"
        spec = self.builder.gpu_lspci()
        result = self._execute(spec, step_id)
        if not self._ok(result, spec) or "nvidia" not in (result.stdout + result.stderr).lower():
            self.steps.append(_step(step_id, "failed", "No NVIDIA GPU found", EnvironmentErrorCode.NO_NVIDIA_GPU, self._commands(step_id)))
            raise _EnvFailure(EnvironmentErrorCode.NO_NVIDIA_GPU, "No NVIDIA GPU found")
        self.steps.append(_step(step_id, "passed", "NVIDIA GPU is visible on PCI bus", commands=self._commands(step_id)))

    def _check_driver(self) -> bool:
        step_id = "driver"
        spec = self.builder.driver_query()
        result = self._execute(spec, step_id)
        if not self._ok(result, spec):
            self.reboot_required = True
            self.resume_step = "driver"
            self.risk_notice = (
                "NVIDIA driver is not ready. Install or repair the host NVIDIA driver manually, "
                "reboot the host, then continue this environment check."
            )
            self.install_plan.append(
                {
                    "id": "driver.manual_install",
                    "description": "Manually install a compatible NVIDIA data center driver, reboot the host, then continue from driver check.",
                    "risk": "manual_reboot_required",
                    "manual": True,
                    "reboot_required": True,
                    "resume_step": "driver",
                }
            )
            self.steps.append(
                _step(
                    step_id,
                    "reboot_required",
                    "NVIDIA driver is unavailable; manual installation or repair and reboot are required before continuing",
                    EnvironmentErrorCode.DRIVER_NOT_READY,
                    self._commands(step_id),
                )
            )
            return False
        self.host.gpu_info = mask_secret(result.stdout.strip(), self.secrets) or None
        self.steps.append(
            _step(step_id, "passed", "nvidia-smi reported driver and GPU information", commands=self._commands(step_id))
        )
        return True

    def _check_docker(self, install: bool = False) -> bool:
        step_id = "docker"
        version_spec = self.builder.docker_version()
        version = self._execute(version_spec, step_id)
        service_spec = self.builder.docker_service_active()
        service = self._execute(service_spec, step_id) if self._ok(version, version_spec) else None
        if self._ok(version, version_spec) and service is not None and "active" in service.stdout:
            status = "skipped" if install else "passed"
            self.steps.append(_step(step_id, status, "Docker is installed and running", commands=self._commands(step_id)))
            return True
        if install:
            for spec in self.builder.install_docker():
                result = self._execute(spec, step_id)
                if not self._ok(result, spec):
                    self.steps.append(
                        _step(step_id, "failed", "Docker installation failed", EnvironmentErrorCode.DOCKER_NOT_INSTALLED, self._commands(step_id))
                    )
                    self._fail(EnvironmentErrorCode.DOCKER_NOT_INSTALLED, "Docker installation failed")
                    return False
            return self._check_docker(install=False)
        self.install_plan.extend(spec.safe_summary() for spec in self.builder.install_docker())
        self.steps.append(
            _step(
                step_id,
                "waiting_confirmation",
                "Docker must be installed or started",
                EnvironmentErrorCode.DOCKER_NOT_INSTALLED,
                self._commands(step_id),
            )
        )
        return False

    def _check_nvidia_runtime(self, install: bool = False) -> bool:
        step_id = "nvidia_runtime"
        ctk_spec = self.builder.nvidia_ctk_version()
        ctk = self._execute(ctk_spec, step_id)
        runtime_spec = self.builder.docker_runtime_info()
        runtime = self._execute(runtime_spec, step_id) if self._ok(ctk, ctk_spec) else None
        if self._ok(ctk, ctk_spec) and runtime is not None and "nvidia" in runtime.stdout.lower():
            status = "skipped" if install else "passed"
            self.steps.append(
                _step(step_id, status, "NVIDIA Docker runtime is configured", commands=self._commands(step_id))
            )
            return True
        if install:
            for spec in self.builder.configure_nvidia_runtime():
                result = self._execute(spec, step_id)
                if not self._ok(result, spec):
                    self.steps.append(
                        _step(
                            step_id,
                            "failed",
                            "NVIDIA runtime configuration failed",
                            EnvironmentErrorCode.NVIDIA_RUNTIME_MISSING,
                            self._commands(step_id),
                        )
                    )
                    self._fail(
                        EnvironmentErrorCode.NVIDIA_RUNTIME_MISSING,
                        "NVIDIA runtime configuration failed",
                    )
                    return False
            return self._check_nvidia_runtime(install=False)
        self.install_plan.extend(
            spec.safe_summary() for spec in self.builder.configure_nvidia_runtime()
        )
        self.steps.append(
            _step(
                step_id,
                "waiting_confirmation",
                "NVIDIA Container Toolkit/runtime must be configured",
                EnvironmentErrorCode.NVIDIA_RUNTIME_MISSING,
                self._commands(step_id),
            )
        )
        return False

    def _check_container_gpu(self) -> None:
        step_id = "container_test"
        spec = self.builder.container_gpu_test()
        result = self._execute(spec, step_id)
        if not self._ok(result, spec):
            self.steps.append(
                _step(
                    step_id,
                    "failed",
                    "CUDA test container cannot access GPU",
                    EnvironmentErrorCode.RUNTIME_VERIFY_FAILED,
                    self._commands(step_id),
                )
            )
            raise _EnvFailure(
                EnvironmentErrorCode.RUNTIME_VERIFY_FAILED,
                "CUDA test container cannot access GPU",
            )
        self.steps.append(_step(step_id, "passed", "CUDA test container can access GPU", commands=self._commands(step_id)))

    def _wait_for_confirmation(
        self,
        message: str = "Environment check requires explicit installation confirmation",
    ) -> None:
        self._save_result()
        self._log("warning", message)
        job_service.mark_waiting_confirmation(self.db, self.job, result=self._result())

    def _save_result(self) -> None:
        job_service.save_result(self.db, self.job, self._result())

    def _result(self) -> dict[str, object]:
        result: dict[str, object] = {"steps": self.steps, "install_plan": self.install_plan}
        if self.reboot_required:
            result["reboot_required"] = True
        if self.resume_step:
            result["resume_step"] = self.resume_step
        if self.risk_notice:
            result["risk_notice"] = self.risk_notice
        return result

    def _fail(self, error_code: str | EnvironmentErrorCode, message: str) -> None:
        self.host.last_check_status = "failed"
        self.db.add(self.host)
        self.db.commit()
        self._save_result()
        safe_message = mask_secret(message, self.secrets)
        self._log("error", safe_message)
        job_service.mark_failed(
            self.db,
            self.job,
            error_code=str(error_code),
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
    def __init__(self, error_code: str | EnvironmentErrorCode, message: str) -> None:
        super().__init__(message)
        self.error_code = str(error_code)


def _load_job_host(db: Session, job_id: int, secrets: list[str]) -> tuple[Job, Host] | None:
    job = job_service.get_job(db, job_id)
    if job is None:
        return None
    host = db.get(Host, job.target_id)
    if host is None:
        job_service.mark_failed(
            db,
            job,
            error_code=EnvironmentErrorCode.SSH_CONNECT_FAILED,
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
    settings = get_settings()
    with SessionLocal() as db:
        try:
            run_environment_check(
                db,
                job_id=job_id,
                password=password,
                executor=SSHExecutor(
                    connect_timeout=settings.ssh_connect_timeout,
                    command_timeout=settings.ssh_command_timeout,
                ),
            )
        except Exception:
            rollback = getattr(db, "rollback", None)
            if rollback is not None:
                rollback()


def confirm_environment_install_task(job_id: int, password: str, confirmed: bool) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        try:
            confirm_environment_install(
                db,
                job_id=job_id,
                password=password,
                confirmed=confirmed,
                executor=SSHExecutor(
                    connect_timeout=settings.ssh_connect_timeout,
                    command_timeout=settings.ssh_command_timeout,
                ),
            )
        except Exception:
            rollback = getattr(db, "rollback", None)
            if rollback is not None:
                rollback()
