import './RegimeIndicator.css';

const REGIME_COLOURS = {
  SHOCK:    { bg: 'rgba(239,68,68,0.12)',   border: 'rgba(239,68,68,0.6)',   text: '#f87171' },
  VOLATILE: { bg: 'rgba(245,158,11,0.12)',  border: 'rgba(245,158,11,0.6)',  text: '#fbbf24' },
  NORMAL:   { bg: 'rgba(34,197,94,0.12)',   border: 'rgba(34,197,94,0.6)',   text: '#4ade80' },
  RANGE:    { bg: 'rgba(168,162,242,0.12)', border: 'rgba(168,162,242,0.6)', text: '#a8a2f2' },
};

export default function RegimeIndicator({ regime = {} }) {
  const name    = regime.regime || 'NORMAL';
  const label   = regime.label  || name;
  const atr     = regime.atr_pct ?? 0;
  const kMult   = regime.kelly_multiplier ?? 1.0;
  const colours = REGIME_COLOURS[name] || REGIME_COLOURS.NORMAL;

  return (
    <div className="regime-indicator" style={{ borderColor: colours.border, background: colours.bg }}>
      <div className="regime-left">
        <span className="regime-dot" style={{ background: colours.text }} />
        <div>
          <span className="regime-name" style={{ color: colours.text }}>{name}</span>
          <span className="regime-label">{label}</span>
        </div>
        <div className="regime-stats">
          <span>ATR <strong>{atr.toFixed(2)}%</strong></span>
          <span>Kelly ×<strong>{kMult.toFixed(2)}</strong></span>
        </div>
      </div>
    </div>
  );
}
