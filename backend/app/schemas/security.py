"""Security analytics schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import Field

from app.schemas.common import CloudGuardModel, DataFreshness, Severity

EventStatus = Literal["ROUTINE", "ANOMALY"]


class SecurityMetrics(CloudGuardModel):
    """Behavioural feature vector for one observation window."""

    traffic_volume: float = Field(ge=0, description="Bytes observed in the window")
    network_transfer: float = Field(ge=0, description="Megabytes transferred out")
    connection_count: float = Field(ge=0)
    request_frequency: float = Field(ge=0, description="Requests per minute")
    failed_api_requests: float = Field(ge=0)
    authentication_failures: float = Field(ge=0)
    distinct_source_ips: float = Field(ge=0)


class FeatureDeviation(CloudGuardModel):
    """Why the model found this record odd, in feature terms."""

    feature: str
    value: float
    baseline_mean: float
    z_score: float
    direction: Literal["above", "below"]


class SecurityEvent(CloudGuardModel):
    event_id: str
    timestamp: datetime
    event_type: str = Field(description="Human label, e.g. 'Network transfer spike'")
    source: str = Field(description="Originating resource, interface or principal")
    region: str = "us-east-1"
    metric: str = Field(description="Dominant metric for this event")
    value: float = Field(description="Value of the dominant metric")
    metrics: SecurityMetrics

    anomaly_score: float = Field(
        ge=0, le=100, description="0 = typical, 100 = extremely unusual"
    )
    raw_decision_score: Optional[float] = Field(
        None, description="Raw IsolationForest decision_function output"
    )
    status: EventStatus = "ROUTINE"
    severity: Severity = Severity.INFO
    deviations: List[FeatureDeviation] = Field(default_factory=list)
    context: Optional[str] = Field(None, description="Human-readable explanation")
    recommended_action: Optional[str] = None
    # Ground truth for evaluation only. Never fed to the model.
    simulated_anomaly: bool = Field(
        False, description="Seeded outlier, used to compute precision/recall"
    )


class AnomalyAccuracy(CloudGuardModel):
    """Spec section 24: precision/recall for anomaly detection."""

    precision: Optional[float] = Field(None, ge=0, le=1)
    recall: Optional[float] = Field(None, ge=0, le=1)
    f1_score: Optional[float] = Field(None, ge=0, le=1)
    true_positives: int = Field(0, ge=0)
    false_positives: int = Field(0, ge=0)
    false_negatives: int = Field(0, ge=0)
    labelled_anomalies: int = Field(0, ge=0)
    note: Optional[str] = None


class SecurityEventsResponse(CloudGuardModel):
    total_events: int = Field(0, ge=0)
    returned_events: int = Field(0, ge=0)
    anomaly_count: int = Field(0, ge=0)
    routine_count: int = Field(0, ge=0)
    events: List[SecurityEvent] = Field(default_factory=list)
    freshness: DataFreshness = Field(default_factory=DataFreshness)


class SecurityPostureResponse(CloudGuardModel):
    model_name: str = "IsolationForest"
    generated_at: Optional[datetime] = None
    secure: bool = True
    security_health_score: float = Field(
        100.0, ge=0, le=100, description="100 = clean, lowered by anomaly volume/severity"
    )
    total_events: int = Field(0, ge=0)
    anomaly_count: int = Field(0, ge=0)
    anomaly_rate_pct: float = Field(0.0, ge=0, le=100)
    severity_breakdown: Dict[str, int] = Field(default_factory=dict)
    top_anomalies: List[SecurityEvent] = Field(default_factory=list)
    recent_events: List[SecurityEvent] = Field(default_factory=list)
    features_used: List[str] = Field(default_factory=list)
    contamination: float = Field(0.0, ge=0, le=0.5)
    score_threshold: Optional[float] = Field(
        None,
        ge=0,
        le=100,
        description="Minimum 0-100 score this run required to classify an event as an "
        "anomaly (ANOMALY_SCORE_THRESHOLD). Null for payloads cached before it was recorded.",
    )
    preparation_notes: List[str] = Field(
        default_factory=list,
        description="Feature-cleaning actions taken (dropped rows, imputed values)",
    )
    accuracy: AnomalyAccuracy = Field(default_factory=AnomalyAccuracy)
    freshness: DataFreshness = Field(default_factory=DataFreshness)
