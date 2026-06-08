from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sglang_ops_stack.clients.sglang_http import SGLangHTTPClient
from sglang_ops_stack.commands.docker import DockerCommandBuilder
from sglang_ops_stack.db.models.deployment import Deployment
from sglang_ops_stack.db.models.host import Host
from sglang_ops_stack.remote.executor import SSHExecutionError, SSHExecutor
from sglang_ops_stack.remote.result import CommandResult


@dataclass(frozen=True)
class HealthLayerResult:
    name: str
    status: str
    message: str
    checked_at: str
    details: dict[str, Any]


class HealthService:
    def __init__(
        self,
        *,
        executor: SSHExecutor | None = None,
        client_factory: Callable[[str], SGLangHTTPClient] | None = None,
    ) -> None:
        self.executor = executor or SSHExecutor()
        self.client_factory = client_factory or SGLangHTTPClient

    def check_deployment(
        self,
        *,
        host: Host,
        deployment: Deployment,
        password: str,
        docker_builder: DockerCommandBuilder,
    ) -> dict[str, Any]:
        checked_at = datetime.now(UTC).isoformat()
        layers: list[HealthLayerResult] = []

        host_ok = self._run_layer(
            checked_at,
            "host_reachable",
            lambda: self.executor.execute_spec(
                host=host.ip,
                port=host.ssh_port,
                username=host.ssh_user,
                password=password,
                spec=docker_builder.ps(),
            ),
            ok_message="Host reachable over SSH",
        )
        layers.append(host_ok)
        if host_ok.status == "ERROR":
            return self._aggregate(layers, checked_at)

        layers.append(
            self._run_layer(
                checked_at,
                "docker_ready",
                lambda: self.executor.execute_spec(
                    host=host.ip,
                    port=host.ssh_port,
                    username=host.ssh_user,
                    password=password,
                    spec=docker_builder.ps(),
                ),
                ok_message="Docker command is available",
            )
        )
        layers.append(
            self._run_layer(
                checked_at,
                "container_running",
                lambda: self.executor.execute_spec(
                    host=host.ip,
                    port=host.ssh_port,
                    username=host.ssh_user,
                    password=password,
                    spec=docker_builder.inspect_running(),
                ),
                ok_message="Container is running",
                stdout_must_contain="true",
            )
        )
        layers.append(
            self._run_layer(
                checked_at,
                "sglang_process_exists",
                lambda: self.executor.execute_spec(
                    host=host.ip,
                    port=host.ssh_port,
                    username=host.ssh_user,
                    password=password,
                    spec=docker_builder.process_check(),
                ),
                ok_message="SGLang process exists",
            )
        )

        client = self.client_factory(deployment.service_url or f"http://{host.ip}:{deployment.port}")
        health = client.health()
        layers.append(
            HealthLayerResult(
                name="http_health",
                status="OK" if health.ok else "ERROR",
                message="HTTP /health OK" if health.ok else (health.error or "HTTP /health failed"),
                checked_at=checked_at,
                details={"status_code": health.status_code, "payload": health.payload},
            )
        )
        info = client.model_info()
        if not info.ok:
            info = client.server_info()
        info_message = (
            "Model/server info available"
            if info.ok
            else (info.error or "Info endpoint unavailable")
        )
        layers.append(
            HealthLayerResult(
                name="model_or_server_info",
                status="OK" if info.ok else "WARN",
                message=info_message,
                checked_at=checked_at,
                details={"status_code": info.status_code, "payload": info.payload},
            )
        )
        load = client.get_load()
        load_message = (
            "Load endpoint available" if load.ok else (load.error or "Load endpoint unavailable")
        )
        layers.append(
            HealthLayerResult(
                name="load_optional",
                status="OK" if load.ok else "WARN",
                message=load_message,
                checked_at=checked_at,
                details={"status_code": load.status_code, "payload": load.payload},
            )
        )
        return self._aggregate(layers, checked_at)

    def _run_layer(
        self,
        checked_at: str,
        name: str,
        command: Callable[[], CommandResult],
        *,
        ok_message: str,
        stdout_must_contain: str | None = None,
    ) -> HealthLayerResult:
        try:
            result = command()
        except SSHExecutionError as exc:
            return HealthLayerResult(name, "ERROR", str(exc), checked_at, {})
        ok = result.exit_code == 0
        if stdout_must_contain is not None:
            ok = ok and stdout_must_contain in result.stdout.lower()
        message = (
            ok_message
            if ok
            else (result.stderr.strip() or result.stdout.strip() or "command failed")
        )
        return HealthLayerResult(
            name,
            "OK" if ok else "ERROR",
            message,
            checked_at,
            {"exit_code": result.exit_code, "stdout": result.stdout[-500:]},
        )

    def _aggregate(self, layers: list[HealthLayerResult], checked_at: str) -> dict[str, Any]:
        statuses = [layer.status for layer in layers]
        if any(status == "ERROR" for status in statuses):
            aggregate = "ERROR"
        elif any(status == "WARN" for status in statuses):
            aggregate = "WARN"
        elif statuses:
            aggregate = "OK"
        else:
            aggregate = "UNKNOWN"
        return {
            "status": aggregate,
            "checked_at": checked_at,
            "layers": [layer.__dict__ for layer in layers],
        }
