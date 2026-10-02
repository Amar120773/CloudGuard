"""Dashboard and AI insight endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Body, status

from app.api.deps import submit_task
from app.api.security_deps import PROTECTED
from app.logging_config import get_logger
from app.schemas.dashboard import AiInsightsResponse, DashboardResponse
from app.schemas.task import RunTaskRequest, TaskSubmission, TaskType
from app.services import (
    cloud_service,
    cost_service,
    dashboard_service,
    insight_service,
    security_service,
)
from app.workers import tasks

logger = get_logger(__name__)

router = APIRouter(tags=["dashboard"])


@router.get(
    "/dashboard",
    response_model=DashboardResponse,
    summary="Everything the dashboard needs in one call",
)
def get_dashboard() -> DashboardResponse:
    """Aggregate cached pipeline output.

    Always 200: sections that are not ready come back null with a `pipelines`
    entry explaining why, so the UI can render partial state instead of an error
    page.
    """
    return dashboard_service.dashboard_response()


@router.post(
    "/dashboard/refresh",
    dependencies=PROTECTED,
    response_model=TaskSubmission,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run every pipeline in the background",
)
def refresh_dashboard(
    body: RunTaskRequest = Body(default_factory=RunTaskRequest),
) -> TaskSubmission:
    """Kick off ingestion, forecasting and anomaly detection, then poll the task."""
    return submit_task(
        TaskType.REFRESH_ALL, tasks.refresh_all, {"force": body.force}
    )


@router.get(
    "/insights",
    response_model=AiInsightsResponse,
    summary="Human-readable ML insights",
)
def get_insights() -> AiInsightsResponse:
    insights = insight_service.build_insights(
        forecast=cost_service.get_cached_forecast(),
        posture=security_service.get_cached_posture(),
        resources=cloud_service.get_cached_resources(),
        history=cost_service.get_cached_history(),
    )
    return AiInsightsResponse.model_validate(
        {
            "insights": insights,
            "count": len(insights),
            "freshness": {
                "cached": False,
                "generated_at": datetime.now(timezone.utc),
                "source": "pipeline",
            },
        }
    )
