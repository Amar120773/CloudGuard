"""Background task submission and status tracking.

Celery + Redis is the primary execution path. Two things make this layer more
than a thin wrapper:

* **A durable task registry.** Celery's own result backend expires, and a
  ``PENDING`` state from Celery is ambiguous (unknown id and not-yet-started look
  identical). Every task therefore also gets a record in the cache, so
  ``GET /api/tasks/{id}`` can answer precisely, including after a worker restart.
* **An inline fallback.** If the broker is unreachable, work runs on a local
  thread pool instead of failing the request. The dashboard keeps working on a
  laptop with no Redis, and the response says which executor ran it rather than
  pretending Celery was involved. Only an outage qualifies: if the broker is
  up but Celery refuses the submission (a bad TLS option, wrong credentials),
  `TaskDispatchError` is raised instead, because quietly doing the work on the
  web process would hide a broken deployment behind a successful response.
"""
from __future__ import annotations

import socket
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from app.cache import Keys, cache
from app.config import settings
from app.connection_urls import redact_url, scrub_credentials
from app.logging_config import get_logger
from app.schemas.task import TaskStatus, TaskType

logger = get_logger(__name__)


class TaskDispatchError(RuntimeError):
    """Celery refused a submission while its broker was reachable.

    That is a configuration fault, not an outage, so it is reported to the
    caller (503) rather than absorbed by the inline fallback.
    """


def _is_connection_error(exc: BaseException) -> bool:
    """True for the errors a broker outage produces."""
    from kombu.exceptions import OperationalError
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import TimeoutError as RedisTimeoutError

    # OSError covers ConnectionError, TimeoutError and raw socket failures.
    return isinstance(exc, (OSError, OperationalError, RedisConnectionError, RedisTimeoutError))

# Bounded so a burst of submissions cannot exhaust the API process. Created
# lazily and replaced after a shutdown, so the module stays usable if the app is
# restarted inside the same process (which is exactly what the test suite does).
_INLINE_WORKERS = 4
_inline_pool: Optional[ThreadPoolExecutor] = None
_inline_futures: "set[Future]" = set()
_lock = threading.RLock()


def _pool() -> ThreadPoolExecutor:
    global _inline_pool
    with _lock:
        if _inline_pool is None:
            _inline_pool = ThreadPoolExecutor(
                max_workers=_INLINE_WORKERS, thread_name_prefix="cg-inline"
            )
        return _inline_pool

# --------------------------------------------------------------------------
# broker reachability
# --------------------------------------------------------------------------
# Celery's result backend runs its own long reconnect loop (20 attempts) inside
# apply_async and AsyncResult, which turns an absent broker into a ~2 minute
# request. A cheap TCP probe in front of those calls keeps the fallback instant.
_BROKER_PROBE_TIMEOUT = 0.35
_BROKER_PROBE_INTERVAL = 10.0
_DEFAULT_PORTS = {"redis": 6379, "rediss": 6379, "amqp": 5672, "amqps": 5671}

_broker_up: Optional[bool] = None
_broker_probed_at = 0.0


def broker_available(force: bool = False) -> bool:
    """True when the Celery broker accepts TCP connections (cached briefly)."""
    global _broker_up, _broker_probed_at

    if settings.standalone:
        return False  # no task queue by design; jobs run in this process

    with _lock:
        now = time.time()
        if (
            not force
            and _broker_up is not None
            and now - _broker_probed_at < _BROKER_PROBE_INTERVAL
        ):
            return _broker_up

        parsed = urlparse(settings.broker_url)
        scheme = (parsed.scheme or "").lower()
        if scheme in ("memory", "filesystem", "sqla", "db") or not parsed.hostname:
            # Nothing to probe; let Celery decide.
            _broker_up, _broker_probed_at = True, now
            return True

        host = parsed.hostname
        port = parsed.port or _DEFAULT_PORTS.get(scheme, 6379)
        previous = _broker_up
        try:
            with socket.create_connection((host, port), timeout=_BROKER_PROBE_TIMEOUT):
                _broker_up = True
        except OSError:
            _broker_up = False
        _broker_probed_at = now

        if previous is not None and previous != _broker_up:
            logger.info(
                "Celery broker at %s:%s is now %s",
                host,
                port,
                "reachable" if _broker_up else "unreachable",
            )
        return _broker_up


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------
def create_record(
    task_id: str,
    task_type: TaskType | str,
    executor: str = "celery",
    status: TaskStatus = TaskStatus.PENDING,
) -> Dict[str, Any]:
    record = {
        "task_id": task_id,
        "task_type": str(getattr(task_type, "value", task_type)),
        "status": status.value if isinstance(status, TaskStatus) else str(status),
        "created_at": _iso(_now()),
        "started_at": None,
        "completed_at": None,
        "duration_seconds": None,
        "executor": executor,
        "progress": 0,
        "stage": "Queued",
        "result": None,
        "cache_keys": [],
        "error": None,
    }
    cache.set_json(Keys.task(task_id), record, settings.task_record_ttl)
    cache.push_task_index(task_id)
    return record


