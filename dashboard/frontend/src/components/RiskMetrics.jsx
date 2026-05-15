import React, { useEffect, useState } from 'react';
import './RiskMetrics.css';

export default function RiskMetrics({ data }) {
  const [risks, setRisks] = useState({
    maxDrawdown: 0,
    currentDrawdown: 0,
    consecutiveLosses: 0,
    riskOfRuin: 'Low',
  });

  useEffect(() => {
    if (data) {
      setRisks({
        maxDrawdown: data.maxDrawdown || 0,
        currentDrawdown: data.currentDrawdown || 0,
        consecutiveLosses: data.consecutiveLosses || 0,
        riskOfRuin: data.riskOfRuin || 'Low',
      });
    }
  }, [data]);

  const getRiskColor = (riskLevel) => {
    switch (riskLevel) {
      case 'High': return '#ff6363';
      case 'Medium': return '#f59e0b';
      default: return '#59d499';
    }
  };

  return (
    <section className="risk-metrics">
      <h3>Risk Management</h3>

      <div className="risk-grid">
        <div className="risk-card">
          <span className="risk-label">Max Drawdown</span>
          <span className="risk-value">{risks.maxDrawdown.toFixed(2)}%</span>
          <span className="risk-help">Peak to trough loss</span>
        </div>

        <div className="risk-card">
          <span className="risk-label">Current Drawdown</span>
          <span className="risk-value">{risks.currentDrawdown.toFixed(2)}%</span>
          <span className="risk-help">Active losing streak</span>
        </div>

        <div className="risk-card">
          <span className="risk-label">Consecutive Losses</span>
          <span className="risk-value">{risks.consecutiveLosses}</span>
          <span className="risk-help">Losing streak length</span>
        </div>

        <div className="risk-card">
          <span className="risk-label">Risk of Ruin</span>
          <span
            className="risk-value"
            style={{ color: getRiskColor(risks.riskOfRuin) }}
          >
            {risks.riskOfRuin}
          </span>
          <span className="risk-help">Portfolio health</span>
        </div>
      </div>
    </section>
  );
}
