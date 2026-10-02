"""Freshness state machine.

Regression coverage for the bug this replaced: age used to be derived from
Redis' *remaining* TTL (``age = ttl_total - remaining``), which bounds age by the
TTL and made the ``age > ttl`` staleness test unreachable. Every state below was
dead code before.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.cache import Keys, cache
from app.freshness import (
    EXPIRED,
    FRESH,
    STALE,
    UNAVAILABLE,
    age_seconds,
    describe,
    parse_timestamp,
    stamp,
    threshold_for,
    utcnow,
)

THRESHOLD = 300


def _payload(age: int, now: datetime) -> dict:
    return {"generated_at": (now - timedelta(seconds=age)).isoformat()}


class TestStateMachine:
    """The four states must all be reachable - that was the whole bug."""

    def test_just_generated_is_fresh(self):
        now = utcnow()
        result = describe(_payload(0, now), THRESHOLD, now=now)
        assert result["state"] == FRESH
        assert result["stale"] is False
        assert result["age_seconds"] == 0

    def test_one_second_under_threshold_is_fresh(self):
        now = utcnow()
        result = describe(_payload(THRESHOLD - 1, now), THRESHOLD, now=now)
        assert result["state"] == FRESH
        assert result["stale"] is False

    def test_exactly_at_threshold_is_fresh(self):
        """The boundary is inclusive: `age > threshold` is the stale test."""
        now = utcnow()
        result = describe(_payload(THRESHOLD, now), THRESHOLD, now=now)
        assert result["state"] == FRESH
        assert result["stale"] is False

    def test_one_second_over_threshold_is_stale(self):
        now = utcnow()
        result = describe(_payload(THRESHOLD + 1, now), THRESHOLD, now=now)
        assert result["state"] == STALE
        assert result["stale"] is True
        assert result["age_seconds"] == THRESHOLD + 1

    def test_far_past_threshold_is_stale(self):
        now = utcnow()
        result = describe(_payload(THRESHOLD * 10, now), THRESHOLD, now=now)
        assert result["state"] == STALE
        assert result["stale"] is True

    def test_missing_payload_with_prior_success_is_expired(self):
        """The cache expired, but the pipeline has produced good data before."""
        now = utcnow()
        last = (now - timedelta(seconds=900)).isoformat()
        result = describe(None, THRESHOLD, last_success=last, now=now)

        assert result["state"] == EXPIRED
        assert result["stale"] is True
        assert result["age_seconds"] == 900
        assert result["source"] == "fallback"

    def test_missing_payload_with_no_history_is_unavailable(self):
        result = describe(None, THRESHOLD, last_success=None)
        assert result["state"] == UNAVAILABLE
        assert result["stale"] is False
        assert result["age_seconds"] is None

    def test_expired_is_distinct_from_unavailable(self):
        """Redis expiry must not be conflated with "never ran"."""
        now = utcnow()
        expired = describe(None, THRESHOLD, last_success=now.isoformat(), now=now)
        never = describe(None, THRESHOLD, last_success=None, now=now)
        assert expired["state"] != never["state"]


class TestMalformedInput:
    """A corrupt timestamp degrades to unavailable; it never raises."""

    @pytest.mark.parametrize(
        "value", ["not-a-date", "", "   ", "2026-13-45T99:99:99", 12345, [], {}]
    )
    def test_malformed_timestamp_is_unavailable(self, value):
        result = describe({"generated_at": value}, THRESHOLD)
        assert result["state"] == UNAVAILABLE
        assert result["stale"] is False
        assert result["age_seconds"] is None

    def test_absent_timestamp_is_unavailable(self):
        result = describe({"some": "data"}, THRESHOLD)
        assert result["state"] == UNAVAILABLE

    def test_present_but_undatable_payload_is_not_claimed_fresh(self):
        """Not knowing the age must never be reported as "recent"."""
        assert describe({"generated_at": None}, THRESHOLD)["state"] != FRESH


class TestTimestampParsing:
    def test_parses_iso_with_offset(self):
        parsed = parse_timestamp("2026-10-01T12:00:00+00:00")
        assert parsed == datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

    def test_parses_trailing_z(self):
        """Python < 3.11 cannot parse 'Z' natively; many writers emit it."""
        assert parse_timestamp("2026-10-01T12:00:00Z") == datetime(
            2026, 10, 1, 12, 0, tzinfo=timezone.utc
        )

    def test_naive_timestamp_is_treated_as_utc(self):
        parsed = parse_timestamp("2026-10-01T12:00:00")
        assert parsed.tzinfo is not None
        assert parsed == datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

    def test_non_utc_offset_is_normalised(self):
        parsed = parse_timestamp("2026-10-01T14:00:00+02:00")
        assert parsed == datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

    def test_datetime_object_passes_through(self):
        when = datetime(2026, 10, 1, tzinfo=timezone.utc)
        assert parse_timestamp(when) == when

    def test_future_timestamp_clamps_to_zero(self):
        """Clock skew must not produce a negative age."""
        future = (utcnow() + timedelta(seconds=120)).isoformat()
        assert age_seconds(future) == 0


class TestStamp:
    def test_stamps_timezone_aware_utc(self):
        payload = stamp({})
        parsed = parse_timestamp(payload["generated_at"])
        assert parsed.tzinfo is not None
        assert abs((utcnow() - parsed).total_seconds()) < 5

    def test_explicit_time_is_honoured(self):
        when = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert parse_timestamp(stamp({}, when)["generated_at"]) == when


class TestThresholdDerivation:
    def test_default_threshold_is_below_the_ttl(self):
        """If the threshold reached the TTL the stale window would vanish again."""
        for ttl in (60, 180, 600, 1800):
            assert threshold_for(ttl) < ttl

    def test_never_returns_zero(self):
        assert threshold_for(1) >= 1


class TestPipelineIntegration:
    """The cached pipelines must round-trip through the real cache."""

    def test_cost_history_reports_fresh_after_ingestion(self):
        from app.services import cost_service

        cost_service.ingest_cost_history()
        payload = cost_service.get_cached_history()

        assert payload["generated_at"]
        assert payload["freshness"]["state"] == FRESH
        assert payload["freshness"]["stale"] is False

    def test_cost_history_reports_stale_once_past_threshold(self):
        """Drive the real service past its threshold by back-dating the stamp."""
        from app.config import settings
        from app.services import cost_service

        cost_service.ingest_cost_history()
        payload = cache.get_json(Keys.COST_HISTORY)
        payload["generated_at"] = (
            utcnow() - timedelta(seconds=settings.freshness_cost + 60)
        ).isoformat()
        cache.set_json(Keys.COST_HISTORY, payload, settings.cache_ttl_cost)

        freshness = cost_service.get_cached_history()["freshness"]
        assert freshness["state"] == STALE
        assert freshness["stale"] is True

    def test_expired_cache_still_reports_the_last_success(self):
        from app.services import cost_service

        cost_service.ingest_cost_history()
        cache.delete(Keys.COST_HISTORY)  # TTL elapsed

        freshness = cost_service.history_freshness(None)
        assert freshness["state"] == EXPIRED
        assert freshness["age_seconds"] is not None

    def test_beat_refreshes_each_pipeline_before_it_expires(self):
        """beat interval < freshness threshold < cache TTL, for every pipeline.

        If a beat interval exceeds its TTL the pipeline expires before the
        scheduler refreshes it, and the dashboard empties out for part of every
        cycle. That shipped once; this pins it.
        """
        from app.config import settings

        pipelines = [
            ("cloud", settings.beat_cloud_interval, settings.freshness_cloud,
             settings.cache_ttl_cloud),
            ("security", settings.beat_security_interval, settings.freshness_security,
             settings.cache_ttl_anomaly),
            ("cost", settings.beat_cost_interval, settings.freshness_cost,
             settings.cache_ttl_cost),
            ("forecast", settings.beat_cost_interval, settings.freshness_forecast,
             settings.cache_ttl_forecast),
        ]
        for name, beat, threshold, ttl in pipelines:
            assert beat < threshold, (
                f"{name}: beat runs every {beat}s but data is called stale after "
                f"{threshold}s - it would look stale between every refresh"
            )
            assert threshold < ttl, (
                f"{name}: freshness threshold {threshold}s must be below the "
                f"{ttl}s TTL or the stale state is unreachable"
            )

    def test_one_missed_beat_cycle_does_not_look_stale(self):
        """Headroom check: a single late refresh must not alarm the operator."""
        from app.config import settings

        for beat, threshold in (
            (settings.beat_cloud_interval, settings.freshness_cloud),
            (settings.beat_security_interval, settings.freshness_security),
            (settings.beat_cost_interval, settings.freshness_cost),
        ):
            assert threshold >= beat * 2, (
                f"a threshold of {threshold}s leaves no room for one missed "
                f"{beat}s beat cycle"
            )

    def test_every_freshness_threshold_sits_below_its_ttl(self):
        """Configuration guard: the bug returns the moment this stops holding."""
        from app.config import settings

        pairs = [
            (settings.freshness_cloud, settings.cache_ttl_cloud),
            (settings.freshness_cost, settings.cache_ttl_cost),
            (settings.freshness_forecast, settings.cache_ttl_forecast),
            (settings.freshness_security, settings.cache_ttl_security),
            (settings.freshness_anomaly, settings.cache_ttl_anomaly),
        ]
        for threshold, ttl in pairs:
            assert threshold < ttl, (
                f"freshness threshold {threshold}s must be below its {ttl}s TTL, "
                "or the stale state is unreachable"
            )


class TestDashboardPipelineStates:
    def test_cold_pipelines_report_unavailable(self, client):
        pipelines = client.get("/api/dashboard").json()["pipelines"]
        assert all(p["state"] == UNAVAILABLE for p in pipelines)
        assert all(p["stale"] is False for p in pipelines)

    def test_warm_pipelines_report_fresh(self, warm_client):
        pipelines = warm_client.get("/api/dashboard").json()["pipelines"]
        assert all(p["state"] == FRESH for p in pipelines), [
            (p["name"], p["state"]) for p in pipelines
        ]

    def test_stale_pipeline_surfaces_in_the_api(self, warm_client):
        """End-to-end proof the stale path is now reachable over HTTP."""
        from app.config import settings

        payload = cache.get_json(Keys.COST_FORECAST)
        payload["generated_at"] = (
            utcnow() - timedelta(seconds=settings.freshness_forecast + 120)
        ).isoformat()
        cache.set_json(Keys.COST_FORECAST, payload, settings.cache_ttl_forecast)

        body = warm_client.get("/api/dashboard").json()
        forecast_state = next(
            p for p in body["pipelines"] if p["name"] == "cost_forecast"
        )

        assert forecast_state["state"] == STALE
        assert forecast_state["stale"] is True
        assert "beyond the freshness threshold" in forecast_state["message"]
        # The aggregate signal follows.
        assert body["freshness"]["stale"] is True
