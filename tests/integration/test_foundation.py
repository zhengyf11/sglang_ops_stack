from fastapi.testclient import TestClient


def test_dashboard_home_page_links_to_existing_hosts_page(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Dashboard" in response.text
    assert 'href="/hosts"' in response.text
    assert "Hosts" in response.text


def test_api_health_reports_app_and_database_without_sensitive_details(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"]["status"] == "ok"
    assert body["app"]["name"] == "sglang_ops_stack"
    assert isinstance(body["app"]["version"], str)
    assert body["app"]["version"]
    assert body["app"]["environment"] == "development"
    assert body["database"]["status"] == "ok"
    serialized = response.text.lower()
    assert "sqlite" not in serialized
    assert "database_url" not in serialized
    assert "sglang_ops_stack.db" not in serialized


def test_legacy_health_endpoint_remains_available(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_docs_are_available_by_default(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
