import { useState, useEffect } from 'react';
import './AnalyticsBySection.css';

export default function AnalyticsBySection() {
  const [analytics, setAnalytics] = useState({ byCoin: [], byStrength: [] });

  useEffect(() => {
    const fetchAnalytics = async () => {
      try {
        const res = await fetch('/api/health');
        const data = await res.json();
        setAnalytics({
          byCoin: data.byCoin || [],
          byStrength: data.byStrength || [],
        });
      } catch (error) {
        console.error('Analytics fetch failed:', error);
      }
    };

    fetchAnalytics();
    const interval = setInterval(fetchAnalytics, 15000);
    return () => clearInterval(interval);
  }, []);

  return (
    <section className="analytics-section">
      <h2>Signal Analytics</h2>

      <div className="analytics-grid">
        <div className="analytics-card">
          <h3>By Coin</h3>
          <div className="coin-list">
            {analytics.byCoin.length > 0 ? (
              analytics.byCoin.map((coin, idx) => (
                <div key={idx} className="coin-item">
                  <span className="coin-name">{coin.name}</span>
                  <span className="coin-count">{coin.count}</span>
                </div>
              ))
            ) : (
              <p className="text-muted">No data yet</p>
            )}
          </div>
        </div>

        <div className="analytics-card">
          <h3>By Signal Strength</h3>
          <div className="strength-list">
            {analytics.byStrength.length > 0 ? (
              analytics.byStrength.map((s, idx) => (
                <div key={idx} className="strength-item">
                  <span className="strength-label">Score {s.level}</span>
                  <span className="strength-count">{s.count}</span>
                </div>
              ))
            ) : (
              <p className="text-muted">No data yet</p>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
