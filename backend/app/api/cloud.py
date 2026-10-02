"""Cloud inventory and metric endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.deps import pipeline_unavailable, submit_task
from app.api.security_deps import PROTECTED
from app.logging_config import get_logger
from app.schemas.cloud import (
    CloudHealthResponse,
    CloudMetricsResponse,
    CloudResourcesResponse,
)
from app.schemas.task import TaskSubmission, TaskType
from app.services import cloud_service
from app.workers import tasks

logger = get_logger(__name__)

router = APIRouter(prefix="/cloud", tags=["cloud"])


@router.get(
    "",
    response_model=CloudHealthResponse,
    summary="Aggregate cloud estate health",
)
def get_cloud() -> CloudHealthResponse:
    response = cloud_service.health_response()
    if response is None:
        raise pipeline_unavailable(
            "Cloud health",
            "Collect inventory with POST /api/cloud/ingest.",
            TaskType.INGEST_CLOUD,
        )
    return response


@router.get(
    "/resources",
    response_model=CloudResourcesResponse,
    summary="Cloud resource inventory with filters",
)
def get_resources(
    status_filter: str | None = Query(None, alias="status", max_length=32),
    environment: str | None = Query(None, max_length=64),
    search: str | None = Query(None, max_length=120),
    idle_only: bool = Query(False),
) -> CloudResourcesResponse:
    response = cloud_service.resources_response(
        status=status_filter,
        environment=environment,
        search=search,
        idle_only=idle_only,
    )
    if response is None:
        raise pipeline_unavailable(
            "Cloud resources",
            "Collect inventory with POST /api/cloud/ingest.",
            TaskType.INGEST_CLOUD,
        )
    return response


@router.get(
    "/metrics",
    response_model=CloudMetricsResponse,
    summary="CloudWatch metric series",
)
def get_metrics() -> CloudMetricsResponse:
    response = cloud_service.metrics_response()
    if response is None:
        raise pipeline_unavailable(
            "Cloud metrics",
            "Collect metrics with POST /api/cloud/ingest.",
            TaskType.INGEST_CLOUD,
        )
    return response


@router.post(
    "/ingest",
    dependencies=PROTECTED,
    response_model=TaskSubmission,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start background EC2 + CloudWatch collection",
)
def ingest_cloud() -> TaskSubmission:
    return submit_task(TaskType.INGEST_CLOUD, tasks.ingest_cloud_data)
