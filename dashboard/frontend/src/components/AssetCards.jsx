import { useState, useEffect } from 'react';

const ASSETS = [
  { asset: 'BTC',  tf: '5m',  color: '#f7931a', tier: '100%' },
  { asset: 'BTC',  tf: '15m', color: '#ffb042', tier: '100%' },
  { asset: 'BTC',  tf: '1h',  color: '#e27622', tier: '100%' },
  { asset: 'ETH',  tf: '5m',  color: '#627eea', tier: '100%' },
  { asset: 'ETH',  tf: '15m', color: '#8a9eed', tier: '100%' },
  { asset: 'ETH',  tf: '1h',  color: '#a08eed', tier: '100%' },
  { asset: 'SOL',  tf: '5m',  color: '#14f195', tier: '60%' },
  { asset: 'SOL',  tf: '15m', color: '#9945ff', tier: '60%' },
  { asset: 'XRP',  tf: '5m',  color: '#00aae4', tier: '60%' },
  { asset: 'XRP',  tf: '15m', color: '#006097', tier: '60%' },
  { asset: 'DOGE', tf: '5m',  color: '#e1b303', tier: '35%' },
  { asset: 'DOGE', tf: '15m', color: '#cc9e02', tier: '35%' },
];


function getAssetStats(key, positions, clPrice) {
  const [asset, tf] = key.split('/');
  const active = (positions?.active || []).filter(p => {
    const t = (p.event_title || '').toUpperCase();
    const isClosed = p.status === 'CLOSED' || p.status === 'CANCELLED';
    return !isClosed && t.includes(`[${asset}]`) && t.includes(`[${tf.toUpperCase()}]`);
  });

  const enrichedActive = active.map(p => {
    const entrySpot = parseFloat(p.entry_spot || 0);
    const entryPrice = parseFloat(p.entry_price || p.price || 0.5);
    const shares = parseFloat(p.shares || 0);
    const cost = parseFloat(p.size || 0);
    let unrealized = parseFloat(p.unrealized_pnl || 0);
    let currentPrice = p.current_price;

    if (clPrice && entrySpot > 0 && entryPrice > 0) {
      const priceDiffPct = (clPrice - entrySpot) / entrySpot;
      const delta = priceDiffPct * 20.0;
      let derivedPrice = entryPrice;
      const dir = (p.direction || 'YES').toUpperCase();
      if (dir === 'YES' || dir === 'UP') {
        derivedPrice += delta;
      } else {
        derivedPrice -= delta;
      }
      derivedPrice = Math.max(0.01, Math.min(0.99, derivedPrice));
      currentPrice = derivedPrice;
      unrealized = Math.round((shares * derivedPrice - cost) * 100) / 100;
    }

    return {
      ...p,
      unrealized_pnl_derived: unrealized,
      current_price_derived: currentPrice
    };
  });

  return {
    count: enrichedActive.length,
    unrealizedPnl: enrichedActive.reduce((s, p) => s + parseFloat(p.unrealized_pnl_derived || 0), 0),
    activeList: enrichedActive,
  };
}

