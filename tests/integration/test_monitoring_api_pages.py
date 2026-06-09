from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.monitoring import MonitoringConfigUpsert
from sglang_ops_stack.db.models.deployment import Deployment
from sglang_ops_stack.services import monitoring_service


def _create_host(client: TestClient) -> int:
    response = client.post("/api/hosts", json={"name": "gpu-1", "ip": "10.0.0.1"})
    assert response.status_code == 201
    return int(response.json()["id"])


def _create_deployment(client: TestClient, host_id: int) -> int:
    response = client.post(
        "/api/deployments",
        json={
            "host_id": host_id,
            "name": "demo",
            "container_name": "sglang_demo",
            "image": "lmsysorg/sglang:latest",
            "model_path": "/models/demo",
            "served_model_name": "demo-model",
        },
    )
    assert response.status_code == 201
    return int(response.json()["id"])


def test_monitoring_api_and_page_round_trip(client: TestClient) -> None:
    get_empty = client.get("/api/monitoring/config")
    assert get_empty.status_code == 200
    assert get_empty.json()["configured"] is False

    save = client.put(
        "/api/monitoring/config",
        json={
            "prometheus_base_url": "http://prometheus.local:9090/",
            "grafana_base_url": "http://grafana.local",
            "default_dashboard_path": "/d/sglang/overview?var-deployment={deployment}",
        },
    )
    assert save.status_code == 200
    assert save.json()["configured"] is True
    assert save.json()["config"]["prometheus_base_url"] == "http://prometheus.local:9090"

    page = client.get("/monitoring")
    assert page.status_code == 200
    assert "Monitoring configuration" in page.text
    assert "http://prometheus.local:9090" in page.text

    post = client.post(
        "/monitoring",
        data={
            "prometheus_base_url": "http://prometheus.local:9091",
            "grafana_base_url": "http://grafana.local",
            "default_dashboard_path": "/d/sglang/overview",
        },
        follow_redirects=False,
    )
    assert post.status_code == 303
    assert post.headers["location"] == "/monitoring"


def test_deployment_detail_and_dashboard_show_monitoring_status(
    client: TestClient, db_session: Session
) -> None:
    host_id = _create_host(client)
    deployment_id = _create_deployment(client, host_id)
    monitoring_service.upsert_config(
        db_session,
        MonitoringConfigUpsert(
            prometheus_base_url="http://prometheus.local:9090",
            grafana_base_url="http://grafana.local",
            default_dashboard_path="/d/sglang/overview?var-deployment={deployment}",
        ),
    )
    deployment = db_session.get(Deployment, deployment_id)
    assert deployment is not None
    deployment.status = "degraded"
    deployment.last_health_status = {
        "status": "WARN",
        "layers": [
            {
                "name": "metrics",
                "status": "WARN",
                "message": "HTTP /metrics unavailable",
                "checked_at": "2026-06-09T00:00:00Z",
                "details": {"reachable": False, "metrics_url": "http://10.0.0.1:30000/metrics"},
            }
        ],
    }
    db_session.add(deployment)
    db_session.commit()

    info = client.get(f"/api/monitoring/deployments/{deployment_id}")
    assert info.status_code == 200
    assert info.json()["target"]["prometheus_target"] == "10.0.0.1:30000"
    assert "job_name: sglang-demo" in info.json()["scrape_config"]

    detail = client.get(f"/deployments/{deployment_id}")
    assert detail.status_code == 200
    assert "Prometheus scrape config" in detail.text
    assert "10.0.0.1:30000" in detail.text
    assert "Open Grafana" in detail.text
    assert "Prometheus targets" in detail.text
    assert "Metrics reachability" in detail.text

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Deployment health overview" in dashboard.text
    assert "Metrics unreachable deployments" in dashboard.text
    assert "demo" in dashboard.text
    assert "Grafana configured" in dashboard.text
    assert "Prometheus configured" in dashboard.text
