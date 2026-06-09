import re
import string
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.monitoring import (
    MetricsTarget,
    MonitoringConfigUpsert,
    MonitoringLinks,
)
from sglang_ops_stack.db.models.deployment import Deployment
from sglang_ops_stack.db.models.host import Host
from sglang_ops_stack.db.models.monitoring import MonitoringConfig

_PROMETHEUS_TEXT_MARKERS = ("# HELP", "# TYPE", "_total", "{}")
_JOB_SAFE_RE = re.compile(r"[^a-zA-Z0-9_-]+")
_GRAFANA_TEMPLATE_FIELDS = {"deployment", "host", "port", "served_model_name"}


@dataclass(frozen=True)
class DashboardSummary:
    host_count: int
    deployment_count: int
    deployment_health: dict[str, int]
    metrics_unreachable: list[Deployment]
    monitoring_configured: bool
    prometheus_configured: bool
    grafana_configured: bool


def get_config(db: Session) -> MonitoringConfig | None:
    return db.scalar(select(MonitoringConfig).order_by(MonitoringConfig.id.asc()).limit(1))


def upsert_config(db: Session, payload: MonitoringConfigUpsert) -> MonitoringConfig:
    config = get_config(db)
    if config is None:
        config = MonitoringConfig()
    config.grafana_base_url = payload.grafana_base_url
    config.prometheus_base_url = payload.prometheus_base_url
    config.default_dashboard_path = payload.default_dashboard_path
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def build_metrics_target(host: Host, deployment: Deployment) -> MetricsTarget | None:
    if deployment.metrics_url is None:
        return None
    return MetricsTarget(
        metrics_url=deployment.metrics_url,
        prometheus_target=f"{host.ip}:{deployment.port}",
        deployment=deployment.name,
        host=host.name,
        port=deployment.port,
    )


def build_prometheus_scrape_config(host: Host, deployment: Deployment) -> str | None:
    target = build_metrics_target(host, deployment)
    if target is None:
        return None
    job_name = _safe_job_name(deployment.name)
    deployment_label = _escape_yaml_scalar(deployment.name)
    host_label = _escape_yaml_scalar(host.name)
    target_value = _escape_yaml_scalar(target.prometheus_target)
    return "\n".join(
        [
            f"- job_name: sglang-{job_name}",
            "  static_configs:",
            "    - targets:",
            f"        - {target_value}",
            "      labels:",
            f"        deployment: {deployment_label}",
            f"        host: {host_label}",
        ]
    )


def build_monitoring_links(
    config: MonitoringConfig | None, host: Host, deployment: Deployment
) -> MonitoringLinks:
    if config is None:
        return MonitoringLinks(grafana_url=None, prometheus_targets_url=None)
    return MonitoringLinks(
        grafana_url=_build_grafana_url(config, host, deployment),
        prometheus_targets_url=_build_prometheus_targets_url(config),
    )


def metrics_status_from_health(deployment: Deployment) -> dict[str, Any]:
    if deployment.metrics_url is None:
        return {
            "status": "DISABLED",
            "reachable": None,
            "message": "Metrics endpoint is disabled for this deployment",
            "metrics_url": None,
        }
    health = deployment.last_health_status
    if not isinstance(health, dict):
        return {
            "status": "UNKNOWN",
            "reachable": None,
            "message": "Metrics have not been checked yet",
            "metrics_url": deployment.metrics_url,
        }
    layers = health.get("layers")
    if not isinstance(layers, list):
        return {
            "status": "UNKNOWN",
            "reachable": None,
            "message": "Metrics have not been checked yet",
            "metrics_url": deployment.metrics_url,
        }
    for layer in layers:
        if isinstance(layer, dict) and layer.get("name") == "metrics":
            raw_details = layer.get("details")
            details: dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}
            return {
                "status": layer.get("status", "UNKNOWN"),
                "reachable": details.get("reachable"),
                "message": layer.get("message", ""),
                "metrics_url": details.get("metrics_url", deployment.metrics_url),
                "checked_at": layer.get("checked_at"),
                "last_status_code": details.get("status_code"),
            }
    return {
        "status": "UNKNOWN",
        "reachable": None,
        "message": "Metrics have not been checked yet",
        "metrics_url": deployment.metrics_url,
    }


