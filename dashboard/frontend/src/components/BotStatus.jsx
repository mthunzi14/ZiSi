import { useState, useEffect } from 'react';
import { useSSE } from '../hooks/useSSE';
import './BotStatus.css';

export default function BotStatus() {
  // ── SSE: instant balance / PnL / trade-count updates ─────────────────────
  const {
    balance: sseBalance,
    pnl: ssePnl,
    trades: sseTrades,
    botStatus: sseBotStatus,
    winCount, lossCount,
    connected,
  } = useSSE();

  // ── Polling: uptime, dailySignals, mules (change infrequently) ───────────
  const [uptime,       setUptime]       = useState('—');
  const [dailySignals, setDailySignals] = useState(0);
  const [isPaused,     setIsPaused]     = useState(false);
  const [mules,        setMules]        = useState({
    mule1: { name: 'Mule1', enabled: true },
    mule2: { name: 'Mule2', enabled: true },
  });

  useEffect(() => {
    const fetchSlow = async () => {
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
        setUptime(h > 0 || m > 0 ? `${h}h ${m}m` : '< 1m');
        setDailySignals(health.dailySignals || 0);
        setIsPaused(control.status === 'paused');
      } catch {
        // health failures show in botStatus via SSE
      }
    };

    fetchSlow();
    const interval = setInterval(fetchSlow, 10_000);   // slower poll — SSE handles the hot path
    return () => clearInterval(interval);
  }, []);

  const handleToggle = async () => {
    try {
      const ep  = isPaused ? '/api/control/resume' : '/api/control/pause';
      const res = await fetch(ep, { method: 'POST' });
      const out = await res.json();
      setIsPaused(out.status === 'paused');
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

  // ── Derived display values ────────────────────────────────────────────────
  const isRunning   = (sseBotStatus === 'running' || sseBotStatus === 'loading') && !isPaused;
  const isOffline   = sseBotStatus === 'offline' || (!connected && sseBotStatus !== 'loading');
  const statusColor = isOffline ? '#ff6363' : isPaused ? '#f59e0b' : '#59d499';
  const statusLabel = sseBotStatus === 'loading' ? 'Loading…'
                    : isPaused       ? 'Paused'
                    : isOffline      ? 'Offline'
                    : 'Running';

  const totalTrades = sseTrades || (winCount + lossCount);
  const winRate     = totalTrades > 0 ? winCount / totalTrades : null;
  const pnlColor    = ssePnl > 0 ? '#59d499' : ssePnl < 0 ? '#ff6363' : 'var(--color-ash-text)';
  const pnlSign     = ssePnl >= 0 ? '+' : '-';

  return (
    <div className="bot-strip-wrap">
      {/* ── Row 1: Core metrics (SSE-powered — updates instantly) ── */}
      <div className="bot-strip">
        <div className="strip-status" style={{ borderColor: statusColor }}>
          <span className="strip-dot" style={{ background: statusColor }} />
          <span style={{ color: statusColor, fontWeight: 700, fontSize: '0.8rem' }}>{statusLabel}</span>
          {connected && (
            <span style={{ marginLeft: 4, fontSize: '0.6rem', color: '#59d499', opacity: 0.7 }}>●</span>
          )}
        </div>

        <div className="strip-divider" />

        <div className="strip-metric">
          <span className="strip-label">Balance</span>
          <span className="strip-value" style={{ color: '#59d499' }}>${sseBalance.toFixed(2)}</span>
        </div>

        <div className="strip-metric">
          <span className="strip-label">P&amp;L</span>
          <span className="strip-value" style={{ color: pnlColor }}>
            {pnlSign}${Math.abs(ssePnl).toFixed(2)}
          </span>
        </div>

        <div className="strip-metric">
          <span className="strip-label">Trades</span>
          <span className="strip-value">{totalTrades}</span>
        </div>

        <div className="strip-metric">
          <span className="strip-label">Win Rate</span>
          <span className="strip-value">
            {winRate !== null ? `${(winRate * 100).toFixed(0)}%` : '—'}
          </span>
        </div>

        <div className="strip-metric">
          <span className="strip-label">W / L</span>
          <span className="strip-value">{winCount} / {lossCount}</span>
        </div>

        <div className="strip-metric">
          <span className="strip-label">Signals Today</span>
          <span className="strip-value">{dailySignals}</span>
        </div>

        <div className="strip-metric">
          <span className="strip-label">Uptime</span>
          <span className="strip-value">{uptime}</span>
        </div>

        <div style={{ flex: 1 }} />

        <button
          onClick={handleToggle}
          className={`strip-toggle ${isPaused ? 'paused' : 'running'}`}
        >
          {isPaused ? '▶ Resume' : '⏸ Pause'}
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