def update_record(task_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """Merge fields into a task record, normalising datetimes and enums."""
    with _lock:
        record = cache.get_json(Keys.task(task_id))
        if record is None:
            return None
        for key, value in fields.items():
            if isinstance(value, datetime):
                value = _iso(value)
            elif isinstance(value, (TaskStatus, TaskType)):
                value = value.value
            record[key] = value

        started, completed = record.get("started_at"), record.get("completed_at")
        if started and completed and record.get("duration_seconds") is None:
            try:
                record["duration_seconds"] = round(
                    (
                        datetime.fromisoformat(completed) - datetime.fromisoformat(started)
                    ).total_seconds(),
                    3,
                )
            except (TypeError, ValueError):
                pass

        cache.set_json(Keys.task(task_id), record, settings.task_record_ttl)
        return record


def get_record(task_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a task record, reconciling it with live Celery state."""
    record = cache.get_json(Keys.task(task_id))
    celery_state = _celery_state(task_id)

    if record is None:
        if celery_state is None:
            return None
        # Known to Celery but not to us (e.g. cache was flushed).
        return {
            "task_id": task_id,
            "task_type": TaskType.REFRESH_ALL.value,
            "status": celery_state["status"],
            "created_at": _iso(_now()),
            "started_at": None,
            "completed_at": None,
            "duration_seconds": None,
            "executor": "celery",
            "progress": celery_state["progress"],
            "stage": celery_state.get("stage"),
            "result": None,
            "cache_keys": [],
            "error": celery_state.get("error"),
        }

    # A terminal record is authoritative; otherwise let Celery advance it.
    if record["status"] in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value):
        return record

    if celery_state:
        record["status"] = celery_state["status"]
        record["progress"] = max(record.get("progress", 0), celery_state["progress"])
        if celery_state.get("stage"):
            record["stage"] = celery_state["stage"]
        if celery_state.get("error"):
            record["error"] = celery_state["error"]
    return record


def _celery_state(task_id: str) -> Optional[Dict[str, Any]]:
    """Translate Celery's state machine into our four statuses."""
    if task_id.startswith("inline-") or not broker_available():
        # Inline tasks have no Celery record, and querying an absent broker
        # would stall the poll for seconds.
        return None
    try:
        from celery.result import AsyncResult

        from app.workers.celery_app import celery_app

        result = AsyncResult(task_id, app=celery_app)
        state = result.state
    except Exception as exc:  # pragma: no cover - broker/env dependent
        logger.debug("Could not read Celery state for %s: %s", task_id, exc)
        return None

    if state in ("PENDING", "RETRY"):
        # Celery reports PENDING for unknown ids too, so this is only a hint.
        return {"status": TaskStatus.PENDING.value, "progress": 0}
    if state in ("STARTED", "PROGRESS"):
        meta = result.info if isinstance(result.info, dict) else {}
        return {
            "status": TaskStatus.PROCESSING.value,
            "progress": int(meta.get("progress", 25)),
            "stage": meta.get("stage"),
        }
    if state == "SUCCESS":
        return {"status": TaskStatus.COMPLETED.value, "progress": 100}
    if state in ("FAILURE", "REVOKED"):
        return {
            "status": TaskStatus.FAILED.value,
            "progress": 100,
            # Task records are served publicly by /api/tasks/{id}.
            "error": scrub_credentials(str(result.info)) if result.info else f"Task {state.lower()}",
        }
    return None


def recent_records(limit: int = 25) -> List[Dict[str, Any]]:
    """Recent task records, fetched in one round-trip.

    The previous implementation issued one cache read *and* one Celery
    AsyncResult lookup per task. One MGET now fetches every record, and Celery is
    consulted only for the records that are still running - a terminal record is
    already authoritative, so asking the broker about it is pure latency.
    """
    task_ids = cache.recent_task_ids(limit)
    if not task_ids:
        return []

    by_key = cache.get_many_json([Keys.task(task_id) for task_id in task_ids])

    terminal = (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value)
    records: List[Dict[str, Any]] = []
    for task_id in task_ids:
        record = by_key.get(Keys.task(task_id))
        if record is None:
            # Unknown to the cache; fall back to the single-record path, which
            # can still reconstruct it from Celery.
            record = get_record(task_id)
            if record:
                records.append(record)
            continue

        if record["status"] not in terminal:
            celery_state = _celery_state(task_id)
            if celery_state:
                record["status"] = celery_state["status"]
                record["progress"] = max(record.get("progress", 0), celery_state["progress"])
                if celery_state.get("stage"):
                    record["stage"] = celery_state["stage"]
                if celery_state.get("error"):
                    record["error"] = celery_state["error"]
        records.append(record)
    return records


def pending_task_ids(limit: int = 25) -> List[str]:
    return [
        record["task_id"]
        for record in recent_records(limit)
        if record["status"] in (TaskStatus.PENDING.value, TaskStatus.PROCESSING.value)
    ]


# --------------------------------------------------------------------------
# submission
# --------------------------------------------------------------------------
def submit(
    task_type: TaskType,
    celery_task: Any,
    kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Queue work on Celery, falling back to a local thread pool.

    Returns the fields needed to build a `TaskSubmission`.
    """
    kwargs = dict(kwargs or {})

    if broker_available():
        try:
            async_result = celery_task.apply_async(kwargs=kwargs)
        except Exception as exc:
            reason = scrub_credentials(f"{type(exc).__name__}: {exc}")
            # Run inline only if the broker has really gone away since the
            # probe. A reachable broker that refuses the task is misconfigured.
            if not (_is_connection_error(exc) and not broker_available(force=True)):
                logger.error(
                    "Celery refused %s although its broker is reachable (%s); "
                    "not falling back to the API process",
                    task_type.value,
                    reason,
                )
                raise TaskDispatchError(
                    f"Celery refused the {task_type.value} task ({type(exc).__name__})"
                ) from exc
        else:
            task_id = async_result.id
            create_record(task_id, task_type, executor="celery")
            logger.info("Dispatched %s to Celery as %s", task_type.value, task_id)
            return {
                "task_id": task_id,
                "task_type": task_type,
                "status": TaskStatus.PENDING,
                "executor": "celery",
                "message": "Queued on the Celery worker pool.",
            }
    elif settings.standalone:
        reason = None
        logger.info("Running %s in this process (standalone mode)", task_type.value)
    else:
        reason = (
            f"Celery broker at {redact_url(settings.broker_url)} is not accepting connections"
        )

    if reason is not None:
        logger.warning(
            "Celery dispatch of %s failed (%s); running inline on the local pool",
            task_type.value,
            reason,
        )

    task_id = f"inline-{uuid.uuid4().hex[:16]}"
    create_record(task_id, task_type, executor="inline")

    runner = getattr(celery_task, "run", celery_task)
    future = _pool().submit(_run_inline, task_id, task_type, runner, kwargs)
    with _lock:
        _inline_futures.add(future)
    future.add_done_callback(lambda f: _inline_futures.discard(f))

    return {
        "task_id": task_id,
        "task_type": task_type,
        "status": TaskStatus.PENDING,
        "executor": "inline",
        "message": (
            "Running in the API process (standalone mode)."
            if settings.standalone
            else "Celery broker unreachable - running on a local worker thread. "
            "Start Redis and the Celery worker for the full asynchronous path."
        ),
    }


def _run_inline(
    task_id: str, task_type: TaskType, runner: Callable[..., Any], kwargs: Dict[str, Any]
) -> None:
    """Execute a task body off the request thread when Celery is unavailable."""
    update_record(
        task_id,
        status=TaskStatus.PROCESSING,
        started_at=_now(),
        progress=10,
        stage="Running inline",
    )
    try:
        runner(cg_task_id=task_id, **kwargs)
    except Exception as exc:
        logger.exception("Inline task %s (%s) failed", task_id, task_type.value)
        update_record(
            task_id,
            status=TaskStatus.FAILED,
            completed_at=_now(),
            progress=100,
            stage="Failed",
            error=scrub_credentials(f"{type(exc).__name__}: {exc}"),
        )


def wait_for_inline(timeout: float = 120.0) -> bool:
    """Block until queued inline tasks finish. Returns False on timeout.

    Used on shutdown and by the test suite, so a task cannot still be writing to
    the cache after the thing that started it has gone away.
    """
    with _lock:
        pending = set(_inline_futures)
    if not pending:
        return True
    done, not_done = wait(pending, timeout=timeout)
    return not not_done


def shutdown(wait_for_tasks: bool = False, timeout: float = 30.0) -> None:
    """Stop the inline pool (called on API shutdown).

    The pool reference is cleared rather than left shut down, so a subsequent
    submission transparently gets a fresh pool instead of raising
    "cannot schedule new futures after shutdown".
    """
    global _inline_pool

    if wait_for_tasks:
        wait_for_inline(timeout)
    with _lock:
        pool, _inline_pool = _inline_pool, None
    if pool is not None:
        pool.shutdown(wait=wait_for_tasks)
