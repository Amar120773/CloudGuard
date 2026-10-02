"""Cost forecasting with Prophet.

Pipeline (spec section 6):

    historical spend -> cleaning -> aggregation -> Prophet -> future forecast
    -> risk / budget analysis

Prophet is the primary engine. Because Prophet depends on a compiled Stan
backend that can be absent or broken in some environments, a deterministic
trend+seasonality estimator stands in when Prophet cannot fit, and the response
reports which engine produced the numbers rather than hiding the difference.
"""
from __future__ import annotations

import calendar
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.config import settings
from app.logging_config import get_logger, quiet_ml_loggers

logger = get_logger(__name__)

MIN_DAYS_FOR_FORECAST = 14


class InsufficientDataError(ValueError):
    """Raised when there is not enough clean history to forecast responsibly."""


class ForecastError(RuntimeError):
    """Raised when every available forecasting engine failed."""


@dataclass
class PreparedSeries:
    """Cleaned, gap-filled daily totals ready for Prophet."""

    frame: pd.DataFrame  # columns: ds (datetime64), y (float)
    rows_in: int = 0
    rows_dropped: int = 0
    gaps_filled: int = 0
    start: Optional[date] = None
    end: Optional[date] = None

    @property
    def days(self) -> int:
        return len(self.frame)

    def notes(self) -> List[str]:
        notes: List[str] = []
        if self.rows_dropped:
            notes.append(f"dropped {self.rows_dropped} invalid record(s)")
        if self.gaps_filled:
            notes.append(f"interpolated {self.gaps_filled} missing day(s)")
        return notes


@dataclass
class ForecastResult:
    points: List[Dict[str, Any]] = field(default_factory=list)
    model_name: str = "prophet"
    model_detail: Optional[str] = None
    # Coverage of the lower/upper bounds, from whichever engine answered.
    interval_width: Optional[float] = None


