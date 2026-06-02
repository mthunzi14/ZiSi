// PortfolioPerformance.jsx — Bloomberg P&L Chart + Asset Win Rates
import { useState, useEffect } from 'react';
import CountUpStats from './common/CountUpStats';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  LineChart, Line, ReferenceLine, Legend,
} from 'recharts';

const ASSET_COLORS = {
  'BTC/5m':  '#f7931a',
  'BTC/15m': '#ffb042',
  'ETH/5m':  '#627eea',
  'SOL/5m':  '#14f195',
  'XRP/5m':  '#00aae4',
  'DOGE/5m': '#e1b303',
  'LINK/5m': '#2b7fff',
  'BNB/5m':  '#f3ba2f',
};

const ASSETS = ['BTC/5m', 'BTC/15m', 'ETH/5m', 'SOL/5m', 'XRP/5m', 'DOGE/5m', 'LINK/5m', 'BNB/5m'];

function buildWrSeries(trades) {
  const outcomes = {};
  for (const t of trades) {
    if (!t.asset || !t.timeframe || t.result === null) continue;
    const key = `${t.asset}/${t.timeframe}`;
    if (!outcomes[key]) outcomes[key] = [];
    outcomes[key].push(t.result === 'WIN' ? 1 : 0);
  }
  const maxLen = Math.max(...Object.values(outcomes).map(a => a.length), 0);
  if (maxLen === 0) return [];
  const points = [];
  for (let i = 0; i < maxLen; i++) {
    const pt = { index: i + 1 };
    for (const key of ASSETS) {
      const arr = outcomes[key] || [];
      if (i < arr.length) {
        const window = arr.slice(Math.max(0, i - 39), i + 1);
        pt[key] = window.length >= 5 ? parseFloat((window.reduce((s, v) => s + v, 0) / window.length).toFixed(3)) : null;
      }
    }
    points.push(pt);
  }
  return points;
}

function assetFromTitle(title) {
  if (/bitcoin/i.test(title))  return 'BTC';
  if (/ethereum/i.test(title)) return 'ETH';
  if (/solana/i.test(title))   return 'SOL';
  if (/\bxrp\b/i.test(title))  return 'XRP';
  if (/doge/i.test(title))     return 'DOGE';
  if (/\blink\b/i.test(title) || /chainlink/i.test(title)) return 'LINK';
  if (/bnb/i.test(title) || /binance/i.test(title)) return 'BNB';
  return null;
}

function tfFromTitle(title) {
  const tag = title.match(/\[(5m|15m)\]/);
  if (tag) return tag[1];
  return '?';
}

const TF_PILLS = ['1D', '1W', '1M', '1Y', 'ALL'];

