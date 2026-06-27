import { useState, useEffect } from 'react';

const ASSETS = [
  { asset: 'BTC',  tf: '5m',  color: '#f7931a', tier: '100%' },
  { asset: 'BTC',  tf: '15m', color: '#ffb042', tier: '100%' },
  { asset: 'ETH',  tf: '5m',  color: '#627eea', tier: '100%' },
  { asset: 'ETH',  tf: '15m', color: '#8a9eed', tier: '100%' },
  { asset: 'SOL',  tf: '5m',  color: '#14f195', tier: '100%' },
  { asset: 'SOL',  tf: '15m', color: '#9945ff', tier: '100%' },
  { asset: 'XRP',  tf: '5m',  color: '#00aae4', tier: '100%' },
  { asset: 'XRP',  tf: '15m', color: '#006097', tier: '100%' },
  { asset: 'DOGE', tf: '5m',  color: '#e1b303', tier: '100%' },
  { asset: 'DOGE', tf: '15m', color: '#cc9e02', tier: '100%' },
];


function getAssetStats(key, positions) {
  const [asset, tf] = key.split('/');
  const active = (positions?.active || []).filter(p => {
    const t = (p.event_title || '').toUpperCase();
    return t.includes(`[${asset}]`) && t.includes(`[${tf.toUpperCase()}]`);
  });
  return {
    count: active.length,
    unrealizedPnl: active.reduce((s, p) => s + parseFloat(p.unrealized_pnl || 0), 0),
  };
}

