from fastapi.testclient import TestClient


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

    page = client.get(f"/deployments/{deployment_id}")
    assert page.status_code == 200
    assert "Layered health" in page.text
    assert "Risk: --network host" in page.text
    assert "/models/secret" not in page.text
