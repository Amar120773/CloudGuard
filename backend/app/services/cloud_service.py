"""Cloud inventory and metric pipeline."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.cache import Keys, cache
from app.cloud.aws import get_provider
from app.config import settings
from app.freshness import describe as describe_freshness
from app.freshness import stamp
from app.logging_config import get_logger
from app.schemas.cloud import (
    CloudHealthResponse,
    CloudMetricsResponse,
    CloudResourcesResponse,
)

logger = get_logger(__name__)

# Below this CPU average a running instance is treated as idle waste.
IDLE_CPU_THRESHOLD = 6.0


def ingest_cloud_inventory() -> Dict[str, Any]:
    """Describe EC2, join CloudWatch utilisation, and cache the inventory."""
    provider = get_provider()
    resources = provider.list_resources()

    running = [r for r in resources if r["status"] == "running"]
    stopped = [r for r in resources if r["status"] == "stopped"]
    idle = [r for r in resources if r.get("idle")]

    estimated = round(sum(r["estimated_cost"] for r in resources), 2)
    # Idle instances could be reclaimed outright; stopped ones only shed storage.
    savings = round(
        sum(r["estimated_cost"] for r in idle)
        + sum(r["estimated_cost"] for r in stopped) * 0.5,
        2,
    )

    payload = {
        "region": settings.aws_region,
        "total_resources": len(resources),
        "running": len(running),
        "stopped": len(stopped),
        "idle_resources": len(idle),
        "estimated_monthly_cost": estimated,
        "potential_monthly_savings": savings,
        "resources": resources,
    }
    stamp(payload)
    cache.set_json(Keys.CLOUD_RESOURCES, payload, settings.cache_ttl_cloud)
    cache.mark_success("cloud_resources", payload["generated_at"])
    logger.info(
        "Ingested %d cloud resources (%d running, %d idle, $%.2f/mo)",
        len(resources),
        len(running),
        len(idle),
        estimated,
    )
    return payload


def ingest_cloud_metrics(hours: int = 24, period_seconds: int = 3600) -> Dict[str, Any]:
    """Collect CloudWatch series and cache the aggregate view."""
    series = get_provider().get_metrics(hours=hours, period_seconds=period_seconds)

    cpu_series = [s for s in series if s["metric_name"] == "CPUUtilization"]
    out_series = [s for s in series if s["metric_name"] == "NetworkOut"]

    averages = [s["average"] for s in cpu_series if s.get("average") is not None]
    peaks = [s["maximum"] for s in cpu_series if s.get("maximum") is not None]
    total_out_bytes = sum(
        point["value"] for s in out_series for point in s["datapoints"]
    )

    payload = {
        "region": settings.aws_region,
        "collected_at": datetime.now(timezone.utc),
        "period_seconds": period_seconds,
        "series": series,
        "average_cpu": round(sum(averages) / len(averages), 2) if averages else None,
        "peak_cpu": round(max(peaks), 2) if peaks else None,
        "total_network_out_mb": round(total_out_bytes / (1024 * 1024), 2),
    }
    # collected_at is exactly this payload's generation time.
    payload["generated_at"] = payload["collected_at"].isoformat()
    cache.set_json(Keys.CLOUD_METRICS, payload, settings.cache_ttl_cloud)
    cache.mark_success("cloud_metrics", payload["generated_at"])
    logger.info("Collected %d CloudWatch metric series", len(series))
    return payload


# --------------------------------------------------------------------------
# cached reads
# --------------------------------------------------------------------------
def get_cached_resources() -> Optional[Dict[str, Any]]:
    payload = cache.get_json(Keys.CLOUD_RESOURCES)
    if payload is None:
        return None
    payload["freshness"] = resources_freshness(payload)
    return payload


def get_cached_metrics() -> Optional[Dict[str, Any]]:
    payload = cache.get_json(Keys.CLOUD_METRICS)
    if payload is None:
        return None
    payload["freshness"] = metrics_freshness(payload)
    return payload


def resources_freshness(payload):
    """Freshness of the inventory payload, from its generated_at stamp."""
    return describe_freshness(
        payload,
        settings.freshness_cloud,
        last_success=cache.last_success("cloud_resources"),
    )


def metrics_freshness(payload):
    """Freshness of the CloudWatch metrics payload."""
    return describe_freshness(
        payload,
        settings.freshness_cloud,
        last_success=cache.last_success("cloud_metrics"),
    )


def resources_response(
    status: Optional[str] = None,
    environment: Optional[str] = None,
    search: Optional[str] = None,
    idle_only: bool = False,
) -> Optional[CloudResourcesResponse]:
    """Inventory with the filters the Cloud Resources page exposes."""
    payload = get_cached_resources()
    if payload is None:
        return None

    resources = payload["resources"]
    if status:
        resources = [r for r in resources if str(r.get("status", "")).lower() == status.lower()]
    if environment:
        resources = [
            r for r in resources if str(r.get("environment", "")).lower() == environment.lower()
        ]
    if idle_only:
        resources = [r for r in resources if r.get("idle")]
    if search:
        needle = search.lower()
        resources = [
            r
            for r in resources
            if needle in str(r.get("resource_id", "")).lower()
            or needle in str(r.get("name") or "").lower()
            or needle in str(r.get("instance_type") or "").lower()
            or needle in str(r.get("owner") or "").lower()
        ]

    filtered = {**payload, "resources": resources}
    return CloudResourcesResponse.model_validate(filtered)


def metrics_response() -> Optional[CloudMetricsResponse]:
    payload = get_cached_metrics()
    return CloudMetricsResponse.model_validate(payload) if payload else None


def health_response() -> Optional[CloudHealthResponse]:
    """Aggregate estate health, blending inventory and utilisation."""
    resources = get_cached_resources()
    if resources is None:
        return None

    metrics = get_cached_metrics() or {}
    average_cpu = metrics.get("average_cpu")

    running = resources["running"]
    idle = resources["idle_resources"]
    total = max(1, resources["total_resources"])

    # Penalise idle waste, and penalise an estate running hot.
    score = 100.0 - (idle / total) * 45.0
    if average_cpu is not None:
        if average_cpu > 85:
            score -= 20.0
        elif average_cpu > 75:
            score -= 10.0
        elif average_cpu < 10:
            score -= 12.0
    score = round(max(0.0, min(100.0, score)), 1)

    if score >= 85:
        status = "Healthy"
    elif score >= 60:
        status = "Warning"
    else:
        status = "Critical"

    return CloudHealthResponse.model_validate(
        {
            "status": status,
            "health_score": score,
            "active_resources": running,
            "idle_resources": idle,
            "average_cpu": average_cpu,
            "estimated_monthly_cost": resources["estimated_monthly_cost"],
            "freshness": resources_freshness(resources),
        }
    )


