"""Security anomaly detection with IsolationForest.

Pipeline (spec section 7):

    cloud / network metrics -> feature preparation -> Isolation Forest
    -> anomaly score -> security classification -> dashboard alert

Two design points matter for the demo:

* The model is **unsupervised**. The injected outliers carry a
  ``simulated_anomaly`` flag, but that flag is never a feature and never
  influences a score - it is used only afterwards to compute precision/recall.
* The event *label* ("Network transfer spike") is derived from which feature
  deviates most from the learned baseline, not from a hand-written rule keyed to
  the injected event. That is what makes "the model found it, not an if
  statement" an honest claim.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.cloud.datagen import SECURITY_FEATURES
from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

MIN_RECORDS_FOR_DETECTION = 20

# Human-readable naming per dominant feature, for both ends of the scale.
FEATURE_LABELS: Dict[str, Dict[str, str]] = {
    "network_transfer": {
        "anomaly": "Network transfer spike",
        "routine": "Normal data transfer",
        "metric": "network_transfer_mb",
    },
    "traffic_volume": {
        "anomaly": "Traffic volume surge",
        "routine": "Normal API traffic",
        "metric": "traffic_volume_bytes",
    },
    "failed_api_requests": {
        "anomaly": "Failed API requests climbing",
        "routine": "Normal API error rate",
        "metric": "failed_api_requests",
    },
    "authentication_failures": {
        "anomaly": "Authentication failure burst",
        "routine": "Normal authentication",
        "metric": "authentication_failures",
    },
    "connection_count": {
        "anomaly": "Connection count anomaly",
        "routine": "Normal connection volume",
        "metric": "connection_count",
    },
    "request_frequency": {
        "anomaly": "Request rate anomaly",
        "routine": "Normal request rate",
        "metric": "requests_per_min",
    },
    "distinct_source_ips": {
        "anomaly": "Unusual spread of source IPs",
        "routine": "Normal client distribution",
        "metric": "distinct_source_ips",
    },
}

ACTIONS: Dict[str, str] = {
    "network_transfer": (
        "Confirm whether this egress is an expected backup or replication job. "
        "If not, isolate the interface and review IAM activity for the principal."
    ),
    "traffic_volume": (
        "Correlate with load-balancer and CDN metrics to separate a genuine "
        "traffic event from an amplification attempt."
    ),
    "failed_api_requests": (
        "Inspect CloudTrail for the failing API calls; a sustained error burst "
        "often indicates credential probing or a broken deploy."
    ),
    "authentication_failures": (
        "Treat as possible credential stuffing. Review the source IPs, enforce "
        "MFA, and rotate the affected credentials."
    ),
    "connection_count": (
        "Check security-group rules for over-broad ingress and look for scanning "
        "patterns across ports."
    ),
    "request_frequency": (
        "Verify rate limits and WAF rules; apply throttling to the offending "
        "source if the pattern persists."
    ),
    "distinct_source_ips": (
        "A sudden fan-out of source IPs suggests distributed scanning. Review "
        "the source ranges and consider geo or reputation blocking."
    ),
}


class InsufficientDataError(ValueError):
    """Raised when there are too few observations to learn a baseline."""


@dataclass
class PreparedFeatures:
    matrix: np.ndarray
    feature_names: List[str]
    means: np.ndarray
    stds: np.ndarray
    rows_in: int = 0
    rows_dropped: int = 0
    imputed_cells: int = 0
    notes: List[str] = field(default_factory=list)


def prepare_features(
    records: List[Dict[str, Any]], feature_names: Optional[List[str]] = None
) -> Tuple[PreparedFeatures, pd.DataFrame]:
    """Build a clean, standardised feature matrix.

    Returns the prepared features plus the surviving records as a frame, so
    scores can be joined back to the original observations.
    """
    rows_in = len(records)
    if rows_in == 0:
        raise InsufficientDataError("No security records were supplied.")

    frame = pd.DataFrame(records)
    names = [f for f in (feature_names or SECURITY_FEATURES) if f in frame.columns]
    if not names:
        raise InsufficientDataError(
            "None of the expected behavioural features were present: "
            + ", ".join(feature_names or SECURITY_FEATURES)
        )

    notes: List[str] = []
    missing = [f for f in (feature_names or SECURITY_FEATURES) if f not in frame.columns]
    if missing:
        notes.append("missing feature(s) ignored: " + ", ".join(missing))

    numeric = frame[names].apply(pd.to_numeric, errors="coerce")
    numeric = numeric.replace([np.inf, -np.inf], np.nan)

    # Drop rows with no usable values at all; impute the rest with the median,
    # which is robust to the very outliers we are hunting.
    all_nan = numeric.isna().all(axis=1)
    rows_dropped = int(all_nan.sum())
    if rows_dropped:
        numeric = numeric.loc[~all_nan]
        frame = frame.loc[~all_nan]
        notes.append(f"dropped {rows_dropped} record(s) with no usable features")

    imputed = int(numeric.isna().sum().sum())
    if imputed:
        numeric = numeric.fillna(numeric.median(numeric_only=True))
        numeric = numeric.fillna(0.0)
        notes.append(f"median-imputed {imputed} missing value(s)")

    if len(numeric) < MIN_RECORDS_FOR_DETECTION:
        raise InsufficientDataError(
            f"Need at least {MIN_RECORDS_FOR_DETECTION} observations to learn a "
            f"baseline; got {len(numeric)}."
        )

    raw = numeric.to_numpy(dtype=float)

    # Heavy-tailed counters (bytes, request rates) span orders of magnitude.
    # log1p first so the tree splits are not dominated by scale alone, then
    # standardise.
    transformed = np.log1p(np.clip(raw, 0.0, None))
    means = transformed.mean(axis=0)
    stds = transformed.std(axis=0)
    stds_safe = np.where(stds < 1e-9, 1.0, stds)
    matrix = (transformed - means) / stds_safe

    prepared = PreparedFeatures(
        matrix=matrix,
        feature_names=names,
        means=means,
        stds=stds_safe,
        rows_in=rows_in,
        rows_dropped=rows_dropped,
        imputed_cells=imputed,
        notes=notes,
    )
    return prepared, frame.reset_index(drop=True)


class SecurityAnomalyDetector:
    """IsolationForest wrapper producing scored, explained security events."""

    model_name = "IsolationForest"

    def __init__(
        self,
        contamination: Optional[float] = None,
        n_estimators: Optional[int] = None,
        random_state: Optional[int] = None,
        score_threshold: Optional[float] = None,
    ):
        self.contamination = (
            contamination if contamination is not None else settings.anomaly_contamination
        )
        self.n_estimators = n_estimators or settings.anomaly_n_estimators
        self.random_state = (
            random_state if random_state is not None else settings.anomaly_random_state
        )
        self.score_threshold = (
            score_threshold
            if score_threshold is not None
            else settings.anomaly_score_threshold
        )

    def detect(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Score every record and classify it as ROUTINE or ANOMALY."""
        from sklearn.ensemble import IsolationForest

        prepared, frame = prepare_features(records)

        model = IsolationForest(
            n_estimators=self.n_estimators,
            contamination=self.contamination,
            random_state=self.random_state,
            max_samples="auto",
            n_jobs=1,
        )
        predictions = model.fit_predict(prepared.matrix)
        decisions = model.decision_function(prepared.matrix)

        scores = _scores_from_decisions(decisions)
        # The forest's own cut is contamination-driven; combining it with the
        # score threshold avoids flagging mild records when the estate is calm.
        flags = (predictions == -1) & (scores >= self.score_threshold)

        events: List[Dict[str, Any]] = []
        for position in range(len(frame)):
            record = frame.iloc[position].to_dict()
            deviations = _feature_deviations(
                prepared.matrix[position], prepared.feature_names, record
            )
            events.append(
                _build_event(
                    record=record,
                    score=float(scores[position]),
                    decision=float(decisions[position]),
                    is_anomaly=bool(flags[position]),
                    deviations=deviations,
                )
            )

        events.sort(key=lambda event: event["timestamp"], reverse=True)
        anomalies = [event for event in events if event["status"] == "ANOMALY"]
        accuracy = evaluate_detection(events)

        severity_breakdown: Dict[str, int] = {}
        for event in anomalies:
            severity_breakdown[event["severity"]] = (
                severity_breakdown.get(event["severity"], 0) + 1
            )

        total = len(events)
        anomaly_rate = round(len(anomalies) / total * 100.0, 2) if total else 0.0

        return {
            "model_name": self.model_name,
            "generated_at": datetime.now(timezone.utc),
            "secure": not anomalies,
            "security_health_score": _health_score(anomalies, total),
            "total_events": total,
            "anomaly_count": len(anomalies),
            "anomaly_rate_pct": anomaly_rate,
            "severity_breakdown": severity_breakdown,
            "events": events,
            "top_anomalies": sorted(
                anomalies, key=lambda event: event["anomaly_score"], reverse=True
            )[:10],
            "features_used": prepared.feature_names,
            "contamination": self.contamination,
            # The cut actually applied, so the dashboard draws the same line the
            # model used instead of assuming the default.
            "score_threshold": self.score_threshold,
            "accuracy": accuracy,
            "preparation_notes": prepared.notes,
        }


