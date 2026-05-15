import React from 'react';
import './MissedTrades.css';

function MetricRow({ label, value, color, sub }) {
  return (
    <div className="comp-metric">
      <span className="comp-label">{label}</span>
      <span className="comp-value" style={color ? { color } : undefined}>
        {value}
      </span>
      {sub && <span className="comp-sub">{sub}</span>}
    </div>
  );
}

function ExchangeCard({ title, icon, trades, winRate, pnl, awaiting }) {
  const hasTrades = trades > 0;
  const pnlColor = pnl > 0 ? '#59d499' : pnl < 0 ? '#ff6363' : undefined;

  return (
    <div className="market-section">
      <h4 className="market-section-title">{icon} {title}</h4>
      <MetricRow label="Trades" value={hasTrades ? trades : '—'} />
      <MetricRow
        label="Win Rate"
        value={hasTrades ? `${(winRate * 100).toFixed(1)}%` : 'N/A'}
      />
      <MetricRow
        label="P&L"
        value={awaiting ? 'Awaiting resolution' : `$${(pnl || 0).toFixed(2)}`}
        color={awaiting ? '#f59e0b' : pnlColor}
      />
    </div>
  );
}

export default function MissedTrades({ data }) {
  if (!data) return null;

  const realPolyTrades   = data.realTrades       || 0;
  const polyWinRate      = data.winRate          || 0;
  const polyPnl          = data.pnl              || 0;
  const kalshiTrades     = data.kalshi_real_trades || 0;
  const totalRealTrades  = realPolyTrades + kalshiTrades;
  const totalPnl         = polyPnl + (data.kalshi_real_pnl || 0);

  const missedSignals    = data.missedTrades     || 0;
  const signalQuality    = data.signal_quality_rate || data.missedWinRate || 0;

  // Phase 1 target: 20 real trades
  const phase1Target = 20;
  const phase1Progress = Math.min((totalRealTrades / phase1Target) * 100, 100);

  return (
    <section className="missed-trades">
      <h2>Trade Performance</h2>

      {/* Real trades grid */}
      <div className="comparison-grid">
        <div className="comparison-card real">
          <h3>📊 Polymarket</h3>
          <ExchangeCard
            title="Polymarket"
            icon=""
            trades={realPolyTrades}
            winRate={polyWinRate}
            pnl={polyPnl}
          />
        </div>

        <div className="comparison-card real">
          <h3>📊 Kalshi</h3>
          <ExchangeCard
            title="Kalshi"
            icon=""
            trades={kalshiTrades}
            winRate={data.kalshi_real_win_rate || 0}
            pnl={data.kalshi_real_pnl || 0}
            awaiting={kalshiTrades > 0 && (data.kalshi_real_pnl || 0) === 0}
          />
        </div>
      </div>

      {/* Combined totals */}
      <div className="total-row" style={{ marginTop: '12px' }}>
        <MetricRow label="Total Real Trades" value={totalRealTrades} />
        <MetricRow
          label="Combined P&L"
          value={`$${totalPnl.toFixed(2)}`}
          color={totalPnl > 0 ? '#59d499' : totalPnl < 0 ? '#ff6363' : undefined}
        />
      </div>

      {/* Phase 1 progress */}
      <div className="edge-progress" style={{ marginTop: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
          <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
            Phase 1 Target: {totalRealTrades} / {phase1Target} real trades
          </span>
          <span style={{ fontSize: '0.85rem', color: '#6b62f2' }}>
            {phase1Progress.toFixed(0)}%
          </span>
        </div>
        <div className="progress-bar">
          <div
            className="progress-fill"
            style={{ width: `${phase1Progress}%`, background: '#6b62f2' }}
          />
        </div>
      </div>

    </section>
  );
}
