"""Mocked AWS environment backed by Moto.

Two mock modes are supported:

* ``moto_inproc`` (default) - a process-wide ``mock_aws()`` patch. No extra
  service to run; each process (API, worker) holds its own mocked account.
  Because all seed data is deterministic, every process converges on identical
  state.
* ``moto_server`` - talk to a standalone ``moto_server`` over HTTP via
  ``endpoint_url``. One shared mocked account across API and workers, which is
  what docker-compose uses.

Seeding is idempotent: it tags everything with ``Project=CloudGuard`` and skips
work when the expected resources already exist.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.cloud import datagen
from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

PROJECT_TAG = "CloudGuard"
METRIC_NAMESPACE = "CloudGuard/EC2"

_lock = threading.RLock()
_mock_ctx: Any = None
_mock_started = False
_seeded = False


def ensure_mock_aws() -> None:
    """Start the in-process Moto patch exactly once, if that mode is active."""
    global _mock_ctx, _mock_started

    if settings.cloud_mode != "moto_inproc":
        return

    with _lock:
        if _mock_started:
            return
        from moto import mock_aws

        _mock_ctx = mock_aws()
        _mock_ctx.start()
        _mock_started = True
        logger.info("Moto in-process mock AWS environment started")


def stop_mock_aws() -> None:
    """Tear the patch down (used by tests and on shutdown)."""
    global _mock_ctx, _mock_started, _seeded

    with _lock:
        if _mock_ctx is not None and _mock_started:
            try:
                _mock_ctx.stop()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Error stopping Moto mock: %s", exc)
        _mock_ctx = None
        _mock_started = False
        _seeded = False


def reset_seed_flag() -> None:
    """Force the next `seed_environment` call to re-seed (test helper)."""
    global _seeded
    with _lock:
        _seeded = False


def is_seeded() -> bool:
    return _seeded


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------
def seed_environment(force: bool = False) -> Dict[str, Any]:
    """Create EC2 instances, CloudWatch metrics and flow-log events in Moto."""
    global _seeded

    from app.cloud.aws import get_client

    with _lock:
        if _seeded and not force:
            return {"seeded": False, "reason": "already_seeded"}

        ensure_mock_aws()
        summary: Dict[str, Any] = {"seeded": True}
        try:
            summary["ec2"] = _seed_ec2(get_client("ec2"), force=force)
            summary["cloudwatch"] = _seed_cloudwatch(get_client("cloudwatch"))
            summary["logs"] = _seed_flow_logs(get_client("logs"))
            _seeded = True
            logger.info("Moto environment seeded: %s", summary)
        except Exception as exc:
            logger.exception("Failed to seed mocked AWS environment: %s", exc)
            summary.update({"seeded": False, "error": str(exc)})
        return summary


def _seed_ec2(ec2, force: bool = False) -> Dict[str, Any]:
    """Launch the deterministic demo fleet, unless it is already there."""
    specs = datagen.generate_instance_specs()

    existing = _describe_project_instances(ec2)
    if existing and not force and len(existing) >= len(specs):
        return {"created": 0, "existing": len(existing)}

    # A dedicated VPC/subnet so flow logs and AZ data look realistic.
    vpc_id, subnet_id, az = _ensure_network(ec2)

    created = 0
    for spec in specs:
        try:
            response = ec2.run_instances(
                ImageId="ami-0abcdef1234567890",
                InstanceType=spec["instance_type"],
                MinCount=1,
                MaxCount=1,
                SubnetId=subnet_id,
                TagSpecifications=[
                    {
                        "ResourceType": "instance",
                        "Tags": [
                            {"Key": "Project", "Value": PROJECT_TAG},
                            {"Key": "Name", "Value": spec["name"]},
                            {"Key": "Environment", "Value": spec["environment"]},
                            {"Key": "Owner", "Value": spec["owner"]},
                            {"Key": "CGIndex", "Value": str(spec["index"])},
                            {"Key": "CGHourlyCost", "Value": f"{spec['hourly_cost']:.4f}"},
                        ],
                    }
                ],
            )
            instance_id = response["Instances"][0]["InstanceId"]
            created += 1
            if spec["state"] == "stopped":
                ec2.stop_instances(InstanceIds=[instance_id])
        except Exception as exc:
            logger.warning("Could not launch demo instance %s: %s", spec["name"], exc)

    return {"created": created, "vpc": vpc_id, "subnet": subnet_id, "az": az}


def _ensure_network(ec2) -> tuple[str, str, str]:
    """Return (vpc_id, subnet_id, az), creating them if needed."""
    try:
        vpcs = ec2.describe_vpcs(
            Filters=[{"Name": "tag:Project", "Values": [PROJECT_TAG]}]
        ).get("Vpcs", [])
        if vpcs:
            vpc_id = vpcs[0]["VpcId"]
        else:
            vpc = ec2.create_vpc(CidrBlock="10.42.0.0/16")
            vpc_id = vpc["Vpc"]["VpcId"]
            ec2.create_tags(
                Resources=[vpc_id],
                Tags=[
                    {"Key": "Project", "Value": PROJECT_TAG},
                    {"Key": "Name", "Value": "cloudguard-vpc"},
                ],
            )

        subnets = ec2.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        ).get("Subnets", [])
        if subnets:
            subnet = subnets[0]
        else:
            created = ec2.create_subnet(
                VpcId=vpc_id,
                CidrBlock="10.42.1.0/24",
                AvailabilityZone=f"{settings.aws_region}a",
            )
            subnet = created["Subnet"]
        return vpc_id, subnet["SubnetId"], subnet.get("AvailabilityZone", f"{settings.aws_region}a")
    except Exception as exc:
        logger.warning("Falling back to default VPC/subnet: %s", exc)
        return "", "", f"{settings.aws_region}a"


def _describe_project_instances(ec2) -> List[Dict[str, Any]]:
    try:
        pages = ec2.describe_instances(
            Filters=[{"Name": "tag:Project", "Values": [PROJECT_TAG]}]
        )
    except Exception as exc:
        logger.warning("describe_instances failed during seeding probe: %s", exc)
        return []

    instances: List[Dict[str, Any]] = []
    for reservation in pages.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            if instance.get("State", {}).get("Name") != "terminated":
                instances.append(instance)
    return instances


def _seed_cloudwatch(cloudwatch) -> Dict[str, Any]:
    """Publish real metric data into the mocked CloudWatch.

    The dashboard then reads it back through `get_metric_statistics`, so the
    numbers on screen have genuinely round-tripped through the AWS API surface.
    """
    from app.cloud.aws import get_client

    specs = datagen.generate_instance_specs()
    instances = _describe_project_instances(get_client("ec2"))
    id_by_index = _map_instances_by_index(instances)

    published = 0
    for spec in specs:
        instance_id = id_by_index.get(spec["index"])
        if not instance_id:
            continue
        series = datagen.generate_metric_datapoints(spec)
        for metric_name, points in series.items():
            if not points:
                continue
            # put_metric_data accepts at most 20 metric items per call.
            for chunk_start in range(0, len(points), 20):
                chunk = points[chunk_start : chunk_start + 20]
                metric_data = [
                    {
                        "MetricName": metric_name,
                        "Dimensions": [{"Name": "InstanceId", "Value": instance_id}],
                        "Timestamp": point["timestamp"],
                        "Value": point["value"],
                        "Unit": point["unit"],
                    }
                    for point in chunk
                ]
                try:
                    cloudwatch.put_metric_data(
                        Namespace=METRIC_NAMESPACE, MetricData=metric_data
                    )
                    published += len(metric_data)
                except Exception as exc:
                    logger.warning("put_metric_data failed for %s: %s", instance_id, exc)
                    break

    return {"datapoints_published": published, "namespace": METRIC_NAMESPACE}


def _map_instances_by_index(instances: List[Dict[str, Any]]) -> Dict[int, str]:
    mapping: Dict[int, str] = {}
    for instance in instances:
        for tag in instance.get("Tags", []):
            if tag.get("Key") == "CGIndex":
                try:
                    mapping[int(tag["Value"])] = instance["InstanceId"]
                except (TypeError, ValueError):
                    continue
    return mapping


def _seed_flow_logs(logs) -> Dict[str, Any]:
    """Write behavioural records into a mocked CloudWatch Logs group."""
    group = settings.log_group_flow_logs
    stream = "cloudguard-demo-stream"

    try:
        logs.create_log_group(logGroupName=group)
    except Exception:
        pass  # ResourceAlreadyExistsException on re-seed
    try:
        logs.create_log_stream(logGroupName=group, logStreamName=stream)
    except Exception:
        pass

    # Skip re-writing if the stream already holds events.
    try:
        existing = logs.describe_log_streams(
            logGroupName=group, logStreamNamePrefix=stream
        ).get("logStreams", [])
        if existing and existing[0].get("storedBytes", 0) > 0:
            return {"log_group": group, "written": 0, "existing": True}
    except Exception:
        pass

    records = datagen.generate_security_records()
    events = [
        {
            "timestamp": int(record["timestamp"].timestamp() * 1000),
            "message": datagen.record_to_flow_log_line(record),
        }
        for record in records
    ]
    # CloudWatch Logs requires chronologically ordered batches.
    events.sort(key=lambda event: event["timestamp"])

    written = 0
    token = None
    for chunk_start in range(0, len(events), 500):
        chunk = events[chunk_start : chunk_start + 500]
        kwargs: Dict[str, Any] = {
            "logGroupName": group,
            "logStreamName": stream,
            "logEvents": chunk,
        }
        if token:
            kwargs["sequenceToken"] = token
        try:
            response = logs.put_log_events(**kwargs)
            token = response.get("nextSequenceToken")
            written += len(chunk)
        except Exception as exc:
            logger.warning("put_log_events failed: %s", exc)
            break

    return {"log_group": group, "log_stream": stream, "written": written}


def reset_flow_logs() -> Dict[str, Any]:
    """Delete and re-seed the flow-log group, discarding injected events.

    The live-anomaly demo appends real events to the log group, so repeated runs
    accumulate. This restores the pristine seeded baseline.
    """
    from app.cloud.aws import get_client

    logs = get_client("logs")
    try:
        logs.delete_log_group(logGroupName=settings.log_group_flow_logs)
    except Exception:
        pass  # ResourceNotFoundException on a cold environment
    return _seed_flow_logs(logs)


def write_live_flow_log_event(record: Dict[str, Any]) -> bool:
    """Append one record to the mocked log group (live anomaly demo)."""
    from app.cloud.aws import get_client

    logs = get_client("logs")
    group = settings.log_group_flow_logs
    stream = "cloudguard-demo-stream"
    try:
        logs.put_log_events(
            logGroupName=group,
            logStreamName=stream,
            logEvents=[
                {
                    "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
                    "message": datagen.record_to_flow_log_line(record),
                }
            ],
        )
        return True
    except Exception as exc:
        logger.warning("Could not append live flow log event: %s", exc)
        return False
