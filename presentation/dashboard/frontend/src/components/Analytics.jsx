// Analytics.jsx — Trader-facing performance breakdown (6 panels)
import { useMemo, memo } from 'react';
import {
  BarChart, Bar, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Cell,
} from 'recharts';

const ASSETS = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'HYPE', 'BNB'];

const ASSET_COLOR = {
  BTC:  '#2b7fff',
  ETH:  '#00d4a3',
  SOL:  '#9945ff',
  XRP:  '#ff9500',
  DOGE: '#f1b90d',
  HYPE: '#ff007a',
  BNB:  '#f3ba2f',
};

const EXIT_META = {
  TARGET_HIT:     { label: 'Target Hit',    color: '#00d4a3' },
  MARKET_EXPIRED: { label: 'Market Expired', color: '#f59e0b' },
  STOP_HIT:       { label: 'Stop Hit',       color: '#ef4444' },
};

function assetFromTitle(title = '') {
  if (/bitcoin/i.test(title))  return 'BTC';
  if (/ethereum/i.test(title)) return 'ETH';
  if (/solana/i.test(title))   return 'SOL';
  if (/\bxrp\b/i.test(title))  return 'XRP';
  if (/doge/i.test(title))     return 'DOGE';
  if (/hype/i.test(title))     return 'HYPE';
  if (/bnb/i.test(title) || /binance/i.test(title)) return 'BNB';
  const tag = title.match(/\[(BTC|ETH|SOL|XRP|DOGE|HYPE|BNB)\]/);
  return tag ? tag[1] : null;
}

// ── data derivation helpers ───────────────────────────────────────────────────

function buildAssetStats(closed) {
  const byAsset = {};
  for (const t of closed) {
    const asset = assetFromTitle(t.event_title);
    if (!asset) continue;
    if (!byAsset[asset]) byAsset[asset] = { wins: 0, total: 0, pnl: 0 };
    byAsset[asset].total += 1;
    byAsset[asset].pnl   += parseFloat(t.realized_pnl || 0);
    if (parseFloat(t.realized_pnl || 0) > 0) byAsset[asset].wins += 1;
  }
  return ASSETS
    .filter(a => byAsset[a] && byAsset[a].total > 0)
    .map(a => ({
      asset: a,
      wr:    Math.round((byAsset[a].wins / byAsset[a].total) * 100),
      pnl:   Math.round(byAsset[a].pnl * 100) / 100,
      count: byAsset[a].total,
    }));
}

function buildExitBreakdown(closed) {
  const counts = {};
  for (const t of closed) {
    const r = t.exit_reason || 'UNKNOWN';
    counts[r] = (counts[r] || 0) + 1;
  }
  return Object.entries(counts).map(([reason, count]) => ({
    reason,
    count,
    meta: EXIT_META[reason] || { label: reason, color: 'var(--color-iron)' },
  }));
}

function buildVolumeSeries(closed) {
  const days = {};
  const now = Date.now();
  for (let i = 6; i >= 0; i--) {
    const d = new Date(now - i * 86400000);
    days[d.toISOString().slice(0, 10)] = { date: d.toISOString().slice(5, 10), ...Object.fromEntries(ASSETS.map(a => [a, 0])) };
  }
  for (const t of closed) {
    if (!t.entry_time) continue;
    const day = t.entry_time.slice(0, 10);
    if (!days[day]) continue;
    const asset = assetFromTitle(t.event_title);
    if (asset && days[day][asset] !== undefined) days[day][asset] += 1;
  }
  return Object.values(days);
}

function buildHourlyPnl(closed) {
  const hours = Array.from({ length: 24 }, (_, h) => ({ hour: h, label: `${h}h`, pnl: null, count: 0 }));
  for (const t of closed) {
    if (!t.entry_time) continue;
    const h = new Date(t.entry_time).getUTCHours();
    hours[h].pnl = (hours[h].pnl ?? 0) + parseFloat(t.realized_pnl || 0);
    hours[h].count += 1;
  }
  return hours.map(h => ({
    ...h,
    pnl: h.count > 0 ? Math.round((h.pnl / h.count) * 100) / 100 : null,
  }));
}

