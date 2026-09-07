from sklearn.ensemble import IsolationForest
import pandas as pd
import numpy as np
from typing import List, Dict

class SecurityAnomalyDetector:
    def __init__(self):
        # contamination sets the expected proportion of outliers (anomalies)
        self.model = IsolationForest(contamination=0.1, random_state=42)
        
    def detect_anomalies(self, event_logs: List[Dict]) -> List[Dict]:
        """
        Takes a list of event logs (e.g., VPC Flow Logs, CloudTrail) and detects anomalies.
        Expects a list of dictionaries with numerical features (e.g., 'bytes_transferred', 'failed_logins').
        """
        if not event_logs:
            return []
            
        df = pd.DataFrame(event_logs)
        
        # In a real scenario, we would preprocess the data to ensure we only pass numerical features
        # For this example, we assume the dicts only contain numerical values we want to score
        numerical_df = df.select_dtypes(include=[np.number])
        
        if numerical_df.empty:
            return []
            
        # Fit the model and predict
        # Predict returns 1 for normal, -1 for anomaly
        predictions = self.model.fit_predict(numerical_df)
        
        # Attach predictions to the original logs
        results = []
        for i, log in enumerate(event_logs):
            log_result = log.copy()
            log_result['is_anomaly'] = bool(predictions[i] == -1)
            results.append(log_result)
            
        return results
