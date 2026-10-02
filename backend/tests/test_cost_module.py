"""Cost intelligence: cleaning, aggregation, Prophet, projection, accuracy."""
from __future__ import annotations

import calendar
from datetime import date, timedelta

import pytest

from app.ml.forecasting import (
    CostForecaster,
    InsufficientDataError,
    MIN_DAYS_FOR_FORECAST,
    TrendSeasonalEngine,
    analyse_services,
    prepare_series,
    project_month_end,
)


# ==========================================================================
# Stage 1-3: cleaning and aggregation
# ==========================================================================
class TestPrepareSeries:
    def test_aggregates_services_into_daily_totals(self, cost_records):
        series = prepare_series(cost_records)
        # 8 services x 118 populated days collapse to one row per calendar day.
        assert series.days == 120
        assert series.rows_in == len(cost_records)
        assert list(series.frame.columns) == ["ds", "y"]

    def test_interpolates_missing_days(self, cost_records):
        """The generator leaves two deliberate gaps, as Cost Explorer does."""
        series = prepare_series(cost_records)
        assert series.gaps_filled == 2
        # The calendar is continuous after reindexing.
        gaps = series.frame["ds"].diff().dropna().dt.days.unique()
        assert set(gaps) == {1}
        assert "interpolated 2 missing day(s)" in " ".join(series.notes())

    def test_drops_invalid_rows_but_keeps_the_rest(self):
        records = [
            {"date": f"2026-01-{day:02d}", "service": "EC2", "cost": 10.0}
            for day in range(1, 21)
        ]
        records += [
            {"date": "not-a-date", "service": "EC2", "cost": 10.0},
            {"date": "2026-01-10", "service": "EC2", "cost": "abc"},
            {"date": "2026-01-11", "service": "EC2", "cost": float("inf")},
            {"date": "2026-01-12", "service": "EC2", "cost": None},
        ]
        series = prepare_series(records)
        assert series.rows_dropped == 4
        assert series.days == 20
        assert "dropped 4 invalid record(s)" in " ".join(series.notes())

    def test_floors_negative_costs(self):
        """Refunds appear as negative spend; they must not drag the fit below zero."""
        records = [
            {"date": f"2026-01-{day:02d}", "service": "EC2", "cost": -5.0 if day == 3 else 10.0}
            for day in range(1, 16)
        ]
        series = prepare_series(records)
        assert (series.frame["y"] >= 0).all()

    def test_empty_input_raises(self):
        with pytest.raises(InsufficientDataError, match="No cost records"):
            prepare_series([])

    def test_all_invalid_raises(self):
        with pytest.raises(InsufficientDataError, match="invalid after cleaning"):
            prepare_series([{"date": "bad", "service": "x", "cost": "bad"}])

    def test_missing_required_fields_raises(self):
        with pytest.raises(InsufficientDataError, match="must contain"):
            prepare_series([{"service": "EC2", "amount": 1.0}])


# ==========================================================================
# Stage 4-6: forecasting
# ==========================================================================
class TestForecast:
    @pytest.fixture(scope="class")
    def result(self, request):
        records = request.getfixturevalue("cost_records")
        return CostForecaster(horizon_days=30).forecast(records)

    def test_uses_prophet(self, result):
        """Prophet is the primary engine; a fallback here signals a broken Stan."""
        assert result["model_name"] == "prophet", (
            f"Expected Prophet, got {result['model_name']}: {result['model_detail']}"
        )

    def test_returns_full_horizon_with_ordered_future_dates(self, result):
        points = result["forecast"]
        assert len(points) == 30
        dates = [p["date"] for p in points]
        assert dates == sorted(dates)
        assert dates[0] > date.today() - timedelta(days=1)

    def test_bounds_bracket_the_prediction_and_stay_non_negative(self, result):
        for point in result["forecast"]:
            assert point["lower_bound"] <= point["predicted_cost"] <= point["upper_bound"]
            assert point["lower_bound"] >= 0

    def test_month_end_projection_sits_inside_its_interval(self, result):
        assert (
            result["projected_month_end_lower"]
            <= result["projected_month_end"]
            <= result["projected_month_end_upper"]
        )
        # Projection must be at least the spend already booked this month.
        assert result["projected_month_end"] >= result["month_to_date"]

    def test_horizon_totals_are_consistent(self, result):
        assert result["next_7_days_total"] == pytest.approx(
            sum(p["predicted_cost"] for p in result["forecast"][:7]), abs=0.05
        )
        assert result["next_30_days_total"] >= result["next_7_days_total"]

    def test_reports_trend_direction(self, result):
        assert result["trend_direction"] in ("increasing", "stable", "decreasing")
        if result["trend_pct"] >= 3.0:
            assert result["trend_direction"] == "increasing"

    def test_budget_analysis_is_internally_consistent(self, result):
        budget = result["budget"]
        assert budget["headroom"] == pytest.approx(
            budget["monthly_budget"] - budget["projected_month_end"], abs=0.02
        )
        assert budget["budget_breach_expected"] == (
            budget["projected_month_end"] > budget["monthly_budget"]
        )

    def test_is_deterministic(self, cost_records):
        """Seeded data must reproduce, or the demo is not repeatable."""
        a = CostForecaster(horizon_days=14).forecast(cost_records, run_backtest=False)
        b = CostForecaster(horizon_days=14).forecast(cost_records, run_backtest=False)
        assert a["projected_month_end"] == pytest.approx(b["projected_month_end"], rel=1e-6)

    def test_rejects_too_little_history(self):
        records = [
            {"date": f"2026-01-{day:02d}", "service": "EC2", "cost": 10.0}
            for day in range(1, MIN_DAYS_FOR_FORECAST)
        ]
        with pytest.raises(InsufficientDataError, match="at least"):
            CostForecaster().forecast(records)


