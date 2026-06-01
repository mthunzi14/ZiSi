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
import Settings from './components/Settings';


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

  const [isPrivate, setIsPrivate] = useState(false);

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
        setState(s => ({ ...s, ...d }));
      } catch { /* offline */ }

      try {
        const r = await fetch('/api/positions');
        const d = await r.json();
        setPositions(d);
      } catch { /* offline */ }
    };
    poll();
    const id = setInterval(poll, 15000);
    return () => clearInterval(id);
  }, []);

  // SSE stream for live position + candle events
  useEffect(() => {
    const es = new EventSource('/api/health/stream');
    esRef.current = es;
    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data);
        if (event.type === 'position_update')    setPositions(event.payload);
        if (event.type === 'positions_snapshot') setPositions(p => ({ ...p, ...event.payload }));
        if (event.type === 'balance_update')     setState(s => ({ ...s, ...event.payload }));
        if (event.type === 'candle_boundary')    setCandles(event.payload);
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
        {/* Transparent Hover Detection Bounding Box + Centered Circular Gold Pull-Handle */}
        {!isVisuallyExpanded && (
          <div 
            onMouseEnter={() => setIsHovered(true)}
            style={{
              position: 'absolute',
              left: 0,
              right: 0,
              top: 'calc(50% - 70px)',
              height: '140px',
              zIndex: 110,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center'
            }}
            title="Hover to Expand"
          >
            <div 
              style={{
                width: '26px',
                height: '26px',
                borderRadius: '50%',
                background: 'rgba(197, 155, 39, 0.18)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                opacity: 0.55,
                boxShadow: '0 0 6px rgba(197, 155, 39, 0.3)',
                border: '1px solid rgba(197, 155, 39, 0.35)',
                transition: 'all 200ms ease'
              }}
            >
              <span style={{ fontSize: '8px', color: 'var(--color-accent)', fontWeight: '700', marginLeft: '1px' }}>▶</span>
            </div>
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
            style={{ border: 'none', textAlign: isVisuallyExpanded ? 'left' : 'center', justifyContent: isVisuallyExpanded ? 'flex-start' : 'center', width: '100%', padding: isVisuallyExpanded ? '10px 14px' : '12px 0', background: !isVisuallyExpanded && activeTab !== 'overview' ? 'rgba(197,155,39,0.08)' : undefined, borderRadius: '10px' }}
            title="Overview"
          >
            <svg style={{ width: '16px', height: '16px', opacity: !isVisuallyExpanded ? 0.7 : 1 }} fill="none" stroke="currentColor" strokeWidth={activeTab === 'overview' ? 2.5 : 1.8} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z" />
            </svg>
            {isVisuallyExpanded && <span style={{ marginLeft: '10px' }}>Overview</span>}
          </button>

          <button 
            onClick={() => setActiveTab('analytics')}
            className={`nav-item ${activeTab === 'analytics' ? 'nav-item-active nav-active-glow' : ''}`}
            style={{ border: 'none', textAlign: isVisuallyExpanded ? 'left' : 'center', justifyContent: isVisuallyExpanded ? 'flex-start' : 'center', width: '100%', padding: isVisuallyExpanded ? '10px 14px' : '12px 0', background: !isVisuallyExpanded && activeTab !== 'analytics' ? 'rgba(197,155,39,0.08)' : undefined, borderRadius: '10px' }}
            title="Analytics"
          >
            <svg style={{ width: '16px', height: '16px', opacity: !isVisuallyExpanded ? 0.7 : 1 }} fill="none" stroke="currentColor" strokeWidth={activeTab === 'analytics' ? 2.5 : 1.8} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v5.25c0 .621-.504 1.125-1.125 1.125h-2.25A1.125 1.125 0 0 1 3 18.375v-5.25ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v9.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125v-9.75ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v14.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z" />
            </svg>
            {isVisuallyExpanded && <span style={{ marginLeft: '10px' }}>Analytics</span>}
          </button>

          <button 
            onClick={() => setActiveTab('settings')}
            className={`nav-item ${activeTab === 'settings' ? 'nav-item-active nav-active-glow' : ''}`}
            style={{ border: 'none', textAlign: isVisuallyExpanded ? 'left' : 'center', justifyContent: isVisuallyExpanded ? 'flex-start' : 'center', width: '100%', padding: isVisuallyExpanded ? '10px 14px' : '12px 0', background: !isVisuallyExpanded && activeTab !== 'settings' ? 'rgba(197,155,39,0.08)' : undefined, borderRadius: '10px' }}
            title="Settings"
          >
            <svg style={{ width: '16px', height: '16px', opacity: !isVisuallyExpanded ? 0.7 : 1 }} fill="none" stroke="currentColor" strokeWidth={activeTab === 'settings' ? 2.5 : 1.8} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.324.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.43l-1.003.828c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.02-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.43l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 0 1 0-.255c.007-.378-.138-.75-.43-.991l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.087.22-.128.332-.183.582-.495.645-.869l.214-1.28Z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
            </svg>
            {isVisuallyExpanded && <span style={{ marginLeft: '10px' }}>Settings</span>}
          </button>

          {/* Manual Privacy Screen Lock */}
          <button
            onClick={() => setIsPrivate(p => !p)}
            className={`nav-item ${isPrivate ? 'nav-item-active nav-active-glow' : ''}`}
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
            title={isPrivate ? "Unlock Dashboard" : "Lock Dashboard"}
          >
            {isPrivate ? (
              // Eye Slash SVG (represents hidden / privacy mode active)
              <svg style={{ width: '16px', height: '16px', color: isPrivate ? '#0c0c0e' : 'var(--color-accent)' }} fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" />
              </svg>
            ) : (
              // Eye SVG (represents visible / privacy mode inactive)
              <svg style={{ width: '16px', height: '16px', color: isPrivate ? '#0c0c0e' : 'var(--color-accent)' }} fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
            )}
            {isVisuallyExpanded && (
              <span style={{ fontSize: '13px', fontWeight: '500' }}>
                {isPrivate ? 'Unlock view' : 'Lock view'}
              </span>
            )}
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
              marginTop: '8px'
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
          <div className="page-fade-enter" style={{ display: 'flex', flexDirection: 'column', gap: '20px', position: 'relative' }}>
            {/* Smooth Motion Privacy Screen Overlay */}
            <div className={`privacy-overlay ${isPrivate ? 'privacy-overlay-active' : ''}`}>
              <div className="privacy-card">
                <div className="premium-lock-container" onClick={() => setIsPrivate(false)}>
                  <div className="rotating-gold-border"></div>
                  <div className="premium-lock-body">
                    <svg className="premium-lock-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                      <rect x="5" y="11" width="14" height="11" rx="2" ry="2" />
                      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
                      <circle cx="12" cy="16" r="1.5" />
                    </svg>
                  </div>
                  {/* Opaque moving symbols around the lock */}
                  <div className="orbiting-symbol symbol-1">ZiSi</div>
                  <div className="orbiting-symbol symbol-2">%</div>
                  <div className="orbiting-symbol symbol-3">$</div>
                  <div className="orbiting-symbol symbol-4">⊕</div>
                  <div className="orbiting-symbol symbol-5">ML</div>
                  <div className="orbiting-symbol symbol-6">RSI</div>
                </div>
                <h2 className="privacy-title">ZiSi QUANTITATIVE WORKSTATION</h2>
                <p className="privacy-subtitle">Financial Overview Protected</p>
                <p className="privacy-instructions">Click gold lock or sidebar icon to restore view</p>
              </div>
            </div>

            {/* Overview Stats Strip */}
            <div className="glass-panel reveal-up" style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(4, 1fr)',
              width: '100%',
              padding: 0,
              marginBottom: '4px',
              overflow: 'hidden',
              borderTop: '2px solid rgba(197,155,39,0.65)',
            }}>
              {/* Stat 1: Balance */}
              {(() => {
                const bal = parseFloat(state.balance ?? 0);
                const pnl = parseFloat(state.pnl ?? 0);
                const start = parseFloat(state.starting_balance ?? (bal - pnl)) || 1;
                const pnlPct = start > 0 ? (pnl / start) * 100 : 0;
                const pnlPositive = pnl >= 0;

                const closed = positions.closed || [];
                const wins = closed.filter(p => parseFloat(p.realized_pnl ?? 0) > 0).length;
                const totalClosed = closed.length || state.trades_executed || 0;
                const wr = totalClosed > 0 ? (wins / totalClosed) * 100 : 0;
                const active = positions.active?.length || 0;

                const cellStyle = (borderRight = true) => ({
                  display: 'flex',
                  flexDirection: 'column',
                  padding: '22px 28px',
                  gap: '4px',
                  borderRight: borderRight ? '1px solid var(--color-card-border)' : 'none',
                });
                const labelStyle = {
                  fontSize: '9.5px',
                  fontFamily: 'var(--font-primary)',
                  color: 'var(--color-text-muted)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.13em',
                  fontWeight: 700,
                };
                const numStyle = (color = 'var(--color-obsidian)') => ({
                  fontSize: '28px',
                  fontWeight: 800,
                  color,
                  fontFamily: 'var(--font-display)',
                  lineHeight: 1.05,
                  letterSpacing: '-0.02em',
                });
                const subStyle = {
                  fontSize: '10.5px',
                  color: 'var(--color-text-muted)',
                  fontWeight: 500,
                  marginTop: '1px',
                };

                return (
                  <>
                    <div style={cellStyle()}>
                      <span style={labelStyle}>Balance</span>
                      <span style={numStyle()}>${bal.toFixed(2)}</span>
                      <span style={subStyle}>Paper account</span>
                    </div>

                    <div style={cellStyle()}>
                      <span style={labelStyle}>Net P&amp;L</span>
                      <div style={{ display: 'flex', alignItems: 'baseline', gap: '9px' }}>
                        <span style={numStyle(pnlPositive ? 'var(--color-profit)' : 'var(--color-loss)')}>
                          {pnlPositive ? '+' : ''}{pnl.toFixed(2)}
                        </span>
                        <span style={{
                          fontSize: '11px', fontWeight: 700,
                          color: pnlPositive ? 'var(--color-profit)' : 'var(--color-loss)',
                          background: pnlPositive ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)',
                          padding: '2px 7px', borderRadius: '5px',
                        }}>
                          {pnlPositive ? '+' : ''}{pnlPct.toFixed(1)}%
                        </span>
                      </div>
                      <span style={subStyle}>vs starting balance</span>
                    </div>

                    <div style={cellStyle()}>
                      <span style={labelStyle}>Win Rate</span>
                      <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px' }}>
                        <span style={numStyle(wr >= 55 ? 'var(--color-profit)' : wr >= 45 ? 'var(--color-obsidian)' : 'var(--color-loss)')}>
                          {wr.toFixed(1)}%
                        </span>
                      </div>
                      <span style={subStyle}>{wins}W · {totalClosed - wins}L · {totalClosed} total</span>
                    </div>

                    <div style={cellStyle(false)}>
                      <span style={labelStyle}>Positions</span>
                      <div style={{ display: 'flex', alignItems: 'baseline', gap: '6px' }}>
                        <span style={numStyle()}>{active}</span>
                        <span style={{ fontSize: '14px', fontWeight: 600, color: 'var(--color-text-muted)', alignSelf: 'center' }}>open</span>
                      </div>
                      <span style={subStyle}>{totalClosed} closed lifetime</span>
                    </div>
                  </>
                );
              })()}
            </div>

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

        {activeTab === 'settings' && (
          <Settings />
        )}

      </main>
    </div>
  );
}
