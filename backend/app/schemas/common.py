"""Shared schema primitives."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CloudGuardModel(BaseModel):
    """Base model: forbid silent typos, allow population by field name.

    `protected_namespaces` is cleared because several fields legitimately start
    with "model_" (model_name, model_detail, model_signal) - they describe the ML
    model that produced the payload, not Pydantic internals.
    """

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        protected_namespaces=(),
    )


class Severity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"


class DataFreshness(CloudGuardModel):
    """Lets the UI tell "live" from "last known good" data honestly."""

    cached: bool = Field(False, description="Served from cache rather than recomputed")
    generated_at: Optional[datetime] = Field(
        None, description="When the underlying pipeline produced this payload"
    )
    age_seconds: Optional[int] = Field(
        None, ge=0, description="Approximate age of the cached payload"
    )
    stale: bool = Field(
        False, description="True when the payload is older than its freshness threshold"
    )
    state: Literal["fresh", "stale", "expired", "unavailable"] = Field(
        "fresh",
        description=(
            "fresh = recent; stale = served but old; expired = TTL elapsed but the "
            "pipeline has succeeded before; unavailable = never ran or undatable"
        ),
    )
    source: str = Field(
        "pipeline", description="pipeline | cache | fallback"
    )


class ServiceStatus(CloudGuardModel):
    name: str
    healthy: bool
    detail: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class HealthResponse(CloudGuardModel):
    status: str = Field(description="ok | degraded")
    app: str
    version: str
    environment: str
    cloud_mode: str
    timestamp: datetime = Field(default_factory=utcnow)
    dependencies: list[ServiceStatus] = Field(default_factory=list)
