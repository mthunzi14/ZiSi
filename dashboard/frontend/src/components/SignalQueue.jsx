import { useState, useEffect } from 'react';

const STATUS_STYLE = {
  ACCEPTED: { color: '#22c55e', fontWeight: 600 },
  REJECTED:  { color: '#ef4444', fontWeight: 600 },
};

const ROUTING_BADGE = {
  BOTH:           { background: '#6366f1', color: '#fff' },
  KALSHI_ONLY:    { background: '#f59e0b', color: '#fff' },
  POLYMARKET:     { background: '#3b82f6', color: '#fff' },
  SKIP:           { background: '#6b7280', color: '#fff' },
  BOTH_ARBITRAGE: { background: '#10b981', color: '#fff' },
};

export default function SignalQueue() {
  const [queue, setQueue] = useState([]);
  const [lastFetch, setLastFetch] = useState(null);

  useEffect(() => {
    const fetch_ = async () => {
      try {
        const res = await fetch('/api/signal-queue');
        const data = await res.json();
        setQueue(data);
        setLastFetch(new Date().toLocaleTimeString());
      } catch (_) {}
    };
    fetch_();
    const id = setInterval(fetch_, 5_000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="card" style={{ marginTop: '16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <h3 style={{ margin: 0 }}>Signal Queue</h3>
        {lastFetch && <span className="text-muted" style={{ fontSize: '12px' }}>updated {lastFetch}</span>}
      </div>

      {queue.length === 0 ? (
        <p className="text-muted" style={{ textAlign: 'center', padding: '24px 0' }}>
          No signals evaluated yet — queue populates as the bot runs.
        </p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border, #334155)' }}>
                {['Time', 'Market', 'Platform', 'Conf', 'Price', 'Spread%', 'Routing', 'Status'].map(h => (
                  <th key={h} style={{ padding: '6px 10px', textAlign: 'left', color: 'var(--text-muted, #94a3b8)', fontWeight: 500 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {queue.map((item, i) => {
                const ts = item.timestamp ? new Date(item.timestamp).toLocaleTimeString() : '—';
                const badgeStyle = ROUTING_BADGE[item.routing_decision] || { background: '#374151', color: '#fff' };
                return (
                  <tr
                    key={i}
                    style={{
                      borderBottom: '1px solid var(--border-subtle, #1e293b)',
                      opacity: item.status === 'REJECTED' ? 0.6 : 1,
                    }}
                  >
                    <td style={{ padding: '6px 10px', whiteSpace: 'nowrap' }}>{ts}</td>
                    <td style={{ padding: '6px 10px', maxWidth: '240px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                        title={item.market_title}>
                      {item.market_title}
                    </td>
                    <td style={{ padding: '6px 10px' }}>{item.platform || '—'}</td>
                    <td style={{ padding: '6px 10px' }}>
                      {item.gemini_confidence != null ? item.gemini_confidence.toFixed(1) : '—'}
                    </td>
                    <td style={{ padding: '6px 10px' }}>
                      {item.entry_price != null ? item.entry_price.toFixed(3) : '—'}
                    </td>
                    <td style={{ padding: '6px 10px' }}>
                      {item.spread_pct != null ? `${item.spread_pct.toFixed(1)}%` : '—'}
                    </td>
                    <td style={{ padding: '6px 10px' }}>
                      <span style={{ ...badgeStyle, padding: '2px 7px', borderRadius: '4px', fontSize: '11px' }}>
                        {item.routing_decision || '—'}
                      </span>
                    </td>
                    <td style={{ padding: '6px 10px', ...STATUS_STYLE[item.status] }}>
                      {item.status || '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
