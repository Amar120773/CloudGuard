"""Dashboard aggregation.

Assembles one payload from every pipeline's cached output. Nothing here computes
ML - if a pipeline has not produced results, its section comes back null with a
`PipelineState` explaining why, which is what drives the UI's fallback states.

Two deliberate shaping rules keep this endpoint small, because the dashboard is
polled continuously by every open tab:

* The embedded security posture carries **no event feed**. The Overview renders a
  short feed from `recent_events`; the full history belongs to
  `/api/security/events`, which paginates it.
* Nothing is written back to the cache here. This is a pure read.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.cache import cache
from app.config import settings
from app.freshness import EXPIRED, STALE, UNAVAILABLE, utcnow
from app.logging_config import get_logger
from app.schemas.common import RiskLevel
from app.schemas.dashboard import DashboardResponse
from app.services import (
    cloud_service,
    cost_service,
    insight_service,
    security_service,
    task_service,
)

logger = get_logger(__name__)

# The Overview feed shows a handful of the newest events. Keeping this small
# matters: each event carries its full metric vector and deviations, so the
# difference between 12 and 60 embedded events is tens of kilobytes per poll.
OVERVIEW_EVENT_LIMIT = 10


def build_dashboard() -> Dict[str, Any]:
    """Compose the dashboard payload from whatever each pipeline has cached."""
    history = cost_service.get_cached_history()
    forecast = cost_service.get_cached_forecast()
    posture = security_service.get_cached_posture()
    resources = cloud_service.get_cached_resources()
    metrics = cloud_service.get_cached_metrics()

    cloud_health = cloud_service.health_response()
    insights = insight_service.build_insights(forecast, posture, resources, history)

    overview = _overview(history, forecast, posture, resources, cloud_health)
    pipelines = _pipeline_states(history, forecast, posture, resources, metrics)

    recent_events = (posture or {}).get("recent_events", [])[:OVERVIEW_EVENT_LIMIT]

    return {
        "generated_at": utcnow(),
        "overview": overview,
        "cost": history,
        "forecast": forecast,
        # The posture's own event feed is stripped: it duplicated `recent_events`
        # and carried 60 full event objects the Overview never rendered.
        "security": _slim_posture(posture),
        "cloud_health": cloud_health.model_dump() if cloud_health else None,
        "recent_events": recent_events,
        "insights": insights,
        "pipelines": pipelines,
        "pending_task_ids": task_service.pending_task_ids(),
        # Infrastructure degradation only. A pipeline that simply has not run yet
        # is reported through `pipelines`, so the UI can tell "Redis is down" from
        # "this is a cold start" and offer the right action for each.
        "degraded": cache.degraded,
        "features": {"anomaly_injection": settings.anomaly_injection_enabled},
        "freshness": {
            "cached": False,
            "generated_at": utcnow(),
            "age_seconds": 0,
            "source": "pipeline",
            "stale": any(p["stale"] for p in pipelines),
            "state": _aggregate_state(pipelines),
        },
    }


def dashboard_response() -> DashboardResponse:
    return DashboardResponse.model_validate(build_dashboard())


def _slim_posture(posture: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Posture summary without the embedded feed.

    `top_anomalies` stays: it is small, and the Overview's insight panel is built
    from it. `/api/security` continues to return the full payload.
    """
    if posture is None:
        return None
    return {key: value for key, value in posture.items() if key != "recent_events"}


def _aggregate_state(pipelines: List[Dict[str, Any]]) -> str:
    """Worst state across the pipelines, for a single top-level signal."""
    states = {p["state"] for p in pipelines}
    for state in (UNAVAILABLE, EXPIRED, STALE):
        if state in states:
            return state
    return "fresh"


