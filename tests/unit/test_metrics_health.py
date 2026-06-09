from dataclasses import dataclass
from typing import Any

from sglang_ops_stack.api.schemas.deployment import DeploymentCreate, DockerConfig
from sglang_ops_stack.api.schemas.host import HostCreate
from sglang_ops_stack.clients.sglang_http import SGLangHTTPResponse
from sglang_ops_stack.commands.docker import DockerCommandBuilder
from sglang_ops_stack.remote.result import CommandResult
from sglang_ops_stack.services import deployment_service, host_service
from sglang_ops_stack.services.health_service import HealthService


class _FakeExecutor:
    def execute_spec(self, **_kwargs: object) -> CommandResult:
        return CommandResult(
            exit_code=0,
            stdout="true",
            stderr="",
            timed_out=False,
            started_at=None,
            finished_at=None,
        )


@dataclass
class _FakeClient:
    base_url: str
    metrics_ok: bool = True
    metrics_text: str = "# HELP sglang_requests_total Total requests\nsglang_requests_total 1\n"

    def health(self) -> SGLangHTTPResponse:
        return SGLangHTTPResponse(True, 200, {"status": "ok"})

    def model_info(self) -> SGLangHTTPResponse:
        return SGLangHTTPResponse(True, 200, {"model": "demo"})

    def server_info(self) -> SGLangHTTPResponse:
        return SGLangHTTPResponse(False, 404, None, "not found")

    def get_load(self) -> SGLangHTTPResponse:
        return SGLangHTTPResponse(True, 200, {"load": 0})

    def metrics(self) -> SGLangHTTPResponse:
        status_code = 200 if self.metrics_ok else 503
        return SGLangHTTPResponse(self.metrics_ok, status_code, {"text": self.metrics_text})


def test_metrics_health_layer_is_warning_when_metrics_unreachable(db_session: Any) -> None:
    host = host_service.create_host(db_session, HostCreate(name="gpu-1", ip="10.0.0.1"))
    deployment = deployment_service.create_deployment(
        db_session,
        DeploymentCreate(
            host_id=host.id,
            name="demo",
            container_name="sglang_demo",
            image="lmsysorg/sglang:latest",
            model_path="/models/demo",
        ),
    )

    def client_factory(base_url: str) -> _FakeClient:
        return _FakeClient(base_url=base_url, metrics_ok=False, metrics_text="unavailable")

    health_service = HealthService(executor=_FakeExecutor(), client_factory=client_factory)
    health = health_service.check_deployment(
        host=host,
        deployment=deployment,
        password="pw",
        docker_builder=DockerCommandBuilder(
            image=deployment.image,
            container_name=deployment.container_name,
            port=deployment.port,
            config=DockerConfig(),
        ),
    )

    assert health["status"] == "WARN"
    metrics_layers = [layer for layer in health["layers"] if layer["name"] == "metrics"]
    assert metrics_layers
    assert metrics_layers[0]["status"] == "WARN"
    assert metrics_layers[0]["details"]["metrics_url"] == "http://10.0.0.1:30000/metrics"


def test_metrics_health_layer_accepts_prometheus_text(db_session: Any) -> None:
    host = host_service.create_host(db_session, HostCreate(name="gpu-1", ip="10.0.0.1"))
    deployment = deployment_service.create_deployment(
        db_session,
        DeploymentCreate(
            host_id=host.id,
            name="demo",
            container_name="sglang_demo",
            image="lmsysorg/sglang:latest",
            model_path="/models/demo",
        ),
    )

    health = HealthService(
        executor=_FakeExecutor(), client_factory=lambda base_url: _FakeClient(base_url=base_url)
    ).check_deployment(
        host=host,
        deployment=deployment,
        password="pw",
        docker_builder=DockerCommandBuilder(
            image=deployment.image,
            container_name=deployment.container_name,
            port=deployment.port,
            config=DockerConfig(),
        ),
    )

    metrics_layer = next(layer for layer in health["layers"] if layer["name"] == "metrics")
    assert metrics_layer["status"] == "OK"
    assert metrics_layer["details"]["reachable"] is True
