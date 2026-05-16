import { useState, useEffect } from 'react';
import './PerformanceCard.css';

function EntityCol({ data }) {
  if (!data) return null;
  const { name, trades, wins, losses, win_rate, pnl, best, worst } = data;
  const pnlColor   = pnl > 0 ? '#59d499' : pnl < 0 ? '#ff6363' : 'var(--color-ash-text)';
  const winPct     = Math.min(100, win_rate);
  const barColor   = win_rate >= 60 ? '#59d499' : win_rate >= 45 ? '#facc15' : '#ff6363';
  const isZisi     = name === 'ZiSi';

  return (
    <div className={`perf-col ${isZisi ? 'perf-col--zisi' : ''}`}>
      <div className="perf-entity-name">{name}</div>

      <div className="perf-wr-wrap">
        <div className="perf-wr-bar-bg">
          <div className="perf-wr-bar" style={{ width: `${winPct}%`, background: barColor }} />
        </div>
        <span className="perf-wr-label" style={{ color: barColor }}>{win_rate}%</span>
      </div>

      <div className="perf-pnl" style={{ color: pnlColor }}>
        {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
      </div>

      <div className="perf-stats">
        <span className="perf-stat win">W {wins}</span>
        <span className="perf-stat sep">/</span>
        <span className="perf-stat loss">L {losses}</span>
      </div>

      <div className="perf-meta">
        <span>{trades} trades</span>
      </div>

      {trades > 0 && (
        <div className="perf-extremes">
          <span className="perf-best">▲ +{best.toFixed(2)}</span>
          <span className="perf-worst">▼ {worst.toFixed(2)}</span>
        </div>
      )}
    </div>
  );
}

export default function PerformanceCard() {
  const [data, setData] = useState(null);

  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch('/api/performance');
        if (r.ok) setData(await r.json());
      } catch { /* silent */ }
    };
    load();
    const iv = setInterval(load, 20_000);
    return () => clearInterval(iv);
  }, []);

  return (
    <section className="perf-card">
      <div className="perf-header">
        <h2>ZiSi Performance</h2>
        <span className="perf-sub">All-time closed trade statistics</span>
      </div>

      <div className="perf-cols">
        <EntityCol data={data?.zisi} />
      </div>

      {!data && (
        <div className="perf-empty">Loading performance stats…</div>
      )}
    </section>
  );
}
