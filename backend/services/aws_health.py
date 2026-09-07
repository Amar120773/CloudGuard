import random
from datetime import datetime, timedelta
from typing import Dict, Any
from schemas import WorkloadHealthResponse, InstanceHealth
from services.aws_client import get_boto3_client

def fetch_workload_health() -> Dict[str, Any]:
    """
    Fetches live (or mocked) data from CloudWatch and EC2.
    """
    ec2 = get_boto3_client('ec2')
    cloudwatch = get_boto3_client('cloudwatch')
    
    # In a real scenario with Moto, we would need to mock instance creation first.
    # Since Moto starts empty, we'll simulate the data extraction logic as if instances existed.
    # We will use the Pydantic schema to enforce the structure.
    
    try:
        instances = ec2.describe_instances()
        instance_count = sum(len(res['Instances']) for res in instances.get('Reservations', []))
    except Exception as e:
        instance_count = 0
        
    # Mocking the parsed response for the sake of the dashboard
    health_score = random.randint(70, 100)
    
    instances_data = []
    for i in range(max(1, instance_count)):
        instances_data.append(InstanceHealth(
            instance_id=f"i-{random.randint(1000, 9999)}",
            state="running",
            cpu_utilization=random.uniform(10.0, 90.0),
            memory_utilization=random.uniform(20.0, 85.0)
        ))
        
    response = WorkloadHealthResponse(
        overall_health_score=health_score,
        status="Healthy" if health_score > 85 else "Warning",
        active_instances=len(instances_data),
        instances=instances_data
    )
    
    return response.model_dump()
