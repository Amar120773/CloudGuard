"""Redis-backed cache with a transparent in-process fallback.

Redis is the primary cache and Celery broker. If Redis is unreachable the
platform must still serve the dashboard (spec: "Redis failures" must degrade
gracefully), so every operation falls back to a bounded in-memory TTL map and
flips `degraded` so the API can surface an honest banner to the UI.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.config import settings
from app.connection_urls import redact_url
from app.logging_config import get_logger

logger = get_logger(__name__)


class Keys:
    """Canonical cache keys. Centralised so workers and API never drift."""

    CLOUD_RESOURCES = "cloudguard:cloud:resources"
    CLOUD_METRICS = "cloudguard:cloud:metrics"
    COST_HISTORY = "cloudguard:cost:history"
    COST_FORECAST = "cloudguard:cost:forecast"
    SECURITY_EVENTS = "cloudguard:security:events"
    SECURITY_ANOMALIES = "cloudguard:security:anomalies"
    TASK_PREFIX = "cloudguard:task:"
    TASK_INDEX = "cloudguard:tasks:index"
    LAST_SUCCESS_PREFIX = "cloudguard:lastsuccess:"
    RATE_LIMIT_PREFIX = "cloudguard:ratelimit:"

    @staticmethod
    def task(task_id: str) -> str:
        return Keys.TASK_PREFIX + task_id

    @staticmethod
    def last_success(pipeline: str) -> str:
        """Long-lived marker of a pipeline's last successful run.

        Outlives the payload's own TTL, which is what lets an expired cache still
        report when the data was last good.
        """
        return Keys.LAST_SUCCESS_PREFIX + pipeline

    @staticmethod
    def rate_limit(scope: str, identity: str) -> str:
        return f"{Keys.RATE_LIMIT_PREFIX}{scope}:{identity}"


class _MemoryCache:
    """Minimal thread-safe TTL map used when Redis is unavailable."""

    def __init__(self) -> None:
        self._data: Dict[str, Tuple[float, str]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires_at, payload = entry
            if expires_at and expires_at < time.time():
                self._data.pop(key, None)
                return None
            return payload

    def set(self, key: str, value: str, ttl: Optional[int]) -> None:
        with self._lock:
            self._data[key] = (time.time() + ttl if ttl else 0.0, value)

    def delete(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._data.pop(key, None)

    def ttl(self, key: str) -> int:
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                return -2
            expires_at, _ = entry
            if not expires_at:
                return -1
            return max(0, int(expires_at - time.time()))

    def keys(self, prefix: str) -> Iterable[str]:
        with self._lock:
            return [k for k in self._data if k.startswith(prefix)]


class CacheBackend:
    """Facade over Redis with automatic fallback and health reporting."""

    def __init__(self) -> None:
        self._memory = _MemoryCache()
        self._client = None
        self._available = False
        self._last_probe = 0.0
        self._probe_interval = 10.0
        self._warned = False
        self._lock = threading.RLock()

    def _connect(self):
        import redis

        return redis.from_url(
            settings.redis_url,
            socket_timeout=settings.redis_socket_timeout,
            socket_connect_timeout=settings.redis_connect_timeout,
            decode_responses=True,
            health_check_interval=30,
        )

    def _redis(self):
        """Return a live client, or None when Redis is down (re-probed lazily)."""
        if settings.standalone:
            return None  # no Redis by design: never connect, never warn
        with self._lock:
            now = time.time()
            if self._available and self._client is not None:
                return self._client
            if now - self._last_probe < self._probe_interval:
                return None
            self._last_probe = now
            try:
                client = self._connect()
                client.ping()
                self._client = client
                self._available = True
                self._warned = False
                # A managed REDIS_URL embeds the password; never log it raw.
                logger.info("Redis cache connected at %s", redact_url(settings.redis_url))
                return client
            except Exception as exc:  # pragma: no cover - environment dependent
                self._available = False
                self._client = None
                if not self._warned:
                    logger.warning(
                        "Redis unavailable (%s); falling back to in-process cache. "
                        "Caching is per-process until Redis returns.",
                        exc,
                    )
                    self._warned = True
                return None

    def _demote(self, exc: Exception) -> None:
        with self._lock:
            self._available = False
            self._client = None
        logger.warning("Redis operation failed (%s); using in-process cache", exc)

    @property
    def degraded(self) -> bool:
        """True when Redis is configured but reads/writes fall back to memory.

        Standalone mode serves from memory by design, so it is not degraded.
        """
        return not settings.standalone and self._redis() is None

    def health(self) -> Dict[str, Any]:
        if settings.standalone:
            return {"backend": "memory", "connected": False, "degraded": False, "mode": "standalone"}
        client = self._redis()
        if client is None:
            return {"backend": "memory", "connected": False, "degraded": True}
        try:
            start = time.perf_counter()
            client.ping()
            return {
                "backend": "redis",
                "connected": True,
                "degraded": False,
                "latency_ms": round((time.perf_counter() - start) * 1000, 2),
            }
        except Exception as exc:  # pragma: no cover - environment dependent
            self._demote(exc)
            return {"backend": "memory", "connected": False, "degraded": True}

    def get_json(self, key: str) -> Optional[Any]:
        raw = None
        client = self._redis()
        if client is not None:
            try:
                raw = client.get(key)
            except Exception as exc:  # pragma: no cover - environment dependent
                self._demote(exc)
        if raw is None:
            raw = self._memory.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            logger.error("Discarding corrupt cache payload at %s", key)
            self.delete(key)
            return None

    def set_json(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        payload = json.dumps(value, default=str)
        # Mirror into memory so a later Redis outage keeps serving warm data.
        self._memory.set(key, payload, ttl)
        client = self._redis()
        if client is not None:
            try:
                if ttl:
                    client.setex(key, ttl, payload)
                else:
                    client.set(key, payload)
            except Exception as exc:  # pragma: no cover - environment dependent
                self._demote(exc)

    def delete(self, *keys: str) -> None:
        self._memory.delete(*keys)
        client = self._redis()
        if client is not None and keys:
            try:
                client.delete(*keys)
            except Exception as exc:  # pragma: no cover - environment dependent
                self._demote(exc)

    def ttl(self, key: str) -> int:
        client = self._redis()
        if client is not None:
            try:
                return int(client.ttl(key))
            except Exception as exc:  # pragma: no cover - environment dependent
                self._demote(exc)
        return self._memory.ttl(key)

    def get_many_json(self, keys: List[str]) -> Dict[str, Any]:
        """Fetch several keys in one round-trip (MGET), falling back per key.

        Used by the task list, which previously issued one request per task id.
        """
        if not keys:
            return {}

        results: Dict[str, Any] = {}
        client = self._redis()
        if client is not None:
            try:
                for key, raw in zip(keys, client.mget(keys)):
                    if raw is None:
                        continue
                    try:
                        results[key] = json.loads(raw)
                    except (TypeError, ValueError):
                        logger.error("Discarding corrupt cache payload at %s", key)
                        self.delete(key)
            except Exception as exc:  # pragma: no cover - environment dependent
                self._demote(exc)
                results = {}

        # Fill anything Redis did not answer from the in-process mirror. Read it
        # directly rather than via get_json, which would re-probe Redis once per
        # key and reintroduce the N+1 this method exists to remove.
        for key in keys:
            if key in results:
                continue
            raw = self._memory.get(key)
            if raw is None:
                continue
            try:
                results[key] = json.loads(raw)
            except (TypeError, ValueError):
                logger.error("Discarding corrupt cache payload at %s", key)
                self._memory.delete(key)
        return results

    def mark_success(self, pipeline: str, when: Optional[str] = None) -> None:
        """Record that a pipeline just produced a good result."""
        from app.freshness import utcnow

        timestamp = when or utcnow().isoformat()
        self.set_json(Keys.last_success(pipeline), timestamp, settings.last_success_ttl)

    def last_success(self, pipeline: str) -> Optional[str]:
        return self.get_json(Keys.last_success(pipeline))

    def incr_with_ttl(self, key: str, ttl: int) -> int:
        """Atomic counter used for rate limiting; TTL is set on first increment."""
        client = self._redis()
        if client is not None:
            try:
                pipe = client.pipeline()
                pipe.incr(key)
                pipe.expire(key, ttl, nx=True)
                count, _ = pipe.execute()
                return int(count)
            except Exception as exc:  # pragma: no cover - environment dependent
                self._demote(exc)

        # In-process fallback: still bounded, just per-process.
        with self._lock:
            current = int(self._memory.get(key) or 0) + 1
            existing_ttl = self._memory.ttl(key)
            self._memory.set(
                key,
                str(current),
                ttl if existing_ttl in (-1, -2) else max(1, existing_ttl),
            )
            return current

    def push_task_index(self, task_id: str, limit: int = 100) -> None:
        client = self._redis()
        if client is not None:
            try:
                client.lpush(Keys.TASK_INDEX, task_id)
                client.ltrim(Keys.TASK_INDEX, 0, limit - 1)
                return
            except Exception as exc:  # pragma: no cover - environment dependent
                self._demote(exc)
        existing = self.get_json(Keys.TASK_INDEX) or []
        self.set_json(Keys.TASK_INDEX, ([task_id] + existing)[:limit], None)

    def recent_task_ids(self, limit: int = 25) -> List[str]:
        client = self._redis()
        if client is not None:
            try:
                return list(client.lrange(Keys.TASK_INDEX, 0, limit - 1))
            except Exception as exc:  # pragma: no cover - environment dependent
                self._demote(exc)
        return list(self.get_json(Keys.TASK_INDEX) or [])[:limit]

    def flush_namespace(self, prefix: str = "cloudguard:") -> int:
        removed = 0
        client = self._redis()
        if client is not None:
            try:
                for key in client.scan_iter(match=prefix + "*", count=200):
                    client.delete(key)
                    removed += 1
            except Exception as exc:  # pragma: no cover - environment dependent
                self._demote(exc)
        mem_keys = list(self._memory.keys(prefix))
        self._memory.delete(*mem_keys)
        return removed or len(mem_keys)


cache = CacheBackend()
