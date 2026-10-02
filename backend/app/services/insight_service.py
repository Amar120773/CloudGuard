"""AI insight generation.

Turns model output into operator-facing language. Every insight carries the
three parts the spec mandates (section 25):

    metric  +  model signal (forecast range / anomaly score)  +  action

Insights are derived strictly from pipeline output - there is no hardcoded copy
describing numbers the models did not produce. If a pipeline has not run, no
insight is emitted for it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


def _money(value: Optional[float]) -> str:
    return f"${value:,.0f}" if value is not None else "n/a"


def _short_service(name: str) -> str:
    """Trim AWS's verbose service names for display."""
    for prefix in ("Amazon Elastic Compute Cloud - ", "Amazon ", "AWS "):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def build_insights(
    forecast: Optional[Dict[str, Any]],
    posture: Optional[Dict[str, Any]],
    resources: Optional[Dict[str, Any]],
    history: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Compose the ranked insight list shown on the AI Insights page."""
    now = datetime.now(timezone.utc)
    insights: List[Dict[str, Any]] = []

    if forecast:
        insights.extend(_cost_insights(forecast, now))
    if posture:
        insights.extend(_security_insights(posture, now))
    if resources:
        insights.extend(_resource_insights(resources, now))

    if not insights:
        insights.append(
            {
                "insight_id": "system-cold-start",
                "category": "SYSTEM",
                "title": "Analysis pipelines have not run yet",
                "metric": "0 pipelines complete",
                "model_signal": None,
                "narrative": (
                    "No forecast or anomaly results are cached yet. Trigger the "
                    "pipelines to populate cost and security intelligence."
                ),
                "action": "Run POST /api/dashboard/refresh, or use Refresh data in the UI.",
                "severity": "INFO",
                "confidence": None,
                "source_model": None,
                "generated_at": now,
                "related_resource_ids": [],
            }
        )

    severity_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    insights.sort(key=lambda i: severity_rank.get(i["severity"], 5))
    return insights


# --------------------------------------------------------------------------
# cost
# --------------------------------------------------------------------------
def _cost_insights(forecast: Dict[str, Any], now: datetime) -> List[Dict[str, Any]]:
    insights: List[Dict[str, Any]] = []

    projected = forecast.get("projected_month_end")
    lower = forecast.get("projected_month_end_lower")
    upper = forecast.get("projected_month_end_upper")
    mtd = forecast.get("month_to_date")
    trend = forecast.get("trend_pct", 0.0) or 0.0
    direction = forecast.get("trend_direction", "stable")
    model = forecast.get("model_name", "prophet")
    budget = forecast.get("budget") or {}
    accuracy = forecast.get("accuracy") or {}

    interval = f"{settings.prophet_interval_width * 100:.0f}% interval {_money(lower)} - {_money(upper)}"
    mae = accuracy.get("mae")
    signal = interval + (f"; backtest MAE {_money(mae)}/day" if mae else "")

    # 1. The headline projection.
    if direction == "increasing":
        narrative = (
            f"Projected cloud spending is increasing. Month-to-date spend is "
            f"{_money(mtd)} and the model expects {_money(projected)} by month end, "
            f"about {abs(trend):.1f}% above the average of the last two weeks."
        )
        severity = "HIGH" if trend >= settings.cost_spike_threshold_pct else "MEDIUM"
    elif direction == "decreasing":
        narrative = (
            f"Projected cloud spending is easing. Month-to-date spend is "
            f"{_money(mtd)}, trending {abs(trend):.1f}% below the last two weeks, "
            f"with {_money(projected)} expected by month end."
        )
        severity = "INFO"
    else:
        narrative = (
            f"Cloud spending is stable. Month-to-date spend is {_money(mtd)} and "
            f"{_money(projected)} is expected by month end."
        )
        severity = "INFO"

    insights.append(
        {
            "insight_id": "cost-projection",
            "category": "COST",
            "title": f"Expected end-of-month expenditure: {_money(projected)}",
            "metric": _money(projected),
            "model_signal": signal,
            "narrative": narrative,
            "action": (
                "Review the services contributing most to the projected increase."
                if direction == "increasing"
                else "No action needed; keep monitoring the daily trend."
            ),
            "severity": severity,
            "confidence": settings.prophet_interval_width,
            "source_model": model,
            "generated_at": now,
            "related_resource_ids": [],
        }
    )

    # 2. Budget pressure.
    if budget.get("budget_breach_expected"):
        insights.append(
            {
                "insight_id": "cost-budget-breach",
                "category": "COST",
                "title": "Projected spend exceeds the monthly budget",
                "metric": f"{_money(projected)} vs {_money(budget.get('monthly_budget'))} budget",
                "model_signal": (
                    f"Overrun {_money(abs(budget.get('headroom', 0.0)))} "
                    f"({budget.get('projected_vs_budget_pct', 0):+.1f}%), "
                    f"{budget.get('days_remaining_in_month', 0)} days remaining"
                ),
                "narrative": (
                    f"At the current trajectory the month will close about "
                    f"{_money(abs(budget.get('headroom', 0.0)))} over the "
                    f"{_money(budget.get('monthly_budget'))} budget."
                ),
                "action": (
                    "Cut or defer discretionary workloads now, or raise the budget "
                    "deliberately rather than by surprise."
                ),
                "severity": "HIGH",
                "confidence": settings.prophet_interval_width,
                "source_model": model,
                "generated_at": now,
                "related_resource_ids": [],
            }
        )
    elif budget.get("budget_breach_possible"):
        insights.append(
            {
                "insight_id": "cost-budget-risk",
                "category": "COST",
                "title": "Budget breach is within the forecast range",
                "metric": f"upper bound {_money(upper)}",
                "model_signal": f"Budget {_money(budget.get('monthly_budget'))}",
                "narrative": (
                    "The central forecast stays inside budget, but the upper "
                    f"confidence bound of {_money(upper)} would breach it."
                ),
                "action": "Watch daily spend; a single sustained spike would tip it over.",
                "severity": "MEDIUM",
                "confidence": settings.prophet_interval_width,
                "source_model": model,
                "generated_at": now,
                "related_resource_ids": [],
            }
        )

    # 3. Disproportionate contributors.
    risk_services = forecast.get("risk_services") or []
    if risk_services:
        names = ", ".join(_short_service(s["service"]) for s in risk_services[:3])
        top = risk_services[0]
        count_word = "service is" if len(risk_services) == 1 else "services are"
        insights.append(
            {
                "insight_id": "cost-risk-services",
                "category": "COST",
                "title": f"{len(risk_services)} {count_word} driving projected spend",
                "metric": names,
                "model_signal": (
                    f"{_short_service(top['service'])}: {top['trend_pct']:+.1f}% week over week, "
                    f"{top['share_pct']:.1f}% of total spend, "
                    f"{_money(top['projected_month_end'])} projected"
                ),
                "narrative": (
                    f"{len(risk_services)} {count_word} contributing disproportionately to "
                    f"projected spending. {_short_service(top['service'])} alone is "
                    f"projected at {_money(top['projected_month_end'])} this month."
                ),
                "action": (
                    f"Audit {_short_service(top['service'])} for right-sizing, "
                    "reserved capacity or unused provisioned throughput."
                ),
                "severity": "MEDIUM",
                "confidence": None,
                "source_model": model,
                "generated_at": now,
                "related_resource_ids": [],
            }
        )

    # 4. Honest disclosure when the fallback engine produced the numbers.
    if model != "prophet":
        insights.append(
            {
                "insight_id": "cost-model-fallback",
                "category": "SYSTEM",
                "title": "Forecast produced by the fallback model",
                "metric": model,
                "model_signal": forecast.get("model_detail"),
                "narrative": (
                    "Prophet could not be fitted in this environment, so a "
                    "deterministic trend and weekly-seasonality estimator produced "
                    "this forecast. Treat the confidence interval as approximate."
                ),
                "action": "Check the worker logs for the Prophet/Stan initialisation error.",
                "severity": "LOW",
                "confidence": None,
                "source_model": model,
                "generated_at": now,
                "related_resource_ids": [],
            }
        )

    return insights


# --------------------------------------------------------------------------
# security
# --------------------------------------------------------------------------
def _security_insights(posture: Dict[str, Any], now: datetime) -> List[Dict[str, Any]]:
    insights: List[Dict[str, Any]] = []
    anomalies = posture.get("top_anomalies") or []
    total = posture.get("total_events", 0)
    count = posture.get("anomaly_count", 0)
    accuracy = posture.get("accuracy") or {}

    if count == 0:
        insights.append(
            {
                "insight_id": "security-clean",
                "category": "SECURITY",
                "title": "No statistically unusual activity detected",
                "metric": f"0 anomalies across {total} windows",
                "model_signal": (
                    f"IsolationForest, contamination {posture.get('contamination')}"
                ),
                "narrative": (
                    f"All {total} behavioural windows scored inside the learned "
                    "normal operating envelope."
                ),
                "action": "No action required.",
                "severity": "INFO",
                "confidence": None,
                "source_model": "IsolationForest",
                "generated_at": now,
                "related_resource_ids": [],
            }
        )
        return insights

    # Lead with the single most unusual event.
    top = anomalies[0]
    deviation = (top.get("deviations") or [{}])[0]
    feature = str(deviation.get("feature", "behaviour")).replace("_", " ")

    insights.append(
        {
            "insight_id": f"security-top-{top.get('event_id')}",
            "category": "SECURITY",
            "title": f"{top.get('event_type')} flagged as anomalous",
            "metric": f"{feature} = {deviation.get('value', 0):,.2f}",
            "model_signal": (
                f"Anomaly score {top.get('anomaly_score')}/100, "
                f"{abs(float(deviation.get('z_score', 0))):.1f}σ "
                f"{deviation.get('direction', 'above')} baseline"
            ),
            "narrative": (
                f"A statistically unusual event was detected on "
                f"{top.get('source')}. {top.get('context')}"
            ),
            "action": top.get("recommended_action")
            or "Review the associated traffic and access activity.",
            "severity": top.get("severity", "HIGH"),
            "confidence": None,
            "source_model": "IsolationForest",
            "generated_at": now,
            "related_resource_ids": [str(top.get("source"))],
        }
    )

    # Then the aggregate picture.
    breakdown = posture.get("severity_breakdown") or {}
    breakdown_text = ", ".join(f"{v} {k.lower()}" for k, v in sorted(breakdown.items())) or "none"
    model_signal = (
        f"Security health {posture.get('security_health_score')}/100; "
        f"anomaly rate {posture.get('anomaly_rate_pct')}%"
    )
    if accuracy.get("precision") is not None:
        model_signal += (
            f"; evaluation precision {accuracy['precision']:.2f} / "
            f"recall {accuracy['recall']:.2f}"
        )

    insights.append(
        {
            "insight_id": "security-summary",
            "category": "SECURITY",
            "title": f"{count} anomalous window(s) across {total} observations",
            "metric": f"{count} / {total} flagged",
            "model_signal": model_signal,
            "narrative": (
                f"IsolationForest flagged {count} of {total} behavioural windows as "
                f"statistically rare ({breakdown_text}). These were surfaced without "
                "any rule written for the specific event pattern."
            ),
            "action": (
                "Work the queue by anomaly score, starting with the critical events."
            ),
            "severity": "HIGH" if breakdown.get("CRITICAL") else "MEDIUM",
            "confidence": None,
            "source_model": "IsolationForest",
            "generated_at": now,
            "related_resource_ids": [],
        }
    )
    return insights


# --------------------------------------------------------------------------
# resources
# --------------------------------------------------------------------------
def _resource_insights(resources: Dict[str, Any], now: datetime) -> List[Dict[str, Any]]:
    idle_count = resources.get("idle_resources", 0)
    savings = resources.get("potential_monthly_savings", 0.0)
    if idle_count == 0 and savings <= 0:
        return []

    idle_list = [r for r in resources.get("resources", []) if r.get("idle")]
    names = ", ".join(r.get("name") or r["resource_id"] for r in idle_list[:3])

    return [
        {
            "insight_id": "resource-idle-waste",
            "category": "RESOURCE",
            "title": f"{idle_count} idle resource(s) still accruing cost",
            "metric": f"{_money(savings)}/mo recoverable",
            "model_signal": (
                f"CPU utilisation below {settings_idle_threshold()}% over the last 24h"
            ),
            "narrative": (
                f"{idle_count} running instance(s) are effectively idle while still "
                f"billing: {names}. Reclaiming them recovers about "
                f"{_money(savings)} per month."
            ),
            "action": "Terminate, downsize, or schedule these instances off-hours.",
            "severity": "MEDIUM" if savings > 100 else "LOW",
            "confidence": None,
            "source_model": "heuristic",
            "generated_at": now,
            "related_resource_ids": [r["resource_id"] for r in idle_list[:10]],
        }
    ]


def settings_idle_threshold() -> float:
    from app.services.cloud_service import IDLE_CPU_THRESHOLD

    return IDLE_CPU_THRESHOLD
