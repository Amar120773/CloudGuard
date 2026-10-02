"""Async architecture: task submission, status, caching and failure handling.

The whole suite runs with an unreachable broker (see conftest), so these tests
cover the inline fallback path end to end. The Celery path is exercised by the
same task bodies and is verified against a live worker in the README's demo
commands.
"""
from __future__ import annotations

import time

import pytest

from app.cache import Keys, cache
from app.schemas.task import TaskStatus, TaskType
from app.services import task_service


def _wait_for(client, task_id, timeout=120.0):
    """Poll a task to a terminal state, the way the frontend does."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        record = client.get(f"/api/tasks/{task_id}").json()
        if record["status"] in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value):
            return record
        time.sleep(0.4)
    pytest.fail(f"task {task_id} did not finish within {timeout}s")


class TestCache:
    def test_falls_back_to_memory_when_redis_is_down(self):
        assert cache.degraded is True
        health = cache.health()
        assert health["backend"] == "memory"
        assert health["connected"] is False

    def test_round_trips_json(self):
        cache.set_json("cloudguard:test:key", {"a": 1, "b": [1, 2]}, ttl=60)
        assert cache.get_json("cloudguard:test:key") == {"a": 1, "b": [1, 2]}

    def test_missing_key_returns_none(self):
        assert cache.get_json("cloudguard:test:absent") is None

    def test_respects_ttl(self):
        cache.set_json("cloudguard:test:ttl", {"v": 1}, ttl=1)
        assert cache.get_json("cloudguard:test:ttl") is not None
        time.sleep(1.2)
        assert cache.get_json("cloudguard:test:ttl") is None

    def test_reports_remaining_ttl(self):
        """TTL governs availability only; age now comes from generated_at."""
        cache.set_json("cloudguard:test:age", {"v": 1}, ttl=100)
        assert 0 < cache.ttl("cloudguard:test:age") <= 100

    def test_batch_read_returns_every_present_key(self):
        cache.set_json("cloudguard:test:b1", {"v": 1}, ttl=60)
        cache.set_json("cloudguard:test:b2", {"v": 2}, ttl=60)

        found = cache.get_many_json(
            ["cloudguard:test:b1", "cloudguard:test:b2", "cloudguard:test:missing"]
        )
        assert found == {"cloudguard:test:b1": {"v": 1}, "cloudguard:test:b2": {"v": 2}}

    def test_batch_read_of_nothing(self):
        assert cache.get_many_json([]) == {}

    def test_last_success_marker_outlives_the_payload(self):
        cache.mark_success("demo_pipeline")
        assert cache.last_success("demo_pipeline") is not None
        assert cache.last_success("never_run_pipeline") is None

    def test_counter_increments_and_carries_a_ttl(self):
        key = "cloudguard:ratelimit:test:abc"
        assert cache.incr_with_ttl(key, 60) == 1
        assert cache.incr_with_ttl(key, 60) == 2
        assert cache.incr_with_ttl(key, 60) == 3
        assert cache.ttl(key) != -1  # an expiry is set, not persistent

    def test_delete(self):
        cache.set_json("cloudguard:test:del", {"v": 1}, ttl=60)
        cache.delete("cloudguard:test:del")
        assert cache.get_json("cloudguard:test:del") is None

    def test_corrupt_payload_is_discarded_not_raised(self):
        cache._memory.set("cloudguard:test:corrupt", "{not json", 60)
        assert cache.get_json("cloudguard:test:corrupt") is None


class TestTaskSubmission:
    def test_refresh_returns_202_with_a_poll_url(self, client):
        response = client.post("/api/dashboard/refresh")
        assert response.status_code == 202

        body = response.json()
        assert body["task_id"]
        assert body["task_type"] == TaskType.REFRESH_ALL.value
        assert body["status"] == TaskStatus.PENDING.value
        assert body["poll_url"] == f"/api/tasks/{body['task_id']}"

    def test_falls_back_to_inline_execution_without_a_broker(self, client):
        body = client.post("/api/dashboard/refresh").json()
        assert body["executor"] == "inline"
        assert "broker unreachable" in body["message"].lower()

    def test_submission_returns_immediately(self, client):
        """The request must not block on the work it queues."""
        start = time.perf_counter()
        client.post("/api/costs/forecast/run")
        elapsed = time.perf_counter() - start
        assert elapsed < 3.0, f"submission blocked for {elapsed:.1f}s"

    @pytest.mark.parametrize(
        "path,task_type",
        [
            ("/api/dashboard/refresh", TaskType.REFRESH_ALL),
            ("/api/costs/forecast/run", TaskType.RUN_FORECAST),
            ("/api/costs/ingest", TaskType.INGEST_COST),
            ("/api/security/anomalies/run", TaskType.RUN_ANOMALY),
            ("/api/security/ingest", TaskType.INGEST_SECURITY),
            ("/api/cloud/ingest", TaskType.INGEST_CLOUD),
        ],
    )
    def test_every_run_endpoint_submits_a_task(self, client, path, task_type):
        response = client.post(path)
        assert response.status_code == 202
        assert response.json()["task_type"] == task_type.value


class TestTaskStatus:
    def test_unknown_task_is_404(self, client):
        response = client.get("/api/tasks/no-such-task")
        assert response.status_code == 404
        assert response.json()["detail"]["error_code"] == "task_not_found"

    def test_lifecycle_reaches_completed_with_a_result(self, client):
        submission = client.post("/api/cloud/ingest").json()
        record = _wait_for(client, submission["task_id"])

        assert record["status"] == TaskStatus.COMPLETED.value
        assert record["progress"] == 100
        assert record["error"] is None
        assert record["started_at"] and record["completed_at"]
        assert record["duration_seconds"] >= 0
        assert record["result"]["resources"] > 0
        assert Keys.CLOUD_RESOURCES in record["cache_keys"]

    def test_completed_task_populated_the_cache(self, client):
        submission = client.post("/api/cloud/ingest").json()
        _wait_for(client, submission["task_id"])

        assert cache.get_json(Keys.CLOUD_RESOURCES) is not None
        assert client.get("/api/cloud/resources").status_code == 200

    def test_forecast_task_produces_a_cached_forecast(self, client):
        submission = client.post("/api/costs/forecast/run").json()
        record = _wait_for(client, submission["task_id"])

        assert record["status"] == TaskStatus.COMPLETED.value
        assert record["result"]["projected_month_end"] > 0
        assert record["result"]["model"] in ("prophet", "trend_seasonal_fallback")

        forecast = client.get("/api/costs/forecast")
        assert forecast.status_code == 200
        assert forecast.json()["freshness"]["cached"] is True

    def test_anomaly_task_produces_scored_events(self, client):
        submission = client.post("/api/security/anomalies/run").json()
        record = _wait_for(client, submission["task_id"])

        assert record["result"]["anomalies"] > 0
        assert record["result"]["recall"] == 1.0
        assert client.get("/api/security").status_code == 200

    def test_injecting_an_anomaly_raises_the_count(self, client, pristine_flow_logs):
        baseline = _wait_for(client, client.post("/api/security/anomalies/run").json()["task_id"])
        injected = _wait_for(
            client,
            client.post(
                "/api/security/anomalies/run", json={"inject_anomaly": True}
            ).json()["task_id"],
        )
        assert injected["result"]["anomalies"] == baseline["result"]["anomalies"] + 1

    def test_task_list_includes_recent_submissions(self, client):
        submission = client.post("/api/cloud/ingest").json()
        _wait_for(client, submission["task_id"])

        body = client.get("/api/tasks").json()
        assert body["count"] >= 1
        assert submission["task_id"] in [t["task_id"] for t in body["tasks"]]


class TestFailureHandling:
    def test_a_failing_task_is_recorded_as_failed_with_its_message(self, client, monkeypatch):
        from app.services import cloud_service

        def explode():
            raise RuntimeError("simulated AWS outage")

        monkeypatch.setattr(cloud_service, "ingest_cloud_inventory", explode)

        submission = client.post("/api/cloud/ingest").json()
        record = _wait_for(client, submission["task_id"], timeout=30)

        assert record["status"] == TaskStatus.FAILED.value
        assert "simulated AWS outage" in record["error"]
        assert record["stage"] == "Failed"

    def test_refresh_survives_one_failing_stage(self, client, monkeypatch):
        """A partial refresh still publishes whatever did succeed."""
        from app.services import security_service

        def explode(*args, **kwargs):
            raise RuntimeError("security pipeline down")

        monkeypatch.setattr(security_service, "ingest_security_events", explode)

        submission = client.post("/api/dashboard/refresh").json()
        record = _wait_for(client, submission["task_id"])

        assert record["status"] == TaskStatus.COMPLETED.value
        assert "security" in record["result"]["errors"]
        # Cost and cloud stages still landed.
        assert record["result"]["cost_records"] > 0
        assert cache.get_json(Keys.CLOUD_RESOURCES) is not None

    def test_dashboard_still_renders_when_one_pipeline_is_missing(self, client):
        from app.services import cloud_service

        cloud_service.ingest_cloud_inventory()

        body = client.get("/api/dashboard").json()
        assert body["cost"] is None
        assert body["overview"]["active_resources"] > 0

        states = {p["name"]: p for p in body["pipelines"]}
        assert states["cloud_resources"]["ready"] is True
        assert states["cost_forecast"]["ready"] is False
        assert states["cost_forecast"]["message"]


class TestTaskRegistry:
    def test_records_survive_independently_of_celery(self):
        record = task_service.create_record("test-task-1", TaskType.RUN_FORECAST)
        assert record["status"] == TaskStatus.PENDING.value

        task_service.update_record(
            "test-task-1", status=TaskStatus.PROCESSING, progress=50, stage="Fitting"
        )
        fetched = task_service.get_record("test-task-1")
        assert fetched["status"] == TaskStatus.PROCESSING.value
        assert fetched["progress"] == 50
        assert fetched["stage"] == "Fitting"

    def test_duration_is_computed_on_completion(self):
        from datetime import datetime, timedelta, timezone

        task_service.create_record("test-task-2", TaskType.INGEST_COST)
        start = datetime.now(timezone.utc)
        task_service.update_record("test-task-2", started_at=start)
        task_service.update_record(
            "test-task-2",
            status=TaskStatus.COMPLETED,
            completed_at=start + timedelta(seconds=2.5),
        )
        assert task_service.get_record("test-task-2")["duration_seconds"] == pytest.approx(2.5)

    def test_broker_probe_reports_unreachable(self):
        assert task_service.broker_available(force=True) is False

    def test_pending_ids_exclude_finished_tasks(self):
        task_service.create_record("test-task-3", TaskType.RUN_ANOMALY)
        assert "test-task-3" in task_service.pending_task_ids()

        task_service.update_record("test-task-3", status=TaskStatus.COMPLETED)
        assert "test-task-3" not in task_service.pending_task_ids()
