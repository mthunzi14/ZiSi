// SystemHealth.jsx — Bloomberg-grade infrastructure monitor
import { useState } from 'react';

function StatusDot({ ok, warn, off, pulse = false }) {
  const color = off ? '#ef4444' : warn ? '#f97316' : '#10b981';
  return (
    <span style={{
      display: 'inline-block', width: 7, height: 7, borderRadius: '50%',
      background: color,
      boxShadow: `0 0 ${ok && pulse ? '5px' : '2px'} ${color}88`,
      flexShrink: 0,
      animation: ok && pulse ? 'alertPulse 2.5s infinite' : 'none',
    }} />
  );
}

function KPI({ label, value, sub, color }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{ fontSize: 8, color: '#52525b', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 600 }}>{label}</span>
      <span style={{ fontFamily: 'monospace', fontSize: 14, fontWeight: 800, color: color || 'var(--color-text-primary)', lineHeight: 1 }}>{value}</span>
      {sub && <span style={{ fontSize: 9, color: '#71717a' }}>{sub}</span>}
    </div>
  );
}

function DataRow({ label, value, ok, warn, off }) {
  const valColor = off ? '#ef4444' : warn ? '#f97316' : ok ? '#10b981' : '#a1a1aa';
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '4px 0', borderBottom: '1px solid rgba(255,255,255,0.04)', fontSize: 11,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <StatusDot ok={ok} warn={warn} off={off} pulse={ok} />
        <span style={{ color: '#71717a', fontWeight: 500 }}>{label}</span>
      </div>
      <span style={{ fontFamily: 'monospace', fontWeight: 700, fontSize: 11, color: valColor }}>{value}</span>
    </div>
  );
}

function SecHead({ title, color }) {
  return (
    <div style={{
      fontSize: 8, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase',
      color: color || '#c59b27', marginTop: 10, marginBottom: 3, paddingBottom: 3,
      borderBottom: `1px solid ${color || '#c59b27'}33`,
    }}>{title}</div>
  );
}

