"""Dashboard and AI-insight schemas."""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import Field

from app.schemas.cloud import CloudHealthResponse
from app.schemas.common import CloudGuardModel, DataFreshness, RiskLevel, Severity
from app.schemas.cost import CostForecastResponse, CostHistoryResponse
from app.schemas.security import SecurityEvent, SecurityPostureResponse

InsightCategory = Literal["COST", "SECURITY", "RESOURCE", "SYSTEM"]


class AiInsight(CloudGuardModel):
    """Spec section 25: metric + model output + actionable context."""

    insight_id: str
    category: InsightCategory
    title: str
    # The three mandated parts of every insight.
    metric: str = Field(description="The headline number, formatted for display")
    model_signal: Optional[str] = Field(
        None, description="Prediction range or anomaly score backing the claim"
    )
    narrative: str = Field(description="Plain-language interpretation")
    action: Optional[str] = Field(None, description="What the operator should do next")

    severity: Severity = Severity.INFO
    confidence: Optional[float] = Field(
        None, ge=0, le=1, description="Model confidence where one is meaningful"
    )
    source_model: Optional[str] = Field(None, description="prophet | IsolationForest | heuristic")
    generated_at: datetime
    related_resource_ids: List[str] = Field(default_factory=list)


class AiInsightsResponse(CloudGuardModel):
    insights: List[AiInsight] = Field(default_factory=list)
    count: int = Field(0, ge=0)
    freshness: DataFreshness = Field(default_factory=DataFreshness)


class OverviewMetrics(CloudGuardModel):
    """The headline tiles on the overview page."""

    current_spend_mtd: float = Field(0.0, ge=0)
    forecast_month_end: float = Field(0.0, ge=0)
    forecast_lower: float = Field(0.0, ge=0)
    forecast_upper: float = Field(0.0, ge=0)
    cost_trend_pct: float = 0.0
    cost_risk_level: RiskLevel = RiskLevel.LOW
    cost_risk_services: int = Field(0, ge=0)
    budget_breach_expected: bool = False

    anomaly_count: int = Field(0, ge=0)
    total_security_events: int = Field(0, ge=0)
    security_health_score: float = Field(100.0, ge=0, le=100)
    critical_anomalies: int = Field(0, ge=0)

    active_resources: int = Field(0, ge=0)
    idle_resources: int = Field(0, ge=0)
    potential_monthly_savings: float = Field(0.0, ge=0)
    cloud_health_status: str = "Unknown"
    cloud_health_score: float = Field(0.0, ge=0, le=100)


class PipelineState(CloudGuardModel):
    """Per-pipeline readiness, so the UI can show precise loading/fallback states."""

    name: str
    ready: bool
    stale: bool = False
    state: Literal["fresh", "stale", "expired", "unavailable"] = "fresh"
    age_seconds: Optional[int] = None
    last_success_at: Optional[datetime] = None
    message: Optional[str] = None


class DashboardResponse(CloudGuardModel):
    generated_at: datetime
    overview: OverviewMetrics
    cost: Optional[CostHistoryResponse] = None
    forecast: Optional[CostForecastResponse] = None
    security: Optional[SecurityPostureResponse] = None
    cloud_health: Optional[CloudHealthResponse] = None
    recent_events: List[SecurityEvent] = Field(
        default_factory=list,
        description="Short feed for the Overview; the full history is /api/security/events",
    )
    insights: List[AiInsight] = Field(default_factory=list)
    pipelines: List[PipelineState] = Field(default_factory=list)
    pending_task_ids: List[str] = Field(default_factory=list)
    degraded: bool = Field(
        False,
        description=(
            "True when infrastructure is degraded (Redis unreachable). Pipelines "
            "that have not run yet are reported via `pipelines`, not here."
        ),
    )
    freshness: DataFreshness = Field(default_factory=DataFreshness)
