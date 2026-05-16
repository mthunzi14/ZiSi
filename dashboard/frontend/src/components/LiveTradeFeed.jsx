import { useState, useEffect, useRef, useCallback } from 'react';
import { useSSE } from '../hooks/useSSE';
import './LiveTradeFeed.css';

const MAX_FEED = 15;

function toEntry(t, fresh = false) {
  return {
    id:     t.order_id || t.id || `${Math.random()}`,
    type:   'closed',
    title:  t.event_title || t.title || 'Unknown',
    pnl:    parseFloat(t.realized_pnl ?? t.profit ?? 0),
    pct:    parseFloat(t.realized_pnl_pct ?? t.profit_percent ?? 0),
    market: t.market || (String(t.order_id || '').startsWith('KALSHI') ? 'KALSHI' : 'POLY'),
    ts:     t.close_time || t.exit_timestamp || new Date().toISOString(),
    fresh,
  };
}

// Deduplicate feed by id — always keeps the most recent occurrence
function dedupe(entries) {
  const seen = new Set();
  const out = [];
  for (const e of entries) {
    if (!seen.has(e.id)) {
      seen.add(e.id);
      out.push(e);
    }
  }
  return out;
}

export default function LiveTradeFeed() {
  const { lastTradeClosed, lastTradeOpened } = useSSE();
  const [feed, setFeed] = useState([]);
  const seenIds  = useRef(new Set());
  const prevClosed = useRef(null);
  const prevOpened = useRef(null);

  // Load last 15 closed trades on mount AND after any SSE reconnect
  const loadHistory = useCallback(async () => {
    try {
      const r = await fetch('/api/positions');
      if (!r.ok) return;
      const d = await r.json();
      const closed = (d.closed || [])
        .slice(-MAX_FEED)
        .reverse()
        .map(t => toEntry(t, false));

      setFeed(prev => {
        // Merge: keep SSE-fresh entries at top, fill rest from history
        const freshIds = new Set(prev.filter(x => x.fresh).map(x => x.id));
        const freshEntries = prev.filter(x => x.fresh);
        const histEntries = closed.filter(x => !freshIds.has(x.id));
        // Register all IDs as seen so future SSE events don't duplicate
        closed.forEach(x => seenIds.current.add(x.id));
        return dedupe([...freshEntries, ...histEntries]).slice(0, MAX_FEED);
      });
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  // SSE: append newly closed trade
  useEffect(() => {
    if (!lastTradeClosed || lastTradeClosed === prevClosed.current) return;
    prevClosed.current = lastTradeClosed;
    const d = lastTradeClosed;
    const id = d.order_id || `sse-${Date.now()}`;
    if (seenIds.current.has(id)) {
      // Already in feed from history load — just mark it fresh for animation
      setFeed(prev => prev.map(x => x.id === id ? { ...x, fresh: true } : x));
    } else {
      seenIds.current.add(id);
      const entry = { ...toEntry(d, true), id };
      setFeed(prev => dedupe([entry, ...prev]).slice(0, MAX_FEED));
    }
    setTimeout(() => {
      setFeed(prev => prev.map(x => x.id === id ? { ...x, fresh: false } : x));
    }, 1500);
  }, [lastTradeClosed]);

  // SSE: append newly opened trade (dimmer OPEN entry)
  useEffect(() => {
    if (!lastTradeOpened || lastTradeOpened === prevOpened.current) return;
    prevOpened.current = lastTradeOpened;
    const d = lastTradeOpened;
    const id = d.order_id || `open-${Date.now()}`;
    if (seenIds.current.has(id)) return;
    seenIds.current.add(id);
    const entry = {
      id,
      type:   'opened',
      title:  d.event_title || d.title || 'Unknown',
      pnl:    null,
      pct:    null,
      market: d.market || (String(d.order_id || '').startsWith('KALSHI') ? 'KALSHI' : 'POLY'),
      ts:     new Date().toISOString(),
      fresh:  true,
    };
    setFeed(prev => dedupe([entry, ...prev]).slice(0, MAX_FEED));
    setTimeout(() => {
      setFeed(prev => prev.map(x => x.id === id ? { ...x, fresh: false } : x));
    }, 1500);
  }, [lastTradeOpened]);

  const fmtTime = (ts) => {
    if (!ts) return '—';
    try {
      return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch { return '—'; }
  };

  const fmtTitle = (t) => t.replace(/^\[SHADOW:[^\]]+\]\s*/, '').slice(0, 55);

  return (
    <section className="ltf-card">
      <div className="ltf-header">
        <h2>Live Trade Feed</h2>
        <span className="ltf-sub">Real-time • last {MAX_FEED} trades</span>
      </div>

      {feed.length === 0 ? (
        <div className="ltf-empty">No recent trades — waiting for activity…</div>
      ) : (
        <div className="ltf-list">
          {feed.map(t => {
            const isClosed = t.type === 'closed';
            const isWin    = isClosed && t.pnl > 0;
            const isLoss   = isClosed && t.pnl <= 0;
            const pnlColor = isWin ? '#59d499' : isLoss ? '#ff6363' : '#8b9ab0';
            const icon     = t.type === 'opened' ? '📥' : isWin ? '✅' : '❌';
            const muleTag  = t.title.match(/\[SHADOW:([^\]]+)\]/)?.[1] || null;
            const mkt      = (t.market || 'POLY').toUpperCase();

            return (
              <div key={t.id} className={`ltf-row${t.fresh ? ' ltf-row--fresh' : ''}${t.type === 'opened' ? ' ltf-row--open' : ''}`}>
                <span className="ltf-icon">{icon}</span>

                <div className="ltf-info">
                  <span className="ltf-title">{fmtTitle(t.title)}</span>
                  <div className="ltf-meta">
                    <span className={`ltf-badge ltf-badge--${mkt.toLowerCase()}`}>{mkt}</span>
                    {muleTag && <span className="ltf-badge ltf-badge--mule">{muleTag}</span>}
                    <span className="ltf-time">{fmtTime(t.ts)}</span>
                  </div>
                </div>

                <div className="ltf-pnl" style={{ color: pnlColor }}>
                  {isClosed
                    ? `${t.pnl >= 0 ? '+' : ''}$${Math.abs(t.pnl).toFixed(4)}`
                    : <span className="ltf-open-label">OPEN</span>
                  }
                  {isClosed && t.pct !== null && (
                    <span className="ltf-pct">{t.pct >= 0 ? '+' : ''}{t.pct.toFixed(1)}%</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