function buildRunningEv(closed) {
  let sum = 0;
  return closed.map((t, i) => {
    sum += parseFloat(t.realized_pnl || 0);
    return { n: i + 1, ev: Math.round((sum / (i + 1)) * 1000) / 1000 };
  });
}

function parseType(title, entryType) {
  const titleUpper = (title || '').toUpperCase();
  if (titleUpper.includes('[CLOSE_SNIPE]') || titleUpper.includes('[CLOSE-SNIPE]') || titleUpper.includes('[CLOSE-SNIPE-EARLY]') || titleUpper.includes('[NCS]')) return 'NCS';
  if (titleUpper.includes('[FAIR_VAL]') || titleUpper.includes('[FV]')) return 'FV';
  if (titleUpper.includes('[T2_SWEEPER]') || titleUpper.includes('[SWEEP]')) return 'SWEEP';
  if (titleUpper.includes('[LATENCY_ARB]') || titleUpper.includes('[LAT-ARB]') || titleUpper.includes('[ARB]') || titleUpper.includes('[LAG_ARB_FUSION]')) return 'LAT ARB';
  if (titleUpper.includes('[REVERSAL_SNIPE]') || titleUpper.includes('[REVERSAL-SNIPE]')) return 'REV SNIPE';
  if (titleUpper.includes('[REVERSAL_STREAK]') || titleUpper.includes('[REVERSAL-STREAK]')) return 'REV STREAK';
  if (titleUpper.includes('[SINGLE]') || titleUpper.includes('[SIG]')) return 'SIG';
  if (titleUpper.includes('[DUAL_MAIN]') || titleUpper.includes('[DUAL_HEDGE]') || titleUpper.includes('[DUAL]')) return 'DUAL';

  const typeUpper = (entryType || '').toUpperCase();
  if (typeUpper === 'CLOSE_SNIPE' || typeUpper === 'CLOSE-SNIPE' || typeUpper === 'CLOSE-SNIPE-EARLY' || typeUpper === 'CLOSE_SNIPE_EARLY' || typeUpper === 'NCS') return 'NCS';
  if (typeUpper === 'FAIR_VAL' || typeUpper === 'FAIR-VAL' || typeUpper === 'FV') return 'FV';
  if (typeUpper === 'SIGNAL' || typeUpper === 'SINGLE' || typeUpper === 'SIG') return 'SIG';
  if (typeUpper === 'SWEEP' || typeUpper === 'T2_SWEEPER') return 'SWEEP';
  if (typeUpper === 'LATENCY_ARB' || typeUpper === 'LAT-ARB' || typeUpper === 'ARB' || typeUpper === 'LAT ARB') return 'LAT ARB';
  if (typeUpper === 'REVERSAL-SNIPE' || typeUpper === 'REVERSAL_SNIPE' || typeUpper === 'REV SNIPE' || typeUpper === 'REV') return 'REV SNIPE';
  if (typeUpper === 'REVERSAL-STREAK' || typeUpper === 'REVERSAL_STREAK' || typeUpper === 'REV STREAK') return 'REV STREAK';
  if (typeUpper === 'DUAL' || typeUpper === 'DUAL_MAIN' || typeUpper === 'DUAL_HEDGE') return 'DUAL';

  return 'SIG';
}

