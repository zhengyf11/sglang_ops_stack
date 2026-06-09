from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from sglang_ops_stack.core.auth import create_access_token
from sglang_ops_stack.db.models.audit import AuditLog
from sglang_ops_stack.db.models.user import User
from sglang_ops_stack.services import user_service


def _headers_for(db: Session, username: str, role: str) -> dict[str, str]:
    user = user_service.create_user(db, username=username, password="safe-password", role=role)
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def _create_host_as(client: TestClient, headers: dict[str, str]) -> int:
    response = client.post(
        "/api/hosts",
        json={"name": "gpu-1", "ip": "10.0.0.1", "ssh_user": "root"},
        headers=headers,
    )
    assert response.status_code == 201
    return int(response.json()["id"])


def test_login_returns_bearer_token_and_never_password_hash(
    client: TestClient, db_session: Session
) -> None:
    user_service.create_user(
        db_session, username="operator", password="s3cret-pass", role="operator"
    )

    response = client.post(
        "/api/auth/login", json={"username": "operator", "password": "s3cret-pass"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["username"] == "operator"
    assert body["user"]["role"] == "operator"
    assert "password" not in response.text
    stored = db_session.scalar(select(User).where(User.username == "operator"))
    assert stored is not None
    assert stored.password_hash != "s3cret-pass"
    assert stored.password_hash.startswith("pbkdf2_sha256$")


def test_login_failure_is_audited_without_secret(client: TestClient, db_session: Session) -> None:
    response = client.post(
        "/api/auth/login", json={"username": "missing", "password": "plain-secret"}
    )

    assert response.status_code == 401
    audit = db_session.scalars(select(AuditLog)).all()
    assert len(audit) == 1
    assert audit[0].event_type == "auth.login_failed"
    assert "plain-secret" not in str(audit[0].summary)


def test_dangerous_operation_requires_login_and_viewer_denied_audited(
    raw_client: TestClient, client: TestClient, db_session: Session
) -> None:
    operator_headers = _headers_for(db_session, "operator2", "operator")
    viewer_headers = _headers_for(db_session, "viewer", "viewer")
    host_id = _create_host_as(client, operator_headers)

    unauthenticated = raw_client.post(
        f"/api/hosts/{host_id}/ssh-check", json={"password": "plain-secret"}
    )
    assert unauthenticated.status_code == 401
    assert "plain-secret" not in unauthenticated.text

    denied = raw_client.post(
        f"/api/hosts/{host_id}/ssh-check",
        json={"password": "plain-secret"},
        headers=viewer_headers,
    )
    assert denied.status_code == 403
    assert "plain-secret" not in denied.text

    events = db_session.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    assert any(event.event_type == "auth.permission_denied" for event in events)
    assert all("plain-secret" not in str(event.summary) for event in events)


def test_mutating_host_and_monitoring_routes_create_audit_records(
    client: TestClient, db_session: Session
) -> None:
    create = client.post("/api/hosts", json={"name": "gpu-1", "ip": "10.0.0.1"})
    assert create.status_code == 201
    host_id = create.json()["id"]
    update = client.patch(f"/api/hosts/{host_id}", json={"name": "gpu-2"})
    assert update.status_code == 200
    monitoring = client.put(
        "/api/monitoring/config",
        json={
            "prometheus_base_url": "http://prometheus.local:9090?token=secret-token",
            "grafana_base_url": "http://grafana.local",
        },
    )
    assert monitoring.status_code == 200

    events = db_session.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    event_types = {event.event_type for event in events}
    assert {"host.create", "host.update", "monitoring.config_update"} <= event_types
    assert all("secret-token" not in str(event.summary) for event in events)


def test_viewer_cannot_create_deployment(client: TestClient, db_session: Session) -> None:
    viewer_headers = _headers_for(db_session, "viewer2", "viewer")
    response = client.post(
        "/api/deployments",
        json={
            "host_id": 1,
            "name": "demo",
            "container_name": "sglang_demo",
            "image": "lmsysorg/sglang:latest",
            "model_path": "/models/demo",
        },
        headers=viewer_headers,
    )

    assert response.status_code == 403
    assert db_session.scalar(
        select(AuditLog).where(AuditLog.event_type == "auth.permission_denied")
    )


def test_admin_can_create_user_without_returning_password(
    client: TestClient, db_session: Session
) -> None:
    response = client.post(
        "/api/auth/users",
        json={
            "username": "new-viewer",
            "password": "long-safe-password",
            "role": "viewer",
            "is_active": True,
        },
    )

    assert response.status_code == 201
    assert response.json()["username"] == "new-viewer"
    assert "long-safe-password" not in response.text
    stored = db_session.scalar(select(User).where(User.username == "new-viewer"))
    assert stored is not None
    assert stored.password_hash != "long-safe-password"
    assert db_session.scalar(select(AuditLog).where(AuditLog.event_type == "auth.user_create"))


def test_viewer_cannot_run_deployment_operation(client: TestClient, db_session: Session) -> None:
    operator_headers = _headers_for(db_session, "operator3", "operator")
    viewer_headers = _headers_for(db_session, "viewer3", "viewer")
    host_id = _create_host_as(client, operator_headers)
    created = client.post(
        "/api/deployments",
        json={
            "host_id": host_id,
            "name": "demo-op",
            "container_name": "sglang_demo_op",
            "image": "lmsysorg/sglang:latest",
            "model_path": "/models/demo",
        },
        headers=operator_headers,
    )
    assert created.status_code == 201

    denied = client.post(
        f"/api/deployments/{created.json()['id']}/restart",
        json={"password": "plain-secret"},
        headers=viewer_headers,
    )

    assert denied.status_code == 403
    assert "plain-secret" not in denied.text
    events = db_session.scalars(select(AuditLog)).all()
    assert any(event.event_type == "auth.permission_denied" for event in events)
    assert all("plain-secret" not in str(event.summary) for event in events)