# ==========================================================================
# Stage 7-8: accuracy and risk
# ==========================================================================
class TestAccuracy:
    def test_backtest_reports_mae_and_beats_the_naive_baseline(self, cost_records):
        result = CostForecaster(horizon_days=30).forecast(cost_records)
        accuracy = result["accuracy"]

        assert accuracy["backtest_days"] == 14
        assert accuracy["mae"] > 0
        assert accuracy["rmse"] >= accuracy["mae"]  # RMSE >= MAE always
        assert accuracy["mape"] < 10.0
        # The model is tuned to beat "same weekday last week".
        assert accuracy["skill_vs_baseline_pct"] > 0, (
            f"Prophet MAE {accuracy['mae']} did not beat baseline {accuracy['baseline_mae']}"
        )

    def test_backtest_is_skipped_when_history_is_short(self):
        records = [
            {"date": (date(2026, 1, 1) + timedelta(days=i)).isoformat(),
             "service": "EC2", "cost": 10.0 + i}
            for i in range(20)
        ]
        result = CostForecaster(horizon_days=7).forecast(records)
        assert result["accuracy"]["backtest_days"] == 0
        assert "Not enough history" in result["accuracy"]["note"]


class TestServiceAnalysis:
    def test_shares_sum_to_100(self, cost_records):
        rows = analyse_services(cost_records, projected_month_end=None, last_actual=date.today())
        assert sum(r["share_pct"] for r in rows) == pytest.approx(100.0, abs=0.5)

    def test_projection_allocates_the_whole_aggregate(self, cost_records):
        """Per-service projections must reconcile with the aggregate forecast."""
        rows = analyse_services(cost_records, projected_month_end=5000.0, last_actual=date.today())
        assert sum(r["projected_month_end"] for r in rows) == pytest.approx(5000.0, abs=1.0)

    def test_projection_is_null_without_a_forecast(self, cost_records):
        rows = analyse_services(cost_records, projected_month_end=None, last_actual=date.today())
        assert all(r["projected_month_end"] is None for r in rows)

    def test_flags_fast_growing_services_as_risk(self, cost_records):
        rows = analyse_services(cost_records, projected_month_end=5000.0, last_actual=date.today())
        risky = [r for r in rows if r["is_risk"]]
        assert risky, "expected at least one service flagged as a cost risk"
        # Risk must be justified by growth or dominance, not assigned arbitrarily.
        for row in risky:
            assert row["trend_pct"] >= 15.0 or row["share_pct"] >= 25.0

    def test_handles_empty_input(self):
        assert analyse_services([], projected_month_end=100.0, last_actual=date.today()) == []


class TestMonthEndProjection:
    def test_adds_remaining_days_to_month_to_date(self):
        import pandas as pd

        today = date(2026, 3, 10)
        frame = pd.DataFrame(
            {
                "ds": pd.date_range("2026-03-01", periods=10, freq="D"),
                "y": [100.0] * 10,
            }
        )
        points = [
            {
                "date": today + timedelta(days=i),
                "predicted_cost": 100.0,
                "lower_bound": 90.0,
                "upper_bound": 110.0,
            }
            for i in range(1, 31)
        ]
        projection = project_month_end(frame, points, today)

        days_in_month = calendar.monthrange(2026, 3)[1]  # 31
        remaining = days_in_month - 10
        assert projection["month_to_date"] == pytest.approx(1000.0)
        assert projection["projected_month_end"] == pytest.approx(1000.0 + remaining * 100.0)
        assert projection["days_remaining"] == remaining


class TestFallbackEngine:
    """The fallback must satisfy the same contract when Prophet is unavailable."""

    def test_produces_a_valid_ordered_forecast(self, cost_records):
        series = prepare_series(cost_records)
        tail = TrendSeasonalEngine().fit_predict(series.frame, 20)

        assert len(tail) == 20
        assert (tail["yhat"] >= 0).all()
        assert (tail["yhat_lower"] <= tail["yhat"]).all()
        assert (tail["yhat"] <= tail["yhat_upper"]).all()
        assert tail["ds"].is_monotonic_increasing

    def test_is_selected_when_prophet_raises(self, cost_records, monkeypatch):
        from app.ml import forecasting

        def explode(self, frame, horizon):
            raise RuntimeError("stan backend unavailable")

        monkeypatch.setattr(forecasting.ProphetEngine, "fit_predict", explode)
        result = CostForecaster(horizon_days=10).forecast(cost_records, run_backtest=False)

        assert result["model_name"] == "trend_seasonal_fallback"
        assert "Prophet was unavailable" in result["model_detail"]
        assert len(result["forecast"]) == 10

    def test_raises_when_every_engine_fails(self, cost_records, monkeypatch):
        from app.ml import forecasting

        def explode(self, frame, horizon):
            raise RuntimeError("boom")

        monkeypatch.setattr(forecasting.ProphetEngine, "fit_predict", explode)
        monkeypatch.setattr(forecasting.TrendSeasonalEngine, "fit_predict", explode)
        with pytest.raises(forecasting.ForecastError, match="All forecasting engines failed"):
            CostForecaster(horizon_days=5).forecast(cost_records, run_backtest=False)
