"""Data freshness, computed from an explicit generation timestamp.

The previous implementation inferred age from Redis' *remaining* TTL
(``age = ttl_total - remaining``). That makes age mathematically bounded by the
TTL, so a ``age > ttl`` staleness test could never fire - the whole stale code
path was unreachable.

Freshness and expiry are now two separate concerns:

* **Redis TTL** decides how long a payload is served at all.
* **``generated_at``** decides how old it is allowed to be before the UI warns.

For ``stale`` to be reachable the staleness threshold must be *shorter* than the
TTL: the payload stays available for the full TTL, but once it passes the
threshold the dashboard says so instead of presenting old numbers as current.

Four states are distinguished, because they need different responses:

``fresh``        recent enough to trust
``stale``        still served, but old - show the last-known-good banner
``expired``      the TTL ran out, but this pipeline has succeeded before
``unavailable``  never produced a result, or its timestamp cannot be read
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.logging_config import get_logger

logger = get_logger(__name__)

FRESH = "fresh"
STALE = "stale"
EXPIRED = "expired"
UNAVAILABLE = "unavailable"

GENERATED_AT = "generated_at"


def utcnow() -> datetime:
    """Timezone-aware UTC now. Never use a naive datetime for freshness."""
    return datetime.now(timezone.utc)


def stamp(payload: Dict[str, Any], when: Optional[datetime] = None) -> Dict[str, Any]:
    """Record when a pipeline produced this payload, in UTC."""
    payload[GENERATED_AT] = (when or utcnow()).isoformat()
    return payload


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Read a stored timestamp, tolerating anything a cache might hand back.

    Returns None for missing or malformed values rather than raising - a
    corrupted timestamp must degrade to "unavailable", not crash the dashboard.
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # Python < 3.11 cannot parse the trailing "Z" that many writers emit.
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            logger.warning("Ignoring malformed generated_at value: %r", value)
            return None
    else:
        logger.warning("Ignoring generated_at of unexpected type %s", type(value).__name__)
        return None

    # A naive timestamp is assumed UTC; comparing naive to aware would raise.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_seconds(generated_at: Any, now: Optional[datetime] = None) -> Optional[int]:
    """Seconds since the payload was generated, or None if unknowable."""
    parsed = parse_timestamp(generated_at)
    if parsed is None:
        return None
    delta = (now or utcnow()) - parsed
    # Clock skew can make a timestamp look slightly future-dated.
    return max(0, int(delta.total_seconds()))


def describe(
    payload: Optional[Dict[str, Any]],
    threshold_seconds: int,
    *,
    last_success: Any = None,
    source: str = "cache",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build the DataFreshness body for a cached payload.

    `last_success` is the pipeline's independently recorded last success time. It
    is what separates "the cache expired" from "this never ran", which the UI
    needs in order to say *"last successful forecast: 10 minutes ago"*.
    """
    now = now or utcnow()

    if payload is None:
        last_age = age_seconds(last_success, now)
        if last_age is not None:
            return {
                "cached": False,
                "generated_at": parse_timestamp(last_success),
                "age_seconds": last_age,
                "stale": True,
                "state": EXPIRED,
                "source": "fallback",
            }
        return {
            "cached": False,
            "generated_at": None,
            "age_seconds": None,
            "stale": False,
            "state": UNAVAILABLE,
            "source": "fallback",
        }

    generated = payload.get(GENERATED_AT)
    age = age_seconds(generated, now)

    if age is None:
        # Present but undatable: do not claim it is fresh.
        return {
            "cached": True,
            "generated_at": None,
            "age_seconds": None,
            "stale": False,
            "state": UNAVAILABLE,
            "source": source,
        }

    is_stale = age > threshold_seconds
    return {
        "cached": True,
        "generated_at": parse_timestamp(generated),
        "age_seconds": age,
        "stale": is_stale,
        "state": STALE if is_stale else FRESH,
        "source": source,
    }


def threshold_for(ttl_seconds: int, fraction: float = 0.6) -> int:
    """Default staleness threshold derived from a cache TTL.

    Deliberately below the TTL so the stale window is reachable: the payload is
    still served for the remaining TTL, but the UI flags it as old.
    """
    return max(1, int(ttl_seconds * fraction))