# ==========================================================================
# Stage 1-3: cleaning and aggregation
# ==========================================================================
def prepare_series(records: List[Dict[str, Any]]) -> PreparedSeries:
    """Turn raw cost records into a continuous daily total series.

    Handles the messiness real billing exports have: duplicate rows, blank or
    non-numeric amounts, NaN/inf, out-of-order dates and missing days.
    """
    rows_in = len(records)
    if rows_in == 0:
        raise InsufficientDataError("No cost records were supplied.")

    frame = pd.DataFrame(records)
    if "date" not in frame.columns or "cost" not in frame.columns:
        raise InsufficientDataError(
            "Cost records must contain 'date' and 'cost' fields."
        )

    frame["ds"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["y"] = pd.to_numeric(frame["cost"], errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan)

    before = len(frame)
    frame = frame.dropna(subset=["ds", "y"])
    # Negative spend appears in real data as refunds/credits; keep the signal
    # but floor it so the model is not dragged below zero.
    frame.loc[frame["y"] < 0, "y"] = 0.0
    rows_dropped = before - len(frame)

    if frame.empty:
        raise InsufficientDataError(
            f"All {rows_in} cost record(s) were invalid after cleaning."
        )

    # Aggregate every service into one daily total.
    frame["ds"] = frame["ds"].dt.normalize()
    daily = (
        frame.groupby("ds", as_index=False)["y"]
        .sum()
        .sort_values("ds")
        .reset_index(drop=True)
    )

    # Reindex onto a continuous calendar and interpolate the holes.
    full_index = pd.date_range(daily["ds"].min(), daily["ds"].max(), freq="D")
    gaps_filled = len(full_index) - len(daily)
    if gaps_filled > 0:
        daily = (
            daily.set_index("ds")
            .reindex(full_index)
            .interpolate(method="linear", limit_direction="both")
            .rename_axis("ds")
            .reset_index()
        )

    daily["y"] = daily["y"].astype(float).round(6)

    return PreparedSeries(
        frame=daily[["ds", "y"]],
        rows_in=rows_in,
        rows_dropped=rows_dropped,
        gaps_filled=max(0, gaps_filled),
        start=daily["ds"].iloc[0].date(),
        end=daily["ds"].iloc[-1].date(),
    )


# ==========================================================================
# Stage 4-6: the models
# ==========================================================================
class ProphetEngine:
    """Thin, configurable wrapper around Prophet."""

    name = "prophet"

    def __init__(
        self,
        changepoint_prior_scale: Optional[float] = None,
        seasonality_mode: Optional[str] = None,
        interval_width: Optional[float] = None,
    ):
        self.changepoint_prior_scale = (
            changepoint_prior_scale
            if changepoint_prior_scale is not None
            else settings.prophet_changepoint_prior_scale
        )
        self.seasonality_mode = seasonality_mode or settings.prophet_seasonality_mode
        self.interval_width = (
            interval_width if interval_width is not None else settings.prophet_interval_width
        )

    def _build(self, days: int):
        from prophet import Prophet

        # cmdstanpy re-arms its own DEBUG logger on import; silence it per fit.
        quiet_ml_loggers()

        # Daily-granularity data carries no sub-daily signal, and under ~2
        # years there is no reliable yearly cycle to fit.
        return Prophet(
            daily_seasonality=False,
            weekly_seasonality=days >= 14,
            yearly_seasonality=days >= 730,
            seasonality_mode=self.seasonality_mode,
            changepoint_prior_scale=self.changepoint_prior_scale,
            interval_width=self.interval_width,
        )

    def fit_predict(self, frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
        """Fit and return the horizon rows with yhat and its bounds."""
        model = self._build(len(frame))
        model.fit(frame[["ds", "y"]])
        future = model.make_future_dataframe(periods=horizon, freq="D")
        forecast = model.predict(future)
        tail = forecast.tail(horizon)[["ds", "yhat", "yhat_lower", "yhat_upper"]].copy()
        # Spend cannot be negative; Prophet's linear trend can dip below zero.
        for column in ("yhat", "yhat_lower", "yhat_upper"):
            tail[column] = tail[column].clip(lower=0.0)
        return tail


class TrendSeasonalEngine:
    """Deterministic fallback: OLS trend x day-of-week factors.

    Used only when Prophet is unavailable or its fit raises. Produces the same
    output contract so the rest of the pipeline is agnostic.
    """

    name = "trend_seasonal_fallback"

    def __init__(self, interval_width: Optional[float] = None):
        self.interval_width = (
            interval_width if interval_width is not None else settings.prophet_interval_width
        )

    def fit_predict(self, frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
        values = frame["y"].to_numpy(dtype=float)
        n = len(values)
        x = np.arange(n, dtype=float)

        slope, intercept = np.polyfit(x, values, 1)
        trend = intercept + slope * x
        safe_trend = np.where(np.abs(trend) < 1e-9, 1e-9, trend)

        # Multiplicative day-of-week factors.
        weekdays = frame["ds"].dt.dayofweek.to_numpy()
        ratios = values / safe_trend
        factors = np.ones(7, dtype=float)
        for weekday in range(7):
            mask = weekdays == weekday
            if mask.any():
                factors[weekday] = float(np.clip(np.median(ratios[mask]), 0.4, 1.8))

        fitted = trend * factors[weekdays]
        residual_std = float(np.std(values - fitted)) or float(np.std(values) * 0.1)
        z = _normal_quantile((1.0 + self.interval_width) / 2.0)

        last_ds = frame["ds"].iloc[-1]
        rows = []
        for step in range(1, horizon + 1):
            ds = last_ds + pd.Timedelta(days=step)
            point = (intercept + slope * (n - 1 + step)) * factors[ds.dayofweek]
            point = max(0.0, float(point))
            # Widen the band with the square root of horizon, as uncertainty grows.
            spread = z * residual_std * math.sqrt(1.0 + step / 10.0)
            rows.append(
                {
                    "ds": ds,
                    "yhat": point,
                    "yhat_lower": max(0.0, point - spread),
                    "yhat_upper": point + spread,
                }
            )
        return pd.DataFrame(rows)


def _normal_quantile(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    p_low, p_high = 0.02425, 1 - 0.02425

    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > p_high:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


# ==========================================================================
# Orchestration
# ==========================================================================
class CostForecaster:
    """End-to-end forecasting service used by the Celery tasks."""

    def __init__(self, horizon_days: Optional[int] = None):
        self.horizon_days = horizon_days or settings.forecast_horizon_days

    def _engines(self) -> List[Any]:
        return [ProphetEngine(), TrendSeasonalEngine()]

    def _run_engine(self, frame: pd.DataFrame, horizon: int) -> ForecastResult:
        """Try Prophet, then the fallback, reporting which one answered."""
        errors: List[str] = []
        for engine in self._engines():
            try:
                tail = engine.fit_predict(frame, horizon)
            except Exception as exc:
                errors.append(f"{engine.name}: {type(exc).__name__}: {exc}")
                logger.warning("Forecast engine %s failed: %s", engine.name, exc)
                continue

            points = [
                {
                    "date": row.ds.date(),
                    "predicted_cost": round(float(row.yhat), 4),
                    "lower_bound": round(float(row.yhat_lower), 4),
                    "upper_bound": round(float(row.yhat_upper), 4),
                }
                for row in tail.itertuples()
            ]
            detail = None
            if engine.name != "prophet":
                detail = (
                    "Prophet was unavailable, so a deterministic trend + weekly "
                    "seasonality estimator produced this forecast. "
                    + " | ".join(errors)
                )
            return ForecastResult(
                points=points,
                model_name=engine.name,
                model_detail=detail,
                interval_width=engine.interval_width,
            )

        raise ForecastError("All forecasting engines failed: " + " | ".join(errors))

    # ------------------------------------------------------------- backtest
    def backtest(self, series: PreparedSeries, backtest_days: Optional[int] = None) -> Dict[str, Any]:
        """Hold out the most recent days and score the forecast (MAE/MAPE/RMSE)."""
        backtest_days = backtest_days or settings.forecast_backtest_days
        frame = series.frame
        usable = len(frame) - backtest_days

        if backtest_days < 3 or usable < MIN_DAYS_FOR_FORECAST:
            return {
                "backtest_days": 0,
                "note": (
                    "Not enough history for a hold-out backtest "
                    f"(need {MIN_DAYS_FOR_FORECAST + backtest_days} days, have {len(frame)})."
                ),
            }

        train = frame.iloc[:usable].reset_index(drop=True)
        holdout = frame.iloc[usable:].reset_index(drop=True)

        try:
            result = self._run_engine(train, len(holdout))
        except ForecastError as exc:
            return {"backtest_days": 0, "note": f"Backtest failed: {exc}"}

        predicted = np.array([p["predicted_cost"] for p in result.points], dtype=float)
        actual = holdout["y"].to_numpy(dtype=float)
        length = min(len(predicted), len(actual))
        predicted, actual = predicted[:length], actual[:length]

        errors = np.abs(predicted - actual)
        mae = float(np.mean(errors))
        rmse = float(np.sqrt(np.mean((predicted - actual) ** 2)))
        nonzero = actual != 0
        mape = (
            float(np.mean(errors[nonzero] / actual[nonzero]) * 100.0)
            if nonzero.any()
            else None
        )

        # Seasonal-naive baseline: "same weekday last week".
        baseline_mae = None
        skill = None
        if usable >= 7:
            baseline = np.array(
                [frame["y"].iloc[usable - 7 + i] for i in range(length)], dtype=float
            )
            baseline_mae = float(np.mean(np.abs(baseline - actual)))
            if baseline_mae > 0:
                skill = round((baseline_mae - mae) / baseline_mae * 100.0, 2)

        return {
            "mae": round(mae, 4),
            "mape": round(mape, 4) if mape is not None else None,
            "rmse": round(rmse, 4),
            "backtest_days": length,
            "baseline_mae": round(baseline_mae, 4) if baseline_mae is not None else None,
            "skill_vs_baseline_pct": skill,
        }

    # -------------------------------------------------------------- forecast
    def forecast(
        self,
        records: List[Dict[str, Any]],
        horizon_days: Optional[int] = None,
        monthly_budget: Optional[float] = None,
        run_backtest: bool = True,
    ) -> Dict[str, Any]:
        """Full pipeline: clean, fit, project month end, assess risk."""
        horizon = horizon_days or self.horizon_days
        budget = monthly_budget if monthly_budget is not None else settings.monthly_budget

        series = prepare_series(records)
        if series.days < MIN_DAYS_FOR_FORECAST:
            raise InsufficientDataError(
                f"Need at least {MIN_DAYS_FOR_FORECAST} days of history to forecast; "
                f"only {series.days} usable day(s) available."
            )

        result = self._run_engine(series.frame, horizon)
        points = result.points

        last_actual_date = series.end
        projection = project_month_end(series.frame, points, last_actual_date)
        trend_pct, direction = _trend_vs_recent(series.frame, points)

        accuracy = (
            self.backtest(series)
            if run_backtest
            else {"backtest_days": 0, "note": "Backtest skipped."}
        )

        service_breakdown = analyse_services(
            records, projection["projected_month_end"], last_actual_date
        )
        risk_services = [s for s in service_breakdown if s["is_risk"]]
        risk_level = _risk_level(trend_pct, projection, budget, len(risk_services))
        budget_analysis = _budget_analysis(projection, budget, last_actual_date)

        warning = _spending_warning(
            trend_pct, direction, projection, budget_analysis, risk_services
        )

        next_7 = round(sum(p["predicted_cost"] for p in points[:7]), 2)
        next_30 = round(sum(p["predicted_cost"] for p in points[:30]), 2)

        history_tail = [
            {"date": row.ds.date(), "cost": round(float(row.y), 4)}
            for row in series.frame.tail(60).itertuples()
        ]

        notes = series.notes()
        if result.model_detail:
            notes.append(result.model_detail)

        return {
            "model_name": result.model_name,
            "model_detail": " | ".join(notes) if notes else None,
            "horizon_days": horizon,
            "interval_width": result.interval_width,
            "generated_at": datetime.now(timezone.utc),
            "currency": "USD",
            "forecast": points,
            "history_tail": history_tail,
            "month_to_date": projection["month_to_date"],
            "projected_month_end": projection["projected_month_end"],
            "projected_month_end_lower": projection["projected_month_end_lower"],
            "projected_month_end_upper": projection["projected_month_end_upper"],
            "next_7_days_total": next_7,
            "next_30_days_total": next_30,
            "trend_pct": trend_pct,
            "trend_direction": direction,
            "risk_level": risk_level,
            "risk_services": risk_services,
            "service_breakdown": service_breakdown,
            "spending_warning": warning,
            "abnormal_spending_detected": bool(
                abs(trend_pct) >= settings.cost_spike_threshold_pct or risk_services
            ),
            "budget": budget_analysis,
            "accuracy": accuracy,
        }


# ==========================================================================
# Stage 7-8: projection and risk
# ==========================================================================
def project_month_end(
    frame: pd.DataFrame, points: List[Dict[str, Any]], last_actual: date
) -> Dict[str, float]:
    """Actual month-to-date plus the forecast for the rest of this month."""
    month_start = last_actual.replace(day=1)
    days_in_month = calendar.monthrange(last_actual.year, last_actual.month)[1]
    month_end = last_actual.replace(day=days_in_month)

    mask = (frame["ds"].dt.date >= month_start) & (frame["ds"].dt.date <= last_actual)
    month_to_date = float(frame.loc[mask, "y"].sum())

    remaining = [p for p in points if last_actual < p["date"] <= month_end]
    projected = month_to_date + sum(p["predicted_cost"] for p in remaining)
    lower = month_to_date + sum(p["lower_bound"] for p in remaining)
    upper = month_to_date + sum(p["upper_bound"] for p in remaining)

    return {
        "month_to_date": round(month_to_date, 2),
        "projected_month_end": round(projected, 2),
        "projected_month_end_lower": round(lower, 2),
        "projected_month_end_upper": round(upper, 2),
        "days_remaining": max(0, (month_end - last_actual).days),
        "forecast_days_used": len(remaining),
    }


def _trend_vs_recent(frame: pd.DataFrame, points: List[Dict[str, Any]]) -> Tuple[float, str]:
    """Compare mean forecast spend against the trailing two weeks of actuals."""
    window = min(14, len(frame))
    recent_mean = float(frame["y"].tail(window).mean())
    horizon_window = points[: min(14, len(points))]
    if not horizon_window or recent_mean <= 0:
        return 0.0, "stable"

    forecast_mean = float(np.mean([p["predicted_cost"] for p in horizon_window]))
    trend_pct = round((forecast_mean - recent_mean) / recent_mean * 100.0, 2)

    if trend_pct >= 3.0:
        return trend_pct, "increasing"
    if trend_pct <= -3.0:
        return trend_pct, "decreasing"
    return trend_pct, "stable"


def analyse_services(
    records: List[Dict[str, Any]],
    projected_month_end: Optional[float],
    last_actual: date,
) -> List[Dict[str, Any]]:
    """Per-service share, trend and projected month-end contribution.

    Rather than fitting Prophet once per service (slow, and noisy on thin
    series), the aggregate forecast remainder is allocated across services by
    their recent spend weighted by their own growth rate.

    `projected_month_end` is the *aggregate* forecast. Pass None when no forecast
    exists - each row's `projected_month_end` is then left null rather than being
    quietly filled with month-to-date, which would read as a projection while
    actually being a historical figure.
    """
    if not records:
        return []

    frame = pd.DataFrame(records)
    frame["ds"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["cost"] = pd.to_numeric(frame["cost"], errors="coerce")
    frame = frame.dropna(subset=["ds", "cost", "service"])
    if frame.empty:
        return []

    total = float(frame["cost"].sum())
    month_start = last_actual.replace(day=1)
    last_ts = pd.Timestamp(last_actual)

    recent_cut = last_ts - pd.Timedelta(days=6)
    prior_cut = last_ts - pd.Timedelta(days=13)

    rows: List[Dict[str, Any]] = []
    weights: Dict[str, float] = {}

    for service, group in frame.groupby("service"):
        service_total = float(group["cost"].sum())
        days = max(1, group["ds"].dt.normalize().nunique())

        recent = group.loc[group["ds"] >= recent_cut, "cost"]
        prior = group.loc[(group["ds"] >= prior_cut) & (group["ds"] < recent_cut), "cost"]
        recent_mean = float(recent.mean()) if len(recent) else 0.0
        prior_mean = float(prior.mean()) if len(prior) else 0.0
        trend_pct = (
            round((recent_mean - prior_mean) / prior_mean * 100.0, 2)
            if prior_mean > 0
            else 0.0
        )

        mtd = float(group.loc[group["ds"].dt.date >= month_start, "cost"].sum())

        rows.append(
            {
                "service": str(service),
                "total_cost": round(service_total, 2),
                "share_pct": round(service_total / total * 100.0, 2) if total else 0.0,
                "daily_average": round(service_total / days, 2),
                "trend_pct": trend_pct,
                "_mtd": mtd,
            }
        )
        # Growth-weighted claim on the remaining forecast.
        weights[str(service)] = max(0.0, recent_mean) * (1.0 + max(-0.5, trend_pct / 100.0))

    if projected_month_end is None:
        for row in rows:
            row.pop("_mtd", None)
            row["projected_month_end"] = None
    else:
        mtd_total = sum(r["_mtd"] for r in rows)
        remainder = max(0.0, projected_month_end - mtd_total)
        weight_total = sum(weights.values()) or 1.0
        for row in rows:
            allocated = remainder * weights[row["service"]] / weight_total
            row["projected_month_end"] = round(row.pop("_mtd") + allocated, 2)

    # A service is a risk when it is both growing fast and large enough to matter,
    # or when it dominates the bill while still trending up.
    threshold = settings.cost_spike_threshold_pct
    for row in rows:
        row["is_risk"] = bool(
            (row["trend_pct"] >= threshold and row["share_pct"] >= 4.0)
            or (row["share_pct"] >= 25.0 and row["trend_pct"] > 2.0)
        )

    # Sort by whichever figure the rows actually carry.
    rows.sort(
        key=lambda r: r["projected_month_end"] if r["projected_month_end"] is not None
        else r["total_cost"],
        reverse=True,
    )
    return rows


def _risk_level(
    trend_pct: float, projection: Dict[str, float], budget: float, risk_service_count: int
) -> str:
    projected = projection["projected_month_end"]
    over_budget = budget > 0 and projected > budget

    if over_budget and trend_pct >= 10.0:
        return "HIGH"
    if over_budget or trend_pct >= 20.0 or risk_service_count >= 3:
        return "ELEVATED"
    if trend_pct >= 8.0 or risk_service_count >= 1:
        return "MODERATE"
    return "LOW"


def _budget_analysis(
    projection: Dict[str, float], budget: float, last_actual: date
) -> Dict[str, Any]:
    projected = projection["projected_month_end"]
    days_in_month = calendar.monthrange(last_actual.year, last_actual.month)[1]
    return {
        "monthly_budget": round(budget, 2),
        "projected_month_end": projected,
        "projected_vs_budget_pct": (
            round((projected - budget) / budget * 100.0, 2) if budget > 0 else 0.0
        ),
        "budget_breach_expected": bool(budget > 0 and projected > budget),
        "budget_breach_possible": bool(
            budget > 0 and projection["projected_month_end_upper"] > budget
        ),
        "headroom": round(budget - projected, 2),
        "days_remaining_in_month": max(0, days_in_month - last_actual.day),
    }


def _spending_warning(
    trend_pct: float,
    direction: str,
    projection: Dict[str, float],
    budget: Dict[str, Any],
    risk_services: List[Dict[str, Any]],
) -> Optional[str]:
    parts: List[str] = []
    if budget["budget_breach_expected"]:
        parts.append(
            f"Projected month-end spend of ${projection['projected_month_end']:,.0f} "
            f"exceeds the ${budget['monthly_budget']:,.0f} budget by "
            f"${abs(budget['headroom']):,.0f}."
        )
    elif budget["budget_breach_possible"]:
        parts.append(
            f"The upper forecast bound (${projection['projected_month_end_upper']:,.0f}) "
            f"would breach the ${budget['monthly_budget']:,.0f} budget."
        )

    if direction == "increasing" and trend_pct >= settings.cost_spike_threshold_pct:
        parts.append(f"Daily spend is trending up {trend_pct:.1f}% versus the last two weeks.")

    if risk_services:
        names = ", ".join(s["service"] for s in risk_services[:3])
        parts.append(f"{len(risk_services)} service(s) are driving the increase: {names}.")

    return " ".join(parts) if parts else None
