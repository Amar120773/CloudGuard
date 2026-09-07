import random
from typing import Dict, Any

def get_workload_health() -> Dict[str, Any]:
    """Simulates checking the health of cloud workloads."""
    health_score = random.randint(70, 100)
    status = "Healthy" if health_score > 85 else "Warning"
    
    return {
        "overall_health_score": health_score,
        "status": status,
        "active_instances": random.randint(5, 50),
        "cpu_utilization_avg": f"{random.randint(20, 90)}%",
        "memory_utilization_avg": f"{random.randint(30, 85)}%"
    }

def get_security_posture() -> Dict[str, Any]:
    """Simulates security analysis of the workloads."""
    threats = random.randint(0, 5)
    return {
        "secure": threats == 0,
        "active_threats": threats,
        "open_ports": random.randint(0, 3),
        "unencrypted_volumes": random.randint(0, 2),
        "recent_anomalies": [
            {"type": "Unusual Login", "severity": "High", "time": "2h ago"} for _ in range(threats)
        ]
    }

def get_cost_optimization() -> Dict[str, Any]:
    """Simulates cost and resource efficiency analysis."""
    wasted_spend = random.randint(100, 1500)
    return {
        "monthly_spend": f"${random.randint(5000, 15000)}",
        "wasted_spend": f"${wasted_spend}",
        "efficiency_score": random.randint(60, 98),
        "recommendations": [
            {"action": "Downsize EC2 instance i-0abcd1234", "savings": "$120/mo"},
            {"action": "Delete unattached EBS volume vol-0xyz9876", "savings": "$45/mo"}
        ] if wasted_spend > 500 else []
    }
