"""Cost intelligence endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Body, Query, status

from app.api.deps import pipeline_unavailable, submit_task
from app.config import settings
from app.api.security_deps import PROTECTED
from app.logging_config import get_logger
from app.schemas.cost import CostForecastResponse, CostHistoryResponse
from app.schemas.task import RunTaskRequest, TaskSubmission, TaskType
from app.services import cost_service
from app.workers import tasks

logger = get_logger(__name__)

router = APIRouter(prefix="/costs", tags=["costs"])


@router.get(
    "",
    response_model=CostHistoryResponse,
    summary="Current cost history (cached)",
)
def get_costs() -> CostHistoryResponse:
    response = cost_service.history_response()
    if response is None:
        raise pipeline_unavailable(
            "Cost history",
            "Trigger ingestion with POST /api/costs/ingest, or refresh everything "
            "with POST /api/dashboard/refresh.",
            TaskType.INGEST_COST,
        )
    return response


@router.get(
    "/history",
    response_model=CostHistoryResponse,
    summary="Historical spend, daily and per service",
)
def get_cost_history() -> CostHistoryResponse:
    return get_costs()


@router.get(
    "/forecast",
    response_model=CostForecastResponse,
    summary="Latest Prophet forecast with month-end projection",
)
def get_forecast() -> CostForecastResponse:
    response = cost_service.forecast_response()
    if response is None:
        raise pipeline_unavailable(
            "Cost forecast",
            "Train the model with POST /api/costs/forecast/run.",
            TaskType.RUN_FORECAST,
        )
    return response


@router.post(
    "/forecast/run",
    dependencies=PROTECTED,
    response_model=TaskSubmission,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a background Prophet forecast",
)
def run_forecast(
    body: RunTaskRequest = Body(default_factory=RunTaskRequest),
) -> TaskSubmission:
    """Queue the forecast. Returns immediately with a task id to poll."""
    return submit_task(
        TaskType.RUN_FORECAST,
        tasks.run_cost_forecast,
        {
            "horizon_days": body.horizon_days or settings.forecast_horizon_days,
            "force": body.force,
        },
    )


@router.post(
    "/ingest",
    dependencies=PROTECTED,
    response_model=TaskSubmission,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start background Cost Explorer ingestion",
)
def ingest_costs(
    days: int = Query(
        default=None, ge=14, le=400, description="Days of history to pull"
    ),
) -> TaskSubmission:
    return submit_task(
        TaskType.INGEST_COST,
        tasks.ingest_cost_data,
        {"days": days or settings.demo_history_days},
    )
