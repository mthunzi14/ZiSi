// PositionMonitor.jsx — live open positions table with countdown timers
import { useState, useEffect } from 'react';

function CountdownTimer({ expiry_ts }) {
  const [secs, setSecs] = useState(0);

  useEffect(() => {
    const tick = () => {
      const s = Math.max(0, expiry_ts - Math.floor(Date.now() / 1000));
      setSecs(s);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [expiry_ts]);

  const color = secs < 15 ? 'var(--color-loss)' : secs < 60 ? 'var(--color-amber)' : 'var(--color-profit)';
  const pulse = secs < 15 ? { animation: 'pulse 0.8s infinite' } : {};
  const m = Math.floor(secs / 60), s = secs % 60;

  return (
    <span style={{ fontFamily: 'var(--font-mono)', color, fontSize: 12, ...pulse }}>
      {m}m {s.toString().padStart(2,'0')}s ⏱
    </span>
  );
}

function assetFallback(title) {
  if (/bitcoin/i.test(title))  return 'BTC';
  if (/ethereum/i.test(title)) return 'ETH';
  if (/solana/i.test(title))   return 'SOL';
  if (/\bxrp\b/i.test(title))  return 'XRP';
  return '?';
}

function parseType(title, entryType) {
  const typeUpper = (entryType || '').toUpperCase();
  if (typeUpper === 'CLOSE_SNIPE' || typeUpper === 'CLOSE-SNIPE' || typeUpper === 'CLOSE-SNIPE-EARLY' || typeUpper === 'CLOSE_SNIPE_EARLY' || typeUpper === 'NCS') return 'NCS';
  if (typeUpper === 'FAIR_VAL' || typeUpper === 'FAIR-VAL' || typeUpper === 'FV') return 'FV';
  if (typeUpper === 'SIGNAL' || typeUpper === 'SINGLE' || typeUpper === 'SIG') return 'SIG';
  if (typeUpper === 'SWEEP' || typeUpper === 'T2_SWEEPER') return 'SWEEP';
  if (typeUpper === 'LATENCY_ARB' || typeUpper === 'LAT-ARB' || typeUpper === 'ARB' || typeUpper === 'LAT ARB') return 'LAT ARB';
  if (typeUpper === 'REVERSAL-SNIPE' || typeUpper === 'REVERSAL_SNIPE' || typeUpper === 'REV SNIPE' || typeUpper === 'REV') return 'REV SNIPE';
  if (typeUpper === 'REVERSAL-STREAK' || typeUpper === 'REVERSAL_STREAK' || typeUpper === 'REV STREAK') return 'REV STREAK';
  if (typeUpper === 'DUAL' || typeUpper === 'DUAL_MAIN' || typeUpper === 'DUAL_HEDGE') return 'DUAL';

  const titleUpper = title.toUpperCase();
  if (titleUpper.includes('[CLOSE_SNIPE]') || titleUpper.includes('[CLOSE-SNIPE]') || titleUpper.includes('[CLOSE-SNIPE-EARLY]') || titleUpper.includes('[NCS]')) return 'NCS';
  if (titleUpper.includes('[FAIR_VAL]') || titleUpper.includes('[FV]')) return 'FV';
  if (titleUpper.includes('[T2_SWEEPER]') || titleUpper.includes('[SWEEP]')) return 'SWEEP';
  if (titleUpper.includes('[LATENCY_ARB]') || titleUpper.includes('[ARB]')) return 'LAT ARB';
  if (titleUpper.includes('[REVERSAL_SNIPE]') || titleUpper.includes('[REVERSAL-SNIPE]')) return 'REV SNIPE';
  if (titleUpper.includes('[REVERSAL_STREAK]') || titleUpper.includes('[REVERSAL-STREAK]')) return 'REV STREAK';
  if (titleUpper.includes('[SINGLE]') || titleUpper.includes('[SIG]')) return 'SIG';
  if (titleUpper.includes('[DUAL_MAIN]') || titleUpper.includes('[DUAL_HEDGE]') || titleUpper.includes('[DUAL]')) return 'DUAL';

  return 'SIG';
}

function parsePositionMeta(pos) {
  const title = pos.event_title || '';
  const assetMatch = title.match(/\[(BTC|ETH|SOL|XRP)\]/);
  const tfMatch    = title.match(/\[(5m|15m|1h)\]/);
  return {
    asset:     assetMatch ? assetMatch[1] : assetFallback(title),
    timeframe: tfMatch    ? tfMatch[1]    : '?',
    type:      parseType(title, pos.entry_type),
  };
}

export default function PositionMonitor({ positions = {}, candles = [] }) {
  const active = positions?.active || [];

  const colHeaders = ['Asset','TF','Dir','Type','Entry¢','Current¢','Unr P&L','Closes In'];

  return (
    <div style={{
      background: 'var(--color-bg-surface)',
      borderRadius: 'var(--radius-cards)',
      border: '1px solid var(--color-midnight)',
      padding: 'var(--spacing-20)',
    }}>
      <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 500, fontSize: 16, marginBottom: 12 }}>
        Open Positions ({active.length})
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: '50px 35px 55px 70px 60px 70px 75px 1fr',
        gap: 4, marginBottom: 6,
        fontSize: 10, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em',
      }}>
        {colHeaders.map(h => <span key={h}>{h}</span>)}
      </div>

      <div style={{ overflowY: 'auto', maxHeight: 280 }}>
        {active.length === 0 ? (
          <div style={{ color: 'var(--color-text-muted)', fontSize: 13, textAlign: 'center', padding: 32 }}>
            No open positions
          </div>
        ) : active.map((pos, i) => {
          const meta   = parsePositionMeta(pos);
          const dir    = pos.direction === 'YES' || pos.direction === 'UP' ? 'UP' : 'DOWN';
          const entry  = parseFloat(pos.entry_price || 0);
          const cur    = parseFloat(pos.current_price || entry);
          const unrPnl = parseFloat(pos.unrealized_pnl || 0);
          const isDual = meta.type.startsWith('DUAL');
          const expiry = parseInt(pos.expiry_ts || '0');

          return (
            <div key={pos.order_id || i} style={{
              display: 'grid',
              gridTemplateColumns: '50px 35px 55px 70px 60px 70px 75px 1fr',
              gap: 4, alignItems: 'center',
              padding: '6px 0',
              borderLeft: `3px solid ${isDual ? 'var(--color-accent-muted)' : 'var(--color-text-muted)'}`,
              paddingLeft: 8,
              borderBottom: '1px solid rgba(255,255,255,0.04)',
              fontSize: 12,
            }}>
              <span style={{ fontFamily: 'var(--font-heading)', fontWeight: 700, color: 'var(--color-text-primary)' }}>{meta.asset}</span>
              <span style={{ color: 'var(--color-text-muted)' }}>{meta.timeframe}</span>
              <span style={{ color: dir === 'UP' ? 'var(--color-profit)' : 'var(--color-loss)', fontWeight: 600 }}>
                {dir === 'UP' ? '↑ UP' : '↓ DOWN'}
              </span>
              <span style={{
                background: isDual ? 'var(--color-accent-muted)' : 'transparent',
                borderRadius: 3, padding: '1px 5px', fontSize: 10, textAlign: 'center',
              }}>{meta.type.replace('_MAIN','').replace('_HEDGE','*')}</span>
              <span style={{ fontFamily: 'var(--font-mono)' }}>{(entry * 100).toFixed(0)}¢</span>
              <span style={{ fontFamily: 'var(--font-mono)', color: cur > entry ? 'var(--color-profit)' : 'var(--color-loss)' }}>
                {(cur * 100).toFixed(0)}¢
              </span>
              <span style={{
                fontFamily: 'var(--font-mono)', fontWeight: 600,
                color: unrPnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)',
              }}>
                {unrPnl >= 0 ? '+' : ''}${unrPnl.toFixed(2)}
              </span>
              {expiry > 0 ? <CountdownTimer expiry_ts={expiry} /> : <span style={{ color: 'var(--color-text-muted)' }}>—</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
