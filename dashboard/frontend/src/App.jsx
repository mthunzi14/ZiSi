import { useState, useEffect, useRef } from 'react';
import './App.css';
import CommandCentre from './components/CommandCentre';
import AssetCards    from './components/AssetCards';
import TradeFeed     from './components/TradeFeed';
import WinRateChart  from './components/WinRateChart';
import PositionMonitor from './components/PositionMonitor';
import SystemHealth  from './components/SystemHealth';

export default function App() {
  const [state,     setState]     = useState({});
  const [positions, setPositions] = useState({ active: [], summary: {} });
  const [trades,    setTrades]    = useState([]);
  const [candles,   setCandles]   = useState([]);
  const esRef = useRef(null);

  // Polling fallback for health data
  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch('/api/health');
        const d = await r.json();
        setState(d);
      } catch { /* offline */ }
    };
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, []);

  // SSE stream for live events
  useEffect(() => {
    const es = new EventSource('/api/health/stream');
    esRef.current = es;

    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data);
        if (event.type === 'position_update')  setPositions(event.payload);
        if (event.type === 'balance_update')   setState(s => ({ ...s, ...event.payload }));
        if (event.type === 'candle_boundary')  setCandles(event.payload);
        if (event.type === 'trade_executed' || event.type === 'trade_resolved') {
          setTrades(t => [event.payload, ...t].slice(0, 50));
        }
      } catch { /* ignore malformed */ }
    };

    return () => es.close();
  }, []);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh', background: 'var(--color-bg-base)' }}>
      <CommandCentre state={state} positions={positions} />

      <div style={{ padding: 'var(--spacing-16)', flex: 1, display: 'flex', flexDirection: 'column', gap: 'var(--spacing-16)' }}>

        {/* Row 1: Asset Cards */}
        <AssetCards positions={positions} candles={candles} />

        {/* Row 2: Trade Feed + Win Rate Chart */}
        <div style={{ display: 'grid', gridTemplateColumns: '40% 1fr', gap: 'var(--spacing-16)' }}>
          <TradeFeed trades={trades} positions={positions} />
          <WinRateChart trades={trades} />
        </div>

        {/* Row 3: Position Monitor + System Health */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--spacing-16)' }}>
          <PositionMonitor positions={positions} candles={candles} />
          <SystemHealth state={state} positions={positions} candles={candles} />
        </div>
      </div>
    </div>
  );
}