function AssetCard({ asset, tf, color, tier, positions, candles, state }) {
  const clData    = state?.chainlinkPrices?.[asset] || state?.pythPrices?.[asset];
  const clPrice   = clData?.price;

  const key        = `${asset}/${tf}`;
  const stats      = getAssetStats(key, positions, clPrice);
  const candleInfo = (candles || []).find(c => c.asset === asset && c.tf === tf);
  const serverSecs = candleInfo ? candleInfo.secs : null;
  const [localSecs, setLocalSecs] = useState(null);
  const [hovered, setHovered] = useState(false);
  const clAge     = clData?.timestamp ? Math.max(0, Math.floor(Date.now() / 1000) - clData.timestamp) : null;
  const fresh     = clAge !== null && clAge < 15;

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
    : '#71717a';

  const fmtSecs  = s => s === null ? '—' : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
  const fmtPrice = p => !p ? '—' : p < 1.0 ? `$${p.toFixed(4)}` : `$${p.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  const hasOpen = stats.count > 0;
  const pnlColor = '#71717a';

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className="border-beam-card"
      style={{
        background: `linear-gradient(135deg, rgba(22,22,25,0.85) 0%, rgba(12,12,14,0.92) 100%)`,
        borderRadius: 12,
        border: `1px solid ${hovered ? '#71717a' : hasOpen ? color + '55' : 'rgba(255,255,255,0.06)'}`,
        padding: '12px 14px',
        minWidth: 160, flex: '1 1 calc(16% - 8px)',
        display: 'flex', flexDirection: 'column', gap: 8,
        backdropFilter: 'blur(12px)',
        boxShadow: hovered
          ? `0 8px 28px rgba(0,0,0,0.55), 0 0 20px rgba(113,113,122,0.22)`
          : hasOpen ? `0 0 12px ${color}22` : '0 2px 8px rgba(0,0,0,0.3)',
        transform: hovered ? 'translateY(-3px)' : 'translateY(0)',
        transition: 'all 0.25s cubic-bezier(0.4,0,0.2,1)',
        cursor: 'default',
      }}>
      {/* Header row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 5 }}>
          <span style={{ fontFamily: 'var(--font-display)', fontWeight: 900, fontSize: 15, color: 'var(--color-text-primary)' }}>{asset}</span>
          <span style={{ fontFamily: 'monospace', fontSize: 10, color: '#52525b', fontWeight: 600 }}>{tf}</span>
        </div>
        <span style={{
          fontSize: 8, fontWeight: 700, letterSpacing: '0.06em',
          color, background: `${color}18`,
          border: `1px solid ${color}33`,
          borderRadius: 4, padding: '1px 5px',
        }}>{tier}</span>
      </div>

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
        <span
          id={tf === '5m' ? `spot-ticker-${asset.toLowerCase()}` : `spot-ticker-${asset.toLowerCase()}-${tf.toLowerCase()}`}
          style={{ fontFamily: 'monospace', fontSize: 14, fontWeight: 800, color: 'var(--color-text-primary)' }}
        >
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
              <>
                <span
                  id={tf === '5m' ? `unreal_pnl-${asset.toLowerCase()}` : `unreal_pnl-${asset.toLowerCase()}-${tf.toLowerCase()}`}
                  style={{ fontSize: 10, fontWeight: 700, color: pnlColor, marginLeft: 5 }}
                >
                  {stats.unrealizedPnl >= 0 ? '+' : ''}${stats.unrealizedPnl.toFixed(2)}
                </span>
                <div style={{ fontSize: 8, color: '#52525b', marginTop: 2, textTransform: 'none', letterSpacing: 'normal', fontWeight: 600 }}>
                  {stats.activeList.map(p => {
                    const dir = p.direction || 'YES';
                    const price = p.current_price_derived !== undefined && p.current_price_derived !== null ? `${(p.current_price_derived * 100).toFixed(0)}¢` : '—';
                    return `${dir}: ${price}`;
                  }).join(' | ')}
                </div>
              </>
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

  const withOpen = ASSETS.filter(a => {
    const clData = state?.chainlinkPrices?.[a.asset] || state?.pythPrices?.[a.asset];
    const clPrice = clData?.price;
    return getAssetStats(`${a.asset}/${a.tf}`, positions, clPrice).count > 0;
  });
  const fallback = ASSETS.filter(a => ['BTC', 'ETH', 'SOL'].includes(a.asset) && a.tf === '5m');
  const core     = withOpen.length > 0 ? withOpen : fallback;
  const display  = expanded ? ASSETS : core;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>

      {/* Panel header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 15 }}>Scanning Grid</span>
          <span style={{
            fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
            color: '#71717a', background: 'rgba(113,113,122,0.1)',
            border: '1px solid rgba(113,113,122,0.25)', borderRadius: 6, padding: '2px 8px',
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
