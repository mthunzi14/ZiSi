// CommandCentre.jsx — sticky top bar with balance, regime, time gate, daily loss bar
import { useState, useEffect } from 'react';

const S = {
  bar: {
    position: 'sticky', top: 0, zIndex: 100,
    background: 'var(--color-bg-surface)',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
    padding: '0 var(--spacing-24)',
    height: 64, display: 'flex', alignItems: 'center',
    gap: 'var(--spacing-24)',
  },
  logo: { height: 32, objectFit: 'contain' },
  div:  { color: 'rgba(255,255,255,0.15)', fontSize: 20, userSelect: 'none' },
  label: { fontFamily: 'var(--font-body)', fontSize: 11, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em' },
  val:   { fontFamily: 'var(--font-mono)', fontSize: 15, fontWeight: 600 },
  badge: (color) => ({
    border: `1px solid ${color}`, borderRadius: 'var(--radius-buttons)',
    padding: '3px 10px', fontSize: 11, fontFamily: 'var(--font-body)', fontWeight: 600,
    color, background: 'transparent', letterSpacing: '0.06em',
  }),
  lossBarWrap: { flex: 1, maxWidth: 160 },
  lossBarTrack: { height: 4, background: 'rgba(255,255,255,0.08)', borderRadius: 2, overflow: 'hidden' },
  lossBarFill: (pct) => ({
    height: '100%', borderRadius: 2, background: 'var(--color-loss)',
    width: `${Math.min(pct, 100)}%`,
    transition: 'width 0.5s ease',
  }),
  utcClock: { fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--color-text-muted)' },
  spacer: { flex: 1 },
};

export default function CommandCentre({ state = {}, positions = {} }) {
  const [utc, setUtc] = useState('');

  useEffect(() => {
    const tick = () => {
      const now = new Date();
      setUtc(now.toUTCString().slice(17, 25) + ' UTC');
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  const balance    = parseFloat(state.balance || 100);
  const startBal   = 100;
  const dailyPnl   = parseFloat(state.pnl || 0);
  const lossDrawPct = Math.max(0, ((startBal - balance) / startBal) * 100);
  const regime     = state.regime || 'TREND';
  const timeGateOn = state.time_gate_open !== false;

  const pnlColor = dailyPnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)';
  const pnlSign  = dailyPnl >= 0 ? '+' : '';

  return (
    <header style={S.bar}>
      <img src="/src/assets/ZiSi_Final_Logo.png" alt="ZiSi" style={S.logo} />
      <span style={S.div}>|</span>

      <div>
        <div style={S.label}>Balance</div>
        <div style={{ ...S.val, color: 'var(--color-text-primary)' }}>${balance.toFixed(2)}</div>
      </div>

      <div>
        <div style={S.label}>Daily P&amp;L</div>
        <div style={{ ...S.val, color: pnlColor }}>{pnlSign}${dailyPnl.toFixed(2)}</div>
      </div>

      <div>
        <div style={S.label}>Regime</div>
        <span style={S.badge(regime === 'TREND' ? 'var(--color-accent)' : 'var(--color-accent-muted)')}>
          {regime}
        </span>
      </div>

      <div>
        <div style={S.label}>Time Gate</div>
        <span style={S.badge(timeGateOn ? 'var(--color-profit)' : 'var(--color-loss)')}>
          {timeGateOn ? '● ACTIVE' : '● PAUSED'}
        </span>
      </div>

      <div style={S.spacer} />

      <div style={S.lossBarWrap}>
        <div style={{ ...S.label, marginBottom: 4 }}>Daily Loss {lossDrawPct.toFixed(1)}% / 15%</div>
        <div style={S.lossBarTrack}>
          <div style={S.lossBarFill(lossDrawPct / 15 * 100)} />
        </div>
      </div>

      <span style={S.utcClock}>{utc}</span>
    </header>
  );
}
