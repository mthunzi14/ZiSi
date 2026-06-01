// SystemHealth.jsx — infrastructure status + circuit breaker
function StatusIcon({ ok, warn, off }) {
  let color = '#00e676'; // Ultra-vibrant success emerald green
  let glow = 'rgba(0, 230, 118, 0.85)';
  if (off) {
    color = '#ff1744'; // Ultra-vibrant danger coral red
    glow = 'rgba(255, 23, 68, 0.85)';
  } else if (warn) {
    color = '#ff9100'; // Ultra-vibrant warning amber orange
    glow = 'rgba(255, 145, 0, 0.85)';
  }

  return (
    <div 
      style={{
        width: 14,
        height: 14,
        borderRadius: '50%',
        backgroundColor: color,
        boxShadow: `0 0 14px 3px ${glow}, 0 0 4px 1px ${glow}`,
        display: 'inline-block',
        marginLeft: 6,
        flexShrink: 0,
        border: '1px solid rgba(255, 255, 255, 0.6)' // White ring to pop dot against card background
      }} 
      title={off ? "Off/Offline" : warn ? "Warning/Stale" : "Active/Healthy"}
    />
  );
}

function Row({ label, value, ok, warn, off }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '7px 0', borderBottom: '1px solid var(--color-border-subtle)',
      fontSize: 12.5,
    }}>
      <span style={{ color: 'var(--color-text-secondary)', fontWeight: 500 }}>{label}</span>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-text-primary)', fontWeight: '600' }}>{value}</span>
        <StatusIcon ok={ok} warn={warn} off={off} />
      </div>
    </div>
  );
}

export default function SystemHealth({ state = {}, positions = {}, uptime = '00:00:00' }) {
  const active   = positions?.active || [];
  
  const minutesAgo = state.last_update_minutes_ago ?? state.minutesAgo ?? null;
  const isAlive    = minutesAgo !== null && minutesAgo < 10;
  const isStale    = minutesAgo !== null && minutesAgo >= 10 && minutesAgo < 30;

  const pnl          = parseFloat(state.pnl || 0);
  const balance      = parseFloat(state.balance || 0);
  const startBalance = parseFloat(state.starting_balance || balance || 100);
  const drawPct      = startBalance > 0 ? Math.max(0, ((startBalance - balance) / startBalance * 100)).toFixed(1) : '0.0';

  return (
    <div 
      className="card shadow-sm"
      style={{
        padding: '24px',
      }}
    >
      <div style={{ fontFamily: 'var(--font-primary)', fontWeight: 700, fontSize: '16px', color: 'var(--color-obsidian)', marginBottom: '16px' }}>
        System Health
      </div>

      {/* Infrastructure */}
      <div>
        <div style={{ fontSize: 10, color: 'var(--color-accent)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>
          Infrastructure
        </div>
        <Row label="Bot heartbeat" value={isAlive ? `${minutesAgo}m ago` : isStale ? `${minutesAgo}m ago (STALE)` : 'offline'} ok={isAlive} warn={isStale} off={!isAlive && !isStale} />
        <Row label="Session Uptime" value={uptime} ok={state.running} />
        <Row label="Open positions" value={active.length} ok={active.length <= 4} warn={active.length > 4} />
        <Row label="Daily drawdown" value={`${drawPct}%`} ok={parseFloat(drawPct) < 10} warn={parseFloat(drawPct) >= 10 && parseFloat(drawPct) < 15} off={parseFloat(drawPct) >= 15} />
        <Row label="Total P&L" value={`${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`} ok={pnl >= 0} warn={pnl < 0 && pnl > -5} off={pnl <= -5} />
      </div>
    </div>
  );
}