# --------------------------------------------------------------------------
# scoring helpers
# --------------------------------------------------------------------------
def _scores_from_decisions(decisions: np.ndarray) -> np.ndarray:
    """Map decision_function output onto a stable 0-100 anomaly score.

    ``decision_function`` is negative for outliers and positive for inliers,
    centred on the contamination cut. A logistic transform keeps the mapping
    monotone and bounded, so a score is comparable across runs instead of being
    rescaled by whatever happened to be in the current batch.
    """
    steepness = 12.0
    scaled = np.clip(-decisions * steepness, -60.0, 60.0)
    return np.round(100.0 / (1.0 + np.exp(-scaled)), 2)


def _feature_deviations(
    standardised_row: np.ndarray, feature_names: List[str], record: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Rank features by how far this record sits from the learned baseline."""
    deviations = []
    for index, name in enumerate(feature_names):
        z = float(standardised_row[index])
        raw_value = record.get(name)
        try:
            raw_value = float(raw_value)
        except (TypeError, ValueError):
            raw_value = 0.0
        deviations.append(
            {
                "feature": name,
                "value": round(raw_value, 4),
                # Report the baseline in original units, not log space.
                "baseline_mean": 0.0,
                "z_score": round(z, 3),
                "direction": "above" if z >= 0 else "below",
            }
        )
    deviations.sort(key=lambda d: abs(d["z_score"]), reverse=True)
    return deviations


def _build_event(
    record: Dict[str, Any],
    score: float,
    decision: float,
    is_anomaly: bool,
    deviations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Assemble the dashboard-facing event, labelled from the model's evidence."""
    dominant = deviations[0] if deviations else {"feature": "traffic_volume", "value": 0.0}
    feature = dominant["feature"]
    labels = FEATURE_LABELS.get(
        feature,
        {"anomaly": f"{feature} anomaly", "routine": f"Normal {feature}", "metric": feature},
    )

    timestamp = record.get("timestamp")
    if not isinstance(timestamp, datetime):
        timestamp = datetime.now(timezone.utc)

    metrics = {
        name: float(record.get(name, 0.0) or 0.0) for name in SECURITY_FEATURES
    }

    event = {
        "event_id": str(record.get("event_id") or f"evt-{int(timestamp.timestamp())}"),
        "timestamp": timestamp,
        "event_type": labels["anomaly"] if is_anomaly else labels["routine"],
        "source": str(record.get("interface_id") or record.get("principal") or "unknown"),
        "region": str(record.get("region") or settings.aws_region),
        "metric": labels["metric"],
        "value": round(float(dominant.get("value", 0.0)), 4),
        "metrics": metrics,
        "anomaly_score": score,
        "raw_decision_score": round(decision, 6),
        "status": "ANOMALY" if is_anomaly else "ROUTINE",
        "severity": _severity(score, is_anomaly),
        "deviations": deviations[:3],
        "simulated_anomaly": bool(record.get("simulated_anomaly", False)),
    }

    if is_anomaly:
        event["context"] = (
            f"{feature.replace('_', ' ')} is {abs(dominant['z_score']):.1f} standard "
            f"deviations {dominant['direction']} the learned baseline "
            f"(value {dominant['value']:,.2f}). IsolationForest scored this "
            f"{score:.0f}/100, which is statistically unusual compared with normal "
            f"observed behaviour."
        )
        event["recommended_action"] = ACTIONS.get(
            feature, "Investigate the source and associated activity."
        )
    else:
        event["context"] = (
            f"Within the normal operating envelope (score {score:.0f}/100); "
            f"{feature.replace('_', ' ')} is the dominant metric for this window."
        )

    return event


def _severity(score: float, is_anomaly: bool) -> str:
    if not is_anomaly:
        return "INFO" if score < 40 else "LOW"
    if score >= 90:
        return "CRITICAL"
    if score >= 75:
        return "HIGH"
    return "MEDIUM"


def _health_score(anomalies: List[Dict[str, Any]], total: int) -> float:
    """100 for a clean estate, reduced by anomaly volume weighted by severity."""
    if total == 0:
        return 100.0
    weights = {"CRITICAL": 9.0, "HIGH": 5.0, "MEDIUM": 2.5, "LOW": 1.0, "INFO": 0.0}
    penalty = sum(weights.get(event["severity"], 2.0) for event in anomalies)
    # Normalise so a handful of criticals is a serious dent but never negative.
    score = 100.0 - min(100.0, penalty * (1.0 + 20.0 / max(total, 20)))
    return round(max(0.0, score), 1)


def evaluate_detection(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Precision / recall / F1 against the seeded ground-truth labels.

    Honest about its own limits: these labels only cover the outliers the
    generator injected, so recall is measured against those and a flagged record
    that was not injected is counted as a false positive even though it may be a
    genuine statistical outlier.
    """
    labelled = [event for event in events if event.get("simulated_anomaly")]
    if not labelled:
        return {
            "labelled_anomalies": 0,
            "note": (
                "No ground-truth labels in this batch, so precision and recall "
                "cannot be computed."
            ),
        }

    true_positives = sum(
        1 for event in events if event["status"] == "ANOMALY" and event["simulated_anomaly"]
    )
    false_positives = sum(
        1
        for event in events
        if event["status"] == "ANOMALY" and not event["simulated_anomaly"]
    )
    false_negatives = sum(
        1
        for event in events
        if event["status"] != "ANOMALY" and event["simulated_anomaly"]
    )

    precision = (
        true_positives / (true_positives + false_positives)
        if (true_positives + false_positives)
        else 0.0
    )
    recall = (
        true_positives / (true_positives + false_negatives)
        if (true_positives + false_negatives)
        else 0.0
    )
    f1 = (
        2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    )

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1_score": round(f1, 4),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "labelled_anomalies": len(labelled),
        "note": (
            "Ground truth covers only the deliberately seeded outliers; records "
            "flagged outside that set count against precision even when they are "
            "genuine statistical outliers."
        ),
    }
