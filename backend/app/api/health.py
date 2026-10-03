"""Health and readiness endpoints."""
from __future__ import annotations

import time

from fastapi import APIRouter

from app.cache import cache
from app.config import settings
from app.connection_urls import describe_url, scrub_credentials
from app.logging_config import get_logger
from app.schemas.common import HealthResponse, ServiceStatus

logger = get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Service health")
def health() -> HealthResponse:
    """Report API, Redis, Celery and cloud-provider status.

    Always 200 so a load balancer does not pull the API out when only a
    dependency is degraded; read `status` and `dependencies` for detail.
    """
    api = ServiceStatus(name="api", healthy=True, detail="FastAPI is serving requests")
    if settings.standalone:
        # No Redis and no Celery by design: listing them as "down" would report a
        # deliberate single-process setup as an outage.
        dependencies = [api, _cloud_status()]
    else:
        dependencies = [api, _redis_status(), _celery_status(), _cloud_status()]
    degraded = any(not dependency.healthy for dependency in dependencies)

    return HealthResponse(
        status="degraded" if degraded else "ok",
        mode="standalone" if settings.standalone else "distributed",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        cloud_mode=settings.cloud_mode,
        dependencies=dependencies,
    )


def _redis_status() -> ServiceStatus:
    info = cache.health()
    return ServiceStatus(
        name="redis",
        healthy=info["connected"],
        detail=(
            f"Connected ({info.get('latency_ms')}ms)"
            if info["connected"]
            else "Unreachable - serving from the in-process cache fallback"
        ),
        metadata=info,
    )


# A ping result is reused briefly. The sidebar polls health every 30s and a
# worker pool does not change between two polls often enough to justify paying
# the broadcast cost on every request.
_PING_CACHE_SECONDS = 10.0
_ping_cache: dict = {"at": 0.0, "status": None}


def _celery_status() -> ServiceStatus:
    """Ping the worker pool. A broker with no workers is still degraded."""
    now = time.monotonic()
    cached = _ping_cache["status"]
    if cached is not None and now - _ping_cache["at"] < _PING_CACHE_SECONDS:
        return cached

    status_value = _probe_celery()
    _ping_cache.update({"at": now, "status": status_value})
    return status_value


def _broker_metadata(workers: int) -> dict:
    """What this public endpoint may say about the broker: transport and TLS.

    Never the URL. A managed Redis URL embeds the password, and nothing a client
    does with this response needs even the host.
    """
    broker = describe_url(settings.broker_url)
    return {"workers": workers, "broker_transport": broker["transport"], "broker_tls": broker["tls"]}


def _probe_celery() -> ServiceStatus:
    from app.services.task_service import broker_available

    # control.ping against an absent broker burns seconds in Celery's reconnect
    # loop, so check the socket first.
    if not broker_available():
        return ServiceStatus(
            name="celery",
            healthy=False,
            detail="Broker not reachable - tasks run inline on the API process",
            metadata=_broker_metadata(workers=0),
        )

    try:
        from app.workers.celery_app import celery_app

        # `limit=1` returns as soon as one worker answers. Without it, ping has
        # no idea how many replies to expect and always blocks for the full
        # timeout - which made this endpoint cost ~0.97s on every single call.
        replies = celery_app.control.ping(timeout=1.0, limit=1) or []
        workers = len(replies)
        return ServiceStatus(
            name="celery",
            healthy=workers > 0,
            detail=(
                f"{workers} worker(s) responded"
                if workers
                # The broker is up, so submissions queue rather than run inline.
                else "No workers responded - queued tasks wait until a worker starts"
            ),
            metadata=_broker_metadata(workers=workers),
        )
    except Exception as exc:
        # The TCP probe passed, so this is not an outage; usually configuration.
        return ServiceStatus(
            name="celery",
            healthy=False,
            detail=f"Worker ping failed ({type(exc).__name__}) - check the broker configuration",
            metadata=_broker_metadata(workers=0),
        )


def reset_ping_cache() -> None:
    """Drop the memoised ping (used by tests)."""
    _ping_cache.update({"at": 0.0, "status": None})


def _cloud_status() -> ServiceStatus:
    try:
        from app.cloud.aws import get_provider

        capabilities = get_provider().capabilities()
        return ServiceStatus(
            name="cloud",
            healthy=True,
            detail=f"{capabilities['provider']} via {settings.cloud_mode}",
            metadata=capabilities,
        )
    except Exception as exc:
        return ServiceStatus(
            name="cloud",
            healthy=False,
            detail=scrub_credentials(f"Provider initialisation failed: {type(exc).__name__}: {exc}"),
        )
