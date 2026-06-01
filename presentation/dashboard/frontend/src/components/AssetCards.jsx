import { useState, useEffect } from 'react';

// AssetCards.jsx — All 13 per-asset/timeframe quantitative cards
const ASSETS = [
  { asset: 'BTC', tf: '5m',  color: '#f7931a', tier: 'Tier 1 (100%)' },
  { asset: 'BTC', tf: '15m', color: '#ffb042', tier: 'Tier 1 (100%)' },
  { asset: 'ETH', tf: '5m',  color: '#627eea', tier: 'Tier 1 (100%)' },
  { asset: 'ETH', tf: '15m', color: '#8a9eed', tier: 'Tier 1 (100%)' },
  { asset: 'SOL', tf: '5m',  color: '#14f195', tier: 'Tier 2 (60%)' },
  { asset: 'SOL', tf: '15m', color: '#9945ff', tier: 'Tier 2 (60%)' },
  { asset: 'XRP', tf: '5m',  color: '#00aae4', tier: 'Tier 2 (60%)' },
  { asset: 'XRP', tf: '15m', color: '#006097', tier: 'Tier 2 (60%)' },
  { asset: 'DOGE', tf: '5m',  color: '#e1b303', tier: 'Tier 3 (35%)' },
  { asset: 'DOGE', tf: '15m', color: '#cc9e02', tier: 'Tier 3 (35%)' },
  { asset: 'HYPE', tf: '5m',  color: '#ff3cc8', tier: 'Tier 3 (35%)' },
  { asset: 'HYPE', tf: '15m', color: '#d92ca8', tier: 'Tier 3 (35%)' },
  { asset: 'BNB', tf: '5m',  color: '#f3ba2f', tier: 'Tier 3 (35%)' },
  { asset: 'BNB', tf: '15m', color: '#d6a325', tier: 'Tier 3 (35%)' },
];

function getAssetStats(key, positions) {
  const active = (positions?.active || []).filter(p => {
    const t = (p.event_title || '').toUpperCase();
    return t.includes(`[${key.split('/')[0]}]`) && t.includes(`[${key.split('/')[1].toUpperCase()}]`);
  });
  const pnl = active.reduce((s, p) => s + parseFloat(p.unrealized_pnl || 0), 0);
  return { count: active.length, unrealizedPnl: pnl };
}

