"""CloudGuard FastAPI application.

Request handlers only read cached pipeline output and queue background work.
No Prophet fit, IsolationForest fit or cloud API sweep ever runs inside a
request (spec principle 2).
"""
from __future__ import annotations

import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.api import cloud, costs, dashboard, health, security, tasks
from app.cache import cache
from app.config import settings
from app.logging_config import configure_logging, get_logger
from app.ml.anomaly_detection import InsufficientDataError as AnomalyDataError
from app.ml.forecasting import ForecastError
from app.ml.forecasting import InsufficientDataError as ForecastDataError
from app.api.security_deps import auth_enabled, check_proxy_trust
from app.cloud.base import CloudProviderError
from app.connection_urls import scrub_credentials
from app.services import task_service
from app.services.task_service import TaskDispatchError

# Never fall back to "*": an empty CORS_ORIGINS must not silently open the API to
# every origin on the internet, so it degrades to an explicit development
# allowlist instead.
DEV_FALLBACK_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def resolve_cors_origins() -> list[str]:
    origins = settings.cors_origins
    if origins:
        return origins
    if settings.cors_origin_regex:
        # A regex alone is a complete policy; no allowlist warning is warranted.
        return []
    logger.warning(
        "CORS_ORIGINS is empty; falling back to the development allowlist %s. "
        "Set CORS_ORIGINS explicitly for any non-local deployment.",
        DEV_FALLBACK_ORIGINS,
    )
    return list(DEV_FALLBACK_ORIGINS)


def resolve_cors_origin_regex() -> str | None:
    """Optional pattern matched against the Origin header.

    Used for hosts that mint a new origin per deployment (Vercel previews).
    An invalid pattern is dropped rather than taking the app down at import.
    """
    pattern = settings.cors_origin_regex.strip()
    if not pattern:
        return None
    try:
        re.compile(pattern)
    except re.error as exc:
        logger.error("CORS_ORIGIN_REGEX is not a valid regex (%s); ignoring it", exc)
        return None
    logger.info("CORS origin regex active: %s", pattern)
    return pattern


def cors_options() -> dict:
    """CORSMiddleware settings, resolved from the environment.

    Credentials stay off: the API authenticates with an X-API-Key header, never
    a cookie, so no cross-origin request needs them, and an allowlisted origin
    gains nothing a plain HTTP client could not already do.
    """
    return {
        "allow_origins": resolve_cors_origins(),
        "allow_origin_regex": resolve_cors_origin_regex(),
        "allow_credentials": False,
        "allow_methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["*", "X-API-Key"],
        "expose_headers": ["X-Process-Time-Ms", "Retry-After"],
    }


configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm the mocked cloud account, then hand off to the workers."""
    logger.info(
        "Starting %s v%s (environment=%s, cloud_mode=%s)",
        settings.app_name,
        settings.app_version,
        settings.environment,
        settings.cloud_mode,
    )

    if settings.uses_moto:
        try:
            from app.cloud.moto_setup import seed_environment

            seed_environment()
        except Exception as exc:
            # Never block startup: the health endpoint will report the failure.
            logger.exception("Mock AWS seeding failed at startup: %s", exc)

    if auth_enabled():
        logger.info("API-key authentication is ENABLED for write endpoints")
    else:
        logger.warning(
            "CLOUDGUARD_API_KEY is not set: endpoints that queue ML work are "
            "unauthenticated. Set it for any shared or non-local deployment."
        )
    # Per-client rate limiting depends on uvicorn resolving the real client
    # address; warn loudly if forwarding headers are trusted from anyone.
    check_proxy_trust()

    redis_health = cache.health()
    if not redis_health["connected"]:
        logger.warning(
            "Redis is unavailable at startup; caching falls back to this process "
            "and tasks will run inline until Redis and a Celery worker are up."
        )

    yield

    logger.info("Shutting down %s", settings.app_name)
    task_service.shutdown(wait_for_tasks=False)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "AI-powered cloud security and cost optimisation.\n\n"
        "- **Cost intelligence**: Cost Explorer ingestion, Prophet forecasting, "
        "month-end projection and budget risk.\n"
        "- **Security analytics**: VPC flow-log behavioural windows scored by "
        "IsolationForest.\n"
        "- **Async by design**: every expensive operation runs on Celery; "
        "endpoints return cached results or a task id to poll."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Compress at the application layer so every deployment benefits - direct
# uvicorn, nginx, or any other proxy. nginx leaves an already-encoded response
# alone, so there is no double compression. 500 bytes is below the point where
# gzip framing costs more than it saves.
app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)

# Added last so CORS sits outside compression and its headers apply to every
# response, including compressed and error ones.
app.add_middleware(CORSMiddleware, **cors_options())


@app.middleware("http")
async def add_timing_header(request: Request, call_next):
    """Expose server-side latency; the UI surfaces it and tests assert on it."""
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.2f}"
    if elapsed_ms > 1000:
        logger.warning(
            "Slow request %s %s took %.0fms", request.method, request.url.path, elapsed_ms
        )
    return response


# --------------------------------------------------------------------------
# error handling
# --------------------------------------------------------------------------
@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    logger.info("Validation error on %s: %s", request.url.path, exc.errors())
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Request validation failed.",
            "error_code": "validation_error",
            "errors": [
                {"field": ".".join(str(p) for p in e.get("loc", [])), "message": e.get("msg")}
                for e in exc.errors()
            ],
        },
    )


@app.exception_handler(ForecastDataError)
@app.exception_handler(AnomalyDataError)
async def insufficient_data_handler(request: Request, exc: Exception):
    """Too little/invalid data is a 422, not a server fault."""
    logger.warning("Insufficient data on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": str(exc),
            "error_code": "insufficient_data",
            "hint": "Ingest more history before running the model.",
        },
    )


@app.exception_handler(ForecastError)
async def forecast_error_handler(request: Request, exc: ForecastError):
    logger.error("Forecast failure on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "detail": "Forecasting is temporarily unavailable.",
            "error_code": "forecast_unavailable",
            "hint": "Check the worker logs; the last successful forecast is still cached.",
        },
    )


@app.exception_handler(TaskDispatchError)
async def task_dispatch_error_handler(request: Request, exc: TaskDispatchError):
    """The broker is up but refused the task: a deployment fault, not an outage.

    Already logged with detail by task_service; the response stays generic.
    """
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "detail": "Background work could not be queued.",
            "error_code": "task_dispatch_failed",
            "hint": "The task broker rejected the submission; check the API logs "
            "for the broker configuration error.",
        },
    )


@app.exception_handler(CloudProviderError)
async def cloud_error_handler(request: Request, exc: CloudProviderError):
    logger.error("Cloud provider failure on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={
            "detail": scrub_credentials(f"Cloud API call failed: {exc}"),
            "error_code": "cloud_api_error",
            "service": exc.service,
            "hint": "Retry shortly; cached results continue to be served.",
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    """Last resort: log the detail, return a clean body without a traceback."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An unexpected error occurred.",
            "error_code": "internal_error",
            "hint": "Check the API logs for the full traceback.",
        },
    )


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
api_prefix = settings.api_prefix
app.include_router(health.router, prefix=api_prefix)
app.include_router(dashboard.router, prefix=api_prefix)
app.include_router(costs.router, prefix=api_prefix)
app.include_router(security.router, prefix=api_prefix)
app.include_router(cloud.router, prefix=api_prefix)
app.include_router(tasks.router, prefix=api_prefix)


@app.get("/", tags=["meta"], summary="Service metadata")
def root():
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "cloud_mode": settings.cloud_mode,
        "docs": "/docs",
        "endpoints": {
            "health": f"{api_prefix}/health",
            "dashboard": f"{api_prefix}/dashboard",
            "refresh": f"{api_prefix}/dashboard/refresh",
            "insights": f"{api_prefix}/insights",
            "costs": f"{api_prefix}/costs",
            "cost_history": f"{api_prefix}/costs/history",
            "cost_forecast": f"{api_prefix}/costs/forecast",
            "run_forecast": f"{api_prefix}/costs/forecast/run",
            "security": f"{api_prefix}/security",
            "security_events": f"{api_prefix}/security/events",
            "security_anomalies": f"{api_prefix}/security/anomalies",
            "run_anomalies": f"{api_prefix}/security/anomalies/run",
            "cloud": f"{api_prefix}/cloud",
            "cloud_resources": f"{api_prefix}/cloud/resources",
            "cloud_metrics": f"{api_prefix}/cloud/metrics",
            "tasks": f"{api_prefix}/tasks/{{task_id}}",
        },
    }
