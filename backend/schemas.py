from pydantic import BaseModel, Field
from typing import List, Optional

# --- Health Schemas ---
class InstanceHealth(BaseModel):
    instance_id: str
    state: str
    cpu_utilization: float = Field(..., description="CPU Utilization percentage")
    memory_utilization: float = Field(..., description="Memory Utilization percentage")

class WorkloadHealthResponse(BaseModel):
    overall_health_score: float
    status: str
    active_instances: int
    instances: List[InstanceHealth]

# --- Security Schemas ---
class SecurityFinding(BaseModel):
    id: str
    title: str
    severity: str
    description: str

class SecurityPostureResponse(BaseModel):
    secure: bool
    active_threats: int
    findings: List[SecurityFinding]

# --- Cost Schemas ---
class CostRecord(BaseModel):
    date: str
    amount: float

class CostOptimizationResponse(BaseModel):
    monthly_spend: str
    forecasted_spend: str
    efficiency_score: int
    historical_costs: List[CostRecord]
