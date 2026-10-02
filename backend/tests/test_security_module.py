"""Security analytics: feature prep, IsolationForest, scoring, precision/recall."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from app.cloud import datagen
from app.ml.anomaly_detection import (
    FEATURE_LABELS,
    InsufficientDataError,
    MIN_RECORDS_FOR_DETECTION,
    SecurityAnomalyDetector,
    _scores_from_decisions,
    evaluate_detection,
    prepare_features,
)


class TestFeaturePreparation:
    def test_builds_a_standardised_matrix_over_the_expected_features(self, security_records):
        prepared, frame = prepare_features(security_records)

        assert prepared.feature_names == datagen.SECURITY_FEATURES
        assert prepared.matrix.shape == (len(security_records), 7)
        assert len(frame) == len(security_records)
        # Standardised: ~zero mean, ~unit variance per column.
        assert np.allclose(prepared.matrix.mean(axis=0), 0, atol=1e-6)
        assert np.allclose(prepared.matrix.std(axis=0), 1, atol=1e-6)

    def test_imputes_missing_values_with_the_median(self):
        records = _baseline_records(40)
        records[5]["traffic_volume"] = None
        records[6]["connection_count"] = float("nan")

        prepared, _ = prepare_features(records)
        assert prepared.imputed_cells == 2
        assert np.isfinite(prepared.matrix).all()
        assert "median-imputed 2 missing value(s)" in " ".join(prepared.notes)

    def test_drops_rows_with_no_usable_features(self):
        records = _baseline_records(40)
        for feature in datagen.SECURITY_FEATURES:
            records[3][feature] = None

        prepared, frame = prepare_features(records)
        assert prepared.rows_dropped == 1
        assert len(frame) == 39

    def test_rejects_a_batch_too_small_to_learn_a_baseline(self):
        with pytest.raises(InsufficientDataError, match="at least"):
            prepare_features(_baseline_records(MIN_RECORDS_FOR_DETECTION - 1))

    def test_rejects_records_with_no_known_features(self):
        records = [{"unrelated": i} for i in range(30)]
        with pytest.raises(InsufficientDataError, match="expected behavioural features"):
            prepare_features(records)

    def test_empty_input_raises(self):
        with pytest.raises(InsufficientDataError, match="No security records"):
            prepare_features([])


class TestDetection:
    @pytest.fixture(scope="class")
    def result(self, request):
        records = request.getfixturevalue("security_records")
        return SecurityAnomalyDetector().detect(records)

    def test_scores_every_record(self, result, security_records):
        assert result["total_events"] == len(security_records)
        assert len(result["events"]) == len(security_records)

    def test_scores_are_bounded_and_monotone_with_the_decision_function(self, result):
        for event in result["events"]:
            assert 0 <= event["anomaly_score"] <= 100
        # A more negative decision score must mean a higher anomaly score.
        pairs = sorted(
            (e["raw_decision_score"], e["anomaly_score"]) for e in result["events"]
        )
        scores = [score for _, score in pairs]
        assert scores == sorted(scores, reverse=True)

    def test_flags_only_a_small_minority(self, result):
        assert 0 < result["anomaly_count"] < result["total_events"] * 0.1
        assert result["anomaly_rate_pct"] < 10.0

    def test_finds_every_seeded_outlier(self, result):
        """Recall against the hidden ground truth must be perfect on the demo set."""
        accuracy = result["accuracy"]
        assert accuracy["labelled_anomalies"] == 4
        assert accuracy["recall"] == 1.0, "a seeded anomaly went undetected"
        assert accuracy["false_negatives"] == 0

    def test_precision_is_acceptable(self, result):
        assert result["accuracy"]["precision"] >= 0.8
        assert result["accuracy"]["f1_score"] >= 0.85

    def test_labels_come_from_the_dominant_feature_not_a_rule(self, result):
        """The event name is derived from feature attribution, not the seeded kind.

        This is what makes "the model found it" true rather than a lookup of the
        injected label.
        """
        for event in result["top_anomalies"]:
            dominant = event["deviations"][0]["feature"]
            expected = FEATURE_LABELS[dominant]["anomaly"]
            assert event["event_type"] == expected
            # And the attribution must actually be the largest deviation.
            magnitudes = [abs(d["z_score"]) for d in event["deviations"]]
            assert magnitudes == sorted(magnitudes, reverse=True)

    def test_anomalies_carry_context_and_an_action(self, result):
        for event in result["top_anomalies"]:
            assert event["context"]
            assert "standard deviations" in event["context"]
            assert event["recommended_action"]
            assert event["severity"] in ("MEDIUM", "HIGH", "CRITICAL")

    def test_routine_events_are_labelled_as_routine(self, result):
        routine = [e for e in result["events"] if e["status"] == "ROUTINE"]
        assert routine
        for event in routine[:20]:
            assert event["event_type"].startswith(("Normal", "Routine"))
            assert event["severity"] in ("INFO", "LOW")

    def test_health_score_drops_when_anomalies_are_present(self, result):
        assert 0 <= result["security_health_score"] < 100
        assert result["secure"] is False

    def test_is_deterministic(self, security_records):
        a = SecurityAnomalyDetector().detect(security_records)
        b = SecurityAnomalyDetector().detect(security_records)
        assert [e["anomaly_score"] for e in a["events"]] == [
            e["anomaly_score"] for e in b["events"]
        ]

    def test_ground_truth_never_reaches_the_model(self, security_records):
        """Scores must be identical whether or not the label is present."""
        stripped = [
            {k: v for k, v in record.items() if k not in ("simulated_anomaly", "simulated_kind")}
            for record in security_records
        ]
        with_labels = SecurityAnomalyDetector().detect(security_records)
        without_labels = SecurityAnomalyDetector().detect(stripped)

        assert [e["anomaly_score"] for e in with_labels["events"]] == [
            e["anomaly_score"] for e in without_labels["events"]
        ]
        # Without labels there is nothing to score the model against.
        assert without_labels["accuracy"]["labelled_anomalies"] == 0
        assert without_labels["accuracy"].get("precision") is None


class TestLiveInjection:
    def test_injected_outlier_is_detected(self):
        """Demo B: add an extreme event and the model must flag it."""
        baseline = datagen.generate_security_records(count=240)
        before = SecurityAnomalyDetector().detect(baseline)

        with_injection = datagen.generate_security_records(
            count=240, inject_extra_anomaly=True
        )
        after = SecurityAnomalyDetector().detect(with_injection)

        assert len(with_injection) == len(baseline) + 1
        assert after["anomaly_count"] == before["anomaly_count"] + 1
        assert after["accuracy"]["recall"] == 1.0

        injected = [e for e in after["events"] if e["event_id"].startswith("evt-live-")]
        assert len(injected) == 1
        assert injected[0]["status"] == "ANOMALY"
        assert injected[0]["anomaly_score"] > 90
        assert injected[0]["severity"] == "CRITICAL"


class TestScoreMapping:
    def test_is_monotone_decreasing_and_centred(self):
        decisions = np.array([-0.5, -0.1, 0.0, 0.1, 0.5])
        scores = _scores_from_decisions(decisions)

        assert list(scores) == sorted(scores, reverse=True)
        assert scores[2] == pytest.approx(50.0, abs=0.1)  # decision 0 -> score 50
        assert 0 <= scores.min() and scores.max() <= 100

    def test_saturates_without_overflow(self):
        scores = _scores_from_decisions(np.array([-50.0, 50.0]))
        assert scores[0] == pytest.approx(100.0, abs=0.01)
        assert scores[1] == pytest.approx(0.0, abs=0.01)


class TestEvaluation:
    def test_computes_precision_recall_f1(self):
        events = [
            {"status": "ANOMALY", "simulated_anomaly": True},   # TP
            {"status": "ANOMALY", "simulated_anomaly": True},   # TP
            {"status": "ANOMALY", "simulated_anomaly": False},  # FP
            {"status": "ROUTINE", "simulated_anomaly": True},   # FN
            {"status": "ROUTINE", "simulated_anomaly": False},  # TN
        ]
        accuracy = evaluate_detection(events)

        assert accuracy["true_positives"] == 2
        assert accuracy["false_positives"] == 1
        assert accuracy["false_negatives"] == 1
        # Values are rounded to 4dp before leaving the module.
        assert accuracy["precision"] == pytest.approx(2 / 3, abs=1e-4)
        assert accuracy["recall"] == pytest.approx(2 / 3, abs=1e-4)
        assert accuracy["f1_score"] == pytest.approx(2 / 3, abs=1e-4)

    def test_reports_honestly_when_there_is_no_ground_truth(self):
        accuracy = evaluate_detection([{"status": "ANOMALY", "simulated_anomaly": False}])
        assert accuracy["labelled_anomalies"] == 0
        assert accuracy.get("precision") is None
        assert "cannot be computed" in accuracy["note"]


def _baseline_records(count):
    """Uniform, anomaly-free records for prep-stage tests."""
    now = datetime.now(timezone.utc)
    return [
        {
            "event_id": f"evt-{i}",
            "timestamp": now,
            "traffic_volume": 1000.0 + i,
            "network_transfer": 20.0 + i,
            "connection_count": 90.0,
            "request_frequency": 140.0,
            "failed_api_requests": 2.0,
            "authentication_failures": 0.0,
            "distinct_source_ips": 20.0,
        }
        for i in range(count)
    ]
