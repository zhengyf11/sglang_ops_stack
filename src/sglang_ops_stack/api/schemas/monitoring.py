from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

_ALLOWED_SCHEMES = {"http", "https"}


def validate_monitoring_url(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().rstrip("/")
    if not normalized:
        return None
    parts = urlsplit(normalized)
    if parts.scheme not in _ALLOWED_SCHEMES or not parts.netloc:
        raise ValueError("monitoring URLs must be absolute http/https URLs")
    if parts.username or parts.password:
        raise ValueError("monitoring URLs must not contain credentials")
    return normalized


class MonitoringConfigUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    grafana_base_url: str | None = Field(default=None, max_length=512)
    prometheus_base_url: str | None = Field(default=None, max_length=512)
    default_dashboard_path: str | None = Field(default=None, max_length=1024)

    @field_validator("grafana_base_url", "prometheus_base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        return validate_monitoring_url(value)

    @field_validator("default_dashboard_path")
    @classmethod
    def normalize_dashboard_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.lower().startswith(("javascript:", "data:")):
            raise ValueError("dashboard path must not use javascript/data URLs")
        if "://" in normalized:
            return validate_monitoring_url(normalized)
        return normalized


class MonitoringConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    grafana_base_url: str | None
    prometheus_base_url: str | None
    default_dashboard_path: str | None
    created_at: datetime
    updated_at: datetime


class MonitoringConfigResponse(BaseModel):
    configured: bool
    config: MonitoringConfigRead | None


class MetricsTarget(BaseModel):
    metrics_url: str
    prometheus_target: str
    deployment: str
    host: str
    port: int


class MonitoringLinks(BaseModel):
    grafana_url: str | None
    prometheus_targets_url: str | None


class DeploymentMonitoringInfo(BaseModel):
    target: MetricsTarget | None
    scrape_config: str | None
    links: MonitoringLinks
    metrics_status: dict[str, Any]