function buildTypeStats(closed) {
  const byType = {};
  for (const t of closed) {
    const type = parseType(t.event_title, t.entry_type);
    if (!byType[type]) byType[type] = { wins: 0, total: 0, pnl: 0 };
    byType[type].total += 1;
    byType[type].pnl += parseFloat(t.realized_pnl || 0);
    if (parseFloat(t.realized_pnl || 0) > 0) byType[type].wins += 1;
  }
  const ORDER = ['LAT ARB', 'FV', 'NCS', 'REV SNIPE', 'REV STREAK', 'SIG', 'SWEEP', 'DUAL'];
  return Object.entries(byType)
    .sort((a, b) => (ORDER.indexOf(a[0]) - ORDER.indexOf(b[0])))
    .map(([type, d]) => ({
      type,
      wr:    d.total > 0 ? Math.round((d.wins / d.total) * 100) : 0,
      pnl:   Math.round(d.pnl * 100) / 100,
      count: d.total,
    }));
}

const TYPE_COLOR = {
  'LAT ARB':    '#2b7fff',
  'FV':         '#00d4a3',
  'NCS':        '#e27622',
  'REV SNIPE':  '#ff007a',
  'REV STREAK': '#e076ff',
  'SIG':        'var(--color-iron)',
  'SWEEP':      '#eab308',
  'DUAL':       '#8b5cf6',
};

// ── shared style constants ────────────────────────────────────────────────────

const S = {
  card:    { padding: '20px 24px' },
  title:   { fontFamily: 'var(--font-primary)', fontWeight: 700, fontSize: '14px', color: 'var(--color-obsidian)', marginBottom: '4px' },
  sub:     { fontSize: '11px', color: 'var(--color-iron)', marginBottom: '16px' },
  axisTick: { fontSize: 10, fill: 'var(--color-iron)' },
};

function PanelCard({ title, sub, children, style = {} }) {
  return (
    <div className="card" style={{ ...S.card, ...style }}>
      <div style={S.title}>{title}</div>
      {sub && <div style={S.sub}>{sub}</div>}
      {children}
    </div>
  );
}

function CustomBar(props) {
  const { x, y, width, height, value } = props;
  if (!height || height <= 0) return null;
  return <rect x={x} y={y} width={width} height={height} fill={value >= 0 ? '#00d4a3' : '#ef4444'} rx={3} />;
}

// ── main component ────────────────────────────────────────────────────────────