def metrics_response_is_prometheus_text(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    text = payload.get("text")
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped:
        return False
    return any(marker in stripped for marker in _PROMETHEUS_TEXT_MARKERS)


def dashboard_summary(
    db: Session,
    *,
    hosts: Sequence[Host] | None = None,
    deployments: Sequence[Deployment] | None = None,
) -> DashboardSummary:
    host_items = list(hosts) if hosts is not None else list(db.scalars(select(Host)).all())
    deployment_items = (
        list(deployments) if deployments is not None else list(db.scalars(select(Deployment)).all())
    )
    status_counts = Counter(
        _deployment_health_status(deployment) for deployment in deployment_items
    )
    metrics_unreachable = [
        deployment
        for deployment in deployment_items
        if deployment.metrics_url is not None
        and metrics_status_from_health(deployment).get("reachable") is False
    ]
    config = get_config(db)
    return DashboardSummary(
        host_count=len(host_items),
        deployment_count=len(deployment_items),
        deployment_health={
            "healthy": status_counts["healthy"],
            "warning": status_counts["warning"],
            "failed": status_counts["failed"],
            "unknown": status_counts["unknown"],
        },
        metrics_unreachable=metrics_unreachable,
        monitoring_configured=config is not None
        and bool(config.prometheus_base_url or config.grafana_base_url),
        prometheus_configured=bool(config and config.prometheus_base_url),
        grafana_configured=bool(config and config.grafana_base_url),
    )


def _deployment_health_status(deployment: Deployment) -> str:
    if deployment.status == "running":
        health = deployment.last_health_status
        if isinstance(health, dict) and health.get("status") == "WARN":
            return "warning"
        return "healthy"
    if deployment.status == "degraded":
        return "warning"
    if deployment.status == "failed":
        return "failed"
    return "unknown"


def _build_grafana_url(config: MonitoringConfig, host: Host, deployment: Deployment) -> str | None:
    if not config.grafana_base_url and not config.default_dashboard_path:
        return None
    template = config.default_dashboard_path or ""
    values = {
        "deployment": quote(deployment.name, safe=""),
        "host": quote(host.name, safe=""),
        "port": str(deployment.port),
        "served_model_name": quote(deployment.served_model_name or "", safe=""),
    }
    if not _grafana_template_is_allowed(template):
        return None
    try:
        rendered = template.format(**values)
    except (KeyError, IndexError, ValueError):
        return None
    if rendered.startswith(("http://", "https://")):
        return rendered
    base = (config.grafana_base_url or "").rstrip("/")
    if not base:
        return None
    path = rendered if rendered.startswith("/") else f"/{rendered}"
    return f"{base}{path}"


def _grafana_template_is_allowed(template: str) -> bool:
    try:
        fields = [field_name for _, field_name, _, _ in string.Formatter().parse(template)]
    except ValueError:
        return False
    return all(
        field_name is None or field_name in _GRAFANA_TEMPLATE_FIELDS for field_name in fields
    )


def _build_prometheus_targets_url(config: MonitoringConfig) -> str | None:
    if not config.prometheus_base_url:
        return None
    parts = urlsplit(config.prometheus_base_url)
    query = urlencode({"search": "sglang"})
    return urlunsplit((parts.scheme, parts.netloc, "/targets", query, ""))


def _safe_job_name(value: str) -> str:
    normalized = _JOB_SAFE_RE.sub("-", value.strip()).strip("-").lower()
    return normalized or "deployment"


def _escape_yaml_scalar(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        return value
    return '"' + value.replace('"', '\\"') + '"'
