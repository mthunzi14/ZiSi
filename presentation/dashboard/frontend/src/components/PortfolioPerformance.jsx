// PortfolioPerformance.jsx — Premium Glassmorphic Portfolio Charting (Equity + Asset Win Rates)
import { useState, useEffect } from 'react';
import CountUpStats from './common/CountUpStats';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  LineChart, Line, ReferenceLine, Legend,
} from 'recharts';

const ASSET_COLORS = {
  'BTC/5m':  '#2b7fff',
  'BTC/15m': '#426188',
  'ETH/5m':  '#00d4a3',
  'SOL/5m':  '#f5f5f5',
  'XRP/5m':  '#ff9500',
  'DOGE/5m': '#f1b90d',
  'HYPE/5m': '#ff007a',
  'BNB/5m':  '#f3ba2f',
};

const ASSETS = ['BTC/5m', 'BTC/15m', 'ETH/5m', 'SOL/5m', 'XRP/5m', 'DOGE/5m', 'HYPE/5m', 'BNB/5m'];

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
  if (/hype/i.test(title))     return 'HYPE';
  if (/bnb/i.test(title) || /binance/i.test(title)) return 'BNB';
  return null;
}

function tfFromTitle(title) {
  const tag = title.match(/\[(5m|15m)\]/);
  if (tag) return tag[1];
  const tm = title.match(/(\d+:\d+[AP]M)-(\d+:\d+[AP]M)/i);
  if (tm) {
    const toMin = (t) => {
      const m = t.match(/(\d+):(\d+)([AP]M)/i);
      if (!m) return 0;
      let h = parseInt(m[1]), mm = parseInt(m[2]);
      if (/pm/i.test(m[3]) && h !== 12) h += 12;
      if (/am/i.test(m[3]) && h === 12) h = 0;
      return h * 60 + mm;
    };
    let diff = toMin(tm[2]) - toMin(tm[1]);
    if (diff < 0) diff += 1440;
    if (diff > 0) return `${diff}m`;
  }
  return '?';
}

const S = {
  container: {
    background: 'var(--color-bg-surface)',
    borderRadius: 'var(--radius-cards)',
    border: '1px solid var(--color-midnight)',
    padding: 'var(--spacing-20)',
    display: 'flex',
    flexDirection: 'column',
    gap: 'var(--spacing-16)',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    borderBottom: '1px solid rgba(255, 255, 255, 0.05)',
    paddingBottom: 'var(--spacing-12)',
  },
  tabsWrap: {
    display: 'flex',
    gap: 'var(--spacing-8)',
    background: 'rgba(255,255,255,0.03)',
    padding: 3,
    borderRadius: 'var(--radius-buttons)',
    border: '1px solid rgba(255,255,255,0.04)',
  },
  tab: (active) => ({
    padding: '6px 12px',
    borderRadius: 6,
    border: 'none',
    background: active ? 'rgba(255,255,255,0.08)' : 'transparent',
    color: active ? 'var(--color-text-primary)' : 'var(--color-text-muted)',
    fontSize: 12,
    fontWeight: 500,
    cursor: 'pointer',
    transition: 'all 0.2s ease',
  }),
  pillsWrap: {
    display: 'flex',
    gap: 4,
    background: 'rgba(255,255,255,0.02)',
    padding: 2,
    borderRadius: 6,
    border: '1px solid rgba(255,255,255,0.03)',
  },
  pill: (active) => ({
    padding: '3px 8px',
    borderRadius: 4,
    border: 'none',
    background: active ? 'rgba(43, 127, 255, 0.15)' : 'transparent',
    color: active ? 'var(--color-accent)' : 'var(--color-text-muted)',
    fontSize: 10,
    fontFamily: 'var(--font-mono)',
    fontWeight: 600,
    cursor: 'pointer',
    transition: 'all 0.15s ease',
  }),
  metricsArea: {
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
    marginBottom: 'var(--spacing-8)',
  },
  label: {
    fontSize: 11,
    color: 'var(--color-text-muted)',
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
  },
  pnlValue: {
    fontSize: 28,
    fontWeight: 700,
    fontFamily: 'var(--font-heading)',
    display: 'flex',
    alignItems: 'baseline',
    gap: 'var(--spacing-8)',
  },
  trendSub: {
    fontSize: 12,
    color: 'var(--color-text-muted)',
    fontFamily: 'var(--font-body)',
  },
  tooltipCard: {
    background: 'rgba(22, 22, 25, 0.95)', // organic dark slate surface
    backdropFilter: 'blur(12px)',
    border: '1px solid var(--color-accent)', // premium gold border
    borderRadius: 8,
    padding: '10px 14px',
    boxShadow: '0 8px 32px rgba(0, 0, 0, 0.5)',
  },
};

