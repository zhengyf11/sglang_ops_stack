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


def test_revision_api_and_page_redact_snapshot_secrets(client: TestClient) -> None:
    host_id = _create_host(client)
    payload = {
        "host_id": host_id,
        "name": "demo",
        "container_name": "sglang_demo",
        "image": "lmsysorg/sglang:latest",
        "model_path": "/models/secret",
        "docker_config": {
            "env": {"HF_TOKEN": "secret-token"},
            "volumes": [
                {"host_path": "/host/private", "container_path": "/models", "mode": "ro"}
            ],
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
