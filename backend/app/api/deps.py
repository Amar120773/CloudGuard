"""Shared helpers for the API layer."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException, status

from app.schemas.task import TaskSubmission, TaskType
from app.services import task_service


def submit_task(
    task_type: TaskType,
    celery_task: Any,
    kwargs: Optional[Dict[str, Any]] = None,
) -> TaskSubmission:
    """Queue background work and shape the standard 202 submission body."""
    submission = task_service.submit(task_type, celery_task, kwargs)
    return TaskSubmission.model_validate(
        {
            **submission,
            "poll_url": f"/api/tasks/{submission['task_id']}",
        }
    )


def pipeline_unavailable(
    pipeline: str, hint: str, task_type: Optional[TaskType] = None
) -> HTTPException:
    """503 for "the pipeline has not produced results yet".

    A 404 would imply the route does not exist; 503 with a hint tells the client
    this is a warm-up state that a refresh will resolve.
    """
    detail = {
        "detail": f"{pipeline} results are not available yet.",
        "error_code": "pipeline_not_ready",
        "hint": hint,
    }
    if task_type:
        detail["run_endpoint"] = _run_endpoint(task_type)
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


def _run_endpoint(task_type: TaskType) -> str:
    return {
        TaskType.INGEST_COST: "/api/costs/ingest",
        TaskType.RUN_FORECAST: "/api/costs/forecast/run",
        TaskType.INGEST_SECURITY: "/api/security/ingest",
        TaskType.RUN_ANOMALY: "/api/security/anomalies/run",
        TaskType.INGEST_CLOUD: "/api/cloud/ingest",
        TaskType.REFRESH_ALL: "/api/dashboard/refresh",
    }.get(task_type, "/api/dashboard/refresh")
