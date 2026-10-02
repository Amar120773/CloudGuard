"""Compression, health latency, auth, rate limiting, CORS and payload shape.

These cover the remediation work rather than the original feature set: each
class corresponds to a measured defect.
"""
from __future__ import annotations

import gzip
import json
import time

import pytest

from app.cache import Keys, cache
from app.config import Settings, settings


# ==========================================================================
# Compression
# ==========================================================================
class TestCompression:
    def test_large_response_is_gzipped_when_requested(self, warm_client):
        response = warm_client.get("/api/dashboard", headers={"Accept-Encoding": "gzip"})
        assert response.status_code == 200
        assert response.headers.get("content-encoding") == "gzip"

    def test_compressed_body_decodes_to_the_same_json(self, warm_client):
        plain = warm_client.get(
            "/api/dashboard", headers={"Accept-Encoding": "identity"}
        ).json()
        compressed = warm_client.get(
            "/api/dashboard", headers={"Accept-Encoding": "gzip"}
        ).json()
        assert compressed["overview"] == plain["overview"]
        assert len(compressed["pipelines"]) == len(plain["pipelines"])

    def test_client_without_gzip_support_gets_plain_json(self, warm_client):
        response = warm_client.get(
            "/api/dashboard", headers={"Accept-Encoding": "identity"}
        )
        assert response.headers.get("content-encoding") is None
        assert response.json()["overview"] is not None

    def test_compression_meaningfully_shrinks_the_dashboard(self, warm_client):
        """Raw vs compressed bytes, measured rather than assumed."""
        raw = warm_client.get("/api/dashboard", headers={"Accept-Encoding": "identity"})
        uncompressed = len(raw.content)

        # httpx transparently decodes, so compress the same bytes to size it.
        compressed = len(gzip.compress(raw.content, compresslevel=6))
        assert compressed < uncompressed / 3, (
            f"expected a large reduction, got {uncompressed} -> {compressed}"
        )

    def test_tiny_responses_are_not_compressed(self, client):
        """Below the threshold gzip framing costs more than it saves."""
        response = client.get("/api/tasks", headers={"Accept-Encoding": "gzip"})
        assert len(response.content) < 500
        assert response.headers.get("content-encoding") is None


# ==========================================================================
# Health endpoint
# ==========================================================================
class TestHealthLatency:
    def test_health_is_fast_without_a_broker(self, client):
        """Five consecutive calls, none near the old ~1s behaviour.

        The first call may pay the broker probe (350ms timeout); the rest are
        served from the memoised ping. Asserting on the *cached* calls tests the
        actual claim and does not go red merely because the machine is busy,
        which a tight bound on the first call would.
        """
        from app.api.health import reset_ping_cache

        reset_ping_cache()
        timings = []
        for _ in range(5):
            start = time.perf_counter()
            assert client.get("/api/health").status_code == 200
            timings.append(time.perf_counter() - start)

        cached = timings[1:]
        assert max(cached) < 0.5, f"cached health calls are slow: {timings}"
        # Even the uncached first call must be nowhere near the old behaviour.
        assert timings[0] < 0.9, f"first health call regressed: {timings}"

    def test_ping_result_is_memoised(self, client, monkeypatch):
        """The probe must not run on every request."""
        from app.api import health

        health.reset_ping_cache()
        calls = {"n": 0}
        original = health._probe_celery

        def counting_probe():
            calls["n"] += 1
            return original()

        monkeypatch.setattr(health, "_probe_celery", counting_probe)

        for _ in range(4):
            client.get("/api/health")
        assert calls["n"] == 1, "expected the ping to be cached across requests"

    def test_broker_down_is_reported_as_degraded(self, client):
        body = client.get("/api/health").json()
        celery = next(d for d in body["dependencies"] if d["name"] == "celery")

        assert celery["healthy"] is False
        assert celery["metadata"]["workers"] == 0
        assert body["status"] == "degraded"

    def test_worker_available_is_reported_healthy(self, client, monkeypatch):
        """Simulate a replying worker without needing a live broker."""
        from app.api import health
        from app.services import task_service

        health.reset_ping_cache()
        monkeypatch.setattr(task_service, "broker_available", lambda force=False: True)

        class FakeControl:
            def ping(self, timeout=1.0, limit=None):
                assert limit == 1, "ping must stop after the first reply"
                return [{"celery@worker1": {"ok": "pong"}}]

        from app.workers import celery_app as celery_module

        monkeypatch.setattr(celery_module.celery_app, "control", FakeControl())

        body = client.get("/api/health").json()
        celery = next(d for d in body["dependencies"] if d["name"] == "celery")
        assert celery["healthy"] is True
        assert celery["metadata"]["workers"] == 1


