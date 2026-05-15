import React, { useState, useEffect } from 'react';
import './EdgeValidation.css';

export default function EdgeValidation({ data }) {
  const [edgeStatus, setEdgeStatus] = useState('validating');

  useEffect(() => {
    if (!data || data.realTrades === 0) {
      setEdgeStatus('validating');
    } else if (data.winRate >= 0.55 && data.profitFactor > 1.0) {
      setEdgeStatus('real');
    } else if (data.winRate < 0.45) {
      setEdgeStatus('broken');
    } else {
      setEdgeStatus('uncertain');
    }
  }, [data]);

  const getEdgeColor = () => {
    switch (edgeStatus) {
      case 'real': return '#59d499';
      case 'broken': return '#ff6363';
      case 'uncertain': return '#f59e0b';
      default: return '#6b62f2';
    }
  };

  const getEdgeText = () => {
    switch (edgeStatus) {
      case 'real': return 'Edge Validated';
      case 'broken': return 'Edge Broken';
      case 'uncertain': return 'Edge Uncertain';
      default: return 'Collecting Data';
    }
  };

  return (
    <section className="edge-validation">
      <h2>Edge Validation</h2>

      <div className="edge-status-card">
        <div className="edge-badge" style={{ borderColor: getEdgeColor() }}>
          <span className="edge-dot" style={{ background: getEdgeColor() }}></span>
          <span className="edge-text" style={{ color: getEdgeColor() }}>
            {getEdgeText()}
          </span>
        </div>

        <div className="edge-metrics">
          <div className="edge-metric">
            <span className="metric-label">Win Rate</span>
            <span className="metric-value">
              {data?.winRate !== undefined ? `${(data.winRate * 100).toFixed(1)}%` : 'N/A'}
            </span>
            <span className="metric-requirement">Target: ≥55%</span>
          </div>

          <div className="edge-metric">
            <span className="metric-label">Profit Factor</span>
            <span className="metric-value">
              {data?.profitFactor !== undefined ? data.profitFactor.toFixed(2) : 'N/A'}
            </span>
            <span className="metric-requirement">Target: &gt;1.0</span>
          </div>

          <div className="edge-metric">
            <span className="metric-label">Expectancy</span>
            <span className="metric-value">
              {data?.expectancy !== undefined ? `$${data.expectancy.toFixed(2)}` : 'N/A'}
            </span>
            <span className="metric-requirement">Avg win - Avg loss</span>
          </div>

          <div className="edge-metric">
            <span className="metric-label">Trades for Validation</span>
            <span className="metric-value">
              {data?.realTrades !== undefined ? data.realTrades : 0} / 20
            </span>
            <span className="metric-requirement">Phase 1 target</span>
          </div>
        </div>

        <div className="edge-progress">
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{
                width: `${Math.min((data?.realTrades || 0) / 20 * 100, 100)}%`,
                background: getEdgeColor(),
              }}
            ></div>
          </div>
          <span className="progress-text">
            {data?.realTrades || 0} real trades collected
          </span>
        </div>
      </div>
    </section>
  );
}
