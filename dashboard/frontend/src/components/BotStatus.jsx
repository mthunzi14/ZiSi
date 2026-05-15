import { useState, useEffect } from 'react';
import './BotStatus.css';

export default function BotStatus() {
  const [data, setData] = useState({
    status: 'loading',
    balance: 100.00,
    pnl: 0.00,
    realTrades: 0,
    dailyTrades: 0,
    dailySignals: 0,
    winRate: 0,
    uptime: '—',
    isPaused: false,
  });

  useEffect(() => {
    const fetchAll = async () => {
      try {
        const [healthRes, controlRes] = await Promise.all([
          fetch('/api/health'),
          fetch('/api/control/status'),
        ]);
        const health  = await healthRes.json();
        const control = await controlRes.json();

        const h = Math.floor(health.runtime?.hours || 0);
        const m = Math.floor(((health.runtime?.hours || 0) % 1) * 60);

        setData({
          status:       health.status || 'offline',
          balance:      parseFloat(health.balance  || 100),
          pnl:          parseFloat(health.pnl       || 0),
          realTrades:   health.realTrades  || 0,
          dailyTrades:  health.dailyTrades || 0,
          dailySignals: health.dailySignals || health.totalSignals || 0,
          winRate:      health.winRate || 0,
          uptime:       h > 0 || m > 0 ? `${h}h ${m}m` : '< 1m',
          isPaused:     control.status === 'paused',
        });
      } catch {
        setData(prev => ({ ...prev, status: 'offline' }));
      }
    };

    fetchAll();
    const interval = setInterval(fetchAll, 10_000);
    return () => clearInterval(interval);
  }, []);

  const handleToggle = async () => {
    try {
      const ep  = data.isPaused ? '/api/control/resume' : '/api/control/pause';
      const res = await fetch(ep, { method: 'POST' });
      const out = await res.json();
      setData(prev => ({ ...prev, isPaused: out.status === 'paused' }));
    } catch (e) {
      console.error('Toggle failed:', e);
    }
  };

  const isOnline    = data.status === 'running' && !data.isPaused;
  const statusColor = isOnline ? '#59d499' : data.isPaused ? '#f59e0b' : '#ff6363';
  const statusLabel = data.status === 'loading' ? 'Loading…' : data.isPaused ? 'Paused' : isOnline ? 'Running' : 'Offline';
  const pnlColor    = data.pnl > 0 ? '#59d499' : data.pnl < 0 ? '#ff6363' : 'var(--color-ash-text)';
  const pnlSign     = data.pnl > 0 ? '+' : '';

  return (
    <div className="bot-strip">
      {/* Status pill */}
      <div className="strip-status" style={{ borderColor: statusColor }}>
        <span className="strip-dot" style={{ background: statusColor }} />
        <span style={{ color: statusColor, fontWeight: 700, fontSize: '0.8rem' }}>{statusLabel}</span>
      </div>

      <div className="strip-divider" />

      {/* Key metrics inline */}
      <div className="strip-metric">
        <span className="strip-label">Balance</span>
        <span className="strip-value" style={{ color: '#59d499' }}>${data.balance.toFixed(2)}</span>
      </div>

      <div className="strip-metric">
        <span className="strip-label">P&amp;L</span>
        <span className="strip-value" style={{ color: pnlColor }}>
          {pnlSign}${Math.abs(data.pnl).toFixed(2)}
        </span>
      </div>

      <div className="strip-metric">
        <span className="strip-label">Trades</span>
        <span className="strip-value">{data.realTrades}</span>
      </div>

      <div className="strip-metric">
        <span className="strip-label">Win Rate</span>
        <span className="strip-value">
          {data.realTrades > 0 ? `${(data.winRate * 100).toFixed(0)}%` : '—'}
        </span>
      </div>

      <div className="strip-metric">
        <span className="strip-label">Signals Today</span>
        <span className="strip-value">{data.dailySignals}</span>
      </div>

      <div className="strip-metric">
        <span className="strip-label">Uptime</span>
        <span className="strip-value">{data.uptime}</span>
      </div>

      {/* Spacer + toggle button */}
      <div style={{ flex: 1 }} />

      <button
        onClick={handleToggle}
        className={`strip-toggle ${data.isPaused ? 'paused' : 'running'}`}
      >
        {data.isPaused ? '▶ Resume' : '⏸ Pause'}
      </button>
    </div>
  );
}