function AssetCard({ asset, tf, color, tier, positions, candles, state }) {
  const key        = `${asset}/${tf}`;
  const stats      = getAssetStats(key, positions);
  const candleInfo = (candles || []).find(c => c.asset === asset && c.tf === tf);
  const serverSecs = candleInfo ? candleInfo.secs : null;
  const [localSecs, setLocalSecs] = useState(null);
  const [hovered, setHovered] = useState(false);

  const clData    = state?.chainlinkPrices?.[asset] || state?.pythPrices?.[asset];
  const clPrice   = clData?.price;
  const clAge     = clData?.timestamp ? Math.max(0, Math.floor(Date.now() / 1000) - clData.timestamp) : null;
  const fresh     = clAge !== null && clAge < 90;

  useEffect(() => {
    if (serverSecs !== null && serverSecs !== undefined) setLocalSecs(serverSecs);
  }, [serverSecs]);

  useEffect(() => {
    const id = setInterval(() => setLocalSecs(s => (s !== null && s > 0 ? s - 1 : s)), 1000);
    return () => clearInterval(id);
  }, []);

  const timerColor = localSecs === null ? '#3f3f46'
    : localSecs < 15  ? '#ef4444'
    : localSecs < 60  ? '#f97316'
    : '#94a3b8';

  const fmtSecs  = s => s === null ? '—' : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
  const fmtPrice = p => !p ? '—' : p < 1.0 ? `$${p.toFixed(4)}` : `$${p.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  const hasOpen    = stats.count > 0;
  const nearCycle  = localSecs !== null && localSecs <= 45 && localSecs > 0 && !!candleInfo?.potential;
  const pnlColor   = stats.unrealizedPnl >= 0 ? '#10b981' : '#ef4444';

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: `linear-gradient(135deg, rgba(22,22,25,0.45) 0%, rgba(12,12,14,0.55) 100%)`,
        borderRadius: 12,
        border: `1px solid ${nearCycle ? '#f59e0b' : hovered ? 'var(--color-logo)' : hasOpen ? color + '55' : 'rgba(148, 163, 184, 0.1)'}`,
        padding: '12px 14px',
        minWidth: 160, flex: '1 1 calc(16% - 8px)',
        display: 'flex', flexDirection: 'column', gap: 8,
        backdropFilter: 'blur(20px) saturate(160%)',
        WebkitBackdropFilter: 'blur(20px) saturate(160%)',
        boxShadow: nearCycle
          ? `0 0 18px rgba(245, 158, 11, 0.40), 0 4px 16px rgba(0,0,0,0.5)`
          : hovered
          ? `0 8px 28px rgba(0,0,0,0.55), 0 0 20px rgba(148, 163, 184, 0.18)`
          : hasOpen ? `0 0 12px ${color}22` : '0 2px 8px rgba(0,0,0,0.3)',
        transform: hovered ? 'translateY(-3px)' : 'translateY(0)',
        transition: 'all 0.25s cubic-bezier(0.4,0,0.2,1)',
        cursor: 'default',
      }}>
      {/* Header row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 5 }}>
          <span style={{ fontFamily: 'var(--font-display)', fontWeight: 900, fontSize: 15, color: 'var(--color-logo)' }}>{asset}</span>
          <span style={{ fontFamily: 'monospace', fontSize: 10, color: '#52525b', fontWeight: 600 }}>{tf}</span>
        </div>
        <span style={{
          fontSize: 8, fontWeight: 700, letterSpacing: '0.06em',
          color: 'var(--color-logo)', background: 'rgba(148, 163, 184, 0.09)',
          border: '1px solid rgba(148, 163, 184, 0.2)',
          borderRadius: 4, padding: '1px 5px',
        }}>{tier}</span>
      </div>

      {/* ⚡ Pre-cycle alert — pulses amber when candle boundary is ≤45s away */}
      {nearCycle && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 4,
          fontSize: 8, fontWeight: 800, color: '#f59e0b',
          letterSpacing: '0.06em', textTransform: 'uppercase',
          animation: 'alertPulse 1.1s ease-in-out infinite',
        }}>
          ⚡ NEXT CYCLE SOON
        </div>
      )}

      {/* Chainlink price */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 1 }}>
          <span style={{
            width: 5, height: 5, borderRadius: '50%',
            background: fresh ? '#10b981' : '#3f3f46',
            display: 'inline-block',
            boxShadow: fresh ? '0 0 4px #10b981' : 'none',
            animation: fresh ? 'alertPulse 2s infinite' : 'none',
          }} />
          <span style={{ fontSize: 7, color: '#3f3f46', textTransform: 'uppercase', letterSpacing: '0.07em' }}>Chainlink oracle</span>
        </div>
        <span style={{ fontFamily: 'monospace', fontSize: 14, fontWeight: 800, color: 'var(--color-text-primary)' }}>
          {fmtPrice(clPrice)}
        </span>
      </div>

      {/* Footer row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: 6 }}>
        <div>
          <div style={{ fontSize: 7, color: '#3f3f46', textTransform: 'uppercase', letterSpacing: '0.07em' }}>Open</div>
          <div style={{ fontFamily: 'monospace', fontSize: 13, fontWeight: 800, color: hasOpen ? color : '#3f3f46' }}>
            {stats.count}
            {hasOpen && (
              <span style={{ fontSize: 10, fontWeight: 700, color: pnlColor, marginLeft: 5 }}>
                {stats.unrealizedPnl >= 0 ? '+' : ''}${stats.unrealizedPnl.toFixed(2)}
              </span>
            )}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 7, color: '#3f3f46', textTransform: 'uppercase', letterSpacing: '0.07em' }}>Next</div>
          <span style={{ fontFamily: 'monospace', fontSize: 11, fontWeight: 700, color: timerColor }}>
            {fmtSecs(localSecs)}
          </span>
        </div>
      </div>
    </div>
  );
}

export default function AssetCards({ positions, candles, state }) {
  const [expanded, setExpanded]       = useState(false);
  const [btnHovered, setBtnHovered]   = useState(false);

  const withOpen = ASSETS.filter(a => getAssetStats(`${a.asset}/${a.tf}`, positions).count > 0);
  const fallback = ASSETS.filter(a => ['BTC', 'ETH', 'SOL'].includes(a.asset) && a.tf === '5m');
  const core     = withOpen.length > 0 ? withOpen : fallback;
  const display  = expanded ? ASSETS : core;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>

      {/* Panel header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 15, color: 'var(--color-logo)' }}>Scanning Grid</span>
          <span style={{
            fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
            color: 'var(--color-logo)', background: 'rgba(148, 163, 184, 0.08)',
            border: '1px solid rgba(148, 163, 184, 0.2)', borderRadius: 6, padding: '2px 8px',
          }}>
            {expanded ? `${ASSETS.length} assets` : `${display.length} active`}
          </span>
        </div>
        <button
          onClick={() => setExpanded(e => !e)}
          onMouseEnter={() => setBtnHovered(true)}
          onMouseLeave={() => setBtnHovered(false)}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            background: btnHovered ? 'rgba(192,192,215,0.08)' : 'rgba(255,255,255,0.04)',
            border: `1px solid ${btnHovered ? 'rgba(192,192,215,0.35)' : 'rgba(255,255,255,0.08)'}`,
            boxShadow: btnHovered ? '0 0 10px rgba(192,192,215,0.15), inset 0 1px 0 rgba(255,255,255,0.08)' : 'none',
            borderRadius: 8, padding: '5px 12px', cursor: 'pointer',
            fontSize: 10, fontWeight: 700,
            color: btnHovered ? '#d4d4e8' : '#a1a1aa',
            transition: 'all 0.2s',
          }}>
          <span style={{ display: 'inline-block', transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.25s' }}>▾</span>
          {expanded ? 'Collapse' : 'Show All'}
        </button>
      </div>

      {/* Cards grid */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', width: '100%' }}>
        {display.map(a => (
          <AssetCard key={`${a.asset}/${a.tf}`} {...a} positions={positions} candles={candles} state={state} />
        ))}
      </div>
    </div>
  );
}
