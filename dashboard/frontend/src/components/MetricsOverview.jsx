import { useState, useEffect } from 'react';
import './MetricsOverview.css';

export default function MetricsOverview() {
  const [metrics, setMetrics] = useState({
    totalSignals: 0,
    dailySignals: 0,
    realTrades: 0,
    dailyTrades: 0,
    totalPnL: 0.00,
    dailyPnL: 0.00,
    winRate: 0,
  });

  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        const res = await fetch('/api/health');
        const data = await res.json();

        setMetrics({
          totalSignals: data.totalSignals || 0,
          dailySignals: data.dailySignals || 0,
          realTrades: data.realTrades || 0,
          dailyTrades: data.dailyTrades || 0,
          totalPnL: parseFloat(data.pnl || 0.00),
          dailyPnL: parseFloat(data.dailyPnL || 0.00),
          winRate: data.winRate || 0,
        });
      } catch (error) {
        console.error('Metrics fetch failed:', error);
      }
    };

    fetchMetrics();
    const interval = setInterval(fetchMetrics, 15000);
    return () => clearInterval(interval);
  }, []);

  return (
    <section className="metrics-overview">
      <h2>Performance Metrics</h2>

      <div className="metrics-section">
        <h3>Today</h3>
        <div className="metrics-grid">
          <div className="metric-card">
            <span className="card-label">Signals Evaluated</span>
            <span className="card-value">{metrics.dailySignals}</span>
          </div>
          <div className="metric-card">
            <span className="card-label">Real Trades</span>
            <span className="card-value">{metrics.dailyTrades}</span>
          </div>
          <div className="metric-card">
            <span className="card-label">P&amp;L</span>
            <span
              className="card-value"
              style={{ color: metrics.dailyPnL >= 0 ? '#59d499' : '#ff6363' }}
            >
              ${metrics.dailyPnL.toFixed(2)}
            </span>
          </div>
        </div>
      </div>

      <div className="metrics-section">
        <h3>Lifetime (Phase 1)</h3>
        <div className="metrics-grid">
          <div className="metric-card">
            <span className="card-label">Total Signals</span>
            <span className="card-value">{metrics.totalSignals}</span>
          </div>
          <div className="metric-card">
            <span className="card-label">Real Trades</span>
            <span className="card-value">{metrics.realTrades}</span>
          </div>
          <div className="metric-card">
            <span className="card-label">Total P&amp;L</span>
            <span
              className="card-value"
              style={{ color: metrics.totalPnL >= 0 ? '#59d499' : '#ff6363' }}
            >
              ${metrics.totalPnL.toFixed(2)}
            </span>
          </div>
          <div className="metric-card">
            <span className="card-label">Win Rate</span>
            <span className="card-value">
              {metrics.realTrades > 0 ? `${(metrics.winRate * 100).toFixed(1)}%` : 'N/A'}
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}
