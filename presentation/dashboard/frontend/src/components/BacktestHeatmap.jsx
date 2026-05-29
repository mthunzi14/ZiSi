// BacktestHeatmap.jsx — Parameter Sweep Heatmap (rsi_up x target_threshold)
import { useState, useEffect } from 'react';

const POLL_MS = 30000;

function cellColor(value, mode) {
  if (mode === 'expectancy') {
    if (value >= 0.5)  return 'rgba(16,185,129,0.35)';
    if (value >= 0.2)  return 'rgba(16,185,129,0.18)';
    if (value >= 0)    return 'rgba(249,115,22,0.15)';
    return 'rgba(255,77,77,0.15)';
  }
  // win_rate mode (default)
  if (value >= 70)  return 'rgba(16,185,129,0.35)';
  if (value >= 55)  return 'rgba(16,185,129,0.18)';
  if (value >= 45)  return 'rgba(249,115,22,0.15)';
  return 'rgba(255,77,77,0.15)';
}

function cellTextColor(value, mode) {
  if (mode === 'expectancy') {
    return value >= 0.2 ? '#10b981' : value >= 0 ? '#f97316' : '#ff4d4d';
  }
  return value >= 55 ? '#10b981' : value >= 45 ? '#f97316' : '#ff4d4d';
}

