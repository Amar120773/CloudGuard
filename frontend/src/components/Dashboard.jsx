import React from 'react';
import { Activity, ShieldAlert, DollarSign, AlertTriangle, CheckCircle } from 'lucide-react';

const Dashboard = ({ data }) => {
  if (!data) return null;

  const { health, security, cost } = data;

  return (
    <main className="dashboard-grid">
      {/* Health Card */}
      <section className="glass-card">
        <div className="card-header">
          <div className="card-icon health">
            <Activity size={24} />
          </div>
          <h2>Workload Health</h2>
        </div>
        <div className="metric-value" style={{ color: health.overall_health_score > 85 ? 'var(--success)' : 'var(--warning)' }}>
          {health.overall_health_score}%
        </div>
        <div className="metric-label">Overall Health Score</div>
        
        <ul className="data-list">
          <li className="data-item">
            <span className="data-item-label">Status</span>
            <span className="data-item-value" style={{ color: health.status === 'Healthy' ? 'var(--success)' : 'var(--warning)' }}>
              {health.status}
            </span>
          </li>
          <li className="data-item">
            <span className="data-item-label">Active Instances</span>
            <span className="data-item-value">{health.active_instances}</span>
          </li>
          <li className="data-item">
            <span className="data-item-label">Avg CPU Utilization</span>
            <span className="data-item-value">{health.cpu_utilization_avg}</span>
          </li>
          <li className="data-item">
            <span className="data-item-label">Avg Memory Utilization</span>
            <span className="data-item-value">{health.memory_utilization_avg}</span>
          </li>
        </ul>
      </section>

      {/* Security Card */}
      <section className="glass-card">
        <div className="card-header">
          <div className="card-icon security">
            <ShieldAlert size={24} />
          </div>
          <h2>Security Posture</h2>
        </div>
        <div className="metric-value" style={{ color: security.secure ? 'var(--success)' : 'var(--danger)' }}>
          {security.active_threats}
        </div>
        <div className="metric-label">Active Threats Detected</div>
        
        <ul className="data-list">
          <li className="data-item">
            <span className="data-item-label">Status</span>
            <span className="data-item-value" style={{ display: 'flex', alignItems: 'center', gap: '4px', color: security.secure ? 'var(--success)' : 'var(--danger)' }}>
              {security.secure ? <><CheckCircle size={16} /> Secure</> : <><AlertTriangle size={16} /> Vulnerable</>}
            </span>
          </li>
          <li className="data-item">
            <span className="data-item-label">Open Ports</span>
            <span className="data-item-value">{security.open_ports}</span>
          </li>
          <li className="data-item">
            <span className="data-item-label">Unencrypted Volumes</span>
            <span className="data-item-value">{security.unencrypted_volumes}</span>
          </li>
        </ul>

        {security.recent_anomalies && security.recent_anomalies.length > 0 && (
          <div style={{ marginTop: '1rem' }}>
            <div className="metric-label" style={{ marginBottom: '0.5rem' }}>Recent Anomalies:</div>
            <ul className="data-list" style={{ marginTop: 0 }}>
              {security.recent_anomalies.map((anomaly, idx) => (
                <li key={idx} className="data-item" style={{ border: 'none', padding: '0.25rem 0', fontSize: '0.85rem' }}>
                  <span style={{ color: 'var(--danger)' }}>• {anomaly.type}</span>
                  <span className="data-item-label">{anomaly.time}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {/* Cost Card */}
      <section className="glass-card">
        <div className="card-header">
          <div className="card-icon cost">
            <DollarSign size={24} />
          </div>
          <h2>Cost Optimization</h2>
        </div>
        <div className="metric-value" style={{ color: 'var(--accent-blue)' }}>
          {cost.monthly_spend}
        </div>
        <div className="metric-label">Est. Monthly Spend</div>
        
        <ul className="data-list">
          <li className="data-item">
            <span className="data-item-label">Wasted Spend</span>
            <span className="data-item-value" style={{ color: 'var(--danger)' }}>{cost.wasted_spend}</span>
          </li>
          <li className="data-item">
            <span className="data-item-label">Efficiency Score</span>
            <span className="data-item-value" style={{ color: cost.efficiency_score > 80 ? 'var(--success)' : 'var(--warning)' }}>
              {cost.efficiency_score}/100
            </span>
          </li>
        </ul>

        {cost.recommendations && cost.recommendations.length > 0 && (
          <div style={{ marginTop: '1rem' }}>
            <div className="metric-label" style={{ marginBottom: '0.5rem' }}>Recommendations:</div>
            <ul className="data-list" style={{ marginTop: 0 }}>
              {cost.recommendations.map((rec, idx) => (
                <li key={idx} className="data-item" style={{ border: 'none', padding: '0.25rem 0', fontSize: '0.85rem' }}>
                  <span style={{ color: 'var(--text-primary)' }}>• {rec.action}</span>
                  <span style={{ color: 'var(--success)', fontWeight: 600 }}>{rec.savings}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>
    </main>
  );
};

export default Dashboard;
