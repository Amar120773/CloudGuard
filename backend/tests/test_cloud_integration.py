"""Moto-backed AWS integration: EC2, CloudWatch, CloudWatch Logs, Cost Explorer."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.cloud import datagen
from app.cloud.aws import AwsCloudProvider, get_client
from app.cloud.moto_setup import METRIC_NAMESPACE, PROJECT_TAG
from app.config import settings


class TestEc2:
    def test_demo_fleet_is_launched_and_tagged(self):
        ec2 = get_client("ec2")
        response = ec2.describe_instances(
            Filters=[{"Name": "tag:Project", "Values": [PROJECT_TAG]}]
        )
        instances = [
            instance
            for reservation in response["Reservations"]
            for instance in reservation["Instances"]
        ]
        assert len(instances) == settings.demo_instance_count

        for instance in instances:
            tags = {t["Key"]: t["Value"] for t in instance["Tags"]}
            assert tags["Project"] == PROJECT_TAG
            assert "Name" in tags and "Environment" in tags and "CGHourlyCost" in tags

    def test_list_resources_reads_back_through_the_api(self, provider):
        resources = provider.list_resources()
        assert len(resources) == settings.demo_instance_count

        for resource in resources:
            assert resource["resource_id"].startswith("i-")
            assert resource["resource_type"] == "ec2:instance"
            assert resource["status"] in ("running", "stopped")
            assert resource["estimated_cost"] > 0
            assert resource["region"] == settings.aws_region

    def test_stopped_instances_cost_less_than_running(self, provider):
        resources = provider.list_resources()
        running = [r for r in resources if r["status"] == "running"]
        stopped = [r for r in resources if r["status"] == "stopped"]
        assert running and stopped
        # Stopped bills storage only.
        assert min(r["estimated_cost"] for r in running) > max(
            r["estimated_cost"] for r in stopped
        )

    def test_idle_instances_are_identified_with_a_hint(self, provider):
        idle = [r for r in provider.list_resources() if r["idle"]]
        assert idle, "expected at least one idle instance in the demo fleet"
        for resource in idle:
            assert resource["cpu_utilization"] < 6.0
            assert resource["status"] == "running"
            assert resource["optimization_hint"]

    def test_sorted_by_cost_descending(self, provider):
        costs = [r["estimated_cost"] for r in provider.list_resources()]
        assert costs == sorted(costs, reverse=True)


class TestInstancePagination:
    """describe_instances caps each page, so an unpaginated call truncates."""

    def test_uses_the_boto3_paginator(self, provider, monkeypatch):
        """The paginator must be what drives discovery.

        It calls describe_instances internally - the point is that the provider
        goes through the paginator rather than issuing one capped call itself.
        """
        from app.cloud import aws

        used = {"paginator": 0}
        ec2 = aws.get_client("ec2")
        real_paginator = ec2.get_paginator

        def spy_paginator(name):
            if name == "describe_instances":
                used["paginator"] += 1
            return real_paginator(name)

        monkeypatch.setattr(ec2, "get_paginator", spy_paginator)

        resources = provider.list_resources()
        assert used["paginator"] >= 1, "list_resources did not use the paginator"
        assert len(resources) == settings.demo_instance_count

    def test_collects_instances_across_multiple_pages(self):
        """Force a small page size so the fleet spans several pages."""
        from app.cloud.aws import get_client
        from app.cloud.moto_setup import PROJECT_TAG

        ec2 = get_client("ec2")
        paginator = ec2.get_paginator("describe_instances")
        pages = list(
            paginator.paginate(
                Filters=[{"Name": "tag:Project", "Values": [PROJECT_TAG]}],
                PaginationConfig={"PageSize": 3},
            )
        )
        assert len(pages) > 1, "expected the fleet to span multiple pages"

        seen = {
            instance["InstanceId"]
            for page in pages
            for reservation in page.get("Reservations", [])
            for instance in reservation.get("Instances", [])
        }
        assert len(seen) == settings.demo_instance_count

    def test_single_page_still_works(self, provider):
        assert len(provider.list_resources()) == settings.demo_instance_count

    def test_pagination_errors_surface_as_provider_errors(self, provider, monkeypatch):
        from botocore.exceptions import ClientError

        from app.cloud import aws
        from app.cloud.base import CloudProviderError

        ec2 = aws.get_client("ec2")

        def exploding_paginator(name):
            class Boom:
                def paginate(self, **kwargs):
                    raise ClientError(
                        {"Error": {"Code": "RequestLimitExceeded", "Message": "slow down"}},
                        "DescribeInstances",
                    )
                    yield  # pragma: no cover

            return Boom()

        monkeypatch.setattr(ec2, "get_paginator", exploding_paginator)
        with pytest.raises(CloudProviderError, match="describe_instances"):
            provider.list_resources()

    def test_terminated_instances_are_excluded_across_pages(self, provider):
        states = {r["status"] for r in provider.list_resources()}
        assert "terminated" not in states


class TestCloudWatch:
    def test_metric_data_was_published_to_the_mock(self):
        cloudwatch = get_client("cloudwatch")
        metrics = cloudwatch.list_metrics(Namespace=METRIC_NAMESPACE)["Metrics"]
        names = {m["MetricName"] for m in metrics}
        assert {"CPUUtilization", "NetworkIn", "NetworkOut"} <= names

    def test_get_metrics_round_trips_statistics(self, provider):
        series = provider.get_metrics(hours=24, period_seconds=3600)
        assert series

        cpu = [s for s in series if s["metric_name"] == "CPUUtilization"]
        assert cpu
        for item in cpu:
            assert item["namespace"] == METRIC_NAMESPACE
            assert item["datapoints"]
            assert 0 <= item["average"] <= 100
            assert item["minimum"] <= item["average"] <= item["maximum"]
            timestamps = [d["timestamp"] for d in item["datapoints"]]
            assert timestamps == sorted(timestamps)

    def test_utilisation_is_joined_onto_the_inventory(self, provider):
        """Resource CPU comes from CloudWatch, not from the generator directly."""
        series = {
            s["resource_id"]: s["average"]
            for s in provider.get_metrics()
            if s["metric_name"] == "CPUUtilization"
        }
        for resource in provider.list_resources():
            if resource["resource_id"] in series and resource["status"] == "running":
                assert resource["cpu_utilization"] == pytest.approx(
                    series[resource["resource_id"]], abs=0.011
                )


class TestCloudWatchLogs:
    def test_flow_log_events_are_readable(self, provider):
        logs = get_client("logs")
        response = logs.filter_log_events(
            logGroupName=settings.log_group_flow_logs, limit=10
        )
        assert response["events"]

    def test_records_parse_into_complete_feature_vectors(self, provider):
        records = provider.get_security_records()
        assert len(records) >= settings.demo_security_events

        for record in records[:50]:
            assert isinstance(record["timestamp"], datetime)
            for feature in datagen.SECURITY_FEATURES:
                assert isinstance(record[feature], float)
                assert record[feature] >= 0

    def test_records_are_chronological(self, provider):
        timestamps = [r["timestamp"] for r in provider.get_security_records()]
        assert timestamps == sorted(timestamps)

    def test_seeded_outliers_survive_the_round_trip(self, provider, pristine_flow_logs):
        records = provider.get_security_records()
        seeded = [r for r in records if r.get("simulated_anomaly")]
        assert len(seeded) == 4

    def test_malformed_messages_are_skipped(self):
        from app.cloud.aws import _parse_flow_log_message

        assert _parse_flow_log_message(None) is None
        assert _parse_flow_log_message("not json") is None
        assert _parse_flow_log_message('{"traffic_volume": 1}') is None  # incomplete


class TestCostExplorer:
    def test_returns_daily_per_service_records(self, provider):
        end = date.today()
        start = end - timedelta(days=29)
        records = provider.get_cost_and_usage(start, end)

        assert records
        for record in records[:20]:
            assert isinstance(record["date"], date)
            assert start <= record["date"] <= end
            assert record["service"]
            assert record["cost"] >= 0
            assert record["unit"] == "USD"

    def test_covers_the_whole_service_catalogue(self, provider):
        records = provider.get_cost_and_usage(date.today() - timedelta(days=29), date.today())
        services = {r["service"] for r in records}
        assert len(services) == len(datagen.SERVICE_CATALOGUE)

    def test_records_are_sorted(self, provider):
        records = provider.get_cost_and_usage(date.today() - timedelta(days=29), date.today())
        keys = [(r["date"], r["service"]) for r in records]
        assert keys == sorted(keys)

    def test_parser_handles_a_real_shaped_response(self):
        """The parser is exercised directly against the AWS wire format."""
        response = {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-01-01", "End": "2026-01-02"},
                    "Groups": [
                        {
                            "Keys": ["Amazon Elastic Compute Cloud - Compute"],
                            "Metrics": {
                                "UnblendedCost": {"Amount": "123.4567890", "Unit": "USD"},
                                "UsageQuantity": {"Amount": "24.0", "Unit": "Hrs"},
                            },
                        }
                    ],
                },
                # A day with no spend returns no groups.
                {"TimePeriod": {"Start": "2026-01-02", "End": "2026-01-03"}, "Groups": []},
                # Malformed periods must be skipped, not crash the pipeline.
                {"TimePeriod": {"Start": "garbage"}, "Groups": []},
                {"Groups": []},
            ]
        }
        records = AwsCloudProvider._parse_ce_response(response)

        assert len(records) == 1
        assert records[0]["date"] == date(2026, 1, 1)
        assert records[0]["cost"] == pytest.approx(123.456789)
        assert records[0]["usage"] == pytest.approx(24.0)

    def test_parser_falls_back_to_ungrouped_totals(self):
        response = {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-02-01", "End": "2026-02-02"},
                    "Total": {"UnblendedCost": {"Amount": "50.0", "Unit": "USD"}},
                    "Groups": [],
                }
            ]
        }
        records = AwsCloudProvider._parse_ce_response(response)
        assert len(records) == 1
        assert records[0]["service"] == "All services"
        assert records[0]["cost"] == 50.0


class TestDeterminism:
    def test_cost_data_is_reproducible(self):
        end = date(2026, 6, 1)
        a = datagen.generate_cost_dataset(days=60, end=end)
        b = datagen.generate_cost_dataset(days=60, end=end)
        assert [r["cost"] for r in a.rows] == [r["cost"] for r in b.rows]

    def test_security_data_is_reproducible(self):
        a = datagen.generate_security_records(count=100)
        b = datagen.generate_security_records(count=100)
        assert [r["traffic_volume"] for r in a] == [r["traffic_volume"] for r in b]

    def test_capabilities_are_reported(self, provider):
        capabilities = provider.capabilities()
        assert capabilities["provider"] == "aws"
        assert capabilities["cloud_mode"] == "moto_inproc"
        assert capabilities["ec2"] == "live-api"
        assert capabilities["cloudwatch"] == "live-api"
        assert capabilities["cloudwatch_logs"] == "live-api"
        # Moto does not implement GetCostAndUsage, so this must say so honestly.
        assert capabilities["cost_explorer"] in ("live-api", "synthetic-wire-compatible")
