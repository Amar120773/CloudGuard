"""Background task schemas (spec section 11)."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from app.schemas.common import CloudGuardModel, utcnow


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TaskType(str, Enum):
    INGEST_CLOUD = "ingest_cloud_data"
    INGEST_COST = "ingest_cost_data"
    INGEST_SECURITY = "ingest_security_events"
    RUN_FORECAST = "run_cost_forecast"
    RUN_ANOMALY = "run_anomaly_detection"
    REFRESH_ALL = "refresh_all"


class TaskSubmission(CloudGuardModel):
    """Returned by every POST that kicks off background work."""

    task_id: str
    task_type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    submitted_at: datetime = Field(default_factory=utcnow)
    executor: Literal["celery", "inline"] = Field(
        "celery",
        description="inline means Celery was unreachable and a local worker thread ran it",
    )
    poll_url: str
    message: Optional[str] = None


class TaskRecord(CloudGuardModel):
    """Durable task record kept in Redis, independent of Celery result expiry."""

    task_id: str
    task_type: TaskType
    status: TaskStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = Field(None, ge=0)
    executor: Literal["celery", "inline"] = "celery"
    progress: int = Field(0, ge=0, le=100)
    stage: Optional[str] = Field(None, description="Human-readable current step")
    result: Optional[Dict[str, Any]] = Field(
        None, description="Compact result summary; bulk payloads live in cache keys"
    )
    cache_keys: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class TaskListResponse(CloudGuardModel):
    tasks: List[TaskRecord] = Field(default_factory=list)
    count: int = Field(0, ge=0)


class RunTaskRequest(CloudGuardModel):
    """Optional body for /run endpoints."""

    force: bool = Field(
        False, description="Ignore any warm cache and recompute from scratch"
    )
    horizon_days: Optional[int] = Field(
        None, ge=1, le=180, description="Forecast horizon override"
    )
    contamination: Optional[float] = Field(
        None, gt=0, le=0.5, description="IsolationForest contamination override"
    )
    inject_anomaly: bool = Field(
        False,
        description="Seed an extra extreme event, for the live anomaly demo",
    )