export default function PortfolioPerformance({ positions = {}, state = {} }) {
  const [activeTab, setActiveTab] = useState('equity'); // 'equity' | 'winrate'
  const [timeframe, setTimeframe] = useState('ALL'); // Default to ALL chart view
  const [history, setHistory] = useState([]);

  // Fetch balance history from /api/equity
  useEffect(() => {
    const fetchEquity = async () => {
      try {
        const res = await fetch('/api/equity');
        const data = await res.json();
        setHistory(data.history || []);
      } catch (err) {
        console.error('[PORTFOLIO] Equity read error:', err.message);
      }
    };
    fetchEquity();
    const id = setInterval(fetchEquity, 10000);
    return () => clearInterval(id);
  }, []);

  // Filter history based on timeframe pill
  const getFilteredData = () => {
    const bal = parseFloat(state.balance || state.starting_balance || 0);
    const nowMs = Date.now();
    
    // Synthesis fallback: if history is empty, plot a beautiful flat line starting 24h ago
    if (!history || history.length === 0) {
      return [
        { timestamp: new Date(nowMs - 86400000).toISOString(), balance: bal, pnl: 0, timeMs: nowMs - 86400000 },
        { timestamp: new Date(nowMs).toISOString(), balance: bal, pnl: 0, timeMs: nowMs }
      ];
    }
    
    // Synthesis fallback: if history has 1 element, synthesize a starting baseline point 1 hour before
    if (history.length === 1) {
      const h0 = history[0];
      const h0Ms = new Date(h0.timestamp).getTime();
      return [
        { timestamp: new Date(h0Ms - 3600000).toISOString(), balance: bal, pnl: 0, timeMs: h0Ms - 3600000 },
        { ...h0, timeMs: h0Ms }
      ];
    }

    const now = new Date();
    const cutoff = new Date();
    
    if (timeframe === '1D') cutoff.setHours(now.getHours() - 24);
    else if (timeframe === '1W') cutoff.setDate(now.getDate() - 7);
    else if (timeframe === '1M') cutoff.setMonth(now.getMonth() - 1);
    else if (timeframe === '1Y') cutoff.setFullYear(now.getFullYear() - 1);
    else {
      return history.map(item => ({
        ...item,
        timeMs: new Date(item.timestamp).getTime()
      }));
    }

    const filtered = history.filter(item => new Date(item.timestamp) >= cutoff);
    const data = filtered.length >= 2 ? filtered : history; // fallback to prevent empty plot
    
    // Inject a numeric timeMs field so Recharts can properly scale the X-axis
    let mapped = data.map(item => ({
      ...item,
      timeMs: new Date(item.timestamp).getTime()
    }));

    // Prepend a synthetic baseline point at the timeframe cutoff time if the first trade occurred after the cutoff
    const cutoffMs = cutoff.getTime();
    const firstTimeMs = mapped[0]?.timeMs;
    if (firstTimeMs && firstTimeMs > cutoffMs) {
      mapped.unshift({
        timestamp: cutoff.toISOString(),
        balance: mapped[0].balance,
        pnl: mapped[0].pnl,
        timeMs: cutoffMs
      });
    }

    // SSE Real-time instant append: if high-velocity balance differs from latest history, inject it!
    const lastItem = mapped[mapped.length - 1];
    if (lastItem && Math.abs(lastItem.balance - bal) > 0.001) {
      mapped.push({
        timestamp: new Date(nowMs).toISOString(),
        balance: bal,
        pnl: parseFloat(state.pnl || 0),
        timeMs: nowMs
      });
    }

    return mapped;
  };

  const getChartDomain = () => {
    if (timeframe === 'ALL') return ['dataMin', 'dataMax'];
    const nowMs = Date.now();
    let duration = 24 * 60 * 60 * 1000;
    if (timeframe === '1W') duration = 7 * 24 * 60 * 60 * 1000;
    else if (timeframe === '1M') duration = 30 * 24 * 60 * 60 * 1000;
    else if (timeframe === '1Y') duration = 365 * 24 * 60 * 60 * 1000;
    return [nowMs - duration, nowMs];
  };

  const filteredHistory = getFilteredData();

  // Calculate timeframe-specific P&L
  const getPnlMetrics = () => {
    // Return live total P&L if history is empty or single point
    if (!history || history.length < 2 || timeframe === 'ALL') {
      const totalPnl = parseFloat(state.pnl || 0);
      const start = parseFloat(state.starting_balance || 0) || 1;
      const pct = (totalPnl / start) * 100;
      return { pnl: totalPnl, pct };
    }

    const latest = filteredHistory[filteredHistory.length - 1].balance;
    const first = filteredHistory[0].balance;
    let pnl = latest - first;
    let pct = first > 0 ? (pnl / first) * 100 : 0;

    // Core fallback sync: if calculated PnL from history is 0 but real state has non-zero PnL, display real state PnL
    if (Math.abs(pnl) < 0.01 && Math.abs(parseFloat(state.pnl || 0)) > 0.01) {
      pnl = parseFloat(state.pnl || 0);
      const start = parseFloat(state.starting_balance || 0) || 1;
      pct = start > 0 ? (pnl / start) * 100 : 0;
    }
    
    return { pnl, pct };
  };

  const { pnl: tfPnl, pct: tfPct } = getPnlMetrics();
  const tfPnlColor = tfPnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)';
  const tfPnlSign = tfPnl >= 0 ? '+' : '';

  // Generate legacy win rate chart trades
  const trades = (positions?.closed || []).map(p => ({
    asset: (() => {
      const a = (p.event_title || '').match(/\[(BTC|ETH|SOL|XRP|DOGE|HYPE|BNB)\]/);
      return a ? a[1] : assetFromTitle(p.event_title || '');
    })(),
    timeframe: tfFromTitle(p.event_title || ''),
    result: parseFloat(p.realized_pnl ?? 0) > 0 ? 'WIN' : 'LOSS',
  })).filter(t => t.asset);

  const wrData = buildWrSeries(trades);

  // Custom tooltips
  const CustomEquityTooltip = ({ active, payload }) => {
    if (active && payload && payload.length) {
      const data = payload[0].payload;
      const pnlVal = parseFloat(data.pnl || 0);
      const color = pnlVal >= 0 ? 'var(--color-profit)' : 'var(--color-loss)';
      const sign = pnlVal >= 0 ? '+' : '';
      return (
        <div style={S.tooltipCard}>
          <div style={{ color: 'var(--color-text-muted)', fontSize: 10, marginBottom: 4 }}>
            {new Date(data.timestamp).toLocaleString()}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, fontSize: 12, margin: '2px 0' }}>
            <span style={{ color: 'var(--color-text-secondary)' }}>Balance:</span>
            <span style={{ fontWeight: 600, fontFamily: 'var(--font-mono)' }}>${parseFloat(data.balance || 0).toFixed(2)}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, fontSize: 12, margin: '2px 0' }}>
            <span style={{ color: 'var(--color-text-secondary)' }}>Net P&amp;L:</span>
            <span style={{ fontWeight: 600, fontFamily: 'var(--font-mono)', color }}>
              {sign}${pnlVal.toFixed(2)}
            </span>
          </div>
        </div>
      );
    }
    return null;
  };

  const timeframeLabelMap = {
    '1D': 'Past Day',
    '1W': 'Past Week',
    '1M': 'Past Month',
    '1Y': 'Past Year',
    'ALL': 'All-Time',
  };

  return (
    <div className="glass-panel" style={{ padding: 'var(--spacing-20)', display: 'flex', flexDirection: 'column', gap: 'var(--spacing-16)' }}>
      <div style={S.header}>
        <div style={S.tabsWrap}>
          <button style={S.tab(activeTab === 'equity')} onClick={() => setActiveTab('equity')}>
            Profit/Loss
          </button>
          <button style={S.tab(activeTab === 'winrate')} onClick={() => setActiveTab('winrate')}>
            Asset Win Rates
          </button>
        </div>

        {activeTab === 'equity' && (
          <div style={S.pillsWrap}>
            {['1D', '1W', '1M', '1Y', 'ALL'].map(tf => (
              <button key={tf} style={S.pill(timeframe === tf)} onClick={() => setTimeframe(tf)}>
                {tf}
              </button>
            ))}
          </div>
        )}
      </div>

      {activeTab === 'equity' ? (
        <div style={{ display: 'flex', flexDirection: 'column', flex: 1 }}>
          <div style={S.metricsArea}>
            <span style={S.label}>Profit/Loss</span>
            <div style={{ ...S.pnlValue, color: tfPnlColor }}>
              <CountUpStats
                value={Math.abs(tfPnl)}
                decimals={2}
                prefix={`${tfPnlSign}$`}
              />
              <CountUpStats
                value={tfPct}
                decimals={2}
                prefix={tfPnlSign}
                suffix="%"
                style={{ ...S.trendSub, color: tfPnlColor, fontWeight: 500 }}
              />
              <span style={S.trendSub}>
                ({timeframeLabelMap[timeframe]})
              </span>
            </div>
          </div>

          <div style={{ width: '100%', height: 212, position: 'relative' }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={filteredHistory} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="equityGlow" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="var(--color-accent)" stopOpacity={0.25}/>
                    <stop offset="95%" stopColor="var(--color-accent)" stopOpacity={0.0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" vertical={false} />
                <XAxis 
                  dataKey="timeMs" 
                  type="number"
                  scale="time"
                  domain={getChartDomain()}
                  tickFormatter={ts => {
                    const d = new Date(ts);
                    return timeframe === '1D' 
                      ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                      : d.toLocaleDateString([], { month: 'short', day: 'numeric' });
                  }}
                  tick={{ fill: '#4b4b4b', fontSize: 10 }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis 
                  domain={['dataMin - 0.5', 'dataMax + 0.5']}
                  tickFormatter={v => `$${v.toFixed(0)}`}
                  tick={{ fill: '#4b4b4b', fontSize: 10 }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip content={<CustomEquityTooltip />} />
                <Area 
                  type="monotone" 
                  dataKey="balance" 
                  stroke="var(--color-accent)" 
                  strokeWidth={2.5}
                  fillOpacity={1} 
                  fill="url(#equityGlow)" 
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', flex: 1 }}>
          <div style={{ ...S.metricsArea, marginBottom: 8 }}>
            <span style={S.label}>Performance Edge</span>
            <div style={{ fontSize: 18, fontWeight: 600, fontFamily: 'var(--font-heading)', color: 'var(--color-text-primary)' }}>
              Asset Rolling Edge
            </div>
          </div>

          <div style={{ width: '100%', height: 212 }}>
            {wrData.length < 5 ? (
              <div style={{ color: 'var(--color-text-muted)', fontSize: 12, textAlign: 'center', padding: '60px 0' }}>
                Building win rate data — need 5+ trades per asset
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={wrData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" vertical={false} />
                  <XAxis dataKey="index" tick={{ fill: '#4b4b4b', fontSize: 10 }} axisLine={false} tickLine={false} />
                  <YAxis
                    domain={[0, 1]}
                    tickFormatter={v => `${(v * 100).toFixed(0)}%`}
                    tick={{ fill: '#4b4b4b', fontSize: 10 }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <Tooltip
                    formatter={(v, name) => [`${(v * 100).toFixed(1)}%`, name]}
                    contentStyle={{ 
                      background: 'rgba(245, 245, 240, 0.95)', 
                      border: '1px solid var(--color-midnight)', 
                      borderRadius: 8,
                      boxShadow: '0 8px 32px rgba(28, 28, 26, 0.08)'
                    }}
                    itemStyle={{ color: 'var(--color-text-primary)', fontSize: 12 }}
                    labelStyle={{ color: 'var(--color-text-secondary)', fontSize: 10 }}
                  />
                  <Legend wrapperStyle={{ fontSize: 10, color: '#999', paddingTop: 10 }} iconSize={8} />

                  <ReferenceLine y={0.62} stroke="rgba(43, 127, 255, 0.4)" strokeDasharray="4 3" label={{ value: 'Edge', fill: '#2b7fff', fontSize: 8, position: 'insideTopLeft' }} />
                  <ReferenceLine y={0.52} stroke="rgba(0, 212, 163, 0.4)" strokeDasharray="4 3" label={{ value: 'Recover', fill: '#00d4a3', fontSize: 8, position: 'insideTopLeft' }} />
                  <ReferenceLine y={0.45} stroke="rgba(255, 77, 77, 0.4)" strokeDasharray="4 3" label={{ value: 'Invert', fill: '#ff4d4d', fontSize: 8, position: 'insideTopLeft' }} />

                  {ASSETS.map(key => (
                    <Line
                      key={key}
                      type="monotone"
                      dataKey={key}
                      stroke={ASSET_COLORS[key]}
                      strokeWidth={2}
                      dot={false}
                      connectNulls
                      isAnimationActive={false}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
