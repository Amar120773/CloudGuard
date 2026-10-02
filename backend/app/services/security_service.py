"""Security analytics pipeline and cached reads."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.cache import Keys, cache
from app.cloud.aws import get_provider
from app.config import settings
from app.freshness import describe as describe_freshness
from app.logging_config import get_logger
from app.ml.anomaly_detection import (
    InsufficientDataError,
    SecurityAnomalyDetector,
)
from app.schemas.security import SecurityEventsResponse, SecurityPostureResponse

logger = get_logger(__name__)

# The posture payload keeps a trimmed event feed; the full scored set lives
# under its own key so the events endpoint can paginate without re-scoring.
MAX_FEED_EVENTS = 60


def ingest_security_events(inject_anomaly: bool = False) -> Dict[str, Any]:
    """Read behavioural records out of CloudWatch Logs and cache them raw."""
    records = get_provider().get_security_records(inject_extra_anomaly=inject_anomaly)
    if not records:
        raise InsufficientDataError("No security records were returned by the cloud provider.")

    serialisable = [
        {
            **{k: v for k, v in record.items() if k != "timestamp"},
            "timestamp": record["timestamp"].isoformat()
            if isinstance(record["timestamp"], datetime)
            else record["timestamp"],
        }
        for record in records
    ]
    cache.set_json(f"{Keys.SECURITY_EVENTS}:raw", serialisable, settings.cache_ttl_security)
    logger.info("Ingested %d behavioural observation windows", len(records))
    return {"records": len(records), "injected_anomaly": inject_anomaly}


def load_raw_records(inject_anomaly: bool = False) -> List[Dict[str, Any]]:
    """Raw behavioural records, re-reading from the cloud if the cache is cold."""
    if not inject_anomaly:
        cached = cache.get_json(f"{Keys.SECURITY_EVENTS}:raw")
        if cached:
            return [_revive(record) for record in cached]
    return get_provider().get_security_records(inject_extra_anomaly=inject_anomaly)


def _revive(record: Dict[str, Any]) -> Dict[str, Any]:
    revived = dict(record)
    timestamp = revived.get("timestamp")
    if isinstance(timestamp, str):
        try:
            revived["timestamp"] = datetime.fromisoformat(timestamp)
        except ValueError:
            pass
    return revived


def run_anomaly_detection(
    contamination: Optional[float] = None,
    inject_anomaly: bool = False,
) -> Dict[str, Any]:
    """Fit IsolationForest over the behavioural windows and cache the posture."""
    records = load_raw_records(inject_anomaly=inject_anomaly)
    detector = SecurityAnomalyDetector(contamination=contamination)
    result = detector.detect(records)

    events = result.pop("events")

    posture = {
        **result,
        "recent_events": events[:MAX_FEED_EVENTS],
    }
    cache.set_json(Keys.SECURITY_ANOMALIES, posture, settings.cache_ttl_anomaly)
    cache.mark_success("security_anomalies", posture.get("generated_at"))
    cache.set_json(f"{Keys.SECURITY_EVENTS}:scored", events, settings.cache_ttl_anomaly)

    accuracy = result["accuracy"]
    logger.info(
        "Anomaly detection complete: %d/%d flagged (precision=%s recall=%s)",
        result["anomaly_count"],
        result["total_events"],
        accuracy.get("precision"),
        accuracy.get("recall"),
    )
    return posture


# --------------------------------------------------------------------------
# cached reads (request path)
# --------------------------------------------------------------------------
def get_cached_posture() -> Optional[Dict[str, Any]]:
    payload = cache.get_json(Keys.SECURITY_ANOMALIES)
    if payload is None:
        return None
    payload["freshness"] = posture_freshness(payload)
    return payload


def posture_freshness(payload):
    """Freshness of the security posture, from its generated_at stamp."""
    return describe_freshness(
        payload,
        settings.freshness_anomaly,
        last_success=cache.last_success("security_anomalies"),
    )


def posture_response() -> Optional[SecurityPostureResponse]:
    payload = get_cached_posture()
    return SecurityPostureResponse.model_validate(payload) if payload else None


def get_scored_events() -> Optional[List[Dict[str, Any]]]:
    return cache.get_json(f"{Keys.SECURITY_EVENTS}:scored")


def events_response(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    min_score: Optional[float] = None,
) -> Optional[SecurityEventsResponse]:
    """Filtered, paginated view over the scored event feed."""
    events = get_scored_events()
    if events is None:
        return None

    filtered = events
    if status:
        wanted = status.upper()
        filtered = [event for event in filtered if event.get("status") == wanted]
    if min_score is not None:
        filtered = [
            event for event in filtered if float(event.get("anomaly_score", 0)) >= min_score
        ]

    window = filtered[offset : offset + limit]
    return SecurityEventsResponse.model_validate(
        {
            "total_events": len(events),
            "returned_events": len(window),
            "anomaly_count": sum(1 for e in filtered if e.get("status") == "ANOMALY"),
            "routine_count": sum(1 for e in filtered if e.get("status") == "ROUTINE"),
            "events": window,
            "freshness": posture_freshness(cache.get_json(Keys.SECURITY_ANOMALIES)),
        }
    )


def get_event(event_id: str) -> Optional[Dict[str, Any]]:
    for event in get_scored_events() or []:
        if event.get("event_id") == event_id:
            return event
    return None


