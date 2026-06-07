from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from sglang_ops_stack.db.models.host import Host


def test_host_crud_api(client: TestClient) -> None:
    create = client.post(
        "/api/hosts",
        json={"name": "gpu-1", "ip": "10.0.0.1", "ssh_port": 2222, "ssh_user": "root"},
    )
    assert create.status_code == 201
    body = create.json()
    assert body["name"] == "gpu-1"
    assert "password" not in body

    host_id = body["id"]
    assert client.get("/api/hosts").json()[0]["id"] == host_id
    assert client.get(f"/api/hosts/{host_id}").json()["ip"] == "10.0.0.1"

    update = client.patch(f"/api/hosts/{host_id}", json={"name": "gpu-renamed"})
    assert update.status_code == 200
    assert update.json()["name"] == "gpu-renamed"

    delete = client.delete(f"/api/hosts/{host_id}")
    assert delete.status_code == 200
    assert client.get(f"/api/hosts/{host_id}").status_code == 404


def test_host_api_rejects_password_field(client: TestClient) -> None:
    response = client.post(
        "/api/hosts",
        json={"name": "gpu-1", "ip": "10.0.0.1", "password": "super-secret"},
    )
    assert response.status_code == 422
    assert "super-secret" not in response.text


def test_host_table_has_no_credential_columns(db_session: Session) -> None:
    columns = {
        column["name"]
        for column in inspect(db_session.bind).get_columns(Host.__tablename__)
    }
    assert "password" not in columns
    assert "private_key" not in columns
    assert "token" not in columns
