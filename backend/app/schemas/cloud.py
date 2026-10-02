"""Cloud inventory and metric schemas."""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import Field

from app.schemas.common import CloudGuardModel, DataFreshness


class CloudResource(CloudGuardModel):
    resource_id: str
    resource_type: str = Field(description="e.g. ec2:instance")
    name: Optional[str] = None
    service: str = Field(description="e.g. Amazon Elastic Compute Cloud - Compute")
    region: str
    availability_zone: Optional[str] = None
    status: str = Field(description="running | stopped | terminated | ...")
    instance_type: Optional[str] = None
    usage: float = Field(0.0, ge=0, description="Primary utilisation signal, percent")
    cpu_utilization: Optional[float] = Field(None, ge=0, le=100)
    network_in_mb: Optional[float] = Field(None, ge=0)
    network_out_mb: Optional[float] = Field(None, ge=0)
    estimated_cost: float = Field(0.0, ge=0, description="Estimated monthly cost, USD")
    environment: Optional[str] = None
    owner: Optional[str] = None
    launch_time: Optional[datetime] = None
    idle: bool = Field(False, description="Low utilisation but still incurring cost")
    optimization_hint: Optional[str] = None


class MetricDataPoint(CloudGuardModel):
    timestamp: datetime
    value: float
    unit: str = "None"


class MetricSeries(CloudGuardModel):
    namespace: str = "AWS/EC2"
    metric_name: str
    resource_id: Optional[str] = None
    statistic: str = "Average"
    unit: str = "None"
    datapoints: List[MetricDataPoint] = Field(default_factory=list)
    average: Optional[float] = None
    maximum: Optional[float] = None
    minimum: Optional[float] = None


class CloudResourcesResponse(CloudGuardModel):
    generated_at: Optional[datetime] = Field(
        None, description="When the pipeline produced this payload (UTC)"
    )
    region: str
    total_resources: int = Field(0, ge=0)
    running: int = Field(0, ge=0)
    stopped: int = Field(0, ge=0)
    idle_resources: int = Field(0, ge=0)
    estimated_monthly_cost: float = Field(0.0, ge=0)
    potential_monthly_savings: float = Field(0.0, ge=0)
    resources: List[CloudResource] = Field(default_factory=list)
    freshness: DataFreshness = Field(default_factory=DataFreshness)


class CloudMetricsResponse(CloudGuardModel):
    generated_at: Optional[datetime] = Field(
        None, description="When the pipeline produced this payload (UTC)"
    )
    region: str
    collected_at: Optional[datetime] = None
    period_seconds: int = Field(3600, ge=60)
    series: List[MetricSeries] = Field(default_factory=list)
    average_cpu: Optional[float] = None
    peak_cpu: Optional[float] = None
    total_network_out_mb: Optional[float] = None
    freshness: DataFreshness = Field(default_factory=DataFreshness)


class CloudHealthResponse(CloudGuardModel):
    """Aggregate "is my estate healthy" view used by the overview page."""

    status: Literal["Healthy", "Warning", "Critical", "Unknown"] = "Unknown"
    health_score: float = Field(0.0, ge=0, le=100)
    active_resources: int = Field(0, ge=0)
    idle_resources: int = Field(0, ge=0)
    average_cpu: Optional[float] = None
    estimated_monthly_cost: float = Field(0.0, ge=0)
    freshness: DataFreshness = Field(default_factory=DataFreshness)
