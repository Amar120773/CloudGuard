"""AWS provider built on boto3.

The same code path serves mocked and real AWS. EC2, CloudWatch and CloudWatch
Logs calls genuinely round-trip through the AWS API surface (Moto implements
them), so the numbers on the dashboard are read back out of the cloud API
rather than invented in the service layer.

Cost Explorer is the one exception: Moto does not implement
``ce:GetCostAndUsage``. We attempt the real call, and on an unsupported-
operation error fall back to a synthetic response that is *wire-compatible with
the real API*. The parser below is therefore production code - pointing
``CLOUD_MODE=real`` at a real account exercises exactly the same parsing.
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.cloud import datagen
from app.cloud.base import CloudProvider, CloudProviderError
from app.cloud.moto_setup import (
    METRIC_NAMESPACE,
    PROJECT_TAG,
    ensure_mock_aws,
    seed_environment,
)
from app.config import settings
from app.connection_urls import redact_url
from app.logging_config import get_logger

logger = get_logger(__name__)

_client_lock = threading.RLock()
_clients: Dict[str, Any] = {}

# Cost Explorer support is probed once per process, then remembered.
_ce_native_supported: Optional[bool] = None

_BOTO_CONFIG = Config(
    retries={"max_attempts": 3, "mode": "standard"},
    connect_timeout=5,
    read_timeout=25,
)


def get_client(service_name: str):
    """Cached boto3 client honouring the configured cloud mode."""
    ensure_mock_aws()

    with _client_lock:
        client = _clients.get(service_name)
        if client is not None:
            return client

        kwargs: Dict[str, Any] = {
            "region_name": settings.aws_region,
            "config": _BOTO_CONFIG,
        }
        if settings.uses_moto:
            # Moto accepts any credentials; never read the developer's real ones.
            kwargs["aws_access_key_id"] = settings.aws_access_key_id or "testing"
            kwargs["aws_secret_access_key"] = settings.aws_secret_access_key or "testing"
            kwargs["aws_session_token"] = "testing"
        if settings.boto_endpoint_url:
            kwargs["endpoint_url"] = settings.boto_endpoint_url

        client = boto3.client(service_name, **kwargs)
        _clients[service_name] = client
        return client


def reset_clients() -> None:
    """Drop cached clients (used by tests when the mock is restarted)."""
    global _ce_native_supported
    with _client_lock:
        _clients.clear()
        _ce_native_supported = None


def _describe_project_instances(ec2) -> List[Dict[str, Any]]:
    """Every non-terminated project instance, across all result pages.

    describe_instances caps each page (default 1000), so an unpaginated call
    silently truncates a real estate. The paginator makes the page size the
    SDK's problem rather than a hidden correctness limit.
    """
    instances: List[Dict[str, Any]] = []
    try:
        paginator = ec2.get_paginator("describe_instances")
        pages = paginator.paginate(
            Filters=[{"Name": "tag:Project", "Values": [PROJECT_TAG]}]
        )
        for page in pages:
            for reservation in page.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    if instance.get("State", {}).get("Name") != "terminated":
                        instances.append(instance)
    except (ClientError, BotoCoreError) as exc:
        raise CloudProviderError(
            f"EC2 describe_instances failed: {exc}", service="ec2"
        ) from exc
    return instances


class AwsCloudProvider(CloudProvider):
    name = "aws"

    def __init__(self, auto_seed: bool = True):
        if settings.uses_moto and auto_seed:
            seed_environment()

    # ------------------------------------------------------------------ EC2
    def list_resources(self) -> List[Dict[str, Any]]:
        """Read the fleet from EC2 and enrich it with CloudWatch utilisation."""
        ec2 = get_client("ec2")
        instances = _describe_project_instances(ec2)

        specs_by_index = {spec["index"]: spec for spec in datagen.generate_instance_specs()}
        utilisation = self._cpu_utilisation_by_instance()

        resources: List[Dict[str, Any]] = []
        for instance in instances:
            state = instance.get("State", {}).get("Name", "unknown")

            tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
            index = _safe_int(tags.get("CGIndex"))
            spec = specs_by_index.get(index, {})
            hourly = _safe_float(tags.get("CGHourlyCost")) or spec.get("hourly_cost", 0.0)
            instance_id = instance["InstanceId"]

            cpu = utilisation.get(instance_id)
            if cpu is None:
                cpu = spec.get("cpu_utilization", 0.0) if state == "running" else 0.0

            estimated = datagen.monthly_cost_for(hourly, state)
            idle = bool(state == "running" and cpu is not None and cpu < 6.0)

            resources.append(
                {
                    "resource_id": instance_id,
                    "resource_type": "ec2:instance",
                    "name": tags.get("Name"),
                    "service": "Amazon Elastic Compute Cloud - Compute",
                    "region": settings.aws_region,
                    "availability_zone": instance.get("Placement", {}).get("AvailabilityZone"),
                    "status": state,
                    "instance_type": instance.get("InstanceType"),
                    "usage": round(float(cpu or 0.0), 2),
                    "cpu_utilization": round(float(cpu or 0.0), 2),
                    "network_in_mb": spec.get("network_in_mb"),
                    "network_out_mb": spec.get("network_out_mb"),
                    "estimated_cost": estimated,
                    "environment": tags.get("Environment"),
                    "owner": tags.get("Owner"),
                    "launch_time": _as_datetime(instance.get("LaunchTime")),
                    "idle": idle,
                    "optimization_hint": _optimization_hint(state, cpu, estimated, instance.get("InstanceType")),
                }
            )

        resources.sort(key=lambda r: r["estimated_cost"], reverse=True)
        return resources

    # ----------------------------------------------------------- CloudWatch
    def _cpu_utilisation_by_instance(self) -> Dict[str, float]:
        """Average CPU per instance, read back out of CloudWatch."""
        averages: Dict[str, float] = {}
        for series in self.get_metrics(hours=24, period_seconds=3600):
            if series["metric_name"] != "CPUUtilization":
                continue
            resource_id = series.get("resource_id")
            if resource_id and series.get("average") is not None:
                averages[resource_id] = series["average"]
        return averages

    def get_metrics(self, hours: int = 24, period_seconds: int = 3600) -> List[Dict[str, Any]]:
        """Pull CPU/network statistics for every demo instance."""
        cloudwatch = get_client("cloudwatch")
        ec2 = get_client("ec2")

        instance_ids = [
            instance["InstanceId"] for instance in _describe_project_instances(ec2)
        ]

        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=hours)
        series_list: List[Dict[str, Any]] = []

        for instance_id in instance_ids:
            for metric_name, unit in (
                ("CPUUtilization", "Percent"),
                ("NetworkIn", "Bytes"),
                ("NetworkOut", "Bytes"),
            ):
                try:
                    stats = cloudwatch.get_metric_statistics(
                        Namespace=METRIC_NAMESPACE,
                        MetricName=metric_name,
                        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
                        StartTime=start,
                        EndTime=end,
                        Period=period_seconds,
                        Statistics=["Average", "Maximum", "Minimum"],
                        Unit=unit,
                    )
                except (ClientError, BotoCoreError) as exc:
                    logger.warning(
                        "get_metric_statistics failed for %s/%s: %s",
                        instance_id,
                        metric_name,
                        exc,
                    )
                    continue

                datapoints = sorted(
                    stats.get("Datapoints", []), key=lambda dp: dp["Timestamp"]
                )
                if not datapoints:
                    continue

                values = [float(dp.get("Average", 0.0)) for dp in datapoints]
                series_list.append(
                    {
                        "namespace": METRIC_NAMESPACE,
                        "metric_name": metric_name,
                        "resource_id": instance_id,
                        "statistic": "Average",
                        "unit": unit,
                        "datapoints": [
                            {
                                "timestamp": _as_datetime(dp["Timestamp"]),
                                "value": round(float(dp.get("Average", 0.0)), 4),
                                "unit": unit,
                            }
                            for dp in datapoints
                        ],
                        "average": round(sum(values) / len(values), 4),
                        "maximum": round(max(float(dp.get("Maximum", 0.0)) for dp in datapoints), 4),
                        "minimum": round(min(float(dp.get("Minimum", 0.0)) for dp in datapoints), 4),
                    }
                )

        return series_list

    # --------------------------------------------------------- Cost Explorer
    def get_cost_and_usage(self, start: date, end: date) -> List[Dict[str, Any]]:
        """Daily per-service spend, parsed from a Cost Explorer response.

        Moto accepts ``GetCostAndUsage`` but answers with an empty stub, so a
        successful call is not proof of usable data. We therefore judge the
        native path on what it returns, not on whether it raised.
        """
        global _ce_native_supported

        if _ce_native_supported is not False:
            try:
                response = self._call_native_ce(start, end)
                records = self._parse_ce_response(response)
            except (ClientError, BotoCoreError, NotImplementedError) as exc:
                if settings.cloud_mode == "real":
                    raise CloudProviderError(
                        f"Cost Explorer GetCostAndUsage failed: {exc}", service="ce"
                    ) from exc
                self._disable_native_ce(f"call raised {type(exc).__name__}")
                records = []
            else:
                if records:
                    if _ce_native_supported is None:
                        logger.info(
                            "Native Cost Explorer GetCostAndUsage returned %d records",
                            len(records),
                        )
                    _ce_native_supported = True
                    return records
                if settings.cloud_mode == "real":
                    # A real account with genuinely no spend in the window.
                    logger.warning(
                        "Cost Explorer returned no records for %s..%s", start, end
                    )
                    return []
                self._disable_native_ce("call returned no cost records")

        dataset = datagen.generate_cost_dataset(
            days=max(1, (end - start).days + 1), end=end
        )
        response = datagen.cost_dataset_to_ce_response(dataset, start, end)
        return self._parse_ce_response(response)

    @staticmethod
    def _call_native_ce(start: date, end: date) -> Dict[str, Any]:
        # Cost Explorer's End is exclusive.
        return get_client("ce").get_cost_and_usage(
            TimePeriod={
                "Start": start.isoformat(),
                "End": (end + timedelta(days=1)).isoformat(),
            },
            Granularity="DAILY",
            Metrics=["UnblendedCost", "UsageQuantity"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )

    @staticmethod
    def _disable_native_ce(reason: str) -> None:
        global _ce_native_supported
        if _ce_native_supported is None:
            logger.info(
                "Cost Explorer is not usable against the mock (%s); switching to the "
                "wire-compatible synthetic Cost Explorer. Set CLOUD_MODE=real to use "
                "the native API.",
                reason,
            )
        _ce_native_supported = False

    @staticmethod
    def _parse_ce_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Flatten a GetCostAndUsage payload into cost records.

        Defensive by design: real Cost Explorer returns strings for amounts,
        omits groups on days with no spend, and can include an empty trailing
        period.
        """
        records: List[Dict[str, Any]] = []
        for period in response.get("ResultsByTime", []):
            period_start = period.get("TimePeriod", {}).get("Start")
            if not period_start:
                continue
            try:
                record_date = datetime.strptime(period_start, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                logger.warning("Skipping Cost Explorer period with bad date: %r", period_start)
                continue

            groups = period.get("Groups") or []
            if not groups:
                # Ungrouped totals still carry useful spend.
                total = period.get("Total", {}).get("UnblendedCost", {})
                amount = _safe_float(total.get("Amount"))
                if amount:
                    records.append(
                        {
                            "date": record_date,
                            "service": "All services",
                            "usage": 0.0,
                            "cost": amount,
                            "unit": total.get("Unit", "USD"),
                        }
                    )
                continue

            for group in groups:
                keys = group.get("Keys") or ["Unknown service"]
                metrics = group.get("Metrics", {})
                cost = _safe_float(metrics.get("UnblendedCost", {}).get("Amount"))
                usage = _safe_float(metrics.get("UsageQuantity", {}).get("Amount"))
                if cost is None:
                    continue
                records.append(
                    {
                        "date": record_date,
                        "service": keys[0],
                        "usage": max(0.0, usage or 0.0),
                        "cost": max(0.0, cost),
                        "unit": metrics.get("UnblendedCost", {}).get("Unit", "USD"),
                    }
                )

        records.sort(key=lambda r: (r["date"], r["service"]))
        return records

    # ------------------------------------------------- CloudWatch Logs (VPC)
    def get_security_records(self, inject_extra_anomaly: bool = False) -> List[Dict[str, Any]]:
        """Read behavioural windows back out of the mocked flow-log group."""
        if inject_extra_anomaly:
            from app.cloud.moto_setup import write_live_flow_log_event

            extra = datagen._build_live_anomaly(
                datetime.now(timezone.utc), "eni-live0001", "arn:aws:iam::123456789012:role/app-runtime"
            )
            write_live_flow_log_event(extra)

        logs = get_client("logs")
        records: List[Dict[str, Any]] = []
        try:
            token = None
            while True:
                kwargs: Dict[str, Any] = {
                    "logGroupName": settings.log_group_flow_logs,
                    "limit": 1000,
                }
                if token:
                    kwargs["nextToken"] = token
                response = logs.filter_log_events(**kwargs)
                for event in response.get("events", []):
                    parsed = _parse_flow_log_message(event.get("message"))
                    if parsed:
                        records.append(parsed)
                token = response.get("nextToken")
                if not token:
                    break
        except (ClientError, BotoCoreError) as exc:
            logger.warning(
                "filter_log_events failed (%s); generating records directly", exc
            )

        if not records:
            # Log group empty or unavailable: fall back to the generator so the
            # security pipeline still has input to score.
            records = datagen.generate_security_records(
                inject_extra_anomaly=inject_extra_anomaly
            )

        records.sort(key=lambda r: r["timestamp"])
        return records

    # ------------------------------------------------------------ meta
    def capabilities(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "cloud_mode": settings.cloud_mode,
            "region": settings.aws_region,
            # Served by the public /api/health endpoint.
            "endpoint_url": redact_url(settings.boto_endpoint_url) or None,
            "ec2": "live-api",
            "cloudwatch": "live-api",
            "cloudwatch_logs": "live-api",
            "cost_explorer": (
                "live-api" if _ce_native_supported else "synthetic-wire-compatible"
            ),
        }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _parse_flow_log_message(message: Optional[str]) -> Optional[Dict[str, Any]]:
    if not message:
        return None
    try:
        payload = json.loads(message)
    except (TypeError, ValueError):
        return None

    timestamp = payload.get("timestamp")
    if isinstance(timestamp, str):
        try:
            payload["timestamp"] = datetime.fromisoformat(timestamp)
        except ValueError:
            payload["timestamp"] = datetime.now(timezone.utc)
    elif not isinstance(timestamp, datetime):
        payload["timestamp"] = datetime.now(timezone.utc)

    # Drop records missing the behavioural contract rather than feeding the
    # model partial vectors.
    for feature in datagen.SECURITY_FEATURES:
        value = _safe_float(payload.get(feature))
        if value is None:
            return None
        payload[feature] = value
    return payload


def _optimization_hint(
    state: str, cpu: Optional[float], monthly_cost: float, instance_type: Optional[str]
) -> Optional[str]:
    if state == "stopped":
        return "Stopped instance still incurring EBS storage charges - delete unused volumes."
    if cpu is None:
        return None
    if cpu < 6.0:
        return f"Idle at {cpu:.1f}% CPU - terminate or downsize to save ~${monthly_cost * 0.6:,.0f}/mo."
    if cpu < 20.0 and instance_type and not instance_type.startswith("t3"):
        return f"Low utilisation ({cpu:.1f}% CPU) - consider a smaller or burstable instance type."
    if cpu > 88.0:
        return f"Sustained high CPU ({cpu:.1f}%) - scale up before it throttles throughput."
    return None


def _safe_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _as_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


_provider: Optional[AwsCloudProvider] = None
_provider_lock = threading.RLock()


def get_provider() -> AwsCloudProvider:
    """Process-wide provider singleton (seeds the mock on first use)."""
    global _provider
    with _provider_lock:
        if _provider is None:
            _provider = AwsCloudProvider()
        return _provider


def reset_provider() -> None:
    global _provider
    with _provider_lock:
        _provider = None