def _overview(
    history: Optional[Dict[str, Any]],
    forecast: Optional[Dict[str, Any]],
    posture: Optional[Dict[str, Any]],
    resources: Optional[Dict[str, Any]],
    cloud_health: Optional[Any],
) -> Dict[str, Any]:
    """The headline tiles. Every value traces back to a pipeline result."""
    budget = (forecast or {}).get("budget") or {}
    severity_breakdown = (posture or {}).get("severity_breakdown") or {}

    # Month-to-date prefers the forecast's figure (same series the model saw),
    # falling back to the raw history aggregation.
    mtd = (forecast or {}).get("month_to_date")
    if mtd is None:
        mtd = (history or {}).get("month_to_date", 0.0)

    return {
        "current_spend_mtd": round(float(mtd or 0.0), 2),
        "forecast_month_end": round(float((forecast or {}).get("projected_month_end") or 0.0), 2),
        "forecast_lower": round(
            float((forecast or {}).get("projected_month_end_lower") or 0.0), 2
        ),
        "forecast_upper": round(
            float((forecast or {}).get("projected_month_end_upper") or 0.0), 2
        ),
        "cost_trend_pct": float((forecast or {}).get("trend_pct") or 0.0),
        "cost_risk_level": (forecast or {}).get("risk_level") or RiskLevel.LOW.value,
        "cost_risk_services": len((forecast or {}).get("risk_services") or []),
        "budget_breach_expected": bool(budget.get("budget_breach_expected", False)),
        "anomaly_count": int((posture or {}).get("anomaly_count") or 0),
        "total_security_events": int((posture or {}).get("total_events") or 0),
        "security_health_score": float((posture or {}).get("security_health_score") or 100.0),
        "critical_anomalies": int(severity_breakdown.get("CRITICAL", 0)),
        "active_resources": int((resources or {}).get("running") or 0),
        "idle_resources": int((resources or {}).get("idle_resources") or 0),
        "potential_monthly_savings": round(
            float((resources or {}).get("potential_monthly_savings") or 0.0), 2
        ),
        "cloud_health_status": cloud_health.status if cloud_health else "Unknown",
        "cloud_health_score": cloud_health.health_score if cloud_health else 0.0,
    }


def _pipeline_states(
    history: Optional[Dict[str, Any]],
    forecast: Optional[Dict[str, Any]],
    posture: Optional[Dict[str, Any]],
    resources: Optional[Dict[str, Any]],
    metrics: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Per-pipeline readiness, so the UI can be specific about what is missing.

    Freshness comes from each payload's `generated_at`, never from the remaining
    Redis TTL - deriving age from TTL made the stale state unreachable.
    """
    specs = [
        ("cost_history", history, cost_service.history_freshness,
         "Cost Explorer ingestion has not run yet."),
        ("cost_forecast", forecast, cost_service.forecast_freshness,
         "Prophet forecast has not run yet."),
        ("security_anomalies", posture, security_service.posture_freshness,
         "IsolationForest scoring has not run yet."),
        ("cloud_resources", resources, cloud_service.resources_freshness,
         "EC2 inventory has not been collected yet."),
        ("cloud_metrics", metrics, cloud_service.metrics_freshness,
         "CloudWatch metrics have not been collected yet."),
    ]

    states: List[Dict[str, Any]] = []
    for name, payload, freshness_fn, missing_message in specs:
        freshness = freshness_fn(payload)
        ready = payload is not None
        state = freshness["state"]

        if ready and state == STALE:
            message = (
                f"Last updated {freshness['age_seconds']}s ago, beyond the "
                "freshness threshold."
            )
        elif state == EXPIRED:
            message = (
                f"Cache expired. Last successful run {freshness['age_seconds']}s ago."
            )
        elif not ready:
            message = missing_message
        else:
            message = None

        states.append(
            {
                "name": name,
                "ready": ready,
                "stale": freshness["stale"],
                "state": state,
                "age_seconds": freshness["age_seconds"],
                "last_success_at": freshness["generated_at"],
                "message": message,
            }
        )
    return states
