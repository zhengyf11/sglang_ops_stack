from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.deployment import DeploymentCreate
from sglang_ops_stack.api.schemas.host import HostCreate
from sglang_ops_stack.api.schemas.monitoring import MonitoringConfigUpsert
from sglang_ops_stack.services import deployment_service, host_service, monitoring_service


def _create_deployment(db: Session):
    host = host_service.create_host(db, HostCreate(name="gpu-1", ip="10.0.0.1"))
    deployment = deployment_service.create_deployment(
        db,
        DeploymentCreate(
            host_id=host.id,
            name="demo",
            container_name="sglang_demo",
            image="lmsysorg/sglang:latest",
            model_path="/models/demo",
            served_model_name="demo-model",
            port=30000,
        ),
    )
    return host, deployment


def test_monitoring_config_validates_and_round_trips(db_session: Session) -> None:
    config = monitoring_service.upsert_config(
        db_session,
        MonitoringConfigUpsert(
            prometheus_base_url="http://prometheus.local:9090/",
            grafana_base_url="https://grafana.local",
            default_dashboard_path="/d/sglang/overview?var-deployment={deployment}&var-host={host}",
        ),
    )

    assert config.prometheus_base_url == "http://prometheus.local:9090"
    assert config.grafana_base_url == "https://grafana.local"

    loaded = monitoring_service.get_config(db_session)
    assert loaded is not None
    assert loaded.id == config.id


def test_monitoring_config_rejects_unsafe_urls(db_session: Session) -> None:
    for payload in (
        {"prometheus_base_url": "javascript:alert(1)"},
        {"grafana_base_url": "data:text/html,boom"},
        {"grafana_base_url": "https://token:grafana.local@evil.example"},
    ):
        try:
            monitoring_service.upsert_config(db_session, MonitoringConfigUpsert(**payload))
        except ValueError as exc:
            assert "http/https" in str(exc) or "credentials" in str(exc)
        else:  # pragma: no cover - assertion branch
            raise AssertionError(f"unsafe payload accepted: {payload}")


def test_metrics_target_scrape_config_and_links(db_session: Session) -> None:
    host, deployment = _create_deployment(db_session)
    config = monitoring_service.upsert_config(
        db_session,
        MonitoringConfigUpsert(
            prometheus_base_url="http://prometheus.local:9090",
            grafana_base_url="http://grafana.local",
            default_dashboard_path="/d/sglang/overview?var-deployment={deployment}&var-port={port}&var-model={served_model_name}",
        ),
    )

    target = monitoring_service.build_metrics_target(host, deployment)
    assert target.metrics_url == "http://10.0.0.1:30000/metrics"
    assert target.prometheus_target == "10.0.0.1:30000"

    scrape_config = monitoring_service.build_prometheus_scrape_config(host, deployment)
    assert "job_name: sglang-demo" in scrape_config
    assert "- 10.0.0.1:30000" in scrape_config
    assert "deployment: demo" in scrape_config
    assert "host: gpu-1" in scrape_config

    links = monitoring_service.build_monitoring_links(config, host, deployment)
    assert links.grafana_url is not None
    assert links.grafana_url.startswith("http://grafana.local/d/sglang/overview")
    assert "var-deployment=demo" in links.grafana_url
    assert "var-port=30000" in links.grafana_url
    assert links.prometheus_targets_url is not None
    assert links.prometheus_targets_url.startswith("http://prometheus.local:9090/targets")
