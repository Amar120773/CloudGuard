"""Celery tasks: all cloud I/O and ML work happens here, never in a request.

Each task body is wrapped by `_tracked`, which keeps the durable task record in
step with execution (PENDING -> PROCESSING -> COMPLETED/FAILED), records stage
and progress for the UI's progress bar, and converts an exception into a FAILED
record with a readable message instead of an opaque Celery traceback.

`cg_task_id` lets the inline fallback executor reuse the same bodies: Celery
supplies the id via `self.request.id`, the thread-pool path passes it explicitly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from app.cache import Keys, cache
from app.config import settings
from app.connection_urls import scrub_credentials
from app.logging_config import get_logger
from app.schemas.task import TaskStatus, TaskType
from app.services import (
    cloud_service,
    cost_service,
    dashboard_service,
    security_service,
    task_service,
)
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tracked(
    task_id: Optional[str],
    task_type: TaskType,
    body: Callable[[Callable[[int, str], None]], Dict[str, Any]],
) -> Dict[str, Any]:
    """Run a task body while keeping its registry record current."""
    if task_id and task_service.get_record(task_id) is None:
        # Beat-scheduled runs have no API-side record yet.
        task_service.create_record(task_id, task_type)

    def progress(percent: int, stage: str) -> None:
        if task_id:
            task_service.update_record(
                task_id, status=TaskStatus.PROCESSING, progress=percent, stage=stage
            )

    if task_id:
        task_service.update_record(
            task_id,
            status=TaskStatus.PROCESSING,
            started_at=_now(),
            progress=5,
            stage="Starting",
        )

    try:
        result = body(progress)
    except Exception as exc:
        # Stored in the task record, which /api/tasks/{id} serves publicly.
        message = scrub_credentials(f"{type(exc).__name__}: {exc}")
        logger.exception("Task %s (%s) failed", task_id, task_type.value)
        if task_id:
            task_service.update_record(
                task_id,
                status=TaskStatus.FAILED,
                completed_at=_now(),
                progress=100,
                stage="Failed",
                error=message,
            )
        raise

    if task_id:
        task_service.update_record(
            task_id,
            status=TaskStatus.COMPLETED,
            completed_at=_now(),
            progress=100,
            stage="Complete",
            result=result.get("summary"),
            cache_keys=result.get("cache_keys", []),
        )
    return result


# --------------------------------------------------------------------------
# cloud ingestion
# --------------------------------------------------------------------------
@celery_app.task(bind=True, name="cloudguard.ingest_cloud_data")
def ingest_cloud_data(self, cg_task_id: Optional[str] = None) -> Dict[str, Any]:
    """Collect EC2 inventory and CloudWatch metrics."""
    task_id = cg_task_id or getattr(self.request, "id", None)

    def body(progress):
        progress(25, "Describing EC2 instances")
        inventory = cloud_service.ingest_cloud_inventory()
        progress(65, "Collecting CloudWatch metrics")
        metrics = cloud_service.ingest_cloud_metrics()
        return {
            "summary": {
                "resources": inventory["total_resources"],
                "running": inventory["running"],
                "idle": inventory["idle_resources"],
                "metric_series": len(metrics["series"]),
                "estimated_monthly_cost": inventory["estimated_monthly_cost"],
            },
            "cache_keys": [Keys.CLOUD_RESOURCES, Keys.CLOUD_METRICS],
        }

    return _tracked(task_id, TaskType.INGEST_CLOUD, body)


# --------------------------------------------------------------------------
# cost
# --------------------------------------------------------------------------
@celery_app.task(bind=True, name="cloudguard.ingest_cost_data")
def ingest_cost_data(
    self, cg_task_id: Optional[str] = None, days: Optional[int] = None
) -> Dict[str, Any]:
    """Pull Cost Explorer history and cache the aggregated view."""
    task_id = cg_task_id or getattr(self.request, "id", None)

    def body(progress):
        progress(30, "Querying Cost Explorer")
        history = cost_service.ingest_cost_history(days=days)
        progress(85, "Aggregating per-service spend")
        return {
            "summary": {
                "records_analysed": history["records_analysed"],
                "total_cost": history["total_cost"],
                "month_to_date": history["month_to_date"],
                "services": len(history["by_service"]),
            },
            "cache_keys": [Keys.COST_HISTORY],
        }

    return _tracked(task_id, TaskType.INGEST_COST, body)


@celery_app.task(bind=True, name="cloudguard.run_cost_forecast")
def run_cost_forecast(
    self,
    cg_task_id: Optional[str] = None,
    horizon_days: Optional[int] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Train Prophet and cache the forecast with its backtest metrics."""
    task_id = cg_task_id or getattr(self.request, "id", None)

    def body(progress):
        if force:
            cache.delete(Keys.COST_FORECAST)
        progress(15, "Loading cost history")
        if cache.get_json(f"{Keys.COST_HISTORY}:raw") is None:
            progress(25, "Ingesting cost history")
            cost_service.ingest_cost_history()

        progress(45, "Fitting Prophet model")
        forecast = cost_service.run_forecast(horizon_days=horizon_days)
        progress(90, "Analysing budget risk")
        return {
            "summary": {
                "model": forecast["model_name"],
                "projected_month_end": forecast["projected_month_end"],
                "trend_pct": forecast["trend_pct"],
                "risk_level": forecast["risk_level"],
                "mae": forecast["accuracy"].get("mae"),
                "risk_services": len(forecast["risk_services"]),
            },
            "cache_keys": [Keys.COST_FORECAST],
        }

    return _tracked(task_id, TaskType.RUN_FORECAST, body)


