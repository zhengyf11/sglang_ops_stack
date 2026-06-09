from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from sglang_ops_stack.api.routers import pages
from sglang_ops_stack.db.models.deployment import Deployment
from sglang_ops_stack.remote.command_spec import CommandSpec
from sglang_ops_stack.remote.result import CommandResult


def _create_host(client: TestClient) -> int:
    response = client.post("/api/hosts", json={"name": "gpu-1", "ip": "10.0.0.1"})
    assert response.status_code == 201
    return int(response.json()["id"])


def test_deployment_api_preview_create_and_detail_page(client: TestClient) -> None:
    host_id = _create_host(client)
    payload = {
        "host_id": host_id,
        "name": "demo",
        "container_name": "sglang_demo",
        "image": "lmsysorg/sglang:latest",
        "model_path": "/models/secret",
    }

    preview = client.post("/api/deployments/preview", json=payload)
    assert preview.status_code == 200
    assert "/models/secret" not in preview.json()["command_preview"]
    assert "***" in preview.json()["command_preview"]
    assert preview.json()["risk_warnings"]

    create = client.post("/api/deployments", json=payload)
    assert create.status_code == 201
    body = create.json()
    assert body["status"] == "draft"
    assert body["service_url"] == "http://10.0.0.1:30000"
    deployment_id = body["id"]

    list_response = client.get("/api/deployments")
    assert list_response.status_code == 200
    assert list_response.json()[0]["id"] == deployment_id

    revisions = client.get(f"/api/deployments/{deployment_id}/revisions")
    assert revisions.status_code == 200
    assert len(revisions.json()) == 1
    revision_id = revisions.json()[0]["id"]
    revision_detail = client.get(f"/api/deployments/{deployment_id}/revisions/{revision_id}")
    assert revision_detail.status_code == 200

    plan = client.post(
        f"/api/deployments/{deployment_id}/redeploy/plan",
        json={**payload, "password": "pw", "image": "lmsysorg/sglang:new"},
    )
    assert plan.status_code == 200
    assert any(item["field"] == "image" for item in plan.json()["diff"])

    stop_blocked = client.post(
        f"/api/deployments/{deployment_id}/stop",
        json={"password": "pw"},
    )
    assert stop_blocked.status_code == 400

    page = client.get(f"/deployments/{deployment_id}")
    assert page.status_code == 200
    assert "Layered health" in page.text
    assert "Redeploy" in page.text
    assert "View logs" in page.text
    assert "Revision history" in page.text
    assert "Risk: --network host" in page.text
    assert "/models/secret" not in page.text