export default function SystemHealth({ state = {}, positions = {}, candles = [], uptime = '00:00:00' }) {
  const [expanded, setExpanded] = useState(true);

  const active       = positions?.active || [];
  const closed       = positions?.closed || [];
  const minutesAgo   = state.last_update_minutes_ago ?? state.minutesAgo ?? null;
  const isAlive      = minutesAgo !== null && minutesAgo < 4;
  const isStale      = minutesAgo !== null && minutesAgo >= 4 && minutesAgo < 10;
  const isOffline    = minutesAgo === null || minutesAgo >= 10;

  const pnl          = parseFloat(state.pnl || 0);
  const balance      = parseFloat(state.balance || 0);
  const startBalance = parseFloat(state.starting_balance || balance || 50);
  const drawPct      = startBalance > 0 ? Math.max(0, (startBalance - balance) / startBalance * 100) : 0;

  const wins   = closed.filter(p => parseFloat(p.realized_pnl ?? 0) > 0).length;
  const losses = closed.filter(p => parseFloat(p.realized_pnl ?? 0) < 0).length;
  const decisive = wins + losses;
  const wr     = decisive > 0 ? (wins / decisive * 100).toFixed(1) : '—';

  const grossW = closed.filter(p => parseFloat(p.realized_pnl ?? 0) > 0).reduce((s, p) => s + parseFloat(p.realized_pnl ?? 0), 0);
  const grossL = closed.filter(p => parseFloat(p.realized_pnl ?? 0) < 0).reduce((s, p) => s + Math.abs(parseFloat(p.realized_pnl ?? 0)), 0);
  const pf     = grossL > 0 ? (grossW / grossL).toFixed(2) : '∞';

  const cbActive = state.circuit_breaker_active || false;
  const heartbeat = isOffline ? 'OFFLINE' : isStale ? `${minutesAgo}m (STALE)` : minutesAgo !== null ? `${minutesAgo}m ago` : '—';

  const liveColor = isAlive ? '#10b981' : isStale ? '#f97316' : '#ef4444';
  const liveLabel = isAlive ? 'LIVE' : isStale ? 'STALE' : 'OFFLINE';

  return (
    <div className="glass-panel" style={{ padding: 'var(--spacing-20)', display: 'flex', flexDirection: 'column' }}>

      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: expanded ? 12 : 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 15 }}>System Health</span>

          <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 5,
            background: `${liveColor}15`, border: `1px solid ${liveColor}44`,
            borderRadius: 8, padding: '3px 9px',
            fontSize: 9, fontWeight: 700, letterSpacing: '0.06em', color: liveColor,
          }}>
            <span style={{
              width: 5, height: 5, borderRadius: '50%', background: liveColor,
              display: 'inline-block', animation: isAlive ? 'alertPulse 2s infinite' : 'none',
            }} />
            {liveLabel}
          </span>

          <span style={{
            fontFamily: 'monospace', fontSize: 11, fontWeight: 800,
            color: pnl >= 0 ? '#10b981' : '#ef4444',
            background: pnl >= 0 ? 'rgba(16,185,129,0.08)' : 'rgba(239,68,68,0.08)',
            border: `1px solid ${pnl >= 0 ? '#10b98130' : '#ef444430'}`,
            borderRadius: 6, padding: '2px 8px',
          }}>
            ${balance.toFixed(2)}
          </span>
        </div>

        <button onClick={() => setExpanded(e => !e)} style={{
          background: 'none', border: 'none', cursor: 'pointer', color: '#52525b', fontSize: 9,
          display: 'inline-block', padding: '2px 6px',
          transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)',
          transition: 'transform 0.25s cubic-bezier(0.4,0,0.2,1)',
        }}>▾</button>
      </div>

      {/* Body */}
      <div style={{
        maxHeight: expanded ? '600px' : '0px', overflow: 'hidden',
        transition: 'max-height 0.35s cubic-bezier(0.4,0,0.2,1)',
      }}>

        {/* KPI row */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
          <KPI label="Net P&L"
            value={`${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`}
            sub={`${pnl >= 0 ? '+' : ''}${startBalance > 0 ? ((pnl/startBalance)*100).toFixed(1) : 0}% return`}
            color={pnl >= 0 ? '#10b981' : '#ef4444'} />
          <KPI label="Win Rate"
            value={wr === '—' ? '—' : `${wr}%`}
            sub={`${wins}W · ${losses}L`}
            color={parseFloat(wr) >= 65 ? '#10b981' : parseFloat(wr) >= 50 ? '#f97316' : '#ef4444'} />
          <KPI label="Profit Factor"
            value={pf}
            sub={`${grossW.toFixed(2)} gross`}
            color={parseFloat(pf) >= 1.5 || pf === '∞' ? '#10b981' : parseFloat(pf) >= 1 ? '#f97316' : '#ef4444'} />
        </div>

        <SecHead title="Infrastructure" color="#c59b27" />
        <DataRow label="Engine heartbeat"  value={heartbeat}              ok={isAlive}   warn={isStale}  off={isOffline} />
        <DataRow label="Session uptime"    value={uptime}                 ok={!!state.running} warn={false} off={!state.running} />
        <DataRow label="Open positions"    value={`${active.length} / 6`} ok={active.length <= 3} warn={active.length > 3 && active.length < 6} off={active.length >= 6} />
        <DataRow label="Daily drawdown"    value={`${drawPct.toFixed(1)}%`} ok={drawPct < 5} warn={drawPct >= 5 && drawPct < 12} off={drawPct >= 12} />

        <SecHead title="Circuit Breaker" color="#f97316" />
        <DataRow label="CB status"      value={cbActive ? `ACTIVE` : 'CLEAR'}             ok={!cbActive} off={cbActive} />
        <DataRow label="Trades session" value={state.trades_executed ?? closed.length}     ok />

        <SecHead title="Session Stats" color="#2b7fff" />
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', columnGap: 16 }}>
          <DataRow label="Starting bal" value={`$${startBalance.toFixed(2)}`} ok />
          <DataRow label="Current bal"  value={`$${balance.toFixed(2)}`}      ok={balance >= startBalance} warn={balance < startBalance && balance > startBalance * 0.9} off={balance <= startBalance * 0.9} />
          <DataRow label="Avg win"  value={wins > 0 ? `+$${(grossW/wins).toFixed(2)}` : '—'}       ok={wins > 0} />
          <DataRow label="Avg loss" value={losses > 0 ? `-$${(grossL/losses).toFixed(2)}` : '—'}   ok={losses === 0} warn={losses > 0 && (grossL/losses) < 3} off={losses > 0 && (grossL/losses) >= 5} />
        </div>

      </div>
    </div>
  );
}
