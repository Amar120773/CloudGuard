"""Cost intelligence schemas."""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import Field, field_validator

from app.schemas.common import CloudGuardModel, DataFreshness, RiskLevel


class CostRecord(CloudGuardModel):
    """One day of spend for one service, as returned by Cost Explorer."""

    date: date
    service: str = Field(min_length=1, max_length=120)
    usage: float = Field(0.0, ge=0, description="Usage quantity for the period")
    cost: float = Field(ge=0, description="Unblended cost in USD")
    unit: str = Field("USD", max_length=16)

    @field_validator("cost", "usage")
    @classmethod
    def _finite(cls, value: float) -> float:
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("value must be a finite number")
        return round(float(value), 6)


class DailyCost(CloudGuardModel):
    """Spend aggregated across all services for a single day."""

    date: date
    cost: float = Field(ge=0)


class ServiceCostBreakdown(CloudGuardModel):
    service: str
    total_cost: float = Field(ge=0)
    share_pct: float = Field(ge=0, le=100)
    daily_average: float = Field(ge=0)
    trend_pct: float = Field(
        description="Change of the last 7 days vs the previous 7 days, percent"
    )
    projected_month_end: Optional[float] = Field(
        None,
        ge=0,
        description="Null when no aggregate forecast was available to allocate from",
    )
    is_risk: bool = Field(
        False, description="Flagged as a disproportionate contributor to projected spend"
    )


class CostHistoryResponse(CloudGuardModel):
    generated_at: Optional[datetime] = Field(
        None, description="When the pipeline produced this payload (UTC)"
    )
    currency: str = "USD"
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    total_cost: float = Field(0.0, ge=0)
    month_to_date: float = Field(0.0, ge=0)
    daily_average: float = Field(0.0, ge=0)
    records_analysed: int = Field(0, ge=0)
    daily: List[DailyCost] = Field(default_factory=list)
    by_service: List[ServiceCostBreakdown] = Field(default_factory=list)
    freshness: DataFreshness = Field(default_factory=DataFreshness)


class ForecastPoint(CloudGuardModel):
    date: date
    predicted_cost: float
    lower_bound: float
    upper_bound: float


class ForecastAccuracy(CloudGuardModel):
    """Hold-out backtest metrics (spec section 24: MAE for forecasting)."""

    mae: Optional[float] = Field(None, ge=0, description="Mean absolute error, USD/day")
    mape: Optional[float] = Field(None, ge=0, description="Mean absolute percentage error")
    rmse: Optional[float] = Field(None, ge=0)
    backtest_days: int = Field(0, ge=0)
    baseline_mae: Optional[float] = Field(
        None, ge=0, description="MAE of a seasonal-naive baseline, for comparison"
    )
    skill_vs_baseline_pct: Optional[float] = Field(
        None, description="Percent improvement over the naive baseline"
    )


class BudgetAnalysis(CloudGuardModel):
    monthly_budget: float = Field(ge=0)
    projected_month_end: float = Field(ge=0)
    projected_vs_budget_pct: float
    budget_breach_expected: bool
    budget_breach_possible: bool = Field(
        False, description="Upper confidence bound exceeds the budget"
    )
    headroom: float = Field(description="Budget minus projection; negative means overrun")
    days_remaining_in_month: int = Field(ge=0)


class CostForecastResponse(CloudGuardModel):
    model_name: Literal["prophet", "trend_seasonal_fallback"] = "prophet"
    model_detail: Optional[str] = None
    horizon_days: int = Field(ge=1)
    interval_width: Optional[float] = Field(
        None,
        gt=0,
        lt=1,
        description="Coverage of lower_bound/upper_bound, e.g. 0.85 for an 85% interval "
        "(PROPHET_INTERVAL_WIDTH). Null for payloads cached before it was recorded.",
    )
    generated_at: Optional[datetime] = None
    currency: str = "USD"

    forecast: List[ForecastPoint] = Field(default_factory=list)
    history_tail: List[DailyCost] = Field(
        default_factory=list, description="Recent actuals, so charts can join the series"
    )

    month_to_date: float = Field(0.0, ge=0)
    projected_month_end: float = Field(0.0, ge=0)
    projected_month_end_lower: float = Field(0.0, ge=0)
    projected_month_end_upper: float = Field(0.0, ge=0)
    next_7_days_total: float = Field(0.0, ge=0)
    next_30_days_total: float = Field(0.0, ge=0)

    trend_pct: float = Field(
        0.0, description="Forecast mean vs trailing actual mean, percent"
    )
    trend_direction: Literal["increasing", "stable", "decreasing"] = "stable"
    risk_level: RiskLevel = RiskLevel.LOW
    risk_services: List[ServiceCostBreakdown] = Field(default_factory=list)
    service_breakdown: List[ServiceCostBreakdown] = Field(
        default_factory=list,
        description="Every service with its projected month-end contribution",
    )
    spending_warning: Optional[str] = None
    abnormal_spending_detected: bool = False

    budget: Optional[BudgetAnalysis] = None
    accuracy: ForecastAccuracy = Field(default_factory=ForecastAccuracy)
    freshness: DataFreshness = Field(default_factory=DataFreshness)
