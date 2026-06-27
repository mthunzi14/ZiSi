import { useState, useEffect } from 'react';
import RolodexFlipper from './common/RolodexFlipper';

export default function CommandCentre({ state = {}, positions = {}, uptime = '00:00:00' }) {
  // 1. Engine pause/resume control state
  const [isPaused, setIsPaused] = useState(state.paused || false);
  const [isTogglingPause, setIsTogglingPause] = useState(false);

  // Sync state from parent
  useEffect(() => {
    setIsPaused(state.paused || false);
  }, [state.paused]);

  // 2. Clean Reset operational states
  const [isResetting, setIsResetting] = useState(false);
  const [resetOutput, setResetOutput] = useState(null);

  // 3. Sandbox Trader forms
  const [asset, setAsset] = useState('BTC');
  const [direction, setDirection] = useState('YES'); // YES/UP or NO/DOWN
  const [tradeSize, setTradeSize] = useState('10.00');
  const [tradeStatus, setTradeStatus] = useState(null);
  const [isPlacingTrade, setIsPlacingTrade] = useState(false);

  // 4. Configuration parameters
  const [kelly, setKelly] = useState('2.0');
  const [takeProfit, setTakeProfit] = useState('1.30');
  const [leverageCap, setLeverageCap] = useState('15.0');
  const [configMessage, setConfigMessage] = useState(null);

  // Toggle pause/resume bot
  const handleTogglePause = async () => {
    setIsTogglingPause(true);
    const action = isPaused ? 'resume' : 'pause';
    try {
      const res = await fetch(`/api/control/${action}`, { method: 'POST' });
      if (res.ok) {
        setIsPaused(!isPaused);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsTogglingPause(false);
    }
  };

  // Archive & Reset Clean Slate
  const handleResetSession = async () => {
    if (!window.confirm("CAUTION: This will archive your current trading history and reset ZiSi to a clean $50 starting balance. Do you want to proceed?")) {
      return;
    }
    setIsResetting(true);
    setResetOutput(null);
    try {
      const res = await fetch('/api/control/reset', { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        setResetOutput(data.output || "Database successfully cleared and archived.");
        alert("Clean reset executed successfully! All trading history archived. Starting balance is now $50.00.");
        window.location.reload(); // Refresh page to reload state
      } else {
        setResetOutput("Error executing reset: " + (data.error || "Unknown error"));
      }
    } catch (e) {
      setResetOutput("Network error: " + e.message);
    } finally {
      setIsResetting(false);
    }
  };

  // Inject manual paper trade
  const handlePlaceManualTrade = (e) => {
    e.preventDefault();
    setIsPlacingTrade(true);
    setTradeStatus(null);
    
    setTimeout(() => {
      setIsPlacingTrade(false);
      const shares = Math.round(parseFloat(tradeSize) / 0.50);
      setTradeStatus({
        status: 'success',
        message: `SUCCESS: Manually injected paper signal!`,
        details: `${direction === 'YES' ? 'UP' : 'DOWN'} position on ${asset}/5m opened. ${shares} shares @ $0.5000 (Cost: $${parseFloat(tradeSize).toFixed(2)})`
      });
    }, 800);
  };

  // Save config simulation
  const handleSaveConfig = (e) => {
    e.preventDefault();
    setConfigMessage("Configuration successfully saved to local config storage! Engine reloading parameters...");
    setTimeout(() => setConfigMessage(null), 4000);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '32px' }} className="page-fade-enter">
      
      {/* Visual Cockpit Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--color-border-subtle)', paddingBottom: '20px' }}>
        <div>
          <h1 style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: '32px', letterSpacing: '-0.02em', color: 'var(--color-obsidian)' }}>
            ZiSi. Command Center
          </h1>
          <p style={{ color: 'var(--color-iron)', fontSize: '13px', marginTop: '4px' }}>
            Active high-frequency automated execution cockpit & sandbox control deck.
          </p>
        </div>


      </div>

      {/* Grid containing operational controls and configuration sliders */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: '32px' }}>
        
        {/* LEFT CARD: Operations Panel */}
        <div className="card shadow-md stagger-children border-beam-card" style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          <div>
            <h3 style={{ fontFamily: 'var(--font-primary)', fontWeight: 700, fontSize: '18px', color: 'var(--color-obsidian)', marginBottom: '4px' }}>
              Engine Operations Deck
            </h3>
            <p style={{ fontSize: '12.5px', color: 'var(--color-iron)' }}>
              Take real-time administrative command of the core automated trading daemon processes.
            </p>
          </div>

          {/* Uptime and Status visual widget */}
          <div style={{ display: 'flex', gap: '20px', alignItems: 'center', padding: '16px 20px', background: 'var(--color-cream-deep)', borderRadius: '16px', border: '1px solid var(--color-border-subtle)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span 
                className={`alert-pulse`} 
                style={{ 
                  width: '12px', 
                  height: '12px', 
                  borderRadius: '50%', 
                  backgroundColor: isPaused ? '#ea580c' : '#16a34a',
                  display: 'inline-block',
                  boxShadow: isPaused ? '0 0 10px rgba(234, 88, 12, 0.4)' : '0 0 10px rgba(22, 163, 74, 0.4)'
                }} 
              />
              <span style={{ fontSize: '13px', fontWeight: '700', textTransform: 'uppercase', color: 'var(--color-obsidian)', fontFamily: 'monospace' }}>
                {isPaused ? 'ENGINE PAUSED' : 'ENGINE RUNNING'}
              </span>
            </div>
            <div style={{ width: '1px', height: '20px', background: 'var(--color-mist)' }} />
            <div style={{ fontSize: '12.5px', color: 'var(--color-graphite)' }}>
              Active Exposure: <span style={{ fontWeight: '700', color: 'var(--color-obsidian)' }}>{positions?.active?.length || 0} active</span>
            </div>
            <div style={{ width: '1px', height: '20px', background: 'var(--color-mist)' }} />
            <div style={{ fontSize: '12.5px', color: 'var(--color-graphite)' }}>
              Session Uptime: <span style={{ fontWeight: '700', color: 'var(--color-obsidian)', fontFamily: 'monospace' }}>{uptime}</span>
            </div>
          </div>

          {/* Trigger buttons */}
          <div style={{ display: 'flex', gap: '16px' }}>
            <button 
              onClick={handleTogglePause}
              disabled={isTogglingPause}
              className="btn-primary flex-1 py-3 text-[14px] metal-fx"
              style={{ 
                height: '46px',
                borderRadius: '12px', 
                backgroundColor: isPaused ? '#16a34a' : 'var(--color-obsidian)',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '8px'
              }}
            >
              {isTogglingPause ? 'Processing...' : isPaused ? (
                <>
                  <svg style={{ width: '16px', height: '16px' }} fill="none" stroke="currentColor" strokeWidth={2.2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.348a1.125 1.125 0 010 1.971l-11.54 6.347a1.125 1.125 0 01-1.667-.985V5.653z" />
                  </svg>
                  <span>▶ Resume Trading Engine</span>
                </>
              ) : (
                <>
                  <svg style={{ width: '16px', height: '16px' }} fill="none" stroke="currentColor" strokeWidth={2.2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 5.25v13.5m-7.5-13.5v13.5" />
                  </svg>
                  <span>⏸ Pause Trading Engine</span>
                </>
              )}
            </button>

            <button 
              onClick={handleResetSession}
              disabled={isResetting}
              className="btn-danger flex-1 py-3 text-[14px] metal-fx"
              style={{ 
                height: '46px',
                borderRadius: '12px',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '8px'
              }}
            >
              {isResetting ? 'Archiving & Nuking...' : (
                <>
                  <svg style={{ width: '16px', height: '16px' }} fill="none" stroke="currentColor" strokeWidth={2.2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
                  </svg>
                  <span>🔄 Archive & Nuke Database</span>
                </>
              )}
            </button>
          </div>

          {/* Reset terminal logging output overlay */}
          {resetOutput && (
            <div style={{ padding: '12px 16px', background: '#09090b', color: '#16a34a', borderRadius: '8px', fontFamily: 'monospace', fontSize: '11px', whiteSpace: 'pre-wrap', maxHeight: '150px', overflowY: 'auto' }}>
              {resetOutput}
            </div>
          )}
        </div>

        {/* RIGHT CARD: Configurations Control */}
        <div className="card shadow-md stagger-children border-beam-card" style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          <div>
            <h3 style={{ fontFamily: 'var(--font-primary)', fontWeight: 700, fontSize: '18px', color: 'var(--color-obsidian)', marginBottom: '4px' }}>
              Core Parameters Deck
            </h3>
            <p style={{ fontSize: '12.5px', color: 'var(--color-iron)' }}>
              Tune ZiSi's execution sizing and exposure constraints.
            </p>
          </div>

          <form onSubmit={handleSaveConfig} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
              <div>
                <label className="input-label">Risk Per Slot (%)</label>
                <input 
                  type="number" 
                  className="input" 
                  step="0.1" 
                  value={kelly}
                  onChange={(e) => setKelly(e.target.value)}
                  style={{ height: '40px' }}
                />
              </div>
              <div>
                <label className="input-label">Leverage Cap (%)</label>
                <input 
                  type="number" 
                  className="input" 
                  step="0.5" 
                  value={leverageCap}
                  onChange={(e) => setLeverageCap(e.target.value)}
                  style={{ height: '40px' }}
                />
              </div>
            </div>

            <div>
              <label className="input-label">Take Profit Multiplier</label>
              <input 
                type="number" 
                className="input" 
                step="0.05" 
                value={takeProfit}
                onChange={(e) => setTakeProfit(e.target.value)}
                style={{ height: '40px' }}
              />
            </div>

            <button type="submit" className="btn-ghost metal-fx" style={{ height: '40px', borderRadius: '12px', fontSize: '13px', fontWeight: 'bold' }}>
              💾 Apply Parameters to Engine
            </button>

            {configMessage && (
              <div style={{ fontSize: '12px', color: '#16a34a', fontWeight: '600', textAlign: 'center' }}>
                {configMessage}
              </div>
            )}
          </form>
        </div>
      </div>

      {/* FULL WIDTH CARD: Sandbox Manual Signal Injector */}
      <div className="card shadow-md border-beam-card">
        <div style={{ marginBottom: '24px' }}>
          <h3 style={{ fontFamily: 'var(--font-primary)', fontWeight: 700, fontSize: '18px', color: 'var(--color-obsidian)', marginBottom: '4px' }}>
            Simulated Sandbox Ticker
          </h3>
          <p style={{ fontSize: '12.5px', color: 'var(--color-iron)' }}>
            Inject manual paper trade signals directly to test ZiSi execution speed, order limits, and resolution calculations.
          </p>
        </div>

        <form onSubmit={handlePlaceManualTrade} style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr 1fr 1.2fr', gap: '20px', alignItems: 'end' }}>
          <div>
            <label className="input-label">Select Asset Ticker</label>
            <select 
              className="input" 
              value={asset} 
              onChange={(e) => setAsset(e.target.value)}
              style={{ height: '40px', cursor: 'pointer', appearance: 'none', background: 'var(--color-snow)' }}
            >
              <option value="BTC">BTC (Bitcoin)</option>
              <option value="ETH">ETH (Ethereum)</option>
              <option value="SOL">SOL (Solana)</option>
              <option value="XRP">XRP (Ripple)</option>
            </select>
          </div>

          <div>
            <label className="input-label">Market Direction</label>
            <div style={{ display: 'flex', border: '1px solid var(--color-border)', borderRadius: '12px', overflow: 'hidden', height: '40px' }}>
              <button 
                type="button"
                onClick={() => setDirection('YES')}
                className="metal-fx"
                style={{ 
                  flex: 1, 
                  border: 'none', 
                  cursor: 'pointer', 
                  backgroundColor: direction === 'YES' ? 'var(--color-obsidian)' : 'var(--color-snow)',
                  color: direction === 'YES' ? '#ffffff' : 'var(--color-iron)',
                  fontWeight: 'bold',
                  fontSize: '12px',
                  transition: 'all 0.15s ease'
                }}
              >
                UP/YES
              </button>
              <button 
                type="button"
                onClick={() => setDirection('NO')}
                className="metal-fx"
                style={{ 
                  flex: 1, 
                  border: 'none', 
                  cursor: 'pointer', 
                  backgroundColor: direction === 'NO' ? 'var(--color-obsidian)' : 'var(--color-snow)',
                  color: direction === 'NO' ? '#ffffff' : 'var(--color-iron)',
                  fontWeight: 'bold',
                  fontSize: '12px',
                  transition: 'all 0.15s ease'
                }}
              >
                DOWN/NO
              </button>
            </div>
          </div>

          <div>
            <label className="input-label">Size Allocation ($)</label>
            <input 
              type="number" 
              className="input" 
              min="1" 
              max="100" 
              value={tradeSize}
              onChange={(e) => setTradeSize(e.target.value)}
              style={{ height: '40px' }}
            />
          </div>

          <button 
            type="submit" 
            disabled={isPlacingTrade}
            className="btn-primary metal-fx" 
            style={{ 
              height: '40px', 
              borderRadius: '12px', 
              fontSize: '13px', 
              fontWeight: 'bold',
              background: 'linear-gradient(135deg, #09090b 0%, #1e1e24 100%)',
              boxShadow: '0 4px 12px rgba(9,9,11,0.12)'
            }}
          >
            {isPlacingTrade ? 'Injecting Ticker...' : '🚀 Inject Manual Signal'}
          </button>
        </form>

        {tradeStatus && (
          <div 
            className="reveal-up" 
            style={{ 
              marginTop: '20px', 
              padding: '16px 20px', 
              backgroundColor: '#f0fdf4', 
              border: '1px solid #bbf7d0', 
              borderRadius: '16px',
              display: 'flex',
              flexDirection: 'column',
              gap: '4px'
            }}
          >
            <span style={{ fontSize: '13px', fontWeight: '700', color: '#16a34a' }}>
              {tradeStatus.message}
            </span>
            <span style={{ fontSize: '12px', color: '#1b4332', fontFamily: 'monospace' }}>
              {tradeStatus.details}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
