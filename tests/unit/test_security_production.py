import logging
from typing import Any

import pytest
from pydantic import ValidationError

from sglang_ops_stack.api.schemas.deployment import DeploymentCreate
from sglang_ops_stack.api.schemas.monitoring import MonitoringConfigUpsert
from sglang_ops_stack.config import Settings
from sglang_ops_stack.core.logging import configure_logging
from sglang_ops_stack.db import session as db_session_module
from sglang_ops_stack.db.session import create_db_engine
from sglang_ops_stack.utils.masking import mask_secret
from sglang_ops_stack.worker.queue import enqueue_job, should_use_worker_queue


def test_mask_secret_redacts_authorization_query_tokens_and_json_like_values() -> None:
    text = (
        "Authorization: Bearer secret-token "
        "https://grafana.local/d/x?token=url-token&orgId=1 "
        "{'password': 'json-secret', 'api_key': 'key-secret'}"
    )

    masked = mask_secret(text)

    assert "secret-token" not in masked
    assert "url-token" not in masked
    assert "json-secret" not in masked
    assert "key-secret" not in masked
    assert "***" in masked


def test_json_logging_redacts_sensitive_payload() -> None:
    configure_logging(Settings(log_format="json"))
    logger = logging.getLogger("sglang_ops_stack.test")
    record = logger.makeRecord(
        logger.name,
        logging.INFO,
        __file__,
        1,
        "password=hunter2 Authorization: Bearer secret-token",
        None,
        None,
    )

    for handler in logging.getLogger().handlers:
        handler.handle(record)

    assert "hunter2" not in record.getMessage()
    assert "secret-token" not in record.getMessage()
    assert "***" in record.getMessage()


def test_postgresql_engine_uses_queue_pool_options(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, Any] = {}

    def fake_create_engine(database_url: str, **kwargs: Any) -> object:
        captured["database_url"] = database_url
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(db_session_module, "create_engine", fake_create_engine)
    engine = create_db_engine(
        "postgresql+psycopg://ops:secret@db.local/sglang",
        pool_size=7,
        pool_timeout=11,
        pool_recycle=120,
    )

    assert engine is not None
    assert captured["pool_size"] == 7
    assert captured["pool_timeout"] == 11
    assert captured["pool_recycle"] == 120


