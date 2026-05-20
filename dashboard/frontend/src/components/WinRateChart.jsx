// WinRateChart.jsx — rolling 40-window WR per asset + inversion event markers
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer, Legend,
} from 'recharts';

const ASSET_COLORS = {
  'BTC/5m':  '#2b7fff',
  'BTC/15m': '#426188',
  'ETH/5m':  '#00d4a3',
  'SOL/5m':  '#f5f5f5',
  'XRP/5m':  '#ff9500',
};

const ASSETS = ['BTC/5m', 'BTC/15m', 'ETH/5m', 'SOL/5m', 'XRP/5m'];

function buildWrSeries(trades) {
  // Group trades by asset/tf and compute rolling 40-window WR
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
        pt[key] = window.length >= 5 ? parseFloat((window.reduce((s,v) => s+v,0)/window.length).toFixed(3)) : null;
      }
    }
    points.push(pt);
  }
  return points;
}

export default function WinRateChart({ trades = [] }) {
  const data = buildWrSeries(trades);

  return (
    <div style={{
      background: 'var(--color-bg-surface)',
      borderRadius: 'var(--radius-cards)',
      border: '1px solid var(--color-midnight)',
      padding: 'var(--spacing-20)',
    }}>
      <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 500, fontSize: 16, marginBottom: 16 }}>
        Rolling Win Rate (40-window)
      </div>

      {data.length < 5 ? (
        <div style={{ color: 'var(--color-text-muted)', fontSize: 13, textAlign: 'center', padding: 48 }}>
          Building win rate data — need 5+ trades per asset
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={data} margin={{ top: 5, right: 16, left: 0, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
            <XAxis dataKey="index" tick={{ fill: '#6b6b6b', fontSize: 11 }} />
            <YAxis
              domain={[0, 1]}
              tickFormatter={v => `${(v * 100).toFixed(0)}%`}
              tick={{ fill: '#6b6b6b', fontSize: 11 }}
            />
            <Tooltip
              formatter={(v, name) => [`${(v * 100).toFixed(1)}%`, name]}
              contentStyle={{ background: '#1a1a1a', border: '1px solid #333', borderRadius: 8 }}
              labelStyle={{ color: '#999' }}
            />
            <Legend wrapperStyle={{ fontSize: 12, color: '#999' }} />

            {/* Reference lines */}
            <ReferenceLine y={0.62} stroke="#2b7fff"  strokeDasharray="4 3" label={{ value: 'Edge', fill: '#2b7fff', fontSize: 10 }} />
            <ReferenceLine y={0.52} stroke="#00d4a3"  strokeDasharray="4 3" label={{ value: 'Recover', fill: '#00d4a3', fontSize: 10 }} />
            <ReferenceLine y={0.45} stroke="#ff4d4d"  strokeDasharray="4 3" label={{ value: 'Invert', fill: '#ff4d4d', fontSize: 10 }} />

            {ASSETS.map(key => (
              <Line
                key={key}
                type="monotone"
                dataKey={key}
                stroke={ASSET_COLORS[key]}
                strokeWidth={2}
                dot={false}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