# --------------------------------------------------------------------------
# security
# --------------------------------------------------------------------------
@celery_app.task(bind=True, name="cloudguard.ingest_security_events")
def ingest_security_events(
    self, cg_task_id: Optional[str] = None, inject_anomaly: bool = False
) -> Dict[str, Any]:
    """Read behavioural windows from CloudWatch Logs, then score them."""
    task_id = cg_task_id or getattr(self.request, "id", None)

    def body(progress):
        progress(30, "Reading VPC flow logs")
        ingestion = security_service.ingest_security_events(inject_anomaly=inject_anomaly)
        progress(60, "Scoring with IsolationForest")
        posture = security_service.run_anomaly_detection(inject_anomaly=inject_anomaly)
        return {
            "summary": {
                "records": ingestion["records"],
                "anomalies": posture["anomaly_count"],
                "security_health_score": posture["security_health_score"],
                "precision": posture["accuracy"].get("precision"),
                "recall": posture["accuracy"].get("recall"),
            },
            "cache_keys": [Keys.SECURITY_EVENTS, Keys.SECURITY_ANOMALIES],
        }

    return _tracked(task_id, TaskType.INGEST_SECURITY, body)


@celery_app.task(bind=True, name="cloudguard.run_anomaly_detection")
def run_anomaly_detection(
    self,
    cg_task_id: Optional[str] = None,
    contamination: Optional[float] = None,
    inject_anomaly: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """Re-score the behavioural windows, optionally injecting a live outlier."""
    task_id = cg_task_id or getattr(self.request, "id", None)

    def body(progress):
        if force or inject_anomaly:
            cache.delete(f"{Keys.SECURITY_EVENTS}:raw", Keys.SECURITY_ANOMALIES)
        progress(30, "Preparing behavioural features")
        posture = security_service.run_anomaly_detection(
            contamination=contamination, inject_anomaly=inject_anomaly
        )
        progress(90, "Classifying events")
        return {
            "summary": {
                "total_events": posture["total_events"],
                "anomalies": posture["anomaly_count"],
                "security_health_score": posture["security_health_score"],
                "precision": posture["accuracy"].get("precision"),
                "recall": posture["accuracy"].get("recall"),
                "injected_anomaly": inject_anomaly,
            },
            "cache_keys": [Keys.SECURITY_ANOMALIES],
        }

    return _tracked(task_id, TaskType.RUN_ANOMALY, body)


# --------------------------------------------------------------------------
# full refresh
# --------------------------------------------------------------------------
@celery_app.task(bind=True, name="cloudguard.refresh_all")
def refresh_all(
    self, cg_task_id: Optional[str] = None, force: bool = False
) -> Dict[str, Any]:
    """Run every pipeline in sequence, then rebuild the dashboard summary.

    Sequential by design: the forecast depends on cost ingestion, and running a
    Prophet fit alongside an IsolationForest fit on one worker only lengthens
    both.
    """
    task_id = cg_task_id or getattr(self.request, "id", None)

    def body(progress):
        if force:
            cache.delete(
                Keys.COST_HISTORY,
                f"{Keys.COST_HISTORY}:raw",
                Keys.COST_FORECAST,
                Keys.SECURITY_ANOMALIES,
                f"{Keys.SECURITY_EVENTS}:raw",
                Keys.CLOUD_RESOURCES,
                Keys.CLOUD_METRICS,
            )

        summary: Dict[str, Any] = {}
        errors: Dict[str, str] = {}

        progress(10, "Collecting cloud inventory")
        try:
            inventory = cloud_service.ingest_cloud_inventory()
            cloud_service.ingest_cloud_metrics()
            summary["resources"] = inventory["total_resources"]
        except Exception as exc:
            errors["cloud"] = f"{type(exc).__name__}: {exc}"
            logger.warning("Cloud ingestion failed during refresh: %s", exc)

        progress(35, "Ingesting cost history")
        try:
            history = cost_service.ingest_cost_history()
            summary["cost_records"] = history["records_analysed"]
        except Exception as exc:
            errors["cost"] = f"{type(exc).__name__}: {exc}"
            logger.warning("Cost ingestion failed during refresh: %s", exc)

        progress(55, "Fitting Prophet forecast")
        try:
            forecast = cost_service.run_forecast()
            summary["projected_month_end"] = forecast["projected_month_end"]
            summary["forecast_model"] = forecast["model_name"]
        except Exception as exc:
            errors["forecast"] = f"{type(exc).__name__}: {exc}"
            logger.warning("Forecast failed during refresh: %s", exc)

        progress(75, "Scoring security events")
        try:
            security_service.ingest_security_events()
            posture = security_service.run_anomaly_detection()
            summary["anomalies"] = posture["anomaly_count"]
        except Exception as exc:
            errors["security"] = f"{type(exc).__name__}: {exc}"
            logger.warning("Security scoring failed during refresh: %s", exc)

        progress(95, "Building dashboard summary")
        dashboard_service.build_dashboard()

        if errors:
            summary["errors"] = errors
        # A partial refresh is still useful; only a total failure is an error.
        if errors and len(errors) >= 4:
            raise RuntimeError("All refresh stages failed: " + "; ".join(errors.values()))

        return {
            "summary": summary,
            "cache_keys": [
                Keys.CLOUD_RESOURCES,
                Keys.CLOUD_METRICS,
                Keys.COST_HISTORY,
                Keys.COST_FORECAST,
                Keys.SECURITY_ANOMALIES,
            ],
        }

    return _tracked(task_id, TaskType.REFRESH_ALL, body)
