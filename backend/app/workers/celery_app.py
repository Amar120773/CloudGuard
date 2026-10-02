"""Celery application and beat schedule."""
from __future__ import annotations

import os

from celery import Celery
from celery.signals import setup_logging, worker_ready

from app.config import settings
from app.connection_urls import with_redis_tls_defaults
from app.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

# Celery reads these straight from the environment, *ahead of* the URLs passed
# to Celery() below, so a rediss:// value set there would bypass the TLS default
# in Settings.broker_url and Celery would refuse it.
_CELERY_URL_VARIABLES = (
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "CELERY_BROKER_READ_URL",
    "CELERY_BROKER_WRITE_URL",
)


def normalise_celery_environment() -> None:
    """Apply with_redis_tls_defaults to Celery's own URL variables, in place."""
    for name in _CELERY_URL_VARIABLES:
        value = os.environ.get(name)
        if value:
            os.environ[name] = with_redis_tls_defaults(value)


normalise_celery_environment()

celery_app = Celery(
    "cloudguard",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    result_expires=3600,
    task_track_started=True,
    task_time_limit=settings.celery_task_time_limit,
    task_soft_time_limit=settings.celery_task_soft_time_limit,
    # A Prophet fit is CPU-bound and takes seconds; prefetching many of them
    # would leave tasks queued behind a busy worker.
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    broker_connection_retry_on_startup=True,
    # Fail fast when the broker is down so the API can fall back to inline
    # execution instead of blocking the request.
    broker_transport_options={"max_retries": 1, "socket_timeout": 2.0},
    broker_connection_max_retries=1,
    # The result backend keeps its own reconnect loop; without these it retries
    # 20 times inside apply_async and turns a missing Redis into a ~2min request.
    result_backend_always_retry=False,
    result_backend_max_retries=1,
    redis_socket_connect_timeout=2.0,
    redis_socket_timeout=2.0,
    redis_retry_on_timeout=False,
    beat_schedule={
        "collect-cloud-metrics": {
            "task": "cloudguard.ingest_cloud_data",
            "schedule": float(settings.beat_cloud_interval),
        },
        "collect-security-events": {
            "task": "cloudguard.ingest_security_events",
            "schedule": float(settings.beat_security_interval),
        },
        "refresh-cost-and-forecast": {
            "task": "cloudguard.refresh_all",
            "schedule": float(settings.beat_cost_interval),
        },
    },
)


@setup_logging.connect
def _configure_celery_logging(**_kwargs):
    """Use CloudGuard's log format in the worker instead of Celery's default."""
    configure_logging()


@worker_ready.connect
def _on_worker_ready(**_kwargs):
    """Warm the mocked AWS environment so the first task is not slowed by seeding."""
    try:
        from app.cloud.aws import get_provider

        get_provider()
        logger.info("Worker ready; cloud provider initialised (%s)", settings.cloud_mode)
    except Exception as exc:  # pragma: no cover - startup resilience
        logger.warning("Could not pre-initialise cloud provider: %s", exc)
