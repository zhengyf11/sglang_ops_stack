import pytest
from pydantic import ValidationError

from sglang_ops_stack.api.schemas.deployment import DeploymentCreate, DockerConfig, DockerVolume
from sglang_ops_stack.services import deployment_service


def test_deployment_validation_rejects_bad_container_name() -> None:
    with pytest.raises(ValidationError):
        DeploymentCreate(
            host_id=1,
            name="demo",
            container_name="-bad",
            image="lmsysorg/sglang:latest",
            model_path="/models/secret-model",
        )


def test_deployment_validation_rejects_raw_volume_strings() -> None:
    with pytest.raises(ValidationError):
        DockerVolume(host_path="-v /host:/container", container_path="/models", mode="ro")


def test_deployment_validation_rejects_unknown_sglang_extra_args() -> None:
    with pytest.raises(ValidationError):
        DeploymentCreate(
            host_id=1,
            name="demo",
            container_name="sglang_demo",
            image="lmsysorg/sglang:latest",
            model_path="/models/secret-model",
            sglang_config={"extra_args": {"arbitrary_shell": "bad"}},
        )


def test_deployment_preview_uses_command_specs_and_masks_sensitive_values() -> None:
    payload = DeploymentCreate(
        host_id=1,
        name="demo",
        container_name="sglang_demo",
        image="lmsysorg/sglang:latest",
        model_path="/srv/private/model-a",
        docker_config=DockerConfig(
            env={"HF_TOKEN": "secret-token"},
            volumes=[DockerVolume(host_path="/srv/private", container_path="/models", mode="ro")],
        ),
    )

    preview, warnings = deployment_service.preview_commands(payload)
    specs = deployment_service.build_command_specs(payload)

    assert [spec.id for spec in specs] == [
        "deployment.docker_pull",
        "deployment.docker_run_idle",
        "deployment.sglang_exec_start",
    ]
    assert "docker pull lmsysorg/sglang:latest" in preview
    assert "docker exec -d sglang_demo sglang serve" in preview
    assert "/srv/private/model-a" not in preview
    assert "secret-token" not in preview
    assert "***" in preview
    assert any("--network host" in warning for warning in warnings)
    assert any("--gpus all" in warning for warning in warnings)
