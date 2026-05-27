import React from 'react';

/**
 * RegimeRadarHUD - A premium visual component that monitors the active market volatility
 * regime, dynamically calculating execution ceilings, and rendering golden gradient intensity metrics.
 */
export default function RegimeRadarHUD({ state = {} }) {
  const regimeRaw = state.regime;
  const regimeStr = typeof regimeRaw === 'object' ? (regimeRaw?.regime || 'NORMAL') : (regimeRaw || 'NORMAL');
  const regime = regimeStr.toUpperCase();
  
  const regimeMeta = {
    NORMAL: {
      color: 'var(--color-profit)',
      desc: 'Predictive gates open. Regular volatility thresholds.',
      limit: '3 positions max',
      ratio: '60%',
    },
    RANGE: {
      color: 'var(--color-accent)',
      desc: 'Consolidation regime. Increased trade limits active.',
      limit: '4 positions max',
      ratio: '80%',
    },
    VOLATILE: {
      color: 'var(--color-amber)',
      desc: 'High velocity volatility. Conservative risk controls.',
      limit: '2 positions max',
      ratio: '45%',
    },
    SHOCK: {
      color: 'var(--color-loss)',
      desc: 'Extreme market event. Minimum exposure gate.',
      limit: '1 position max',
      ratio: '20%',
    },
  };

  const meta = regimeMeta[regime] || regimeMeta.NORMAL;

  return (
    <div 
      className="glass-panel" 
      style={{ 
        padding: 'var(--spacing-20)', 
        display: 'flex', 
        flexDirection: 'column', 
        gap: 12,
        background: 'var(--surface-charcoal-canvas)',
        border: '1px solid rgba(255, 255, 255, 0.08)',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ fontFamily: 'var(--font-inter)', fontWeight: 600, fontSize: 13, color: 'var(--color-pure-white)', letterSpacing: '-0.013px' }}>
          REGIME & VOLATILITY RADAR
        </div>
        <div style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          padding: '2px 8px',
          borderRadius: 9999,
          fontSize: 10,
          fontWeight: 600,
          fontFamily: 'var(--font-mono)',
          color: meta.color,
          border: `1px solid ${meta.color}33`,
          background: `${meta.color}11`
        }}>
          <span style={{
            width: 5,
            height: 5,
            borderRadius: '50%',
            backgroundColor: meta.color,
            boxShadow: `0 0 6px ${meta.color}`
          }} />
          {regime}
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}>
        <div style={{ fontSize: 11, color: 'var(--color-stone-text)', textTransform: 'uppercase', fontFamily: 'var(--font-mono)' }}>
          Hurdle Tolerance / Trade Limits
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <div style={{ fontSize: 24, fontWeight: 700, fontFamily: 'var(--font-inter)', color: 'var(--color-pure-white)' }}>
            {meta.limit}
          </div>
        </div>
      </div>

      {/* Volatility Indicator Bar */}
      <div style={{ marginTop: 6 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--color-ash-text)', marginBottom: 4, fontFamily: 'var(--font-mono)' }}>
          <span>Hurdle Floor</span>
          <span>Regime Intensity</span>
        </div>
        <div style={{ height: 6, background: 'var(--color-pewter-accent)', borderRadius: 9999, overflow: 'hidden' }}>
          <div style={{ 
            width: meta.ratio, 
            height: '100%', 
            background: 'var(--gradient-golden-gradient)', 
            borderRadius: 9999,
            transition: 'width 0.5s ease-in-out'
          }} />
        </div>
      </div>

      <div style={{ fontSize: 12, color: 'var(--color-silver-text)', lineHeight: 1.4, marginTop: 4 }}>
        {meta.desc}
      </div>
    </div>
  );
}
