import { useState, useEffect, useRef } from 'react';
import './App.css';
import GhostCursor from './components/common/GhostCursor';
import AssetCards     from './components/AssetCards';
import TradeFeed      from './components/TradeFeed';
import PortfolioPerformance from './components/PortfolioPerformance';
import SystemHealth   from './components/SystemHealth';
import RouteDiagnostics from './components/RouteDiagnostics';
import RegimeRadarHUD from './components/RegimeRadarHUD';
import AIInjectorHUD from './components/AIInjectorHUD';
import ArbitrageMatrix from './components/ArbitrageMatrix';
import Analytics from './components/Analytics';

export default function App() {
  const [activeTab, setActiveTab] = useState('overview'); // overview, engine, feeds
  const [state,     setState]     = useState({});
  const [positions, setPositions] = useState({ active: [], closed: [], summary: {} });
  const [candles,   setCandles]   = useState([]);
  const [diagnostics, setDiagnostics] = useState({
    latency_history: [],
    slippage_history: [],
    asymmetric_fills: 0,
    circuit_breaker_active: false,
    avg_latency_ms: 0,
    avg_slippage_cents: 0
  });
  const [uptime, setUptime] = useState('00:00:00');
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'dark');
  const [isHovered, setIsHovered] = useState(false);
  const esRef = useRef(null);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  }, [theme]);

  const isVisuallyExpanded = isHovered;

  // Centralized ticking uptime clock driven by runtime start_time and backend running status
  useEffect(() => {
    if (!state.runtime?.start_time || !state.running) {
      setUptime('00:00:00');
      return;
    }
    const tick = () => {
      const start = new Date(state.runtime.start_time);
      const diffMs = new Date() - start;
      if (diffMs <= 0) {
        setUptime('00:00:00');
        return;
      }
      const diffSecs = Math.floor(diffMs / 1000);
      const days = Math.floor(diffSecs / 86400);
      const hrs = Math.floor((diffSecs % 86400) / 3600);
      const mins = Math.floor((diffSecs % 3600) / 60);
      const secs = diffSecs % 60;
      
      let uptimeStr = '';
      if (days > 0) uptimeStr += `${days}d `;
      uptimeStr += `${hrs.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
      setUptime(uptimeStr);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [state.runtime?.start_time, state.running]);

  // Polling fallback for health and positions endpoint to ensure frontend resilience
  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch('/api/health');
        const d = await r.json();
        setState(d);
      } catch { /* offline */ }
      
      try {
        const r = await fetch('/api/positions');
        const d = await r.json();
        setPositions(d);
      } catch { /* offline */ }
    };
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, []);

  // SSE stream for live position + candle events
  useEffect(() => {
    const es = new EventSource('/api/health/stream');
    esRef.current = es;
    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data);
        if (event.type === 'position_update') setPositions(event.payload);
        if (event.type === 'balance_update')  setState(s => ({ ...s, ...event.payload }));
        if (event.type === 'candle_boundary') setCandles(event.payload);
        if (event.type === 'diagnostics_update') setDiagnostics(event.payload);
      } catch { /* ignore malformed */ }
    };
    return () => es.close();
  }, []);

  // background particle positions
  const bgSymbols = [
    { text: '{ }', top: '10%', left: '5%' },
    { text: '</>', top: '25%', left: '80%' },
    { text: '∞', top: '40%', left: '15%' },
    { text: '⊕', top: '75%', left: '7%' },
    { text: '[ ]', top: '85%', left: '85%' },
    { text: '→', top: '55%', left: '92%' },
    { text: 'ML', top: '70%', left: '75%' },
  ];

  return (
    <div className="dashboard-container relative overflow-x-hidden min-h-screen">
      <GhostCursor />
      
      {/* Background drifting symbols */}
      {bgSymbols.map((sym, idx) => (
        <span 
          key={idx} 
          className="floating-bg-particle select-none hidden md:block"
          style={{ 
            top: sym.top, 
            left: sym.left, 
            fontSize: '28px',
            animationDelay: `${idx * -4}s`,
            animationDuration: `${30 + idx * 5}s`
          }}
        >
          {sym.text}
        </span>
      ))}

      {/* LEFT PANE: FutureDesk Navigation Sidebar */}
      <aside 
        className={`sidebar ${!isVisuallyExpanded ? 'sidebar-collapsed' : ''}`}
        onMouseLeave={() => setIsHovered(false)}
      >
        {/* Sleek Vertical Center Opaque Pull-Handle */}
        {!isVisuallyExpanded && (
          <div 
            onMouseEnter={() => setIsHovered(true)}
            style={{
              position: 'absolute',
              right: '-1px',
              top: '50%',
              transform: 'translateY(-50%)',
              width: '12px',
              height: '90px',
              background: 'var(--color-accent)',
              borderRadius: '6px 0 0 6px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              zIndex: 110,
              opacity: 0.6,
              boxShadow: '0 0 10px var(--color-accent)',
              transition: 'all 200ms ease'
            }}
            title="Hover to Expand"
          >
            <span style={{ fontSize: '7px', color: 'var(--color-snow)', fontWeight: '900' }}>▶</span>
          </div>
        )}

        <div className="sidebar-brand" style={{ textAlign: isVisuallyExpanded ? 'left' : 'center', paddingLeft: isVisuallyExpanded ? '12px' : '0' }}>
          <span style={{ fontFamily: 'var(--font-display)', fontWeight: 900, fontSize: isVisuallyExpanded ? '32px' : '26px', letterSpacing: '-0.05em', color: 'var(--color-accent)', userSelect: 'none', lineHeight: 1 }}>
            {isVisuallyExpanded ? 'ZiSi.' : 'Z.'}
          </span>
          {isVisuallyExpanded && (
            <div style={{ fontSize: '11px', color: 'var(--color-iron)', letterSpacing: '0.03em', fontFamily: 'var(--font-primary)', fontWeight: '500', textTransform: 'none', marginTop: '4px' }}>
              intuitive investing.
            </div>
          )}
        </div>

        <nav className="sidebar-nav">
          <button 
            onClick={() => setActiveTab('overview')}
            className={`nav-item ${activeTab === 'overview' ? 'nav-item-active nav-active-glow' : ''}`}
            style={{ border: 'none', textAlign: isVisuallyExpanded ? 'left' : 'center', justifyContent: isVisuallyExpanded ? 'flex-start' : 'center', width: '100%', padding: isVisuallyExpanded ? '10px 14px' : '12px 0' }}
            title="Overview"
          >
            <svg style={{ width: '16px', height: '16px' }} fill="none" stroke="currentColor" strokeWidth={activeTab === 'overview' ? 2.5 : 1.8} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z" />
            </svg>
            {isVisuallyExpanded && <span style={{ marginLeft: '10px' }}>Overview</span>}
          </button>

          <button 
            onClick={() => setActiveTab('analytics')}
            className={`nav-item ${activeTab === 'analytics' ? 'nav-item-active nav-active-glow' : ''}`}
            style={{ border: 'none', textAlign: isVisuallyExpanded ? 'left' : 'center', justifyContent: isVisuallyExpanded ? 'flex-start' : 'center', width: '100%', padding: isVisuallyExpanded ? '10px 14px' : '12px 0' }}
            title="Analytics"
          >
            <svg style={{ width: '16px', height: '16px' }} fill="none" stroke="currentColor" strokeWidth={activeTab === 'analytics' ? 2.5 : 1.8} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v5.25c0 .621-.504 1.125-1.125 1.125h-2.25A1.125 1.125 0 0 1 3 18.375v-5.25ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v9.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125v-9.75ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v14.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z" />
            </svg>
            {isVisuallyExpanded && <span style={{ marginLeft: '10px' }}>Analytics</span>}
          </button>

          {/* Theme Toggle Button */}
          <button
            onClick={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
            className="nav-item ThemeToggle"
            style={{
              border: 'none',
              background: 'transparent',
              display: 'flex',
              alignItems: 'center',
              justifyContent: isVisuallyExpanded ? 'flex-start' : 'center',
              gap: '12px',
              padding: isVisuallyExpanded ? '10px 14px' : '12px 0',
              width: '100%',
              cursor: 'pointer',
              color: 'var(--color-iron)',
              transition: 'all 180ms cubic-bezier(0.16, 1, 0.3, 1)',
              marginTop: 'auto'
            }}
            title={`Switch to ${theme === 'dark' ? 'Light' : 'Dark'} Mode`}
          >
            {theme === 'dark' ? (
              <svg style={{ width: '16px', height: '16px', color: 'var(--color-accent)' }} fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v2.25m0 13.5V21M4.22 4.22l1.58 1.58m12.42 12.42l1.58 1.58M3 12h2.25m13.5 0H21M4.22 19.78l1.58-1.58M17.62 6.38l1.58-1.58M12 7.5a4.5 4.5 0 110 9 4.5 4.5 0 010-9z" />
              </svg>
            ) : (
              <svg style={{ width: '16px', height: '16px', color: 'var(--color-accent)' }} fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M21.752 15.002A9.718 9.718 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 009.002-5.998z" />
              </svg>
            )}
            {isVisuallyExpanded && (
              <span style={{ fontSize: '13px', fontWeight: '500' }}>
                {theme === 'dark' ? 'Light Mode' : 'Dark Mode'}
              </span>
            )}
          </button>
        </nav>

        {/* Sidebar Status Info */}
        <div style={{ marginTop: '20px', borderTop: '1px solid var(--color-border)', paddingTop: '20px', paddingLeft: isVisuallyExpanded ? '8px' : '0', display: 'flex', flexDirection: 'column', alignItems: isVisuallyExpanded ? 'flex-start' : 'center' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: isVisuallyExpanded ? '8px' : '0' }}>
            <span 
              className={`w-2.5 h-2.5 rounded-full ${state.running ? 'alert-pulse' : ''}`} 
              style={{ 
                width: '10px', 
                height: '10px', 
                borderRadius: '99px', 
                backgroundColor: state.running ? '#16a34a' : '#dc2626',
                display: 'inline-block' 
              }} 
            />
            {isVisuallyExpanded && (
              <span style={{ fontSize: '11px', fontWeight: '700', textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--color-obsidian)', fontFamily: 'sans-serif' }}>
                {state.running ? 'System Live' : 'System Offline'}
              </span>
            )}
          </div>
          {isVisuallyExpanded && (
            <div style={{ fontSize: '12px', color: 'var(--color-iron)', fontWeight: '500' }}>
              Uptime: <span style={{ fontFamily: 'monospace', color: 'var(--color-obsidian)', fontWeight: '600' }}>{uptime}</span>
            </div>
          )}
        </div>
      </aside>

      {/* RIGHT PANE: Main Content Canvas */}
      <main className="main-canvas page-fade-enter canvas-collapsed" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        {activeTab === 'overview' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
            <AssetCards positions={positions} candles={candles} state={state} />
            
            {/* Row 2: Performance + Health */}
            <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: '20px' }}>
              <PortfolioPerformance positions={positions} state={state} />
              <SystemHealth state={state} positions={positions} candles={candles} uptime={uptime} />
            </div>

            {/* Row 3: Trade Ledger */}
            <TradeFeed positions={positions} />
          </div>
        )}

        {activeTab === 'analytics' && (
          <Analytics state={state} />
        )}


      </main>
    </div>
  );
}
