// AssetCards.jsx — 5 per-asset status cards (BTC-5m, BTC-15m, ETH, SOL, XRP)
const ASSETS = [
  { asset: 'BTC', tf: '5m',  color: 'var(--color-accent)' },
  { asset: 'BTC', tf: '15m', color: 'var(--color-accent-muted)' },
  { asset: 'ETH', tf: '5m',  color: 'var(--color-profit)' },
  { asset: 'SOL', tf: '5m',  color: 'var(--color-text-secondary)' },
  { asset: 'XRP', tf: '5m',  color: 'var(--color-xrp)' },
];

function getAssetStats(key, positions) {
  const active = (positions?.active || []).filter(p => {
    const t = (p.event_title || '').toUpperCase();
    return t.includes(`[${key.split('/')[0]}]`) && t.includes(`[${key.split('/')[1].toUpperCase()}]`);
  });
  const pnl = active.reduce((s, p) => s + parseFloat(p.unrealized_pnl || 0), 0);
  return { count: active.length, unrealizedPnl: pnl };
}

function AssetCard({ asset, tf, color, positions, candles }) {
  const key        = `${asset}/${tf}`;
  const stats      = getAssetStats(key, positions);
  const candleInfo = (candles || []).find(c => c.asset === asset && c.tf === tf);
  const secsLeft   = candleInfo?.secs ?? null;
  const timerColor = secsLeft === null ? 'var(--color-text-muted)'
    : secsLeft < 15 ? 'var(--color-loss)'
    : secsLeft < 60 ? 'var(--color-amber)'
    : 'var(--color-profit)';

  const fmtSecs = (s) => s === null ? '—' : `${Math.floor(s / 60)}m ${s % 60}s`;

  return (
    <div style={{
      background: 'var(--color-bg-elevated)',
      borderRadius: 'var(--radius-cards)',
      padding: 'var(--spacing-20)',
      border: '1px solid var(--color-midnight)',
      minWidth: 0, flex: 1,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontFamily: 'var(--font-display)', fontWeight: 900, fontSize: 18, color }}>
          {asset} <span style={{ fontSize: 13, fontWeight: 400, color: 'var(--color-text-muted)' }}>{tf}</span>
        </span>
      </div>

      <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 6 }}>Open positions</div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 22, fontWeight: 600, color: 'var(--color-text-primary)' }}>
        {stats.count}
        {stats.count > 0 && (
          <span style={{ fontSize: 13, marginLeft: 8, color: stats.unrealizedPnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)' }}>
            {stats.unrealizedPnl >= 0 ? '+' : ''}${stats.unrealizedPnl.toFixed(2)} unr
          </span>
        )}
      </div>

      <div style={{ marginTop: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>
        Next candle: <span style={{ fontFamily: 'var(--font-mono)', color: timerColor }}>{fmtSecs(secsLeft)}</span>
      </div>
    </div>
  );
}

export default function AssetCards({ positions, candles }) {
  return (
    <div style={{ display: 'flex', gap: 'var(--spacing-12)', flexWrap: 'wrap' }}>
      {ASSETS.map(a => (
        <AssetCard key={`${a.asset}/${a.tf}`} {...a} positions={positions} candles={candles} />
      ))}
    </div>
  );
}
