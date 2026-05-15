import React from 'react';
import './MetricsBreakdown.css';

function Metric({ label, value, highlight }) {
  return (
    <div className="mb-metric">
      <span className="mb-label">{label}</span>
      <span className="mb-value" style={highlight ? { color: highlight } : undefined}>
        {value ?? '—'}
      </span>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div className="mb-section">
      <h3 className="mb-section-title">{title}</h3>
      <div className="mb-grid">{children}</div>
    </div>
  );
}

export default function MetricsBreakdown({ data }) {
  if (!data) return null;

  const {
    signals_evaluated = 0,
    avg_confidence = 0,
    confidence_distribution = {},
    signals_by_market = {},
    signals_by_sentiment = {},
    polymarket_matches = 0,
    kalshi_matches = 0,
    peak_hour_signals = 0,
    off_peak_hour_signals = 0,
  } = data;

  const confDisplay = (avg_confidence * 10).toFixed(1);

  return (
    <div className="metrics-breakdown">
      <Section title="📊 Signal Quality">
        <Metric label="Total Signals Evaluated" value={signals_evaluated} />
        <Metric label="Avg Confidence" value={`${confDisplay}/10`} />
        <Metric label="9/10 Confidence" value={confidence_distribution['9'] ?? 0} />
        <Metric label="8/10 Confidence" value={confidence_distribution['8'] ?? 0} />
        <Metric label="7/10 Confidence" value={confidence_distribution['7'] ?? 0} />
      </Section>

      <Section title="🎯 Market Distribution">
        <Metric label="BTC Signals" value={signals_by_market.BTC ?? 0} />
        <Metric label="ETH Signals" value={signals_by_market.ETH ?? 0} />
        <Metric label="Other Signals" value={signals_by_market.OTHER ?? 0} />
      </Section>

      <Section title="💬 Sentiment Breakdown">
        <Metric label="Bullish" value={signals_by_sentiment.bullish ?? 0} />
        <Metric label="Bearish" value={signals_by_sentiment.bearish ?? 0} />
        <Metric label="Neutral" value={signals_by_sentiment.neutral ?? 0} />
      </Section>

      <Section title="🔄 Match Rates">
        <Metric label="Polymarket Matches" value={polymarket_matches} />
        <Metric label="Kalshi Matches" value={kalshi_matches} />
        <Metric label="Total Matches" value={polymarket_matches + kalshi_matches} />
      </Section>

      <Section title="⏰ UTC Hour Performance">
        <Metric label="Peak Hours (22–06 UTC)" value={peak_hour_signals} />
        <Metric label="Off-Peak Hours (06–22 UTC)" value={off_peak_hour_signals} />
      </Section>
    </div>
  );
}
