"""Deterministic synthetic cloud data.

Everything here is seeded from `settings.demo_seed`, so the demo is byte-for-byte
reproducible (spec principle 10) and the evaluation metrics (MAE, precision,
recall) are stable across runs.

Note the split of responsibilities: this module produces *raw observations
only*. It never labels an event as an anomaly for the UI - the IsolationForest
model does that downstream. The `simulated_anomaly` flag travels alongside as
hidden ground truth used purely to score the model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

import numpy as np

from app.config import settings

# --------------------------------------------------------------------------
# Service catalogue. `growth` drives the end-of-month risk story: the two
# services with the steepest growth become the "disproportionate contributors"
# the spec asks the AI Insights panel to call out.
# --------------------------------------------------------------------------
SERVICE_CATALOGUE: List[Dict[str, Any]] = [
    {"name": "Amazon Elastic Compute Cloud - Compute", "short": "EC2", "base": 118.0, "growth": 0.0022, "weekend_factor": 0.82, "noise": 0.07, "unit": "Hrs"},
    {"name": "Amazon Relational Database Service", "short": "RDS", "base": 74.0, "growth": 0.0048, "weekend_factor": 0.95, "noise": 0.05, "unit": "Hrs"},
    {"name": "Amazon Simple Storage Service", "short": "S3", "base": 41.0, "growth": 0.0012, "weekend_factor": 0.98, "noise": 0.04, "unit": "GB-Mo"},
    {"name": "AWS Lambda", "short": "Lambda", "base": 16.5, "growth": 0.0009, "weekend_factor": 0.70, "noise": 0.11, "unit": "Requests"},
    {"name": "Amazon CloudWatch", "short": "CloudWatch", "base": 12.0, "growth": 0.0006, "weekend_factor": 0.93, "noise": 0.06, "unit": "Metrics"},
    {"name": "AWS Data Transfer", "short": "DataTransfer", "base": 28.0, "growth": 0.0052, "weekend_factor": 0.76, "noise": 0.13, "unit": "GB"},
    {"name": "Amazon DynamoDB", "short": "DynamoDB", "base": 19.0, "growth": 0.0014, "weekend_factor": 0.88, "noise": 0.08, "unit": "ReadUnits"},
    {"name": "Amazon Elastic Kubernetes Service", "short": "EKS", "base": 22.0, "growth": 0.0018, "weekend_factor": 1.0, "noise": 0.03, "unit": "Hrs"},
]

INSTANCE_SHAPES: List[Tuple[str, float, int]] = [
    # (instance_type, on-demand USD/hour, vCPU)
    ("t3.micro", 0.0104, 2),
    ("t3.small", 0.0208, 2),
    ("t3.medium", 0.0416, 2),
    ("t3.large", 0.0832, 2),
    ("m5.large", 0.0960, 2),
    ("m5.xlarge", 0.1920, 4),
    ("m5.2xlarge", 0.3840, 8),
    ("c5.xlarge", 0.1700, 4),
    ("r5.large", 0.1260, 2),
]

ENVIRONMENTS = ["production", "staging", "development", "production", "production"]
OWNERS = ["platform-team", "data-team", "web-team", "ml-team"]
HOURS_PER_MONTH = 730.0

# Behavioural features consumed by the IsolationForest. Order is the contract
# between the generator, the model and the schema.
SECURITY_FEATURES: List[str] = [
    "traffic_volume",
    "network_transfer",
    "connection_count",
    "request_frequency",
    "failed_api_requests",
    "authentication_failures",
    "distinct_source_ips",
]


def _rng(salt: int = 0) -> np.random.RandomState:
    return np.random.RandomState(settings.demo_seed + salt)


# ==========================================================================
# Cost Explorer data
# ==========================================================================
@dataclass
class CostDataset:
    """Per-service daily spend plus the gaps we deliberately leave in."""

    rows: List[Dict[str, Any]] = field(default_factory=list)
    missing_dates: List[date] = field(default_factory=list)

    def total(self) -> float:
        return float(sum(r["cost"] for r in self.rows))


def generate_cost_dataset(
    days: int | None = None,
    end: date | None = None,
) -> CostDataset:
    """Build `days` of daily per-service spend ending today (inclusive).

    The series carries a mild upward trend, weekly seasonality (cheaper
    weekends), a mid-history step change, and two deliberate one-day gaps so
    the cleaning stage in the forecaster is genuinely exercised.
    """
    days = days or settings.demo_history_days
    end = end or datetime.now(timezone.utc).date()
    rng = _rng(11)

    start = end - timedelta(days=days - 1)
    all_dates = [start + timedelta(days=i) for i in range(days)]

    # Two gaps in the middle of history: Cost Explorer really does have these.
    gap_indices = {max(1, days // 3), max(2, days // 3 + 1)}
    missing = [all_dates[i] for i in sorted(gap_indices) if i < len(all_dates)]
    missing_set = set(missing)

    # A step change two-thirds in (a new workload went live).
    step_start = all_dates[int(days * 0.66)] if days > 6 else all_dates[0]

    rows: List[Dict[str, Any]] = []
    for svc in SERVICE_CATALOGUE:
        for idx, day in enumerate(all_dates):
            if day in missing_set:
                continue
            trend = 1.0 + svc["growth"] * idx
            seasonal = svc["weekend_factor"] if day.weekday() >= 5 else 1.0
            # Gentle intra-week ripple on top of the weekend effect.
            ripple = 1.0 + 0.035 * np.sin(2 * np.pi * day.weekday() / 7.0)
            step = 1.18 if (day >= step_start and svc["short"] in ("RDS", "DataTransfer")) else 1.0
            noise = 1.0 + rng.normal(0, svc["noise"])
            cost = svc["base"] * trend * seasonal * ripple * step * max(0.35, noise)
            cost = float(max(0.0, round(cost, 4)))
            # Usage is loosely coupled to cost, as it is in real billing data.
            usage = float(round(cost * rng.uniform(7.5, 12.5), 3))
            rows.append(
                {
                    "date": day,
                    "service": svc["name"],
                    "service_short": svc["short"],
                    "usage": usage,
                    "cost": cost,
                    "unit": "USD",
                    "usage_unit": svc["unit"],
                }
            )

    rows.sort(key=lambda r: (r["date"], r["service"]))
    return CostDataset(rows=rows, missing_dates=missing)


def cost_dataset_to_ce_response(
    dataset: CostDataset,
    start: date,
    end: date,
    granularity: str = "DAILY",
) -> Dict[str, Any]:
    """Render a dataset in the exact shape of `ce:GetCostAndUsage`.

    Keeping the synthetic payload wire-compatible with the real API means the
    parsing code in `aws.py` is production code: swapping Moto for real AWS
    changes the client, not the parser.
    """
    by_day: Dict[date, List[Dict[str, Any]]] = {}
    for row in dataset.rows:
        if start <= row["date"] <= end:
            by_day.setdefault(row["date"], []).append(row)

    results = []
    for day in sorted(by_day):
        groups = [
            {
                "Keys": [row["service"]],
                "Metrics": {
                    "UnblendedCost": {"Amount": f"{row['cost']:.10f}", "Unit": "USD"},
                    "UsageQuantity": {
                        "Amount": f"{row['usage']:.10f}",
                        "Unit": row["usage_unit"],
                    },
                },
            }
            for row in sorted(by_day[day], key=lambda r: r["service"])
        ]
        results.append(
            {
                "TimePeriod": {
                    "Start": day.isoformat(),
                    "End": (day + timedelta(days=1)).isoformat(),
                },
                "Total": {},
                "Groups": groups,
                "Estimated": False,
            }
        )

    return {
        "GroupDefinitions": [{"Type": "DIMENSION", "Key": "SERVICE"}],
        "ResultsByTime": results,
        "DimensionValueAttributes": [],
        "ResponseMetadata": {"HTTPStatusCode": 200},
    }


# ==========================================================================
# EC2 inventory
# ==========================================================================
def generate_instance_specs(count: int | None = None) -> List[Dict[str, Any]]:
    """Deterministic EC2 fleet definition, used to seed Moto."""
    count = count or settings.demo_instance_count
    rng = _rng(23)
    now = datetime.now(timezone.utc)

    specs: List[Dict[str, Any]] = []
    for i in range(count):
        shape_idx = rng.randint(0, len(INSTANCE_SHAPES))
        itype, hourly, vcpu = INSTANCE_SHAPES[shape_idx]
        env = ENVIRONMENTS[rng.randint(0, len(ENVIRONMENTS))]
        owner = OWNERS[rng.randint(0, len(OWNERS))]

        # Roughly one in six instances is deliberately idle: that is the
        # "cloud waste" the platform is meant to surface.
        idle = (i % 6 == 2)
        if idle:
            cpu = float(round(rng.uniform(0.6, 4.2), 2))
        elif env == "production":
            cpu = float(round(rng.uniform(38.0, 86.0), 2))
        else:
            cpu = float(round(rng.uniform(12.0, 55.0), 2))

        stopped = (i % 7 == 5)
        specs.append(
            {
                "index": i,
                "instance_type": itype,
                "hourly_cost": hourly,
                "vcpu": vcpu,
                "name": f"cg-{env[:4]}-{['api','worker','db','cache','batch','edge','ml','etl'][i % 8]}-{i + 1:02d}",
                "environment": env,
                "owner": owner,
                "state": "stopped" if stopped else "running",
                "cpu_utilization": 0.0 if stopped else cpu,
                "network_in_mb": 0.0 if stopped else float(round(rng.uniform(40, 2400), 1)),
                "network_out_mb": 0.0 if stopped else float(round(rng.uniform(25, 1800), 1)),
                "launch_time": now - timedelta(days=int(rng.randint(3, 400)), hours=int(rng.randint(0, 24))),
                "idle": idle and not stopped,
            }
        )
    return specs


def monthly_cost_for(hourly_cost: float, state: str) -> float:
    """Stopped instances bill only for attached storage, not compute."""
    if state != "running":
        return float(round(hourly_cost * HOURS_PER_MONTH * 0.08, 2))
    return float(round(hourly_cost * HOURS_PER_MONTH, 2))


# ==========================================================================
# CloudWatch metric datapoints
# ==========================================================================
def generate_metric_datapoints(
    spec: Dict[str, Any],
    hours: int = 24,
    period_seconds: int = 3600,
) -> Dict[str, List[Dict[str, Any]]]:
    """Hourly CPU / network datapoints for one instance, with a diurnal shape."""
    rng = _rng(101 + spec["index"])
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    steps = max(1, int(hours * 3600 / period_seconds))

    series: Dict[str, List[Dict[str, Any]]] = {
        "CPUUtilization": [],
        "NetworkIn": [],
        "NetworkOut": [],
    }
    if spec["state"] != "running":
        return series

    base_cpu = spec["cpu_utilization"]
    for step in range(steps):
        ts = now - timedelta(seconds=period_seconds * (steps - step))
        # Business-hours style bulge, phased on the step index rather than
        # ts.hour: a wall-clock phase would make average CPU - and hence whether
        # an instance is classified idle - depend on the time of day the demo
        # runs.
        hours_elapsed = (step * period_seconds) / 3600.0
        diurnal = 1.0 + 0.32 * np.sin(2 * np.pi * (hours_elapsed - 6.0) / 24.0)
        cpu = float(np.clip(base_cpu * diurnal * (1.0 + rng.normal(0, 0.09)), 0.1, 99.9))
        series["CPUUtilization"].append({"timestamp": ts, "value": round(cpu, 3), "unit": "Percent"})
        # CloudWatch reports network counters in bytes.
        nin = max(0.0, spec["network_in_mb"] * diurnal * (1.0 + rng.normal(0, 0.16)) * 1024 * 1024 / steps)
        nout = max(0.0, spec["network_out_mb"] * diurnal * (1.0 + rng.normal(0, 0.18)) * 1024 * 1024 / steps)
        series["NetworkIn"].append({"timestamp": ts, "value": round(nin, 2), "unit": "Bytes"})
        series["NetworkOut"].append({"timestamp": ts, "value": round(nout, 2), "unit": "Bytes"})

    return series


# ==========================================================================
# VPC flow-log / access behavioural records
# ==========================================================================
ANOMALY_PROFILES: List[Dict[str, Any]] = [
    {
        "kind": "exfiltration",
        "multipliers": {"network_transfer": 34.0, "traffic_volume": 22.0},
        "absolute": {},
    },
    {
        "kind": "credential_stuffing",
        "multipliers": {"request_frequency": 6.0},
        "absolute": {"authentication_failures": 182.0, "failed_api_requests": 240.0},
    },
    {
        "kind": "port_scan",
        "multipliers": {"connection_count": 19.0},
        "absolute": {"distinct_source_ips": 410.0, "failed_api_requests": 96.0},
    },
    {
        "kind": "api_hammering",
        "multipliers": {"request_frequency": 17.0, "connection_count": 7.0},
        "absolute": {"failed_api_requests": 150.0},
    },
]


def generate_security_records(
    count: int | None = None,
    interval_seconds: int = 120,
    inject_extra_anomaly: bool = False,
) -> List[Dict[str, Any]]:
    """Behavioural observation windows for VPC flow / access analytics.

    Each record is one aggregation window over a network interface. A small,
    deterministic subset is pushed far outside the normal operating envelope;
    those carry `simulated_anomaly=True` as hidden ground truth.
    """
    count = count or settings.demo_security_events
    rng = _rng(37)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    interfaces = [f"eni-{rng.randint(0x10000000, 0x7fffffff):08x}" for _ in range(6)]
    principals = [
        "arn:aws:iam::123456789012:user/ci-deployer",
        "arn:aws:iam::123456789012:role/app-runtime",
        "arn:aws:iam::123456789012:user/analytics-readonly",
        "arn:aws:iam::123456789012:role/lambda-exec",
    ]

    # Seed anomaly positions away from the very start so the model has context,
    # and keep the most recent one near the end so the demo surfaces it.
    anomaly_positions = {
        int(count * 0.28),
        int(count * 0.52),
        int(count * 0.77),
        count - 3,
    }

    records: List[Dict[str, Any]] = []
    for i in range(count):
        ts = now - timedelta(seconds=interval_seconds * (count - i))
        # Phase is driven by the record's position, never by the wall clock.
        # Keying it to ts.hour would make the whole dataset - and therefore the
        # anomaly count and the evaluation metrics - depend on what time of day
        # the demo is run, which breaks reproducibility (spec principle 10).
        hours_elapsed = (i * interval_seconds) / 3600.0
        diurnal = 1.0 + 0.40 * np.sin(2 * np.pi * (hours_elapsed - 7.0) / 24.0)

        record = {
            "event_id": f"evt-{settings.demo_seed}-{i:05d}",
            "timestamp": ts,
            "interface_id": interfaces[i % len(interfaces)],
            "principal": principals[i % len(principals)],
            "region": settings.aws_region,
            # Normal operating envelope.
            "traffic_volume": float(max(0.0, rng.lognormal(12.6, 0.42) * diurnal)),
            "network_transfer": float(max(0.0, rng.lognormal(3.1, 0.50) * diurnal)),
            "connection_count": float(max(0.0, rng.normal(96, 22) * diurnal)),
            "request_frequency": float(max(0.0, rng.normal(148, 34) * diurnal)),
            "failed_api_requests": float(max(0.0, rng.poisson(2.1))),
            "authentication_failures": float(max(0.0, rng.poisson(0.55))),
            "distinct_source_ips": float(max(1.0, rng.normal(23, 6))),
            "simulated_anomaly": False,
            "simulated_kind": None,
        }

        if i in anomaly_positions:
            profile = ANOMALY_PROFILES[len(records) % len(ANOMALY_PROFILES)]
            for feature, mult in profile["multipliers"].items():
                record[feature] = float(record[feature] * mult)
            for feature, value in profile["absolute"].items():
                record[feature] = float(value * rng.uniform(0.86, 1.2))
            record["simulated_anomaly"] = True
            record["simulated_kind"] = profile["kind"]

        for feature in SECURITY_FEATURES:
            record[feature] = float(round(record[feature], 4))
        records.append(record)

    if inject_extra_anomaly:
        records.append(_build_live_anomaly(now, interfaces[0], principals[0]))

    return records


def _build_live_anomaly(now: datetime, interface: str, principal: str) -> Dict[str, Any]:
    """An extreme, unmistakable outlier for the live demo button."""
    rng = _rng(997)
    return {
        "event_id": f"evt-live-{int(now.timestamp())}",
        "timestamp": now,
        "interface_id": interface,
        "principal": principal,
        "region": settings.aws_region,
        "traffic_volume": float(round(rng.uniform(9.0e6, 1.4e7), 2)),
        "network_transfer": float(round(rng.uniform(5200, 8800), 2)),
        "connection_count": float(round(rng.uniform(1400, 2600), 1)),
        "request_frequency": float(round(rng.uniform(2300, 4100), 1)),
        "failed_api_requests": float(round(rng.uniform(320, 640), 0)),
        "authentication_failures": float(round(rng.uniform(210, 430), 0)),
        "distinct_source_ips": float(round(rng.uniform(380, 720), 0)),
        "simulated_anomaly": True,
        "simulated_kind": "live_injected_exfiltration",
    }


def record_to_flow_log_line(record: Dict[str, Any]) -> str:
    """Serialise a record for CloudWatch Logs `put_log_events`."""
    import json

    payload = dict(record)
    payload["timestamp"] = record["timestamp"].isoformat()
    return json.dumps(payload, separators=(",", ":"))
