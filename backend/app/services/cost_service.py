"""Cost intelligence pipeline and cached reads."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.cache import Keys, cache
from app.cloud.aws import get_provider
from app.config import settings
from app.freshness import describe as describe_freshness
from app.freshness import stamp
from app.logging_config import get_logger
from app.ml.forecasting import CostForecaster, InsufficientDataError, analyse_services
from app.schemas.cost import CostForecastResponse, CostHistoryResponse

logger = get_logger(__name__)


# --------------------------------------------------------------------------
# ingestion
# --------------------------------------------------------------------------
def ingest_cost_history(days: Optional[int] = None) -> Dict[str, Any]:
    """Pull Cost Explorer data, aggregate it, and cache the history payload.

    Runs inside a Celery task, never in a request handler.
    """
    days = days or settings.demo_history_days
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days - 1)

    provider = get_provider()
    records = provider.get_cost_and_usage(start, end)
    if not records:
        raise InsufficientDataError(
            f"Cost Explorer returned no records for {start}..{end}."
        )

    payload = stamp(build_history_payload(records, end))
    cache.set_json(Keys.COST_HISTORY, payload, settings.cache_ttl_cost)
    cache.mark_success("cost_history", payload["generated_at"])
    # Raw records are kept so the forecast task does not re-query the cloud API.
    cache.set_json(
        f"{Keys.COST_HISTORY}:raw",
        [
            {
                "date": r["date"].isoformat() if hasattr(r["date"], "isoformat") else r["date"],
                "service": r["service"],
                "usage": r["usage"],
                "cost": r["cost"],
                "unit": r.get("unit", "USD"),
            }
            for r in records
        ],
        settings.cache_ttl_cost,
    )
    logger.info(
        "Ingested %d cost records across %s..%s (total $%.2f)",
        len(records),
        start,
        end,
        payload["total_cost"],
    )
    return payload


def build_history_payload(records: List[Dict[str, Any]], as_of: date) -> Dict[str, Any]:
    """Shape raw cost records into the history response."""
    daily_totals: Dict[date, float] = {}
    for record in records:
        record_date = record["date"]
        if isinstance(record_date, str):
            record_date = date.fromisoformat(record_date)
        daily_totals[record_date] = daily_totals.get(record_date, 0.0) + float(record["cost"])

    daily = [
        {"date": day, "cost": round(total, 4)} for day, total in sorted(daily_totals.items())
    ]
    total_cost = round(sum(daily_totals.values()), 2)
    month_start = as_of.replace(day=1)
    month_to_date = round(
        sum(cost for day, cost in daily_totals.items() if day >= month_start), 2
    )

    # No forecast exists at ingestion time, so the per-service projection is left
    # null; the forecast pipeline publishes the projected figures separately.
    by_service = analyse_services(records, projected_month_end=None, last_actual=as_of)

    return {
        "currency": "USD",
        "start_date": daily[0]["date"] if daily else None,
        "end_date": daily[-1]["date"] if daily else None,
        "total_cost": total_cost,
        "month_to_date": month_to_date,
        "daily_average": round(total_cost / len(daily), 2) if daily else 0.0,
        "records_analysed": len(records),
        "daily": daily,
        "by_service": by_service,
    }


def load_raw_records() -> List[Dict[str, Any]]:
    """Raw cost records from cache, re-ingesting from the cloud if absent."""
    cached = cache.get_json(f"{Keys.COST_HISTORY}:raw")
    if cached:
        return [
            {
                "date": date.fromisoformat(r["date"]) if isinstance(r["date"], str) else r["date"],
                "service": r["service"],
                "usage": r.get("usage", 0.0),
                "cost": r["cost"],
                "unit": r.get("unit", "USD"),
            }
            for r in cached
        ]

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=settings.demo_history_days - 1)
    return get_provider().get_cost_and_usage(start, end)


# --------------------------------------------------------------------------
# forecasting
# --------------------------------------------------------------------------
def run_forecast(
    horizon_days: Optional[int] = None,
    monthly_budget: Optional[float] = None,
) -> Dict[str, Any]:
    """Train Prophet and cache the forecast. Celery-only; never in a request."""
    records = load_raw_records()
    forecaster = CostForecaster(horizon_days=horizon_days)
    payload = forecaster.forecast(
        records, horizon_days=horizon_days, monthly_budget=monthly_budget
    )
    cache.set_json(Keys.COST_FORECAST, payload, settings.cache_ttl_forecast)
    cache.mark_success("cost_forecast", payload.get("generated_at"))
    logger.info(
        "Forecast complete via %s: month-end $%.2f (MAE %s)",
        payload["model_name"],
        payload["projected_month_end"],
        payload["accuracy"].get("mae"),
    )
    return payload


# --------------------------------------------------------------------------
# cached reads (request path)
# --------------------------------------------------------------------------
def get_cached_history() -> Optional[Dict[str, Any]]:
    payload = cache.get_json(Keys.COST_HISTORY)
    if payload is None:
        return None
    payload["freshness"] = history_freshness(payload)
    return payload


def get_cached_forecast() -> Optional[Dict[str, Any]]:
    payload = cache.get_json(Keys.COST_FORECAST)
    if payload is None:
        return None
    payload["freshness"] = forecast_freshness(payload)
    return payload


def history_freshness(payload):
    """Freshness of the cost-history payload, from its generated_at stamp."""
    return describe_freshness(
        payload,
        settings.freshness_cost,
        last_success=cache.last_success("cost_history"),
    )


def forecast_freshness(payload):
    """Freshness of the forecast payload, from its generated_at stamp."""
    return describe_freshness(
        payload,
        settings.freshness_forecast,
        last_success=cache.last_success("cost_forecast"),
    )


def history_response() -> Optional[CostHistoryResponse]:
    payload = get_cached_history()
    return CostHistoryResponse.model_validate(payload) if payload else None


def forecast_response() -> Optional[CostForecastResponse]:
    payload = get_cached_forecast()
    return CostForecastResponse.model_validate(payload) if payload else None


