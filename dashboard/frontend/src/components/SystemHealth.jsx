// SystemHealth.jsx — infrastructure status + circuit breaker + candle timers
const ASSETS_TF = ['BTC/5m', 'BTC/15m', 'ETH/5m', 'SOL/5m', 'XRP/5m'];

function StatusIcon({ ok, warn, off }) {
  if (off)  return <span style={{ color: 'var(--color-loss)',  fontSize: 14 }}>🔴</span>;
  if (warn) return <span style={{ color: 'var(--color-amber)', fontSize: 14 }}>⚠️</span>;
  return       <span style={{ color: 'var(--color-profit)',  fontSize: 14 }}>✅</span>;
}

function Row({ label, value, ok, warn, off }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '5px 0', borderBottom: '1px solid rgba(255,255,255,0.04)',
      fontSize: 12,
    }}>
      <span style={{ color: 'var(--color-text-muted)' }}>{label}</span>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-text-primary)' }}>{value}</span>
        <StatusIcon ok={ok} warn={warn} off={off} />
      </div>
    </div>
  );
}

function fmtSecs(s) {
  if (s == null) return '—';
  return `${Math.floor(s / 60)}m ${(s % 60).toString().padStart(2,'0')}s`;
}

export default function SystemHealth({ state = {}, positions = {}, candles = [] }) {
  const active   = positions?.active || [];
  const summary  = positions?.summary || {};

  const minutesAgo = state.last_update_minutes_ago ?? state.minutesAgo ?? null;
  const isAlive    = minutesAgo !== null && minutesAgo < 10;
  const isStale    = minutesAgo !== null && minutesAgo >= 10 && minutesAgo < 30;

  const pnl     = parseFloat(state.pnl || 0);
  const balance = parseFloat(state.balance || 100);
  const drawPct = ((100 - balance) / 100 * 100).toFixed(1);

  return (
    <div style={{
      background: 'var(--color-bg-surface)',
      borderRadius: 'var(--radius-cards)',
      border: '1px solid var(--color-midnight)',
      padding: 'var(--spacing-20)',
    }}>
      <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 500, fontSize: 16, marginBottom: 12 }}>
        System Health
      </div>

      {/* Infrastructure */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 10, color: 'var(--color-accent)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>
          Infrastructure
        </div>
        <Row label="Bot heartbeat" value={isAlive ? `${minutesAgo}m ago` : isStale ? `${minutesAgo}m ago (STALE)` : 'offline'} ok={isAlive} warn={isStale} off={!isAlive && !isStale} />
        <Row label="Open positions" value={active.length} ok={active.length <= 4} warn={active.length > 4} />
        <Row label="Daily drawdown" value={`${drawPct}%`} ok={parseFloat(drawPct) < 10} warn={parseFloat(drawPct) >= 10 && parseFloat(drawPct) < 15} off={parseFloat(drawPct) >= 15} />
        <Row label="Total P&L" value={`${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`} ok={pnl >= 0} warn={pnl < 0 && pnl > -5} off={pnl <= -5} />
      </div>

      {/* Candle timers */}
      <div>
        <div style={{ fontSize: 10, color: 'var(--color-accent)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>
          Next Candle Boundaries
        </div>
        {ASSETS_TF.map(key => {
          const [asset, tf] = key.split('/');
          const candle = candles.find(c => c.asset === asset && c.tf === tf);
          const secs = candle?.secs ?? null;
          return (
            <Row
              key={key}
              label={key}
              value={fmtSecs(secs)}
              ok={secs !== null && secs > 60}
              warn={secs !== null && secs <= 60 && secs > 15}
              off={secs !== null && secs <= 15}
            />
          );
        })}
      </div>
    </div>
  );
}
