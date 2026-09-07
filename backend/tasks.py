from celery_app import celery_app
from services.aws_health import fetch_workload_health
from services.aws_security import fetch_security_posture
from services.aws_cost import fetch_cost_and_forecast

@celery_app.task(name="tasks.update_health_data")
def update_health_data():
    """Background task to fetch and process health data."""
    return fetch_workload_health()

@celery_app.task(name="tasks.update_security_data")
def update_security_data():
    """Background task to fetch security data and run ML anomaly detection."""
    return fetch_security_posture()

@celery_app.task(name="tasks.update_cost_data")
def update_cost_data():
    """Background task to fetch cost data and run ML forecasting."""
    return fetch_cost_and_forecast()
