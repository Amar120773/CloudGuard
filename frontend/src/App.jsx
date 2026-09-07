import React, { useState, useEffect } from 'react';
import Dashboard from './components/Dashboard';
import { Cloud } from 'lucide-react';

function App() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // In Phase 1, we simulate fetching from the Python backend.
    // If the backend isn't running, we provide robust mock data so the UI can be previewed.
    const fetchData = async () => {
      try {
        const response = await fetch('http://localhost:8000/api/summary');
        if (response.ok) {
          const result = await response.json();
          setData(result);
        } else {
          throw new Error('Backend not available');
        }
      } catch (error) {
        console.warn('Backend not reachable, using mock data for dashboard preview.');
        // Fallback mock data
        setData({
          health: {
            overall_health_score: 92,
            status: "Healthy",
            active_instances: 42,
            cpu_utilization_avg: "45%",
            memory_utilization_avg: "60%"
          },
          security: {
            secure: false,
            active_threats: 2,
            open_ports: 1,
            unencrypted_volumes: 0,
            recent_anomalies: [
              { type: "Unusual Login IP", severity: "High", time: "1h ago" },
              { type: "Port 22 Open to 0.0.0.0/0", severity: "Medium", time: "3h ago" }
            ]
          },
          cost: {
            monthly_spend: "$12,450",
            wasted_spend: "$850",
            efficiency_score: 88,
            recommendations: [
              { action: "Downsize staging DB instance", savings: "$320/mo" },
              { action: "Delete unattached EBS volumes", savings: "$150/mo" },
              { action: "Purchase Reserved Instances", savings: "$380/mo" }
            ]
          }
        });
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  return (
    <div className="app-container">
      <header className="header">
        <h1>
          <Cloud size={32} color="#00f2fe" />
          CloudGuard
        </h1>
        <div className="badge">System Active</div>
      </header>

      {loading ? (
        <div className="loading">
          <div className="loader"></div>
          Analyzing Cloud Workloads...
        </div>
      ) : (
        <Dashboard data={data} />
      )}
    </div>
  );
}

export default App;
