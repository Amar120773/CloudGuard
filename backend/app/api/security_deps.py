"""API-key authentication and rate limiting for the expensive endpoints.

Only the endpoints that queue ML work or mutate state are protected. Read
endpoints stay open, which keeps the dashboard a drop-in demo while making it
impossible for an anonymous caller to queue unbounded Prophet fits.

Both dependencies degrade with the cache: when Redis is down the rate-limit
counter falls back to the in-process map, so the limit still applies per API
process rather than disappearing entirely.

Rate-limit buckets: a verified API key when auth is on, otherwise the client
address. That address is whatever uvicorn resolved: it honours X-Forwarded-For
only from peers listed in FORWARDED_ALLOW_IPS (exact IPs or CIDR ranges), so a
client can never choose its own bucket by sending the header itself.
"""
from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status

from app.cache import Keys, cache
from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

API_KEY_HEADER = "X-API-Key"


def auth_enabled() -> bool:
    """True when an API key is configured.

    With no key set the platform runs open, which is the documented local-demo
    default. Startup logs a warning so this is never a silent surprise.
    """
    return bool(settings.cloudguard_api_key)


def _key_matches(candidate: str) -> bool:
    """Constant-time comparison against the configured key.

    Compared as UTF-8 bytes: ``hmac.compare_digest`` raises TypeError for a
    non-ASCII str, and header values are client-controlled, so a crafted header
    must earn a 403 rather than a 500.
    """
    expected = settings.cloudguard_api_key
    return bool(expected) and hmac.compare_digest(
        candidate.encode("utf-8"), expected.encode("utf-8")
    )


async def require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias=API_KEY_HEADER),
) -> None:
    """Reject calls without the configured key.

    The comparison is constant-time, and neither the expected nor the supplied
    key is ever logged or echoed back in the response.
    """
    if not auth_enabled():
        return

    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "detail": "Missing API key.",
                "error_code": "api_key_required",
                "hint": f"Send the {API_KEY_HEADER} header.",
            },
            headers={"WWW-Authenticate": API_KEY_HEADER},
        )

    if not _key_matches(x_api_key):
        logger.warning("Rejected a request carrying an invalid API key")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "detail": "Invalid API key.",
                "error_code": "api_key_invalid",
                "hint": "Check the CLOUDGUARD_API_KEY value configured for this deployment.",
            },
        )


def client_ip(request: Request) -> str:
    """The caller's address, as resolved by uvicorn's proxy-header handling.

    The app deliberately never parses X-Forwarded-For itself: honouring it from
    an untrusted peer would let any caller pick its own rate-limit bucket.
    """
    client = request.client
    return client.host if client and client.host else "unknown"


def _client_identity(request: Request, api_key: Optional[str]) -> str:
    """Rate-limit bucket for this request.

    With auth on, the bucket is the API key, once verified. With auth off any
    key value is accepted, so letting it choose the bucket would hand every
    caller an unlimited supply of fresh buckets: rotate the header, never hit
    the limit. Unauthenticated traffic is bucketed by client address instead.
    """
    if api_key and auth_enabled() and _key_matches(api_key):
        # Never store the key itself; a short stable digest is enough to bucket.
        digest = hmac.new(b"cloudguard-rl", api_key.encode("utf-8"), "sha256").hexdigest()
        return "key:" + digest[:16]
    return "ip:" + client_ip(request)


async def rate_limit_writes(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias=API_KEY_HEADER),
) -> None:
    """Fixed-window limit on the endpoints that queue background work."""
    limit = settings.rate_limit_write_requests
    window = settings.rate_limit_window_seconds
    if limit <= 0:
        return  # explicitly disabled

    identity = _client_identity(request, x_api_key)
    # For confirming FORWARDED_ALLOW_IPS on a new platform (LOG_LEVEL=DEBUG):
    # with the default 127.0.0.1, `client` is the platform proxy's address.
    logger.debug(
        "Rate-limit bucket %s (client=%s, x-forwarded-for=%r)",
        identity,
        client_ip(request),
        request.headers.get("x-forwarded-for"),
    )
    key = Keys.rate_limit("write", identity)
    try:
        count = cache.incr_with_ttl(key, window)
    except Exception as exc:  # pragma: no cover - never fail open on a bug
        logger.warning("Rate-limit counter unavailable (%s); allowing the request", exc)
        return

    if count > limit:
        logger.warning("Rate limit exceeded for %s (%d > %d)", key, count, limit)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "detail": (
                    f"Rate limit exceeded: {limit} request(s) per {window}s for "
                    "endpoints that queue background work."
                ),
                "error_code": "rate_limited",
                "hint": "Wait for the window to reset, or poll the existing task instead.",
            },
            headers={"Retry-After": str(window)},
        )


# Applied to every endpoint that starts background work.
PROTECTED = [Depends(require_api_key), Depends(rate_limit_writes)]


def forwarded_allow_ips() -> str:
    """The proxy allowlist uvicorn reads from the environment at startup.

    uvicorn takes it from FORWARDED_ALLOW_IPS (default 127.0.0.1); the image's
    start command passes no --forwarded-allow-ips flag, so the variable is the
    one source of truth.
    """
    return os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1").strip()


def check_proxy_trust() -> bool:
    """Log how client addresses are resolved. False when the setting is unsafe."""
    trusted = forwarded_allow_ips()
    if trusted == "*":
        logger.warning(
            "FORWARDED_ALLOW_IPS='*' trusts X-Forwarded-For from every peer. uvicorn "
            "then uses the left-most entry, which the client writes itself, so any "
            "caller can pick its own address and sidestep per-IP rate limits. List "
            "your platform proxy's addresses or CIDR ranges instead."
        )
        return False
    logger.info("X-Forwarded-For is honoured only from peers in FORWARDED_ALLOW_IPS=%s", trusted)
    return True
