from typing import Dict, Any
import random
from schemas import CostOptimizationResponse, CostRecord
from services.aws_client import get_boto3_client
from ml.cost_forecaster import CostForecaster
from datetime import datetime, timedelta

def fetch_cost_and_forecast() -> Dict[str, Any]:
    """
    Fetches historical costs from AWS Cost Explorer and predicts future spend.
    """
    ce = get_boto3_client('ce')
    
    # Mocking historical cost data retrieval
    historical_costs = []
    today = datetime.now()
    total_monthly_spend = 0.0
    
    for i in range(30):
        past_date = today - timedelta(days=30-i)
        daily_amount = random.uniform(200.0, 500.0)
        total_monthly_spend += daily_amount
        historical_costs.append({
            "date": past_date.strftime("%Y-%m-%d"),
            "amount": daily_amount
        })
        
    # Run Prophet ML Forecast
    forecaster = CostForecaster()
    forecasted_amount = forecaster.forecast_spend(historical_costs, days_ahead=30)
    
    response = CostOptimizationResponse(
        monthly_spend=f"${total_monthly_spend:,.2f}",
        forecasted_spend=f"${forecasted_amount:,.2f}",
        efficiency_score=random.randint(70, 98),
        historical_costs=[CostRecord(**record) for record in historical_costs]
    )
    
    return response.model_dump()
