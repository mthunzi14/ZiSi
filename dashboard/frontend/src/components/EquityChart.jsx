import { useState, useEffect, useRef } from 'react';
import './EquityChart.css';

function LineChart({ points, width = 600, height = 120 }) {
  if (!points || points.length < 2) {
    return (
      <div className="eq-empty">
        <span>Equity curve builds as trades close</span>
      </div>
    );
  }

  const values = points.map(p => p.balance);
  const min    = Math.min(...values);
  const max    = Math.max(...values);
  const range  = max - min || 1;
  const pad    = { top: 12, right: 16, bottom: 20, left: 48 };
  const w      = width  - pad.left - pad.right;
  const h      = height - pad.top  - pad.bottom;

  const toX = (i)  => pad.left + (i / (points.length - 1)) * w;
  const toY = (val) => pad.top  + h - ((val - min) / range) * h;

  const pathD = points.map((p, i) =>
    `${i === 0 ? 'M' : 'L'} ${toX(i).toFixed(1)} ${toY(p.balance).toFixed(1)}`
  ).join(' ');

  const areaD = `${pathD} L ${toX(points.length - 1).toFixed(1)} ${(pad.top + h).toFixed(1)} L ${pad.left} ${(pad.top + h).toFixed(1)} Z`;

  const isProfit = values[values.length - 1] >= 100;
  const strokeColor = isProfit ? '#59d499' : '#ff6363';
  const gradId      = `eq-grad-${isProfit ? 'g' : 'r'}`;

  // Y-axis labels
  const yLabels = [min, (min + max) / 2, max].map(v => v.toFixed(2));

  // X-axis: first + last timestamp
  const fmtTime = (iso) => {
    try {
      const d = new Date(iso);
      return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
    } catch { return ''; }
  };

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="eq-svg" preserveAspectRatio="none">
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor={strokeColor} stopOpacity="0.25" />
          <stop offset="100%" stopColor={strokeColor} stopOpacity="0.01" />
        </linearGradient>
      </defs>

      {/* Y-axis gridlines */}
      {[0, 0.5, 1].map((t, i) => {
        const y = pad.top + h - t * h;
        return (
          <g key={i}>
            <line x1={pad.left} y1={y} x2={pad.left + w} y2={y}
                  stroke="rgba(255,255,255,0.05)" strokeWidth="1" />
            <text x={pad.left - 4} y={y + 4} textAnchor="end"
                  fontSize="9" fill="rgba(255,255,255,0.35)">
              ${yLabels[i]}
            </text>
          </g>
        );
      })}

      {/* Area fill */}
      <path d={areaD} fill={`url(#${gradId})`} />

      {/* Line */}
      <path d={pathD} fill="none" stroke={strokeColor} strokeWidth="2" strokeLinejoin="round" />

      {/* Start + end dots */}
      <circle cx={toX(0)} cy={toY(values[0])} r="3" fill={strokeColor} />
      <circle cx={toX(points.length - 1)} cy={toY(values[values.length - 1])} r="4" fill={strokeColor} />

      {/* X-axis labels */}
      <text x={pad.left} y={height - 4} fontSize="9" fill="rgba(255,255,255,0.3)">{fmtTime(points[0].timestamp)}</text>
      <text x={pad.left + w} y={height - 4} fontSize="9" fill="rgba(255,255,255,0.3)" textAnchor="end">
        {fmtTime(points[points.length - 1].timestamp)}
      </text>
    </svg>
  );
}

export default function EquityChart() {
  const [history, setHistory]   = useState([]);
  const [loading, setLoading]   = useState(true);
  const [range,   setRange]     = useState('all');
  const containerRef            = useRef(null);
  const [width, setWidth]       = useState(600);

  useEffect(() => {
    const obs = new ResizeObserver(entries => {
      const w = entries[0]?.contentRect.width;
      if (w > 0) setWidth(w);
    });
    if (containerRef.current) obs.observe(containerRef.current);
    return () => obs.disconnect();
  }, []);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch('/api/equity');
        if (!res.ok) { setLoading(false); return; }
        const d = await res.json();
        setHistory(d.history || []);
      } catch { /* silent */ }
      setLoading(false);
    };
    load();
    const iv = setInterval(load, 30_000);
    return () => clearInterval(iv);
  }, []);

  const filtered = (() => {
    if (range === 'all' || history.length === 0) return history;
    const now   = Date.now();
    const hours = range === '24h' ? 24 : range === '12h' ? 12 : 6;
    const cutoff = now - hours * 3600_000;
    return history.filter(p => new Date(p.timestamp).getTime() >= cutoff);
  })();

  const current = history.length > 0 ? history[history.length - 1].balance : 100;
  const start   = history.length > 0 ? history[0].balance : 100;
  const change  = current - start;
  const pnlColor = change > 0 ? '#59d499' : change < 0 ? '#ff6363' : 'var(--color-ash-text)';

  return (
    <section className="equity-chart">
      <div className="eq-header">
        <div>
          <h2>Equity Curve</h2>
          {history.length > 0 && (
            <span className="eq-balance" style={{ color: pnlColor }}>
              ${current.toFixed(2)}
              <span className="eq-change"> ({change >= 0 ? '+' : ''}{change.toFixed(2)})</span>
            </span>
          )}
        </div>
        <div className="eq-range-pills">
          {['6h', '12h', '24h', 'all'].map(r => (
            <button
              key={r}
              className={`eq-pill ${range === r ? 'active' : ''}`}
              onClick={() => setRange(r)}
            >
              {r}
            </button>
          ))}
        </div>
      </div>

      <div className="eq-body" ref={containerRef}>
        {loading ? (
          <div className="eq-empty"><span>Loading…</span></div>
        ) : (
          <LineChart points={filtered} width={width} height={130} />
        )}
      </div>

      {history.length > 0 && (
        <div className="eq-footer">
          <span>{history.length} data points</span>
          <span>Started at ${start.toFixed(2)}</span>
        </div>
      )}
    </section>
  );
}
