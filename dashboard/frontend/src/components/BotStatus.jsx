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
  const [mules, setMules] = useState({
    mule1: { name: 'Mule1', enabled: true },
    mule2: { name: 'Mule2', enabled: true },
  });

  useEffect(() => {
    const fetchAll = async () => {
      try {
        const [healthRes, controlRes, mulesRes] = await Promise.all([
          fetch('/api/health'),
          fetch('/api/control/status'),
          fetch('/api/control/mules'),
        ]);
        const health  = await healthRes.json();
        const control = await controlRes.json();
        if (mulesRes.ok) setMules(await mulesRes.json());

        const h = Math.floor(health.runtime?.hours || 0);
        const m = Math.floor(((health.runtime?.hours || 0) % 1) * 60);

        const ps      = health.positions_summary || {};
        const psWins  = ps.win_count  || 0;
        const psTotal = (ps.win_count || 0) + (ps.loss_count || 0);
        // Win rate: prefer positions_summary (current session), fallback JSONL
        const effWinRate = psTotal > 0 ? psWins / psTotal : (health.winRate || 0);
        // Trade count: include open + closed positions
        const effTrades  = psTotal > 0 ? psTotal : (health.realTrades || 0);
        // Unrealized PnL from open positions
        const unrealizedPnl = parseFloat(ps.unrealized_pnl || 0);
        // Balance: account_state.json is the authority (updated on every trade close)
        const liveBalance = parseFloat(health.balance || 100);
        // Show equity = balance + unrealized
        const equityBalance = liveBalance + unrealizedPnl;
        // P&L = balance minus starting ($100). health.pnl = balance - starting_balance.
        const effPnl = parseFloat(health.pnl || (liveBalance - 100));

        setData({
          status:       health.status || 'offline',
          balance:      parseFloat((equityBalance).toFixed(2)),
          pnl:          parseFloat(effPnl.toFixed(2)),
          realTrades:   effTrades,
          dailyTrades:  health.dailyTrades || 0,
          dailySignals: health.dailySignals || 0,
          winRate:      effWinRate,
          uptime:       h > 0 || m > 0 ? `${h}h ${m}m` : '< 1m',
          isPaused:     control.status === 'paused',
        });
      } catch {
        setData(prev => ({ ...prev, status: 'offline' }));
      }
    };

    fetchAll();
    const interval = setInterval(fetchAll, 5_000);
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

  const handleMuleToggle = async (id) => {
    const current = mules[id]?.enabled ?? true;
    const action  = current ? 'disable' : 'enable';
    try {
      const res = await fetch(`/api/control/mule/${id}/${action}`, { method: 'POST' });
      if (res.ok) {
        setMules(prev => ({ ...prev, [id]: { ...prev[id], enabled: !current } }));
      }
    } catch (e) {
      console.error('Mule toggle failed:', e);
    }
  };

  const isOnline    = data.status === 'running' && !data.isPaused;
  const statusColor = isOnline ? '#59d499' : data.isPaused ? '#f59e0b' : '#ff6363';
  const statusLabel = data.status === 'loading' ? 'Loading…' : data.isPaused ? 'Paused' : isOnline ? 'Running' : 'Offline';
  const pnlColor    = data.pnl > 0 ? '#59d499' : data.pnl < 0 ? '#ff6363' : 'var(--color-ash-text)';
  const pnlSign     = data.pnl >= 0 ? '+' : '-';

  return (
    <div className="bot-strip-wrap">
      {/* ── Row 1: Core metrics ── */}
      <div className="bot-strip">
        <div className="strip-status" style={{ borderColor: statusColor }}>
          <span className="strip-dot" style={{ background: statusColor }} />
          <span style={{ color: statusColor, fontWeight: 700, fontSize: '0.8rem' }}>{statusLabel}</span>
        </div>

        <div className="strip-divider" />

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

        <div style={{ flex: 1 }} />

        <button
          onClick={handleToggle}
          className={`strip-toggle ${data.isPaused ? 'paused' : 'running'}`}
        >
          {data.isPaused ? '▶ Resume' : '⏸ Pause'}
        </button>
      </div>

      {/* ── Row 2: Mule controls ── */}
      <div className="mule-row">
        <span className="mule-row-label">Shadow Mules</span>
        {Object.entries(mules).map(([id, m]) => (
          <button
            key={id}
            onClick={() => handleMuleToggle(id)}
            className={`strip-mule-btn ${m.enabled ? 'mule-on' : 'mule-off'}`}
            title={m.enabled ? `Click to disable ${m.name}` : `Click to enable ${m.name}`}
          >
            {m.enabled ? `👁 ${m.name} ON` : `○ ${m.name} OFF`}
          </button>
        ))}
        <span className="mule-row-hint">Click to toggle shadow copy-trading</span>
      </div>
    </div>
  );
}