export default function BacktestHeatmap() {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);
  const [colorMode, setColorMode] = useState('win_rate'); // 'win_rate' | 'expectancy'

  useEffect(() => {
    let mounted = true;

    const load = async () => {
      try {
        const res = await fetch('/api/backtest/heatmap');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        if (mounted) { setData(json); setError(null); }
      } catch (e) {
        if (mounted) setError(e.message);
      } finally {
        if (mounted) setLoading(false);
      }
    };

    load();
    const id = setInterval(load, POLL_MS);
    return () => { mounted = false; clearInterval(id); };
  }, []);

  // Build sorted unique axis values and a lookup map: (rsi_up, target) -> best cell by expectancy
  const { rsiUpVals, targetVals, cellMap } = (() => {
    const cells = data?.cells || [];
    const rsiSet = new Set();
    const tgtSet = new Set();
    const map = {};

    for (const cell of cells) {
      const r = cell.params?.rsi_up;
      const t = cell.params?.target_threshold;
      if (r == null || t == null) continue;
      rsiSet.add(r);
      tgtSet.add(t);
      const key = `${r}__${t}`;
      const existing = map[key];
      const thisExp = cell.metrics?.expectancy ?? -Infinity;
      const prevExp = existing?.metrics?.expectancy ?? -Infinity;
      if (!existing || thisExp > prevExp) map[key] = cell;
    }

    return {
      rsiUpVals: Array.from(rsiSet).sort((a, b) => a - b),
      targetVals: Array.from(tgtSet).sort((a, b) => a - b),
      cellMap: map,
    };
  })();

  const hasData = rsiUpVals.length > 0 && targetVals.length > 0;

  const labelStyle = {
    fontSize: '10px',
    fontFamily: 'var(--font-mono, monospace)',
    color: 'var(--color-text-muted)',
    textAlign: 'center',
    fontWeight: 600,
  };

  const metricLabel = colorMode === 'win_rate' ? 'Win Rate' : 'Expectancy';

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      {/* Header row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '8px' }}>
        <div>
          <h3 style={{
            fontFamily: 'var(--font-primary)',
            fontWeight: 700,
            fontSize: '15px',
            color: 'var(--color-obsidian)',
            marginBottom: '2px',
            display: 'flex',
            alignItems: 'center',
            gap: '8px'
          }}>
            Backtest Parameter Heatmap
            {data?.isGenerating && (
              <span className="alert-pulse" style={{ fontSize: '9px', background: 'rgba(197, 155, 39, 0.15)', color: 'var(--color-accent)', padding: '2px 6px', borderRadius: '4px', border: '1px solid rgba(197, 155, 39, 0.3)' }}>
                REFRESHING...
              </span>
            )}
          </h3>
          <p style={{ fontSize: '11px', color: 'var(--color-iron)' }}>
            RSI thresholds vs target confidence — coloured by {metricLabel.toLowerCase()}. Polls every 30s.
          </p>
        </div>

        {/* Color-mode toggle */}
        <div style={{
          display: 'flex',
          gap: '4px',
          background: 'rgba(255,255,255,0.03)',
          padding: '3px',
          borderRadius: '8px',
          border: '1px solid var(--color-border-subtle)',
          flexShrink: 0,
        }}>
          {['win_rate', 'expectancy'].map(mode => (
            <button
              key={mode}
              onClick={() => setColorMode(mode)}
              style={{
                padding: '4px 10px',
                borderRadius: '6px',
                border: 'none',
                background: colorMode === mode ? 'rgba(197,155,39,0.18)' : 'transparent',
                color: colorMode === mode ? 'var(--color-accent)' : 'var(--color-text-muted)',
                fontSize: '10px',
                fontFamily: 'var(--font-mono, monospace)',
                fontWeight: 600,
                cursor: 'pointer',
                transition: 'all 0.15s ease',
                textTransform: 'uppercase',
                letterSpacing: '0.04em',
              }}
            >
              {mode === 'win_rate' ? 'Win %' : 'Expect.'}
            </button>
          ))}
        </div>
      </div>

      {/* State: loading */}
      {loading && (
        <div style={{
          padding: '48px 24px',
          textAlign: 'center',
          color: 'var(--color-text-muted)',
          fontSize: '12px',
          fontFamily: 'var(--font-mono, monospace)',
        }}>
          Loading heatmap data…
        </div>
      )}

      {/* State: error */}
      {!loading && error && (
        <div style={{
          padding: '32px 24px',
          textAlign: 'center',
          color: '#f97316',
          fontSize: '12px',
          background: 'rgba(249,115,22,0.06)',
          border: '1px solid rgba(249,115,22,0.2)',
          borderRadius: '10px',
        }}>
          <div style={{ fontWeight: 700, marginBottom: '6px' }}>Failed to load heatmap</div>
          <div style={{ color: 'var(--color-text-muted)', fontSize: '11px' }}>{error}</div>
        </div>
      )}

      {/* State: no data yet */}
      {!loading && !error && !hasData && (
        <div style={{
          padding: '40px 24px',
          textAlign: 'center',
          background: 'var(--color-cream-deep)',
          borderRadius: '12px',
          border: '1px dashed var(--color-border)',
        }}>
          <div style={{ fontSize: '28px', marginBottom: '12px', opacity: 0.5 }}>
            {data?.isGenerating ? '⏳' : '📊'}
          </div>
          <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--color-text-secondary)', marginBottom: '8px' }}>
            {data?.isGenerating ? 'Generating backtest heatmap in background...' : 'No backtest data available yet'}
          </div>
          <div style={{ fontSize: '11px', color: 'var(--color-text-muted)', fontFamily: 'var(--font-mono, monospace)', lineHeight: 1.6 }}>
            {data?.isGenerating 
              ? 'The quantitative parameter sweep is running a simulation over the last 7 days of historical candles. This typically takes 30-60 seconds. This panel will automatically update.'
              : 'Run the backtester to populate the heatmap:'}
          </div>
          {!data?.isGenerating && (
            <div style={{
              marginTop: '10px',
              display: 'inline-block',
              padding: '6px 14px',
              background: 'rgba(0,0,0,0.35)',
              borderRadius: '6px',
              border: '1px solid var(--color-border)',
              fontSize: '11px',
              fontFamily: 'var(--font-mono, monospace)',
              color: '#10b981',
              letterSpacing: '0.03em',
            }}>
              python -m tools.historical_backtest --days 7
            </div>
          )}
        </div>
      )}

      {/* State: heatmap grid */}
      {!loading && !error && hasData && (
        <>
          {/* Grid container: row labels + column headers */}
          <div style={{ overflowX: 'auto' }}>
            <div style={{
              display: 'grid',
              gridTemplateColumns: `56px repeat(${targetVals.length}, minmax(68px, 1fr))`,
              gap: '5px',
              minWidth: `${56 + targetVals.length * 73}px`,
            }}>
              {/* Top-left empty corner */}
              <div style={{ ...labelStyle, display: 'flex', alignItems: 'flex-end', justifyContent: 'flex-end', paddingBottom: '4px', paddingRight: '4px', color: 'var(--color-iron)', fontSize: '9px' }}>
                RSI↓ / Tgt→
              </div>

              {/* Column headers: target_threshold */}
              {targetVals.map(t => (
                <div key={t} style={{ ...labelStyle, paddingBottom: '4px' }}>
                  {(t * 100).toFixed(0)}%
                </div>
              ))}

              {/* Rows: rsi_up */}
              {rsiUpVals.map(r => (
                <div key={r} style={{ display: 'contents' }}>
                  {/* Row label */}
                  <div style={{
                    ...labelStyle,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'flex-end',
                    paddingRight: '6px',
                  }}>
                    RSI {r}
                  </div>

                  {/* Cells */}
                  {targetVals.map(t => {
                    const cell = cellMap[`${r}__${t}`];
                    if (!cell) {
                      return (
                        <div
                          key={t}
                          style={{
                            background: 'rgba(255,255,255,0.02)',
                            border: '1px solid var(--color-border-subtle)',
                            borderRadius: '8px',
                            minHeight: '56px',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                          }}
                        >
                          <span style={{ fontSize: '10px', color: 'var(--color-text-muted)', opacity: 0.5 }}>—</span>
                        </div>
                      );
                    }

                    const m = cell.metrics;
                    const displayVal = colorMode === 'win_rate'
                      ? m.win_rate
                      : m.expectancy;
                    const bg    = cellColor(displayVal, colorMode);
                    const fgCol = cellTextColor(displayVal, colorMode);
                    const label = colorMode === 'win_rate'
                      ? `${m.win_rate?.toFixed(1)}%`
                      : `${m.expectancy >= 0 ? '+' : ''}${m.expectancy?.toFixed(2)}`;
                    const tooltipParts = [
                      `Trades: ${m.trades}  Wins: ${m.wins}`,
                      `Win Rate: ${m.win_rate?.toFixed(1)}%`,
                      `Expectancy: ${m.expectancy >= 0 ? '+' : ''}${m.expectancy?.toFixed(3)}`,
                      `Total P&L: ${m.total_pnl >= 0 ? '+' : ''}$${m.total_pnl?.toFixed(2)}`,
                      `Sharpe: ${m.sharpe?.toFixed(2)}`,
                      `Max DD: ${m.max_drawdown?.toFixed(2)}%`,
                      cell.below_baseline_volume ? '⚠ Below-baseline volume' : '',
                    ].filter(Boolean).join('\n');

                    return (
                      <div
                        key={t}
                        title={tooltipParts}
                        className="glow-hover"
                        style={{
                          background: bg,
                          border: `1px solid ${bg.replace(/[\d.]+\)$/, '0.45)')}`,
                          borderRadius: '8px',
                          minHeight: '56px',
                          display: 'flex',
                          flexDirection: 'column',
                          alignItems: 'center',
                          justifyContent: 'center',
                          cursor: 'pointer',
                          padding: '6px 4px',
                          gap: '2px',
                          transition: 'all 200ms ease',
                          position: 'relative',
                        }}
                      >
                        {/* Low-volume warning badge */}
                        {cell.below_baseline_volume && (
                          <span style={{
                            position: 'absolute',
                            top: '3px',
                            right: '4px',
                            fontSize: '9px',
                            lineHeight: 1,
                          }} title="Below-baseline volume — treat result with caution">
                            ⚠
                          </span>
                        )}

                        <span style={{
                          fontFamily: 'var(--font-mono, monospace)',
                          fontSize: '13px',
                          fontWeight: 800,
                          color: fgCol,
                          lineHeight: 1,
                        }}>
                          {label}
                        </span>
                        <span style={{
                          fontSize: '9px',
                          color: 'var(--color-text-muted)',
                          fontFamily: 'var(--font-mono, monospace)',
                        }}>
                          {m.trades}t
                        </span>
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>

          {/* Legend */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '12px',
            flexWrap: 'wrap',
            borderTop: '1px solid var(--color-border-subtle)',
            paddingTop: '12px',
          }}>
            <span style={{ fontSize: '10px', color: 'var(--color-iron)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Legend ({metricLabel}):
            </span>
            {colorMode === 'win_rate' ? (
              <>
                <LegendChip color="rgba(16,185,129,0.35)" label="≥70%" />
                <LegendChip color="rgba(16,185,129,0.18)" label="≥55%" />
                <LegendChip color="rgba(249,115,22,0.15)" label="≥45%" />
                <LegendChip color="rgba(255,77,77,0.15)"  label="<45%" />
              </>
            ) : (
              <>
                <LegendChip color="rgba(16,185,129,0.35)" label="≥+0.50" />
                <LegendChip color="rgba(16,185,129,0.18)" label="≥+0.20" />
                <LegendChip color="rgba(249,115,22,0.15)" label="≥0" />
                <LegendChip color="rgba(255,77,77,0.15)"  label="<0" />
              </>
            )}
            <span style={{ fontSize: '10px', color: 'var(--color-iron)', marginLeft: 'auto' }}>
              ⚠ = below-baseline volume
            </span>
          </div>

          {/* Note from API + generated_at */}
          {(data?.note || data?.generated_at) && (
            <div style={{
              fontSize: '10px',
              color: 'var(--color-text-muted)',
              fontStyle: 'italic',
              lineHeight: 1.5,
            }}>
              {data.note && <span>{data.note}</span>}
              {data.generated_at && (
                <span style={{ marginLeft: data.note ? '  ·  ' : undefined }}>
                  Generated {new Date(data.generated_at).toLocaleString()}
                </span>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function LegendChip({ color, label }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
      <div style={{
        width: '14px',
        height: '14px',
        borderRadius: '3px',
        background: color,
        border: `1px solid ${color.replace(/[\d.]+\)$/, '0.6)')}`,
        flexShrink: 0,
      }} />
      <span style={{
        fontSize: '10px',
        color: 'var(--color-text-muted)',
        fontFamily: 'var(--font-mono, monospace)',
        fontWeight: 600,
      }}>
        {label}
      </span>
    </div>
  );
}
