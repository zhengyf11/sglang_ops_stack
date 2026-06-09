from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.monitoring import (
    DeploymentMonitoringInfo,
    MonitoringConfigRead,
    MonitoringConfigResponse,
    MonitoringConfigUpsert,
)
from sglang_ops_stack.db.models.host import Host
from sglang_ops_stack.db.session import get_db
from sglang_ops_stack.services import deployment_service, monitoring_service

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/config", response_model=MonitoringConfigResponse)
def get_monitoring_config(db: DbSession) -> MonitoringConfigResponse:
    config = monitoring_service.get_config(db)
    configured = config is not None and bool(
        config.prometheus_base_url or config.grafana_base_url
    )
    return MonitoringConfigResponse(
        configured=configured,
        config=MonitoringConfigRead.model_validate(config) if config else None,
    )


@router.put("/config", response_model=MonitoringConfigResponse)
def put_monitoring_config(
    payload: MonitoringConfigUpsert, db: DbSession
) -> MonitoringConfigResponse:
    try:
        config = monitoring_service.upsert_config(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MonitoringConfigResponse(
        configured=bool(config.prometheus_base_url or config.grafana_base_url),
        config=MonitoringConfigRead.model_validate(config),
    )


@router.get("/deployments/{deployment_id}", response_model=DeploymentMonitoringInfo)
def get_deployment_monitoring(deployment_id: int, db: DbSession) -> DeploymentMonitoringInfo:
    deployment = deployment_service.get_deployment(db, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    host = db.get(Host, deployment.host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Host not found")
    config = monitoring_service.get_config(db)
    return DeploymentMonitoringInfo(
        target=monitoring_service.build_metrics_target(host, deployment),
        scrape_config=monitoring_service.build_prometheus_scrape_config(host, deployment),
        links=monitoring_service.build_monitoring_links(config, host, deployment),
        metrics_status=monitoring_service.metrics_status_from_health(deployment),
    )