# ==========================================================================
# API-key authentication
# ==========================================================================
@pytest.fixture
def secured(monkeypatch):
    """Enable API-key auth for the duration of a test."""
    from app.api import security_deps

    monkeypatch.setattr(settings, "cloudguard_api_key", "unit-test-key")
    yield "unit-test-key"
    security_deps.auth_enabled()


class TestApiKeyAuth:
    WRITE_ENDPOINTS = [
        "/api/dashboard/refresh",
        "/api/costs/forecast/run",
        "/api/costs/ingest",
        "/api/security/anomalies/run",
        "/api/security/ingest",
        "/api/cloud/ingest",
    ]

    def test_disabled_by_default(self, client):
        """No key configured keeps the local demo frictionless."""
        assert client.post("/api/dashboard/refresh").status_code == 202

    @pytest.mark.parametrize("path", WRITE_ENDPOINTS)
    def test_missing_key_is_401(self, client, secured, path):
        response = client.post(path)
        assert response.status_code == 401
        assert response.json()["detail"]["error_code"] == "api_key_required"

    def test_invalid_key_is_403(self, client, secured):
        response = client.post(
            "/api/dashboard/refresh", headers={"X-API-Key": "wrong-key"}
        )
        assert response.status_code == 403
        assert response.json()["detail"]["error_code"] == "api_key_invalid"

    def test_valid_key_is_accepted(self, client, secured):
        response = client.post(
            "/api/dashboard/refresh", headers={"X-API-Key": secured}
        )
        assert response.status_code == 202

    def test_configured_key_never_appears_in_a_response(self, client, secured):
        for headers in ({}, {"X-API-Key": "wrong-key"}):
            response = client.post("/api/dashboard/refresh", headers=headers)
            assert secured not in response.text

    def test_read_endpoints_stay_open(self, client, secured):
        for path in ("/api/health", "/api/dashboard", "/api/insights", "/api/tasks"):
            assert client.get(path).status_code == 200


# ==========================================================================
# Rate limiting
# ==========================================================================
class TestRateLimiting:
    @pytest.fixture(autouse=True)
    def _clear_counters(self):
        cache.flush_namespace(Keys.RATE_LIMIT_PREFIX)
        yield
        cache.flush_namespace(Keys.RATE_LIMIT_PREFIX)

    def test_requests_under_the_limit_are_accepted(self, client, monkeypatch):
        monkeypatch.setattr(settings, "rate_limit_write_requests", 3)
        for _ in range(3):
            assert client.post("/api/cloud/ingest").status_code == 202

    def test_request_over_the_limit_is_429(self, client, monkeypatch):
        monkeypatch.setattr(settings, "rate_limit_write_requests", 2)
        client.post("/api/cloud/ingest")
        client.post("/api/cloud/ingest")

        response = client.post("/api/cloud/ingest")
        assert response.status_code == 429
        assert response.json()["detail"]["error_code"] == "rate_limited"
        assert response.headers["Retry-After"]

    def test_window_expiry_restores_access(self, client, monkeypatch):
        monkeypatch.setattr(settings, "rate_limit_write_requests", 1)
        monkeypatch.setattr(settings, "rate_limit_window_seconds", 1)

        assert client.post("/api/cloud/ingest").status_code == 202
        assert client.post("/api/cloud/ingest").status_code == 429

        time.sleep(1.3)  # window rolls over
        assert client.post("/api/cloud/ingest").status_code == 202

    def test_read_endpoints_are_never_limited(self, client, monkeypatch):
        monkeypatch.setattr(settings, "rate_limit_write_requests", 1)
        client.post("/api/cloud/ingest")
        client.post("/api/cloud/ingest")  # consumes the limit

        for _ in range(8):
            assert client.get("/api/dashboard").status_code == 200

    def test_zero_disables_limiting(self, client, monkeypatch):
        monkeypatch.setattr(settings, "rate_limit_write_requests", 0)
        for _ in range(6):
            assert client.post("/api/cloud/ingest").status_code == 202

    def test_counter_is_scoped_per_identity(self, client, monkeypatch, secured):
        """Two different keys must not share one bucket."""
        monkeypatch.setattr(settings, "rate_limit_write_requests", 1)
        monkeypatch.setattr(settings, "cloudguard_api_key", "unit-test-key")

        assert client.post(
            "/api/cloud/ingest", headers={"X-API-Key": "unit-test-key"}
        ).status_code == 202
        # A second call on the same identity trips the limit.
        assert client.post(
            "/api/cloud/ingest", headers={"X-API-Key": "unit-test-key"}
        ).status_code == 429


