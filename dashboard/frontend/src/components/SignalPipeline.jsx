import { useState, useEffect } from 'react';
import './SignalPipeline.css';

function PipelineStep({ icon, label, count, sub, color, isLast }) {
  return (
    <div className="pipeline-step">
      <div className="pipeline-node" style={{ borderColor: color }}>
        <span className="pipeline-icon">{icon}</span>
        <span className="pipeline-count" style={{ color }}>{count ?? '—'}</span>
        <span className="pipeline-label">{label}</span>
        {sub && <span className="pipeline-sub">{sub}</span>}
      </div>
      {!isLast && <div className="pipeline-arrow">→</div>}
    </div>
  );
}

function MiniMetric({ label, value, color }) {
  return (
    <div className="mini-metric">
      <span className="mini-label">{label}</span>
      <span className="mini-value" style={color ? { color } : undefined}>{value ?? '—'}</span>
    </div>
  );
}

export default function SignalPipeline({ data }) {
  const [positions, setPositions] = useState({ active: 0, closed: 0, realized_pnl: 0 });

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch('/api/positions');
        const d   = await res.json();
        const s   = d.summary || {};
        setPositions({
          active:       s.active_count  || 0,
          closed:       s.closed_count  || 0,
          realized_pnl: s.realized_pnl  || 0,
        });
      } catch { /* silent */ }
    };
    load();
    const iv = setInterval(load, 15_000);
    return () => clearInterval(iv);
  }, []);

  if (!data) return null;

  const articles      = data.totalSignals || data.signals_evaluated || 0;
  const signals       = data.signals_evaluated || articles;
  const polyMatches   = data.polymarket_matches || data.cm_poly_candidates || 0;
  const kalshiMatches = data.kalshi_matches || 0;
  const totalMatches  = polyMatches + kalshiMatches;
  const tradesExec    = (data.realTrades || 0) + (data.kalshi_real_trades || 0);
  const pnl           = positions.realized_pnl;
  const pnlStr        = `${pnl >= 0 ? '+' : ''}$${Math.abs(pnl).toFixed(2)}`;
  const pnlColor      = pnl > 0 ? '#59d499' : pnl < 0 ? '#ff6363' : 'var(--color-ash-text)';

  const avgConf    = data.avg_confidence ?? 0;
  const confPct    = (avgConf * 10).toFixed(1);
  const bySentiment = data.signals_by_sentiment || {};
  const byMarket    = data.signals_by_market    || {};

  return (
    <section className="signal-pipeline">
      <h2>Signal Pipeline</h2>

      {/* Funnel */}
      <div className="pipeline-funnel">
        <PipelineStep icon="📰" label="Articles" count={articles}     color="rgba(148,163,184,0.9)" />
        <PipelineStep icon="🧠" label="Signals"  count={signals}      color="rgba(167,139,250,0.9)" />
        <PipelineStep icon="🎯" label="Matched"  count={totalMatches} color="rgba(251,191,36,0.9)"  sub={`${polyMatches}P + ${kalshiMatches}K`} />
        <PipelineStep icon="💰" label="Traded"   count={tradesExec}   color="rgba(89,212,153,0.9)"  />
        <PipelineStep icon="📈" label="P&L"      count={pnlStr}       color={pnlColor} isLast />
      </div>

      {/* Mini metrics row */}
      <div className="pipeline-meta">
        <MiniMetric label="Avg Confidence" value={`${confPct}/10`} />
        <MiniMetric label="Bullish"  value={bySentiment.bullish  ?? 0} color="#59d499" />
        <MiniMetric label="Bearish"  value={bySentiment.bearish  ?? 0} color="#ff6363" />
        <MiniMetric label="Neutral"  value={bySentiment.neutral  ?? 0} />
        <MiniMetric label="BTC"      value={byMarket.BTC   ?? 0} color="#f59e0b" />
        <MiniMetric label="ETH"      value={byMarket.ETH   ?? 0} color="#a8a2f2" />
        <MiniMetric label="Open Positions" value={positions.active} color="#59d499" />
        <MiniMetric label="Closed"   value={positions.closed} />
      </div>
    </section>
  );
}