def test_worker_queue_feature_flag_and_enqueue(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(
        task_queue_mode="celery",
        redis_url="redis://redis:6379/0",
        auth_secret_key="test-auth-secret-key-value",
    )
    assert should_use_worker_queue(settings) is True
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeTask:
        def delay(self, payload: dict[str, object]) -> None:
            calls.append(("delay", payload))

    monkeypatch.setattr("sglang_ops_stack.worker.queue.dispatch_job", FakeTask())

    assert (
        enqueue_job(
            settings,
            "deployment",
            42,
            "secret",
            confirm_remove_existing=True,
        )
        is True
    )
    assert calls[0][0] == "delay"
    payload = calls[0][1]
    assert payload["job_type"] == "deployment"
    assert payload["job_id"] == 42
    assert payload["confirm_remove_existing"] is True
    assert "secret" not in str(payload)
    assert "encrypted_password" in payload


def test_worker_queue_rejects_default_auth_secret_key_in_celery_mode() -> None:
    settings = Settings(task_queue_mode="celery", redis_url="redis://redis:6379/0")

    with pytest.raises(RuntimeError, match="SGLANG_OPS_AUTH_SECRET_KEY"):
        enqueue_job(settings, "ssh_connect_check", 42, "secret")


def test_worker_queue_uses_auth_secret_key_not_legacy_secret_key(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(
        task_queue_mode="celery",
        redis_url="redis://redis:6379/0",
        auth_secret_key="non-default-auth-secret",
        secret_key="dev-only-secret-key",
    )
    calls: list[dict[str, object]] = []

    class FakeTask:
        def delay(self, payload: dict[str, object]) -> None:
            calls.append(payload)

    monkeypatch.setattr("sglang_ops_stack.worker.queue.dispatch_job", FakeTask())

    assert enqueue_job(settings, "ssh_connect_check", 42, "secret") is True
    assert calls
    assert "secret" not in str(calls[0])


def test_worker_queue_falls_back_for_development() -> None:
    settings = Settings(task_queue_mode="background", redis_url="redis://redis:6379/0")
    assert should_use_worker_queue(settings) is False
    assert enqueue_job(settings, "ssh_connect_check", 42, "secret") is False


@pytest.mark.parametrize(
    "env_key",
    [
        "HF_TOKEN",
        "API_KEY",
        "OPENAI_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "DB_PASSWORD",
        "SSL_PRIVATE_KEY",
    ],
)
def test_deployment_schema_rejects_secret_like_docker_env_keys(env_key: str) -> None:
    payload = {
        "host_id": 1,
        "name": "demo",
        "container_name": "sglang_demo",
        "image": "lmsysorg/sglang:latest",
        "model_path": "/models/demo",
        "docker_config": {"env": {env_key: "plaintext-secret"}},
    }

    with pytest.raises(ValidationError):
        DeploymentCreate.model_validate(payload)


def test_deployment_schema_allows_non_sensitive_docker_env_keys() -> None:
    payload = {
        "host_id": 1,
        "name": "demo",
        "container_name": "sglang_demo",
        "image": "lmsysorg/sglang:latest",
        "model_path": "/models/demo",
        "docker_config": {"env": {"LOG_LEVEL": "debug", "NCCL_DEBUG": "INFO"}},
    }

    deployment = DeploymentCreate.model_validate(payload)

    assert deployment.docker_config.env == {"LOG_LEVEL": "debug", "NCCL_DEBUG": "INFO"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("container_name", "sglang;rm -rf /"),
        ("image", "repo/image:latest;curl bad"),
    ],
)
def test_deployment_schema_rejects_shell_payloads(field: str, value: str) -> None:
    payload = {
        "host_id": 1,
        "name": "demo",
        "container_name": "sglang_demo",
        "image": "lmsysorg/sglang:latest",
        "model_path": "/models/demo",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        DeploymentCreate.model_validate(payload)


def test_deployment_schema_rejects_raw_volume_and_unknown_sglang_args() -> None:
    payload = {
        "host_id": 1,
        "name": "demo",
        "container_name": "sglang_demo",
        "image": "lmsysorg/sglang:latest",
        "model_path": "/models/demo",
        "docker_config": {
            "volumes": [{"host_path": "-v /:/host", "container_path": "/host", "mode": "rw"}]
        },
        "sglang_config": {"extra_args": {"shell": "$(curl bad)"}},
    }

    with pytest.raises(ValidationError):
        DeploymentCreate.model_validate(payload)


@pytest.mark.parametrize("field", ["gpus", "network", "user", "ipc", "shm_size"])
def test_deployment_schema_rejects_docker_config_shell_payloads(field: str) -> None:
    payload = {
        "host_id": 1,
        "name": "demo",
        "container_name": "sglang_demo",
        "image": "lmsysorg/sglang:latest",
        "model_path": "/models/demo",
        "docker_config": {field: "safe;curl bad"},
    }

    with pytest.raises(ValidationError):
        DeploymentCreate.model_validate(payload)


def test_deployment_schema_rejects_volume_shell_metacharacters() -> None:
    payload = {
        "host_id": 1,
        "name": "demo",
        "container_name": "sglang_demo",
        "image": "lmsysorg/sglang:latest",
        "model_path": "/models/demo",
        "docker_config": {
            "volumes": [
                {"host_path": "/models/$(bad)", "container_path": "/models", "mode": "ro"}
            ]
        },
    }

    with pytest.raises(ValidationError):
        DeploymentCreate.model_validate(payload)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/plain,secret"])
def test_monitoring_schema_rejects_javascript_and_data_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        MonitoringConfigUpsert.model_validate({"grafana_base_url": url})
