from typing import Dict, Any
import random
from schemas import SecurityPostureResponse, SecurityFinding
from services.aws_client import get_boto3_client
from ml.anomaly_detector import SecurityAnomalyDetector

def fetch_security_posture() -> Dict[str, Any]:
    """
    Fetches data from AWS Security Hub and VPC Flow Logs, and runs Isolation Forest.
    """
    # Initialize boto3 clients
    securityhub = get_boto3_client('securityhub')
    
    # Simulate fetching raw event logs (e.g. from CloudWatch/VPC Flow logs)
    raw_logs = []
    for _ in range(50):
        raw_logs.append({
            "bytes_transferred": random.uniform(100, 5000),
            "failed_logins": random.randint(0, 5),
            "latency_ms": random.uniform(10, 150)
        })
        
    # Inject a couple of obvious anomalies for the Isolation Forest to detect
    raw_logs.append({"bytes_transferred": 50000, "failed_logins": 50, "latency_ms": 1000})
    raw_logs.append({"bytes_transferred": 45000, "failed_logins": 45, "latency_ms": 900})
    
    # Run ML Model
    detector = SecurityAnomalyDetector()
    analyzed_logs = detector.detect_anomalies(raw_logs)
    
    anomalies_count = sum(1 for log in analyzed_logs if log.get('is_anomaly'))
    
    # Construct Security Findings (Simulating Security Hub format)
    findings = []
    for i in range(anomalies_count):
        findings.append(SecurityFinding(
            id=f"arn:aws:securityhub:us-east-1:123456789012:finding/{i}",
            title="Anomalous Network Activity Detected",
            severity="HIGH" if i % 2 == 0 else "MEDIUM",
            description="Isolation Forest model flagged unusual bytes transferred and failed logins."
        ))
        
    response = SecurityPostureResponse(
        secure=anomalies_count == 0,
        active_threats=anomalies_count,
        findings=findings
    )
    
    return response.model_dump()
