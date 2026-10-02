"""Shared pytest fixtures.

Environment is pinned *before* app modules import, because `Settings` is cached
with lru_cache and reads the environment once.

Redis is deliberately pointed at an unreachable port: the suite must pass on a
machine with no Redis and no Celery worker, which also exercises the in-process
cache fallback and the inline task executor on every run.
"""
from __future__ import annotations

import os

os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/0"
os.environ["CELERY_BROKER_URL"] = "redis://127.0.0.1:6399/0"
os.environ["CELERY_RESULT_BACKEND"] = "redis://127.0.0.1:6399/0"
os.environ["CLOUD_MODE"] = "moto_inproc"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["ENVIRONMENT"] = "test"
os.environ["DEMO_SEED"] = "1337"

from datetime import date, timedelta  # noqa: E402

import pytest  # noqa: E402

from app.cache import cache  # noqa: E402
from app.cloud import datagen  # noqa: E402
from app.cloud.aws import get_provider, reset_clients, reset_provider  # noqa: E402
from app.cloud.moto_setup import (  # noqa: E402
    reset_flow_logs,
    reset_seed_flag,
    seed_environment,
)


@pytest.fixture(scope="session", autouse=True)
def mocked_aws():
    """Start Moto once for the session and seed the demo account."""
    reset_seed_flag()
    seed_environment(force=True)
    yield
    reset_provider()
    reset_clients()


@pytest.fixture(autouse=True)
def clean_cache():
    """Each test starts with an empty cache namespace.

    Inline tasks are drained first: a task still running from the previous test
    would otherwise write into the cache we just cleared, and the next test would
    see state it did not create.
    """
    from app.services import task_service

    task_service.wait_for_inline(timeout=180)
    cache.flush_namespace("cloudguard:")
    yield
    task_service.wait_for_inline(timeout=180)
    cache.flush_namespace("cloudguard:")


@pytest.fixture
def provider():
    return get_provider()


@pytest.fixture
def pristine_flow_logs():
    """Restore the seeded flow-log baseline.

    The live-injection demo appends real events to the mocked log group, so a
    test that asserts on the baseline anomaly count must start from a clean one.
    """
    reset_flow_logs()
    yield
    reset_flow_logs()


@pytest.fixture
def client():
    """FastAPI test client with startup/shutdown events run."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def cost_records():
    """Deterministic 120-day, 8-service cost history."""
    end = date.today()
    dataset = datagen.generate_cost_dataset(days=120, end=end)
    return [
        {
            "date": row["date"],
            "service": row["service"],
            "usage": row["usage"],
            "cost": row["cost"],
            "unit": "USD",
        }
        for row in dataset.rows
    ]


@pytest.fixture(scope="session")
def security_records():
    """Deterministic behavioural windows with four seeded outliers."""
    return datagen.generate_security_records(count=240)


@pytest.fixture
def warm_client(client):
    """A client whose pipelines have all run, for read-endpoint tests.

    Runs the pipelines directly rather than through Celery - the async path is
    covered separately in test_async_architecture.py.
    """
    from app.services import cloud_service, cost_service, security_service
    from app.services import dashboard_service

    cloud_service.ingest_cloud_inventory()
    cloud_service.ingest_cloud_metrics()
    cost_service.ingest_cost_history()
    cost_service.run_forecast()
    security_service.ingest_security_events()
    security_service.run_anomaly_detection()
    dashboard_service.build_dashboard()
    return client