function AssetCard({ asset, tf, color, tier, positions, candles, state, index }) {
  const key        = `${asset}/${tf}`;
  const stats      = getAssetStats(key, positions);
  const candleInfo = (candles || []).find(c => c.asset === asset && c.tf === tf);
  const serverSecs = candleInfo ? candleInfo.secs : null;
  const [localSecs, setLocalSecs] = useState(null);

  // Pyth Real-Time Oracle Spot Pricing Integration
  const pythData  = state?.pythPrices?.[asset];
  const pythPrice = pythData?.price;
  const pythConf  = pythData?.conf;
  const pythAge   = pythData?.timestamp ? Math.max(0, Math.floor(Date.now() / 1000) - pythData.timestamp) : null;

  // Sync local seconds with server updates
  useEffect(() => {
    if (serverSecs !== null && serverSecs !== undefined) {
      setLocalSecs(serverSecs);
    }
  }, [serverSecs]);

  // Local ticker to decrement seconds smoothly every 1s
  useEffect(() => {
    const intervalId = setInterval(() => {
      setLocalSecs(s => (s !== null && s > 0 ? s - 1 : s));
    }, 1000);
    return () => clearInterval(intervalId);
  }, []);

  const timerColor = localSecs === null ? 'var(--color-text-muted)'
    : localSecs < 15 ? 'var(--color-loss)'
    : localSecs < 60 ? 'var(--color-amber)'
    : 'var(--color-profit)';

  const fmtSecs = (s) => s === null ? '—' : `${Math.floor(s / 60)}m ${(s % 60).toString().padStart(2, '0')}s`;

  // Format currency for display
  const formatPrice = (p) => {
    if (!p) return '—';
    if (p < 1.0) return `$${p.toFixed(4)}`;
    return `$${p.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };

  return (
    <div 
      className="glass-panel card-lift reveal-up"
      style={{
        padding: '16px 20px',
        minWidth: '220px',
        flex: '1 1 calc(20% - 12px)',
        animationDelay: `${index * 0.04}s`,
        borderLeft: `3.5px solid ${color}`,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        height: '170px',
      }}
    >
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
          <span style={{ fontFamily: 'var(--font-display)', fontWeight: 900, fontSize: 17, color: 'var(--color-obsidian)' }}>
            {asset} <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-iron)' }}>{tf}</span>
          </span>
          <span style={{ 
            fontSize: '9px', 
            fontWeight: 700, 
            fontFamily: 'var(--font-mono)', 
            padding: '2px 6px', 
            borderRadius: '4px',
            background: 'var(--color-border-subtle)',
            color: 'var(--color-iron)'
          }}>
            {tier}
          </span>
        </div>

        {/* Real-time price line */}
        <div style={{ margin: '6px 0', display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontSize: 10, color: 'var(--color-iron)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
            Pyth Oracle Spot
          </span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 1 }}>
            <span style={{ 
              width: 6, 
              height: 6, 
              borderRadius: '99px', 
              backgroundColor: pythAge !== null && pythAge < 5 ? '#00e676' : '#94a3b8',
              display: 'inline-block',
              boxShadow: pythAge !== null && pythAge < 5 ? '0 0 6px #00e676' : 'none'
            }} className={pythAge !== null && pythAge < 5 ? "alert-pulse" : ""} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 15, fontWeight: 700, color: 'var(--color-obsidian)' }}>
              {formatPrice(pythPrice)}
            </span>
            {pythConf !== undefined && pythConf > 0 && (
              <span style={{ fontSize: 9, color: 'var(--color-iron)', fontFamily: 'var(--font-mono)' }}>
                ±{pythConf.toFixed(2)}
              </span>
            )}
          </div>
        </div>
      </div>

      <div style={{ borderTop: '1px solid var(--color-border-subtle)', paddingTop: 10, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <div>
          <div style={{ fontSize: 10, color: 'var(--color-iron)' }}>Open Slots</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 17, fontWeight: 700, color: 'var(--color-obsidian)', display: 'flex', alignItems: 'center', gap: 6 }}>
            {stats.count}
            {stats.count > 0 && (
              <span style={{ fontSize: 11, fontWeight: 600, color: stats.unrealizedPnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)' }}>
                {stats.unrealizedPnl >= 0 ? '+' : ''}${stats.unrealizedPnl.toFixed(2)}
              </span>
            )}
          </div>
        </div>

        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 10, color: 'var(--color-iron)' }}>Next Candle</div>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 600, color: timerColor }}>
            {fmtSecs(localSecs)}
          </span>
        </div>
      </div>
    </div>
  );
}

export default function AssetCards({ positions, candles, state }) {
  const [isExpanded, setIsExpanded] = useState(false);

  // Filter assets to show all if expanded, otherwise only show those with active positions
  const filteredAssets = ASSETS.filter(a => {
    if (isExpanded) return true;
    const key = `${a.asset}/${a.tf}`;
    const stats = getAssetStats(key, positions);
    return stats.count > 0;
  });

  // Fallback to top 3 core assets baseline if no active positions, keeping layout visually premium
  const displayedAssets = filteredAssets.length > 0 ? filteredAssets : ASSETS.filter(a => 
    (a.asset === 'BTC' && a.tf === '5m') || 
    (a.asset === 'BTC' && a.tf === '15m') || 
    (a.asset === 'ETH' && a.tf === '5m')
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', width: '100%' }}>
      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', width: '100%' }}>
        {displayedAssets.map((a, idx) => (
          <AssetCard key={`${a.asset}/${a.tf}`} {...a} positions={positions} candles={candles} state={state} index={idx} />
        ))}
      </div>
      
      {/* Dynamic Expand/Collapse Scanning Grid Button */}
      <div style={{ display: 'flex', justifyContent: 'center', marginTop: '4px' }}>
        <button 
          onClick={() => setIsExpanded(!isExpanded)}
          className="btn-ghost"
          style={{ 
            display: 'flex', 
            alignItems: 'center', 
            gap: '8px', 
            padding: '8px 24px', 
            borderRadius: 'var(--radius-full)', 
            fontSize: '12px',
            fontWeight: '700',
            color: 'var(--color-accent)',
            borderColor: 'var(--color-border)',
            background: 'var(--color-surface)',
            boxShadow: 'var(--shadow-xs)',
            cursor: 'pointer',
            transition: 'all 200ms ease'
          }}
        >
          <span>{isExpanded ? '🗁 Collapse Scanning Grid' : '🗀 Expand Scanning Grid'}</span>
          <span style={{ fontSize: '10px', color: 'var(--color-iron)' }}>
            ({isExpanded ? '14' : `${displayedAssets.length} core`} assets)
          </span>
        </button>
      </div>
    </div>
  );
}