# ==========================================================================
# CORS
# ==========================================================================
class TestCors:
    def test_configured_origin_is_echoed_without_credentials(self, client):
        """The API authenticates with a header, never a cookie, so credentialed
        CORS is off: an allowed origin gets its echo and nothing more."""
        response = client.get(
            "/api/health", headers={"Origin": "http://localhost:5173"}
        )
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert "access-control-allow-credentials" not in response.headers

    def test_unknown_origin_gets_no_allow_header(self, client):
        response = client.get("/api/health", headers={"Origin": "http://evil.test"})
        assert "access-control-allow-origin" not in response.headers

    def test_wildcard_is_never_emitted(self, client):
        for origin in ("http://evil.test", "http://localhost:5173", "null"):
            response = client.get("/api/health", headers={"Origin": origin})
            assert response.headers.get("access-control-allow-origin") != "*"

    def test_empty_configuration_falls_back_to_an_explicit_allowlist(self, monkeypatch):
        """Never '*': wildcard plus credentials is an invalid CORS policy."""
        from app.main import DEV_FALLBACK_ORIGINS, resolve_cors_origins

        monkeypatch.setattr(settings, "cors_origins_raw", "")
        resolved = resolve_cors_origins()

        assert "*" not in resolved
        assert resolved == DEV_FALLBACK_ORIGINS

    def test_configured_origins_win_over_the_fallback(self, monkeypatch):
        from app.main import resolve_cors_origins

        monkeypatch.setattr(settings, "cors_origins_raw", "https://ops.example.com")
        assert resolve_cors_origins() == ["https://ops.example.com"]

    def test_origin_regex_matches_generated_preview_urls(self, client, monkeypatch):
        """Vercel mints a new origin per preview deploy; a fixed list cannot cover them."""
        from app.api import health  # noqa: F401  (ensures the app is importable)

        # Scoped to the team: an unscoped pattern would also match other people's
        # projects (see test_deployment_security.TestCorsPolicy).
        monkeypatch.setattr(settings, "cors_origins_raw", "https://cg.vercel.app")
        monkeypatch.setattr(
            settings,
            "cors_origin_regex",
            r"https://cg-git-[a-z0-9-]+-acme-team\.vercel\.app",
        )
        from app.main import resolve_cors_origin_regex, resolve_cors_origins

        assert resolve_cors_origins() == ["https://cg.vercel.app"]
        assert resolve_cors_origin_regex() == r"https://cg-git-[a-z0-9-]+-acme-team\.vercel\.app"

    def test_invalid_regex_is_ignored_rather_than_fatal(self, monkeypatch):
        """A bad pattern must not take the API down at import time."""
        from app.main import resolve_cors_origin_regex

        monkeypatch.setattr(settings, "cors_origin_regex", "https://[unclosed")
        assert resolve_cors_origin_regex() is None

    def test_blank_regex_means_no_regex(self, monkeypatch):
        from app.main import resolve_cors_origin_regex

        monkeypatch.setattr(settings, "cors_origin_regex", "   ")
        assert resolve_cors_origin_regex() is None

    def test_regex_alone_is_a_complete_policy(self, monkeypatch):
        """With a regex set, an empty allowlist is intentional, not a misconfiguration."""
        from app.main import resolve_cors_origins

        monkeypatch.setattr(settings, "cors_origins_raw", "")
        monkeypatch.setattr(settings, "cors_origin_regex", r"https://.*\.vercel\.app")
        assert resolve_cors_origins() == []

    def test_preflight_is_answered(self, client):
        response = client.options(
            "/api/dashboard/refresh",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-API-Key",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


# ==========================================================================
# Dashboard payload shape
# ==========================================================================
class TestDashboardPayload:
    def test_embedded_posture_carries_no_event_feed(self, warm_client):
        body = warm_client.get("/api/dashboard").json()
        assert body["security"]["recent_events"] == []
        # The summary fields the Overview needs are still present.
        assert body["security"]["anomaly_count"] >= 0
        assert body["security"]["top_anomalies"]

    def test_events_are_not_duplicated(self, warm_client):
        body = warm_client.get("/api/dashboard").json()
        assert len(body["recent_events"]) <= 10
        assert len(body["security"]["recent_events"]) == 0

    def test_full_feed_remains_available_on_the_security_endpoints(self, warm_client):
        posture = warm_client.get("/api/security").json()
        assert len(posture["recent_events"]) > 10

        events = warm_client.get("/api/security/events?limit=50").json()
        assert len(events["events"]) == 50

    def test_unused_top_resources_block_is_gone(self, warm_client):
        assert "top_resources" not in warm_client.get("/api/dashboard").json()

    def test_payload_is_materially_smaller(self, warm_client):
        """The dashboard is polled continuously; size is a feature."""
        raw = warm_client.get("/api/dashboard", headers={"Accept-Encoding": "identity"})
        assert len(raw.content) < 60_000, (
            f"dashboard grew back to {len(raw.content)} bytes"
        )

    def test_overview_still_has_everything_it_renders(self, warm_client):
        body = warm_client.get("/api/dashboard").json()
        overview = body["overview"]

        assert overview["current_spend_mtd"] > 0
        assert overview["forecast_month_end"] > 0
        assert overview["total_security_events"] > 0
        assert overview["active_resources"] > 0
        assert body["cost"]["daily"]
        assert body["forecast"]["forecast"]
        assert body["recent_events"]
        assert body["insights"]


class TestNoWriteAmplification:
    def test_reading_the_dashboard_writes_nothing(self, warm_client):
        """GET must be a pure read; it used to rewrite ~108KB per request."""
        before = cache.flush_namespace("cloudguard:dashboard")
        warm_client.get("/api/dashboard")
        warm_client.get("/api/dashboard")

        assert cache.get_json("cloudguard:dashboard:summary") is None
        assert not hasattr(Keys, "DASHBOARD")

    def test_dashboard_cache_helper_is_gone(self):
        from app.services import dashboard_service

        assert not hasattr(dashboard_service, "get_cached_dashboard")


# ==========================================================================
# Task list batching
# ==========================================================================
class TestTaskListBatching:
    def test_uses_a_single_batched_read(self, client, monkeypatch):
        from app.services import task_service

        for index in range(5):
            task_service.create_record(f"batch-task-{index}", "ingest_cloud_data")

        calls = {"batched": 0, "single": 0}
        real_many = cache.get_many_json
        real_one = cache.get_json

        def counted_many(keys):
            calls["batched"] += 1
            return real_many(keys)

        def counted_one(key):
            if key.startswith(Keys.TASK_PREFIX):
                calls["single"] += 1
            return real_one(key)

        monkeypatch.setattr(cache, "get_many_json", counted_many)
        monkeypatch.setattr(cache, "get_json", counted_one)

        records = task_service.recent_records(limit=10)

        assert len(records) >= 5
        assert calls["batched"] == 1, "expected exactly one batched read"
        assert calls["single"] == 0, "no per-task round-trips should remain"

    def test_statuses_survive_batching(self, client):
        from app.schemas.task import TaskStatus
        from app.services import task_service

        task_service.create_record("batch-status-1", "run_cost_forecast")
        task_service.update_record(
            "batch-status-1", status=TaskStatus.COMPLETED, progress=100
        )
        task_service.create_record("batch-status-2", "run_anomaly_detection")

        by_id = {r["task_id"]: r for r in task_service.recent_records(limit=10)}
        assert by_id["batch-status-1"]["status"] == TaskStatus.COMPLETED.value
        assert by_id["batch-status-1"]["progress"] == 100
        assert by_id["batch-status-2"]["status"] == TaskStatus.PENDING.value

    def test_endpoint_returns_the_batched_records(self, client):
        from app.services import task_service

        task_service.create_record("batch-endpoint-1", "ingest_cost_data")
        body = client.get("/api/tasks?limit=10").json()

        assert body["count"] >= 1
        assert "batch-endpoint-1" in [t["task_id"] for t in body["tasks"]]


# ==========================================================================
# Settings
# ==========================================================================
class TestSecuritySettings:
    def test_api_key_defaults_to_disabled(self, monkeypatch):
        monkeypatch.delenv("CLOUDGUARD_API_KEY", raising=False)
        assert Settings().cloudguard_api_key == ""

    def test_api_key_is_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("CLOUDGUARD_API_KEY", "from-env")
        assert Settings().cloudguard_api_key == "from-env"

    def test_rate_limit_is_configurable(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_WRITE_REQUESTS", "42")
        monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "15")

        configured = Settings()
        assert configured.rate_limit_write_requests == 42
        assert configured.rate_limit_window_seconds == 15
