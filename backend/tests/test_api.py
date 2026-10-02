"""API endpoints: contracts, validation, error handling, fallback states."""
from __future__ import annotations

import pytest


class TestHealth:
    def test_reports_every_dependency(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200

        body = response.json()
        assert body["status"] in ("ok", "degraded")
        names = {d["name"] for d in body["dependencies"]}
        assert names == {"api", "redis", "celery", "cloud"}

    def test_stays_200_while_redis_is_down(self, client):
        """A degraded dependency must not take the API out of rotation."""
        body = client.get("/api/health").json()
        redis = next(d for d in body["dependencies"] if d["name"] == "redis")

        assert redis["healthy"] is False          # conftest points at a dead port
        assert body["status"] == "degraded"
        assert "fallback" in redis["detail"].lower()

    def test_root_lists_the_endpoint_map(self, client):
        body = client.get("/").json()
        assert body["name"]
        assert body["endpoints"]["dashboard"] == "/api/dashboard"

    def test_adds_a_timing_header(self, client):
        response = client.get("/api/health")
        assert float(response.headers["X-Process-Time-Ms"]) >= 0


class TestColdStart:
    """Before any pipeline runs, the API must degrade informatively."""

    def test_dashboard_is_200_with_null_sections(self, client):
        response = client.get("/api/dashboard")
        assert response.status_code == 200

        body = response.json()
        assert body["cost"] is None
        assert body["forecast"] is None
        assert body["security"] is None
        assert body["overview"]["current_spend_mtd"] == 0.0

    def test_dashboard_explains_which_pipelines_are_missing(self, client):
        pipelines = client.get("/api/dashboard").json()["pipelines"]
        assert len(pipelines) == 5
        assert all(p["ready"] is False for p in pipelines)
        for pipeline in pipelines:
            assert pipeline["message"]

    def test_cold_insights_prompt_the_operator(self, client):
        body = client.get("/api/insights").json()
        assert body["count"] == 1
        assert body["insights"][0]["category"] == "SYSTEM"
        assert "have not run" in body["insights"][0]["title"]

    @pytest.mark.parametrize(
        "path",
        [
            "/api/costs",
            "/api/costs/history",
            "/api/costs/forecast",
            "/api/security",
            "/api/security/events",
            "/api/security/anomalies",
            "/api/cloud",
            "/api/cloud/resources",
            "/api/cloud/metrics",
        ],
    )
    def test_data_endpoints_return_503_not_500(self, client, path):
        response = client.get(path)
        assert response.status_code == 503

        detail = response.json()["detail"]
        assert detail["error_code"] == "pipeline_not_ready"
        assert detail["hint"]
        assert detail["run_endpoint"].startswith("/api/")


class TestWarmReads:
    def test_cost_history(self, warm_client):
        body = warm_client.get("/api/costs/history").json()
        assert body["records_analysed"] > 0
        assert body["total_cost"] > 0
        assert len(body["daily"]) >= 100
        assert body["by_service"]
        # History alone carries no projection.
        assert all(s["projected_month_end"] is None for s in body["by_service"])

    def test_cost_forecast(self, warm_client):
        body = warm_client.get("/api/costs/forecast").json()
        assert body["model_name"] in ("prophet", "trend_seasonal_fallback")
        assert len(body["forecast"]) == 30
        assert body["projected_month_end"] > 0
        assert body["budget"] is not None
        assert body["accuracy"]["mae"] is not None
        # The full breakdown reconciles with the aggregate.
        total = sum(s["projected_month_end"] for s in body["service_breakdown"])
        assert total == pytest.approx(body["projected_month_end"], abs=1.0)

    def test_security_posture(self, warm_client):
        body = warm_client.get("/api/security").json()
        assert body["model_name"] == "IsolationForest"
        assert body["total_events"] > 0
        assert body["anomaly_count"] > 0
        assert len(body["features_used"]) == 7
        assert body["accuracy"]["recall"] == 1.0

    def test_cloud_resources_and_health(self, warm_client):
        resources = warm_client.get("/api/cloud/resources").json()
        assert resources["total_resources"] > 0
        assert resources["estimated_monthly_cost"] > 0

        health = warm_client.get("/api/cloud").json()
        assert health["status"] in ("Healthy", "Warning", "Critical")
        assert 0 <= health["health_score"] <= 100

    def test_dashboard_aggregates_every_pipeline(self, warm_client):
        body = warm_client.get("/api/dashboard").json()
        assert all(p["ready"] for p in body["pipelines"])

        overview = body["overview"]
        assert overview["current_spend_mtd"] > 0
        assert overview["forecast_month_end"] > 0
        assert overview["total_security_events"] > 0
        assert overview["active_resources"] > 0
        assert body["insights"]

    def test_insights_carry_metric_signal_and_action(self, warm_client):
        """Spec section 25: every insight is metric + model signal + action."""
        insights = warm_client.get("/api/insights").json()["insights"]
        assert len(insights) >= 3

        for insight in insights:
            assert insight["metric"]
            assert insight["narrative"]
            assert insight["severity"] in ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")
            if insight["category"] in ("COST", "SECURITY"):
                assert insight["model_signal"], f"{insight['insight_id']} has no model signal"
                assert insight["action"], f"{insight['insight_id']} has no action"

    def test_insights_are_ordered_by_severity(self, warm_client):
        insights = warm_client.get("/api/insights").json()["insights"]
        rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
        ranks = [rank[i["severity"]] for i in insights]
        assert ranks == sorted(ranks)


class TestSecurityEventFiltering:
    def test_status_filter(self, warm_client):
        body = warm_client.get("/api/security/events?status=ANOMALY").json()
        assert body["events"]
        assert all(e["status"] == "ANOMALY" for e in body["events"])

    def test_min_score_filter(self, warm_client):
        body = warm_client.get("/api/security/events?min_score=90").json()
        assert all(e["anomaly_score"] >= 90 for e in body["events"])

    def test_pagination(self, warm_client):
        first = warm_client.get("/api/security/events?limit=5&offset=0").json()
        second = warm_client.get("/api/security/events?limit=5&offset=5").json()

        assert len(first["events"]) == 5
        ids_first = {e["event_id"] for e in first["events"]}
        ids_second = {e["event_id"] for e in second["events"]}
        assert not (ids_first & ids_second)

    def test_anomalies_sorted_by_score(self, warm_client):
        events = warm_client.get("/api/security/anomalies").json()["events"]
        scores = [e["anomaly_score"] for e in events]
        assert scores == sorted(scores, reverse=True)

    def test_event_detail(self, warm_client):
        events = warm_client.get("/api/security/anomalies?limit=1").json()["events"]
        event_id = events[0]["event_id"]

        detail = warm_client.get(f"/api/security/events/{event_id}").json()
        assert detail["event_id"] == event_id
        assert detail["deviations"]
        assert detail["metrics"]

    def test_unknown_event_is_404(self, warm_client):
        response = warm_client.get("/api/security/events/does-not-exist")
        assert response.status_code == 404
        assert response.json()["detail"]["error_code"] == "event_not_found"


class TestCloudResourceFiltering:
    def test_status_filter(self, warm_client):
        body = warm_client.get("/api/cloud/resources?status=running").json()
        assert all(r["status"] == "running" for r in body["resources"])

    def test_idle_filter(self, warm_client):
        body = warm_client.get("/api/cloud/resources?idle_only=true").json()
        assert body["resources"]
        assert all(r["idle"] for r in body["resources"])

    def test_search_matches_substrings(self, warm_client):
        all_resources = warm_client.get("/api/cloud/resources").json()["resources"]
        needle = all_resources[0]["instance_type"]

        body = warm_client.get(f"/api/cloud/resources?search={needle}").json()
        assert body["resources"]
        assert all(needle in r["instance_type"] for r in body["resources"])

    def test_search_with_no_match_returns_empty_not_error(self, warm_client):
        body = warm_client.get("/api/cloud/resources?search=zzzz-nothing").json()
        assert body["resources"] == []
        # Totals still describe the full inventory.
        assert body["total_resources"] > 0


class TestValidation:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/security/events?limit=0",
            "/api/security/events?limit=99999",
            "/api/security/events?offset=-1",
            "/api/security/events?min_score=200",
            "/api/security/events?status=BOGUS",
            "/api/tasks?limit=0",
        ],
    )
    def test_bad_query_params_are_422(self, warm_client, path):
        response = warm_client.get(path)
        assert response.status_code == 422

        body = response.json()
        assert body["error_code"] == "validation_error"
        assert body["errors"]

    def test_bad_request_body_is_422(self, client):
        response = client.post(
            "/api/costs/forecast/run", json={"horizon_days": 9999}
        )
        assert response.status_code == 422

    def test_unknown_body_field_is_rejected(self, client):
        response = client.post("/api/costs/forecast/run", json={"bogus_field": 1})
        assert response.status_code == 422

    def test_unknown_route_is_404(self, client):
        assert client.get("/api/does-not-exist").status_code == 404


class TestOpenApi:
    def test_schema_is_served_and_documents_every_route(self, client):
        schema = client.get("/openapi.json").json()
        paths = schema["paths"]

        for expected in [
            "/api/health", "/api/dashboard", "/api/dashboard/refresh", "/api/insights",
            "/api/costs", "/api/costs/history", "/api/costs/forecast",
            "/api/costs/forecast/run", "/api/security", "/api/security/events",
            "/api/security/anomalies", "/api/security/anomalies/run",
            "/api/cloud", "/api/cloud/resources", "/api/cloud/metrics",
            "/api/tasks/{task_id}",
        ]:
            assert expected in paths, f"{expected} missing from the OpenAPI schema"

    def test_docs_are_reachable(self, client):
        assert client.get("/docs").status_code == 200