const Analytics = memo(function Analytics({ closed = [] }) {
  const assetStats    = useMemo(() => buildAssetStats(closed), [closed]);
  const exitBreakdown = useMemo(() => buildExitBreakdown(closed), [closed]);
  const volumeSeries  = useMemo(() => buildVolumeSeries(closed), [closed]);
  const hourlyPnl     = useMemo(() => buildHourlyPnl(closed), [closed]);
  const runningEv     = useMemo(() => buildRunningEv(closed), [closed]);
  const typeStats     = useMemo(() => buildTypeStats(closed), [closed]);

  const totalTrades = closed.length;
  const totalPnl    = useMemo(() => {
    return Math.round(closed.reduce((s, t) => s + parseFloat(t.realized_pnl || 0), 0) * 100) / 100;
  }, [closed]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }} className="page-fade-enter">

      {/* Page header */}
      <div className="card" style={{ padding: '14px 24px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h2 style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: '19px', color: 'var(--color-obsidian)', letterSpacing: '-0.02em' }}>
            ZiSi. Portfolio Analytics
          </h2>
          <div style={{ fontSize: '11px', color: 'var(--color-iron)', marginTop: '2px' }}>
            Performance breakdown across {totalTrades} closed trades
          </div>
        </div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: '20px', fontWeight: 700, color: totalPnl >= 0 ? '#00d4a3' : '#ef4444' }}>
          {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)}
        </div>
      </div>

      {/* Row 1: Per-asset WR + Per-asset P&L */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>

        <PanelCard title="Win Rate by Asset" sub="Decisive trades only (win % of wins+losses)">
          {assetStats.length === 0 ? (
            <div style={{ color: 'var(--color-iron)', fontSize: '12px', textAlign: 'center', paddingTop: '40px' }}>No closed trades yet</div>
          ) : (
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={assetStats} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                <CartesianGrid vertical={false} stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="asset" tick={S.axisTick} />
                <YAxis domain={[0, 100]} tick={S.axisTick} tickFormatter={v => `${v}%`} />
                <Tooltip
                  formatter={(v, _, p) => [`${v}% (${p.payload.count} trades)`, 'Win Rate']}
                  contentStyle={{ background: 'var(--color-cream-deep)', border: '1px solid var(--color-border-subtle)', borderRadius: 8, fontSize: 11 }}
                />
                <ReferenceLine y={65} stroke="#f59e0b" strokeDasharray="4 2" />
                <Bar dataKey="wr" radius={[4, 4, 0, 0]} isAnimationActive={false}>
                  {assetStats.map(d => (
                    <Cell key={d.asset} fill={ASSET_COLOR[d.asset] || '#888'} fillOpacity={0.85} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
          <div style={{ fontSize: '10px', color: 'var(--color-iron)', marginTop: '6px' }}>
            — Dashed line = 65% mandate
          </div>
        </PanelCard>

        <PanelCard title="Realized P&L by Asset" sub="Cumulative closed P&L per asset">
          {assetStats.length === 0 ? (
            <div style={{ color: 'var(--color-iron)', fontSize: '12px', textAlign: 'center', paddingTop: '40px' }}>No closed trades yet</div>
          ) : (
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={assetStats} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
                <CartesianGrid vertical={false} stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="asset" tick={S.axisTick} />
                <YAxis tick={S.axisTick} tickFormatter={v => `$${v}`} />
                <Tooltip
                  formatter={v => [`$${v.toFixed(2)}`, 'P&L']}
                  contentStyle={{ background: 'var(--color-cream-deep)', border: '1px solid var(--color-border-subtle)', borderRadius: 8, fontSize: 11 }}
                />
                <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" />
                <Bar dataKey="pnl" radius={[4, 4, 0, 0]} isAnimationActive={false} shape={<CustomBar />} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </PanelCard>
      </div>

      {/* Row 2: Exit reasons + Volume timeline */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.6fr', gap: '20px' }}>

        <PanelCard title="Exit Reason Breakdown" sub="How trades are closed">
          {exitBreakdown.length === 0 ? (
            <div style={{ color: 'var(--color-iron)', fontSize: '12px', textAlign: 'center', paddingTop: '40px' }}>No closed trades yet</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginTop: '4px' }}>
              {exitBreakdown.sort((a, b) => b.count - a.count).map(({ reason, count, meta }) => {
                const pct = Math.round((count / totalTrades) * 100);
                return (
                  <div key={reason}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
                      <span style={{ fontSize: '12px', fontWeight: 600, color: meta.color }}>{meta.label}</span>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--color-obsidian)' }}>
                        {count} <span style={{ color: 'var(--color-iron)', fontWeight: 400 }}>({pct}%)</span>
                      </span>
                    </div>
                    <div style={{ height: '6px', background: 'var(--color-cream-deep)', borderRadius: '3px', overflow: 'hidden' }}>
                      <div style={{ height: '100%', width: `${pct}%`, background: meta.color, borderRadius: '3px', opacity: 0.8 }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </PanelCard>

        <PanelCard title="Trade Volume — Last 7 Days" sub="Entries per asset per day">
          <ResponsiveContainer width="100%" height={190}>
            <BarChart data={volumeSeries} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="date" tick={S.axisTick} />
              <YAxis tick={S.axisTick} allowDecimals={false} />
              <Tooltip
                contentStyle={{ background: 'var(--color-cream-deep)', border: '1px solid var(--color-border-subtle)', borderRadius: 8, fontSize: 11 }}
              />
              {ASSETS.map(a => (
                <Bar key={a} dataKey={a} stackId="vol" fill={ASSET_COLOR[a] || '#888'} fillOpacity={0.85} isAnimationActive={false} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </PanelCard>
      </div>

      {/* Row 3: Hourly P&L + Running EV */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>

        <PanelCard title="Avg P&L by UTC Hour" sub="Which trading hours are profitable">
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={hourlyPnl} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="hour" tick={S.axisTick} tickFormatter={v => `${v}h`} interval={3} />
              <YAxis tick={S.axisTick} tickFormatter={v => `$${v}`} />
              <Tooltip
                formatter={(v, _, p) => [v != null ? `$${v.toFixed(2)} avg` : 'No trades', 'P&L']}
                labelFormatter={h => `UTC ${h}:00`}
                contentStyle={{ background: 'var(--color-cream-deep)', border: '1px solid var(--color-border-subtle)', borderRadius: 8, fontSize: 11 }}
              />
              <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" />
              <Bar dataKey="pnl" radius={[3, 3, 0, 0]} isAnimationActive={false} shape={<CustomBar />} />
            </BarChart>
          </ResponsiveContainer>
        </PanelCard>

        <PanelCard title="Running EV Per Trade" sub="Average P&L per trade, cumulative — flat/rising = positive edge">
          {runningEv.length === 0 ? (
            <div style={{ color: 'var(--color-iron)', fontSize: '12px', textAlign: 'center', paddingTop: '40px' }}>No closed trades yet</div>
          ) : (
            <ResponsiveContainer width="100%" height={180}>
              <LineChart data={runningEv} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
                <CartesianGrid vertical={false} stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="n" tick={S.axisTick} label={{ value: 'trade #', position: 'insideBottomRight', offset: -4, style: { fontSize: 9, fill: 'var(--color-iron)' } }} />
                <YAxis tick={S.axisTick} tickFormatter={v => `$${v}`} />
                <Tooltip
                  formatter={v => [`$${v.toFixed(3)}`, 'Avg P&L/trade']}
                  contentStyle={{ background: 'var(--color-cream-deep)', border: '1px solid var(--color-border-subtle)', borderRadius: 8, fontSize: 11 }}
                />
                <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" />
                <Line
                  type="monotone"
                  dataKey="ev"
                  stroke="var(--color-accent)"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </PanelCard>
      </div>

      {/* Row 4: Signal source breakdown */}
      <PanelCard title="Performance by Signal Source" sub="Win rate and P&L split by which system generated the trade">
        {typeStats.length === 0 ? (
          <div style={{ color: 'var(--color-iron)', fontSize: '12px', textAlign: 'center', paddingTop: '20px' }}>
            No typed trades yet — trades entered after this update show their source
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginTop: '4px' }}>
            {typeStats.map(({ type, wr, pnl, count }) => {
              const color = TYPE_COLOR[type] || 'var(--color-iron)';
              const pnlColor = pnl > 0 ? 'var(--color-profit)' : pnl < 0 ? 'var(--color-loss)' : 'var(--color-iron)';
              return (
                <div key={type} style={{ display: 'grid', gridTemplateColumns: '108px 1fr 52px 72px', gap: 12, alignItems: 'center' }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 700, color }}>{type}</span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <div style={{ flex: 1, height: 6, background: 'var(--color-cream-deep)', borderRadius: 3, overflow: 'hidden' }}>
                      <div style={{ height: '100%', width: `${wr}%`, background: wr >= 65 ? 'var(--color-profit)' : wr >= 50 ? '#f59e0b' : 'var(--color-loss)', borderRadius: 3 }} />
                    </div>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, minWidth: 38, color: wr >= 65 ? 'var(--color-profit)' : wr >= 50 ? '#f59e0b' : 'var(--color-loss)' }}>{wr}%</span>
                  </div>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--color-iron)', textAlign: 'right' }}>{count}t</span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 700, color: pnlColor, textAlign: 'right' }}>
                    {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </PanelCard>

    </div>
  );
});

export default Analytics;
