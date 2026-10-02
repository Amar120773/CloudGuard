"""Security analytics endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Path, Query, status

from app.api.deps import pipeline_unavailable, submit_task
from app.api.security_deps import PROTECTED
from app.logging_config import get_logger
from app.schemas.security import (
    SecurityEvent,
    SecurityEventsResponse,
    SecurityPostureResponse,
)
from app.schemas.task import RunTaskRequest, TaskSubmission, TaskType
from app.services import security_service
from app.workers import tasks

logger = get_logger(__name__)

router = APIRouter(prefix="/security", tags=["security"])


@router.get(
    "",
    response_model=SecurityPostureResponse,
    summary="Security posture with anomaly summary",
)
def get_security() -> SecurityPostureResponse:
    response = security_service.posture_response()
    if response is None:
        raise pipeline_unavailable(
            "Security analytics",
            "Run detection with POST /api/security/anomalies/run.",
            TaskType.RUN_ANOMALY,
        )
    return response


@router.get(
    "/events",
    response_model=SecurityEventsResponse,
    summary="Scored event feed, filterable and paginated",
)
def get_events(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status_filter: str | None = Query(
        None, alias="status", pattern="^(?i)(ROUTINE|ANOMALY)$"
    ),
    min_score: float | None = Query(None, ge=0, le=100),
) -> SecurityEventsResponse:
    response = security_service.events_response(
        limit=limit, offset=offset, status=status_filter, min_score=min_score
    )
    if response is None:
        raise pipeline_unavailable(
            "Security events",
            "Run detection with POST /api/security/anomalies/run.",
            TaskType.RUN_ANOMALY,
        )
    return response


@router.get(
    "/anomalies",
    response_model=SecurityEventsResponse,
    summary="Anomalous events only, highest score first",
)
def get_anomalies(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> SecurityEventsResponse:
    response = security_service.events_response(
        limit=limit, offset=offset, status="ANOMALY"
    )
    if response is None:
        raise pipeline_unavailable(
            "Security anomalies",
            "Run detection with POST /api/security/anomalies/run.",
            TaskType.RUN_ANOMALY,
        )
    response.events.sort(key=lambda event: event.anomaly_score, reverse=True)
    return response


@router.get(
    "/events/{event_id}",
    response_model=SecurityEvent,
    summary="Full detail for one event, including feature deviations",
)
def get_event(event_id: str = Path(min_length=1, max_length=200)) -> SecurityEvent:
    event = security_service.get_event(event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "detail": f"No scored event with id {event_id!r}.",
                "error_code": "event_not_found",
                "hint": "Event ids change when detection re-runs; refetch the feed.",
            },
        )
    return SecurityEvent.model_validate(event)


@router.post(
    "/anomalies/run",
    dependencies=PROTECTED,
    response_model=TaskSubmission,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start background IsolationForest scoring",
)
def run_anomaly_detection(
    body: RunTaskRequest = Body(default_factory=RunTaskRequest),
) -> TaskSubmission:
    """Queue detection. `inject_anomaly` seeds a live outlier for the demo."""
    return submit_task(
        TaskType.RUN_ANOMALY,
        tasks.run_anomaly_detection,
        {
            "contamination": body.contamination,
            "inject_anomaly": body.inject_anomaly,
            "force": body.force,
        },
    )


@router.post(
    "/ingest",
    dependencies=PROTECTED,
    response_model=TaskSubmission,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start background flow-log ingestion and scoring",
)
def ingest_security(
    body: RunTaskRequest = Body(default_factory=RunTaskRequest),
) -> TaskSubmission:
    return submit_task(
        TaskType.INGEST_SECURITY,
        tasks.ingest_security_events,
        {"inject_anomaly": body.inject_anomaly},
    )