export default function PortfolioPerformance({ positions = {}, state = {} }) {
  const [activeTab, setActiveTab]   = useState('equity');
  const [timeframe, setTimeframe]   = useState('ALL');
  const [history,   setHistory]     = useState([]);
  const [expanded,  setExpanded]    = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const res  = await fetch('/api/equity');
        const data = await res.json();
        setHistory(data.history || []);
      } catch { /* offline */ }
    };
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, []);

  const getFilteredData = () => {
    const bal   = parseFloat(state.balance || state.starting_balance || 0);
    const nowMs = Date.now();
    if (!history || history.length === 0) {
      return [
        { timestamp: new Date(nowMs - 86400000).toISOString(), balance: bal, pnl: 0, timeMs: nowMs - 86400000 },
        { timestamp: new Date(nowMs).toISOString(),            balance: bal, pnl: 0, timeMs: nowMs },
      ];
    }
    if (history.length === 1) {
      const h0Ms = new Date(history[0].timestamp).getTime();
      return [
        { timestamp: new Date(h0Ms - 3600000).toISOString(), balance: bal, pnl: 0, timeMs: h0Ms - 3600000 },
        { ...history[0], timeMs: h0Ms },
      ];
    }
    const now = new Date(), cutoff = new Date();
    if (timeframe === '1D') cutoff.setHours(now.getHours() - 24);
    else if (timeframe === '1W') cutoff.setDate(now.getDate() - 7);
    else if (timeframe === '1M') cutoff.setMonth(now.getMonth() - 1);
    else if (timeframe === '1Y') cutoff.setFullYear(now.getFullYear() - 1);
    else {
      return history.map(item => ({ ...item, timeMs: new Date(item.timestamp).getTime() }));
    }
    const filtered = history.filter(item => new Date(item.timestamp) >= cutoff);
    let mapped = (filtered.length >= 2 ? filtered : history).map(item => ({ ...item, timeMs: new Date(item.timestamp).getTime() }));
    const cutoffMs = cutoff.getTime();
    if (mapped[0]?.timeMs > cutoffMs) {
      mapped.unshift({ timestamp: cutoff.toISOString(), balance: mapped[0].balance, pnl: mapped[0].pnl, timeMs: cutoffMs });
    }
    const lastItem = mapped[mapped.length - 1];
    if (lastItem && Math.abs(lastItem.balance - bal) > 0.001) {
      mapped.push({ timestamp: new Date(nowMs).toISOString(), balance: bal, pnl: parseFloat(state.pnl || 0), timeMs: nowMs });
    }
    return mapped;
  };

  const getChartDomain = () => {
    if (timeframe === 'ALL') return ['dataMin', 'dataMax'];
    const nowMs = Date.now();
    const dur = { '1D': 86400000, '1W': 604800000, '1M': 2592000000, '1Y': 31536000000 };
    return [nowMs - (dur[timeframe] || 86400000), nowMs];
  };

  const filteredHistory = getFilteredData();

  const getPnlMetrics = () => {
    if (!history || history.length < 2 || timeframe === 'ALL') {
      const totalPnl = parseFloat(state.pnl || 0);
      const start    = parseFloat(state.starting_balance || 0) || 1;
      return { pnl: totalPnl, pct: (totalPnl / start) * 100 };
    }
    const latest = filteredHistory[filteredHistory.length - 1]?.balance || 0;
    const first  = filteredHistory[0]?.balance || 1;
    let pnl = latest - first;
    let pct = first > 0 ? (pnl / first) * 100 : 0;
    if (Math.abs(pnl) < 0.01 && Math.abs(parseFloat(state.pnl || 0)) > 0.01) {
      pnl = parseFloat(state.pnl || 0);
      pct = (pnl / (parseFloat(state.starting_balance || 0) || 1)) * 100;
    }
    return { pnl, pct };
  };

  const { pnl: tfPnl, pct: tfPct } = getPnlMetrics();
  const pnlColor = tfPnl >= 0 ? '#10b981' : '#ef4444';

  // Per-trade stats
  const closed    = positions?.closed || [];
  const wins      = closed.filter(p => parseFloat(p.realized_pnl ?? 0) > 0).length;
  const losses    = closed.filter(p => parseFloat(p.realized_pnl ?? 0) < 0).length;
  const decisive  = wins + losses;
  const wr        = decisive > 0 ? (wins / decisive * 100).toFixed(1) : '—';
  const grossW    = closed.filter(p => parseFloat(p.realized_pnl ?? 0) > 0).reduce((s, p) => s + parseFloat(p.realized_pnl ?? 0), 0);
  const grossL    = closed.filter(p => parseFloat(p.realized_pnl ?? 0) < 0).reduce((s, p) => s + Math.abs(parseFloat(p.realized_pnl ?? 0)), 0);
  const pf        = grossL > 0 ? (grossW / grossL).toFixed(2) : '∞';
  const avgW      = wins > 0 ? (grossW / wins).toFixed(2) : '—';
  const avgL      = losses > 0 ? (grossL / losses).toFixed(2) : '—';

  // WR chart data
  const trades = closed.map(p => ({
    asset:     (() => { const a = (p.event_title || '').match(/\[(BTC|ETH|SOL|XRP|DOGE|LINK|BNB)\]/); return a ? a[1] : assetFromTitle(p.event_title || ''); })(),
    timeframe: tfFromTitle(p.event_title || ''),
    result:    parseFloat(p.realized_pnl ?? 0) > 0 ? 'WIN' : 'LOSS',
  })).filter(t => t.asset);
  const wrData = buildWrSeries(trades);

  const TF_LABELS = { '1D': 'Past Day', '1W': 'Past Week', '1M': 'Past Month', '1Y': 'Past Year', 'ALL': 'All-Time' };

  const TooltipContent = ({ active, payload }) => {
    if (!active || !payload?.length) return null;
    const d      = payload[0].payload;
    const pnlVal = parseFloat(d.pnl || 0);
    const c      = pnlVal >= 0 ? '#10b981' : '#ef4444';
    return (
      <div style={{ background: 'rgba(12,12,14,0.96)', backdropFilter: 'blur(12px)', border: '1px solid #c59b2755', borderRadius: 8, padding: '10px 14px' }}>
        <div style={{ color: '#52525b', fontSize: 10, marginBottom: 4 }}>{new Date(d.timestamp).toLocaleString()}</div>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, fontSize: 11, margin: '2px 0' }}>
          <span style={{ color: '#71717a' }}>Balance</span>
          <span style={{ fontWeight: 700, fontFamily: 'monospace' }}>${parseFloat(d.balance || 0).toFixed(2)}</span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, fontSize: 11, margin: '2px 0' }}>
          <span style={{ color: '#71717a' }}>Net P&L</span>
          <span style={{ fontWeight: 700, fontFamily: 'monospace', color: c }}>{pnlVal >= 0 ? '+' : ''}${pnlVal.toFixed(2)}</span>
        </div>
      </div>
    );
  };

  return (
    <div className="glass-panel" style={{ padding: 'var(--spacing-20)', display: 'flex', flexDirection: 'column' }}>

      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: expanded ? 12 : 0, flexWrap: 'wrap', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 15 }}>P&amp;L Chart</span>

          {/* Tab pills */}
          <div style={{ display: 'flex', gap: 3, background: 'rgba(255,255,255,0.03)', padding: 3, borderRadius: 8, border: '1px solid rgba(255,255,255,0.05)' }}>
            {['equity', 'winrate'].map(t => (
              <button key={t} onClick={() => setActiveTab(t)} style={{
                padding: '4px 10px', borderRadius: 5, border: 'none',
                background: activeTab === t ? 'rgba(255,255,255,0.08)' : 'transparent',
                color: activeTab === t ? 'var(--color-text-primary)' : '#52525b',
                fontSize: 10, fontWeight: 600, cursor: 'pointer', transition: 'all 0.15s',
                letterSpacing: '0.03em',
              }}>{t === 'equity' ? 'Equity' : 'Win Rates'}</button>
            ))}
          </div>

          {/* Live P&L pill */}
          <span style={{
            fontFamily: 'monospace', fontSize: 11, fontWeight: 800,
            color: pnlColor,
            background: tfPnl >= 0 ? 'rgba(16,185,129,0.08)' : 'rgba(239,68,68,0.08)',
            border: `1px solid ${pnlColor}30`,
            borderRadius: 6, padding: '2px 8px',
          }}>
            {tfPnl >= 0 ? '+' : ''}${tfPnl.toFixed(2)}
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {/* Timeframe pills (equity only) */}
          {activeTab === 'equity' && (
            <div style={{ display: 'flex', gap: 2, background: 'rgba(255,255,255,0.02)', padding: 2, borderRadius: 6, border: '1px solid rgba(255,255,255,0.03)' }}>
              {TF_PILLS.map(tf => (
                <button key={tf} onClick={() => setTimeframe(tf)} style={{
                  padding: '2px 8px', borderRadius: 4, border: 'none',
                  background: timeframe === tf ? 'rgba(197,155,39,0.15)' : 'transparent',
                  color: timeframe === tf ? '#c59b27' : '#52525b',
                  fontSize: 9, fontFamily: 'monospace', fontWeight: 700, cursor: 'pointer', transition: 'all 0.15s',
                }}>{tf}</button>
              ))}
            </div>
          )}
          <button onClick={() => setExpanded(e => !e)} style={{
            background: 'none', border: 'none', cursor: 'pointer', color: '#52525b', fontSize: 9, padding: '2px 6px',
            display: 'inline-block',
            transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)',
            transition: 'transform 0.25s cubic-bezier(0.4,0,0.2,1)',
          }}>▾</button>
        </div>
      </div>

      {/* Body */}
      <div style={{ maxHeight: expanded ? '500px' : '0px', overflow: 'hidden', transition: 'max-height 0.35s cubic-bezier(0.4,0,0.2,1)' }}>

        {activeTab === 'equity' ? (
          <>
            {/* Stat row */}
            <div style={{ display: 'flex', gap: 20, marginBottom: 10, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontSize: 8, color: '#52525b', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 2 }}>{TF_LABELS[timeframe]}</div>
                <div style={{ fontFamily: 'var(--font-display)', fontSize: 26, fontWeight: 800, color: pnlColor, lineHeight: 1 }}>
                  <CountUpStats value={Math.abs(tfPnl)} decimals={2} prefix={tfPnl >= 0 ? '+$' : '-$'} />
                </div>
                <div style={{ fontSize: 10, color: '#71717a', marginTop: 2 }}>
                  <CountUpStats value={Math.abs(tfPct)} decimals={2} prefix={tfPct >= 0 ? '+' : '-'} suffix="%" style={{ color: pnlColor }} />
                  <span style={{ marginLeft: 4 }}>return</span>
                </div>
              </div>

              {/* Quick stats */}
              {[
                { label: 'Trades', val: closed.length },
                { label: 'WR', val: wr === '—' ? '—' : `${wr}%`, color: parseFloat(wr) >= 65 ? '#10b981' : parseFloat(wr) >= 50 ? '#f97316' : '#ef4444' },
                { label: 'PF', val: pf, color: parseFloat(pf) >= 1.5 || pf === '∞' ? '#10b981' : parseFloat(pf) >= 1 ? '#f97316' : '#ef4444' },
                { label: 'Avg W', val: avgW !== '—' ? `+$${avgW}` : '—', color: '#10b981' },
                { label: 'Avg L', val: avgL !== '—' ? `-$${avgL}` : '—', color: '#ef4444' },
              ].map(({ label, val, color }) => (
                <div key={label} style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 8, color: '#52525b', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 2 }}>{label}</div>
                  <div style={{ fontFamily: 'monospace', fontSize: 12, fontWeight: 700, color: color || '#a1a1aa' }}>{val}</div>
                </div>
              ))}
            </div>

            {/* Chart */}
            <div style={{ width: '100%', height: 180 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={filteredHistory} margin={{ top: 8, right: 8, left: -22, bottom: 0 }}>
                  <defs>
                    <linearGradient id="eqGlow" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor={pnlColor} stopOpacity={0.22} />
                      <stop offset="95%" stopColor={pnlColor} stopOpacity={0.0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" vertical={false} />
                  <XAxis dataKey="timeMs" type="number" scale="time" domain={getChartDomain()}
                    tickFormatter={ts => {
                      const d = new Date(ts);
                      return timeframe === '1D'
                        ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                        : d.toLocaleDateString([], { month: 'short', day: 'numeric' });
                    }}
                    tick={{ fill: '#3f3f46', fontSize: 9 }} axisLine={false} tickLine={false} />
                  <YAxis domain={['dataMin - 0.5', 'dataMax + 0.5']}
                    tickFormatter={v => `$${v.toFixed(0)}`}
                    tick={{ fill: '#3f3f46', fontSize: 9 }} axisLine={false} tickLine={false} />
                  <Tooltip content={<TooltipContent />} />
                  <Area type="monotone" dataKey="balance" stroke={pnlColor} strokeWidth={2}
                    fillOpacity={1} fill="url(#eqGlow)" isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </>
        ) : (
          <>
            <div style={{ fontSize: 11, color: '#71717a', marginBottom: 8 }}>
              Rolling 40-trade win rate per asset (min 5 trades to appear)
            </div>
            <div style={{ width: '100%', height: 200 }}>
              {wrData.length < 5 ? (
                <div style={{ color: '#52525b', fontSize: 12, textAlign: 'center', padding: '60px 0' }}>
                  Need 5+ trades per asset to build edge chart
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={wrData} margin={{ top: 8, right: 8, left: -22, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" vertical={false} />
                    <XAxis dataKey="index" tick={{ fill: '#3f3f46', fontSize: 9 }} axisLine={false} tickLine={false} />
                    <YAxis domain={[0, 1]} tickFormatter={v => `${(v*100).toFixed(0)}%`}
                      tick={{ fill: '#3f3f46', fontSize: 9 }} axisLine={false} tickLine={false} />
                    <Tooltip formatter={(v, n) => [`${(v*100).toFixed(1)}%`, n]}
                      contentStyle={{ background: 'rgba(12,12,14,0.96)', border: '1px solid #27272a', borderRadius: 8, fontSize: 11 }} />
                    <Legend wrapperStyle={{ fontSize: 9, color: '#52525b', paddingTop: 8 }} iconSize={7} />
                    <ReferenceLine y={0.62} stroke="rgba(43,127,255,0.35)" strokeDasharray="4 3" label={{ value: 'Edge 62%', fill: '#2b7fff', fontSize: 8 }} />
                    <ReferenceLine y={0.50} stroke="rgba(249,115,22,0.35)" strokeDasharray="4 3" label={{ value: 'Breakeven', fill: '#f97316', fontSize: 8 }} />
                    {ASSETS.map(key => (
                      <Line key={key} type="monotone" dataKey={key} stroke={ASSET_COLORS[key]}
                        strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false} />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
