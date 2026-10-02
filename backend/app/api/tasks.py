"""Task status endpoints (spec section 11)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.logging_config import get_logger
from app.schemas.task import TaskListResponse, TaskRecord
from app.services import task_service

logger = get_logger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get(
    "",
    response_model=TaskListResponse,
    summary="Recently submitted background tasks",
)
def list_tasks(limit: int = Query(25, ge=1, le=100)) -> TaskListResponse:
    records = task_service.recent_records(limit)
    return TaskListResponse.model_validate(
        {"tasks": records, "count": len(records)}
    )


@router.get(
    "/{task_id}",
    response_model=TaskRecord,
    summary="Status of one background task",
    responses={404: {"description": "Unknown task id"}},
)
def get_task(task_id: str = Path(min_length=1, max_length=200)) -> TaskRecord:
    """Poll a task until `status` is COMPLETED or FAILED."""
    record = task_service.get_record(task_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "detail": f"No task with id {task_id!r}.",
                "error_code": "task_not_found",
                "hint": (
                    "Task records expire after 24h, and ids are not shared across "
                    "Redis instances."
                ),
            },
        )
    return TaskRecord.model_validate(record)
