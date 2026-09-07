import pandas as pd
from prophet import Prophet
from typing import List, Dict

class CostForecaster:
    def __init__(self):
        # We can configure Prophet parameters here (e.g., weekly_seasonality)
        self.model = Prophet(daily_seasonality=True, yearly_seasonality=False)
        
    def forecast_spend(self, historical_costs: List[Dict], days_ahead: int = 30) -> float:
        """
        Predicts future spend based on historical Cost Explorer data.
        Expects a list of dictionaries with 'date' (YYYY-MM-DD) and 'amount'.
        Returns the forecasted sum for the next `days_ahead` days.
        """
        if not historical_costs or len(historical_costs) < 2:
            return 0.0
            
        # Prophet expects columns to be named 'ds' (datestamp) and 'y' (value)
        df = pd.DataFrame(historical_costs)
        df = df.rename(columns={'date': 'ds', 'amount': 'y'})
        
        # Convert 'ds' to datetime
        df['ds'] = pd.to_datetime(df['ds'])
        
        # Fit the model
        self.model.fit(df)
        
        # Create future dataframe
        future = self.model.make_future_dataframe(periods=days_ahead)
        
        # Predict
        forecast = self.model.predict(future)
        
        # Sum the forecasted 'yhat' for the future periods
        future_forecast = forecast.tail(days_ahead)
        total_forecasted_spend = future_forecast['yhat'].sum()
        
        return round(total_forecasted_spend, 2)