def test_redeploy_and_logs_pages_handle_form_posts(
    client: TestClient, db_session: Session, monkeypatch: Any
) -> None:
    host_id = _create_host(client)
    payload = {
        "host_id": host_id,
        "name": "demo",
        "container_name": "sglang_demo",
        "image": "lmsysorg/sglang:latest",
        "model_path": "/models/secret",
    }
    create = client.post("/api/deployments", json=payload)
    assert create.status_code == 201
    deployment_id = create.json()["id"]
    deployment = db_session.get(Deployment, deployment_id)
    assert deployment is not None
    deployment.status = "running"
    db_session.add(deployment)
    db_session.commit()

    page = client.get(f"/deployments/{deployment_id}/redeploy")
    assert page.status_code == 200
    assert 'value="***"' not in page.text
    assert "Leave blank to keep current model path" in page.text

    high_risk = client.post(
        f"/deployments/{deployment_id}/redeploy",
        data={
            "password": "pw",
            "name": "demo2",
            "container_name": "sglang_demo",
            "image": "lmsysorg/sglang:new",
            "port": "30001",
            "privileged": "yes",
        },
    )
    assert high_risk.status_code == 400
    assert "confirm_high_risk" in high_risk.text

    def fake_redeploy_task(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(pages, "run_redeploy_task", fake_redeploy_task)
    post = client.post(
        f"/deployments/{deployment_id}/redeploy",
        data={
            "password": "pw",
            "name": "demo2",
            "container_name": "sglang_demo",
            "image": "lmsysorg/sglang:new",
            "port": "30001",
            "confirm_high_risk": "yes",
        },
        follow_redirects=False,
    )
    assert post.status_code == 303
    assert post.headers["location"].startswith("/jobs/")

    class FakeExecutor:
        def execute_spec(self, **kwargs: object) -> CommandResult:
            spec = kwargs["spec"]
            assert isinstance(spec, CommandSpec)
            assert spec.args[2] == "100"
            return CommandResult(
                exit_code=0,
                stdout="token=secret-token password=hunter2 /models/secret",
                stderr="",
                timed_out=False,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )

    monkeypatch.setattr(pages, "SSHExecutor", FakeExecutor)
    logs = client.post(
        f"/deployments/{deployment_id}/logs",
        data={"password": "pw", "tail": "100"},
    )
    assert logs.status_code == 200
    assert "Redacted logs" in logs.text
    assert "hunter2" not in logs.text
    assert "/models/secret" not in logs.text


def test_deployment_create_rejects_secret_env_and_read_paths_redact_legacy_secrets(
    client: TestClient, db_session: Session
) -> None:
    host_id = _create_host(client)
    payload = {
        "host_id": host_id,
        "name": "demo",
        "container_name": "sglang_demo",
        "image": "lmsysorg/sglang:latest",
        "model_path": "/models/public",
        "docker_config": {"env": {"HF_TOKEN": "plaintext-secret-token"}},
    }

    with TestClient(
        client.app, headers=client.headers, raise_server_exceptions=False
    ) as no_raise_client:
        rejected = no_raise_client.post("/api/deployments", json=payload)

    assert rejected.status_code == 422
    assert "docker_config.env must not contain secret-like keys" in rejected.text
    assert "plaintext-secret-token" not in rejected.text

    legacy = Deployment(
        host_id=host_id,
        name="legacy",
        container_name="sglang_legacy",
        image="lmsysorg/sglang:latest",
        model_path="/models/legacy-secret",
        bind_host="0.0.0.0",
        port=30000,
        tp_size=1,
        dp_size=1,
        pp_size=1,
        mem_fraction_static=None,
        docker_config={"env": {"HF_TOKEN": "legacy-secret-token", "LOG_LEVEL": "debug"}},
        sglang_config={"model_path": "/models/legacy-secret", "api_key": "sglang-secret-key"},
        status="draft",
        service_url="http://10.0.0.1:30000",
        metrics_url="http://10.0.0.1:30000/metrics",
        last_command_preview="docker run -e HF_TOKEN=legacy-secret-token /models/legacy-secret",
        last_health_status={"layers": [{"message": "token=legacy-secret-token"}]},
        current_version=0,
        last_error_message="failed with legacy-secret-token",
    )
    db_session.add(legacy)
    db_session.commit()
    db_session.refresh(legacy)

    create_text = str(client.get(f"/api/deployments/{legacy.id}").json())
    list_text = str(client.get("/api/deployments").json())
    detail_page = client.get(f"/deployments/{legacy.id}")
    list_page = client.get("/deployments")

    assert detail_page.status_code == 200
    assert list_page.status_code == 200
    for text in (create_text, list_text, detail_page.text, list_page.text):
        assert "legacy-secret-token" not in text
        assert "sglang-secret-key" not in text
        assert "/models/legacy-secret" not in text
    assert "***" in create_text
    assert "***" in detail_page.text


def test_revision_api_and_page_redact_snapshot_secrets(client: TestClient) -> None:
    host_id = _create_host(client)
    payload = {
        "host_id": host_id,
        "name": "demo",
        "container_name": "sglang_demo",
        "image": "lmsysorg/sglang:latest",
        "model_path": "/models/secret",
        "docker_config": {
            "env": {"LOG_LEVEL": "debug"},
            "volumes": [{"host_path": "/host/private", "container_path": "/models", "mode": "ro"}],
        },
    }
    create = client.post("/api/deployments", json=payload)
    assert create.status_code == 201
    deployment_id = create.json()["id"]
    revisions = client.get(f"/api/deployments/{deployment_id}/revisions")
    revision_id = revisions.json()[0]["id"]

    detail = client.get(f"/api/deployments/{deployment_id}/revisions/{revision_id}")
    assert detail.status_code == 200
    text = str(detail.json())
    assert "/models/secret" not in text
    assert "secret-token" not in text
    assert "/host/private" not in text

    page = client.get(f"/deployments/{deployment_id}/revisions/{revision_id}")
    assert page.status_code == 200
    assert "/models/secret" not in page.text
    assert "secret-token" not in page.text
    assert "/host/private" not in page.text
