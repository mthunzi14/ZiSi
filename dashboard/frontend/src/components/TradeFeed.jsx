// TradeFeed.jsx — scrolling trade log, last 50 trades, newest at top
function directionColor(dir) {
  return dir === 'UP' ? 'var(--color-profit)' : 'var(--color-loss)';
}

function ResultBadge({ result }) {
  const color = result === 'WIN' ? 'var(--color-profit)' : result === 'LOSS' ? 'var(--color-loss)' : 'var(--color-text-muted)';
  return (
    <span style={{ fontFamily: 'var(--font-body)', fontWeight: 700, fontSize: 11, color, letterSpacing: '0.05em' }}>
      {result || 'OPEN'}
    </span>
  );
}

function TradeRow({ trade, isOpen }) {
  const borderColor = trade.result === 'WIN' ? 'var(--color-profit)' : trade.result === 'LOSS' ? 'var(--color-loss)' : 'var(--color-text-muted)';
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '45px 45px 30px 55px 50px 50px 55px 55px 60px 50px',
      gap: 4, alignItems: 'center',
      padding: '6px 0',
      borderLeft: `3px solid ${borderColor}`,
      paddingLeft: 8,
      opacity: isOpen ? 0.75 : 1,
      fontStyle: isOpen ? 'italic' : 'normal',
      borderBottom: '1px solid rgba(255,255,255,0.04)',
      fontSize: 12,
    }}>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-text-muted)' }}>{trade.time || '—'}</span>
      <span style={{ fontFamily: 'var(--font-heading)', fontWeight: 700 }}>{trade.asset || '—'}</span>
      <span style={{ color: 'var(--color-text-muted)' }}>{trade.timeframe || '—'}</span>
      <span style={{ color: directionColor(trade.direction), fontWeight: 600 }}>
        {trade.direction === 'UP' ? '↑ UP' : '↓ DOWN'}
      </span>
      <span style={{ fontFamily: 'var(--font-mono)' }}>{trade.entry_price ? `${(trade.entry_price * 100).toFixed(0)}¢` : '—'}</span>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-text-muted)' }}>
        {trade.exit_price != null ? `${(trade.exit_price * 100).toFixed(0)}¢` : '—'}
      </span>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-text-muted)' }}>{trade.score?.toFixed(2) || '—'}</span>
      <span style={{
        background: trade.type === 'DUAL' || trade.type === 'DUAL_MAIN' ? 'var(--color-accent-muted)' : 'transparent',
        borderRadius: 3, padding: '1px 4px', fontSize: 10,
      }}>{trade.type || 'SINGL'}</span>
      <span style={{
        fontFamily: 'var(--font-mono)', fontWeight: 600,
        color: (trade.pnl ?? 0) >= 0 ? 'var(--color-profit)' : 'var(--color-loss)',
      }}>
        {trade.pnl != null ? `${trade.pnl >= 0 ? '+' : ''}$${trade.pnl.toFixed(2)}` : '—'}
      </span>
      <ResultBadge result={trade.result} />
    </div>
  );
}

// Build trade rows from positions_state.json active + closed
function buildTrades(trades, positions) {
  const rows = [];

  const now = Date.now() / 1000;
  const fmt = (ts) => {
    const d = new Date(ts);
    return `${d.getUTCHours().toString().padStart(2,'0')}:${d.getUTCMinutes().toString().padStart(2,'0')}`;
  };

  // Closed trades from SSE feed
  for (const t of trades) {
    rows.push({ ...t, result: (t.pnl ?? 0) > 0 ? 'WIN' : 'LOSS' });
  }

  // Active positions from SSE stream
  for (const p of (positions?.active || [])) {
    const title = p.event_title || '';
    const assetMatch = title.match(/\[(BTC|ETH|SOL|XRP)\]/);
    const tfMatch    = title.match(/\[(5m|15m)\]/);
    const typeMatch  = title.match(/\[(SINGLE|DUAL_MAIN|DUAL_HEDGE|DUAL)\]/);
    rows.push({
      time:        fmt(p.open_time ? new Date(p.open_time).getTime() : now * 1000),
      asset:       assetMatch ? assetMatch[1] : '?',
      timeframe:   tfMatch ? tfMatch[1] : '?',
      direction:   p.direction === 'YES' ? 'UP' : 'DOWN',
      entry_price: parseFloat(p.entry_price || 0),
      exit_price:  null,
      score:       parseFloat(p.score || 0) || null,
      type:        typeMatch ? typeMatch[1].replace('_MAIN','') : 'SINGL',
      pnl:         parseFloat(p.unrealized_pnl || 0),
      result:      null,
    });
  }

  return rows.slice(0, 50);
}

export default function TradeFeed({ trades = [], positions = {} }) {
  const rows = buildTrades(trades, positions);

  const colHeaders = ['Time','Asset','TF','Dir','Entry¢','Exit¢','Score','Type','P&L','Result'];

  return (
    <div style={{
      background: 'var(--color-bg-surface)',
      borderRadius: 'var(--radius-cards)',
      border: '1px solid var(--color-midnight)',
      padding: 'var(--spacing-20)',
      overflow: 'hidden', display: 'flex', flexDirection: 'column',
    }}>
      <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 500, fontSize: 16, marginBottom: 12 }}>
        Live Trade Feed
      </div>

      {/* Column headers */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '45px 45px 30px 55px 50px 50px 55px 55px 60px 50px',
        gap: 4, paddingLeft: 11, marginBottom: 4,
        fontSize: 10, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em',
      }}>
        {colHeaders.map(h => <span key={h}>{h}</span>)}
      </div>

      <div style={{ overflowY: 'auto', maxHeight: 340, flex: 1 }}>
        {rows.length === 0 ? (
          <div style={{ color: 'var(--color-text-muted)', fontSize: 13, textAlign: 'center', padding: 32 }}>
            Waiting for trades…
          </div>
        ) : rows.map((t, i) => (
          <TradeRow key={i} trade={t} isOpen={t.result === null} />
        ))}
      </div>
    </div>
  );
}
