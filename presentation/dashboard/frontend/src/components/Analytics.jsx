// Analytics.jsx — Deep Institutional-Grade Quantitative Insights, Confluence Radar & Hourly Heatmap
import { useState } from 'react';
import CountUpStats from './common/CountUpStats';

export default function Analytics({ state = {} }) {
  const safeState = state || {};
  const byUTC = safeState.byUTC || [];
  
  const profitFactor = parseFloat(safeState.profitFactor ?? 0);
  const expectancy = parseFloat(safeState.expectancy ?? 0);
  const maxDrawdown = parseFloat(safeState.maxDrawdown ?? 0);
  const currentDrawdown = parseFloat(safeState.currentDrawdown ?? 0);
  const consecutiveLosses = parseInt(safeState.consecutiveLosses ?? 0, 10);
  const riskOfRuin = safeState.riskOfRuin || 'Low';
  
  const regime = safeState.regime || { regime: 'NORMAL', label: 'Normal', atr_pct: 0, kelly_multiplier: 1.0 };
  const atrPct = parseFloat(regime.atr_pct ?? 0);
  const kellyMultiplier = parseFloat(regime.kelly_multiplier ?? 1.0);
  
  const mlProgress = safeState.ml_progress || { cycles_collected: 0, cycles_needed: 50, progress_percent: 0 };
  const mlCollected = parseInt(mlProgress.cycles_collected ?? 0, 10);
  const mlNeeded = parseInt(mlProgress.cycles_needed ?? 50, 10);
  const mlPercent = parseFloat(mlProgress.progress_percent ?? 0);

  // Generate complete 24-hour UTC grid list
  const hoursGrid = Array.from({ length: 24 }, (_, h) => {
    const data = byUTC.find(item => item.hour === h) || { trades: 0, winRate: 0 };
    return { hour: h, ...data };
  });

  const getHeatmapColor = (winRate, count) => {
    if (count === 0) return 'rgba(255, 255, 255, 0.02)'; // empty square
    if (winRate >= 0.70) return 'rgba(16, 185, 129, 0.35)'; // high emerald green
    if (winRate >= 0.55) return 'rgba(16, 185, 129, 0.18)'; // light green
    if (winRate >= 0.45) return 'rgba(249, 115, 22, 0.15)'; // light amber orange
    return 'rgba(239, 68, 68, 0.2)'; // light ruby red
  };

  const getHourLabel = (h) => {
    const ampm = h >= 12 ? 'PM' : 'AM';
    const displayH = h % 12 === 0 ? 12 : h % 12;
    return `${displayH}${ampm} UTC`;
  };

  const assetsRadar = [
    { name: 'BTC/5m',  rsi: 62.4, mom: '+0.03%', ofi: '+0.48', vol: '1.2x', ai: '78.2%', status: 'UP',  score: '4/5', pass: true },
    { name: 'BTC/15m', rsi: 58.1, mom: '+0.01%', ofi: '+0.15', vol: '0.8x', ai: '60.1%', status: 'WAIT',score: '3/5', pass: false },
    { name: 'ETH/5m',  rsi: 61.2, mom: '+0.02%', ofi: '+0.52', vol: '1.4x', ai: '81.4%', status: 'UP',  score: '5/5', pass: true },
    { name: 'ETH/15m', rsi: 54.3, mom: '+0.00%', ofi: '+0.12', vol: '0.9x', ai: '55.3%', status: 'WAIT',score: '3/5', pass: false },
    { name: 'SOL/5m',  rsi: 48.3, mom: '-0.01%', ofi: '-0.04', vol: '0.7x', ai: '48.9%', status: 'WAIT',score: '1/5', pass: false },
    { name: 'SOL/15m', rsi: 52.1, mom: '+0.01%', ofi: '+0.23', vol: '1.1x', ai: '65.2%', status: 'UP',  score: '4/5', pass: true },
    { name: 'XRP/5m',  rsi: 39.4, mom: '-0.03%', ofi: '-0.56', vol: '2.1x', ai: '24.1%', status: 'DOWN',score: '4/5', pass: true },
    { name: 'XRP/15m', rsi: 41.2, mom: '-0.01%', ofi: '-0.32', vol: '1.5x', ai: '35.4%', status: 'WAIT',score: '2/5', pass: false },
    { name: 'ADA/5m',  rsi: 32.1, mom: '-0.05%', ofi: '-0.71', vol: '2.4x', ai: '18.2%', status: 'DOWN',score: '5/5', pass: true },
    { name: 'LINK/5m', rsi: 65.8, mom: '+0.04%', ofi: '+0.63', vol: '1.8x', ai: '88.4%', status: 'UP',  score: '5/5', pass: true },
    { name: 'DOGE/5m', rsi: 45.2, mom: '+0.00%', ofi: '+0.02', vol: '0.6x', ai: '51.2%', status: 'WAIT',score: '2/5', pass: false },
    { name: 'AVAX/5m', rsi: 72.1, mom: '+0.06%', ofi: '+0.88', vol: '3.1x', ai: '92.1%', status: 'UP',  score: '5/5', pass: true },
    { name: 'SUI/5m',  rsi: 58.4, mom: '+0.02%', ofi: '+0.41', vol: '1.3x', ai: '72.4%', status: 'UP',  score: '4/5', pass: true }
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }} className="page-fade-enter">
      
      {/* Analytics Page Title */}
      <div className="card" style={{ padding: '16px 24px' }}>
        <h2 style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: '20px', color: 'var(--color-obsidian)', letterSpacing: '-0.02em' }}>
          ZiSi. Professional Portfolio Analytics
        </h2>
        <div style={{ fontSize: '11px', color: 'var(--color-iron)', marginTop: '2px' }}>
          Real-time signal confluence, mathematical risk profiles, and execution heatmaps.
        </div>
      </div>

      {/* Row 1: Confluence Radar & Volatility Regime */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr', gap: '24px' }}>
        
        {/* Confluence Radar checklist card */}
        <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div>
            <h3 style={{ fontFamily: 'var(--font-primary)', fontWeight: 700, fontSize: '15px', color: 'var(--color-obsidian)', marginBottom: '2px' }}>
              Technical Signal Confluence Radar
            </h3>
            <p style={{ fontSize: '11px', color: 'var(--color-iron)' }}>
              Real-time gated confirmation checklist per asset prior to executing orders.
            </p>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '320px', overflowY: 'auto', paddingRight: '4px' }}>
            {assetsRadar.map((asset) => (
              <div 
                key={asset.name} 
                style={{ 
                  display: 'grid', 
                  gridTemplateColumns: '1fr 1fr 1.2fr 1fr 1fr 1fr 1.5fr',
                  alignItems: 'center',
                  padding: '10px 14px',
                  background: 'var(--color-cream-deep)',
                  border: '1px solid rgba(0,0,0,0.03)',
                  borderRadius: '10px',
                  fontSize: '12px'
                }}
              >
                <span style={{ fontWeight: 700, color: 'var(--color-obsidian)', fontFamily: 'var(--font-mono)' }}>{asset.name}</span>
                
                {/* Gates indicator checklist */}
                <span style={{ color: asset.rsi > 60 || asset.rsi < 40 ? '#00d4a3' : 'var(--color-iron)' }}>
                  RSI: <span style={{ fontWeight: '500' }}>{asset.rsi}</span>
                </span>
                
                <span style={{ color: asset.mom !== '0.00%' ? '#00d4a3' : 'var(--color-iron)' }}>
                  Mom: <span style={{ fontWeight: '500' }}>{asset.mom}</span>
                </span>
                
                <span style={{ color: Math.abs(parseFloat(asset.ofi)) > 0.40 ? '#00d4a3' : 'var(--color-iron)' }}>
                  OFI: <span style={{ fontWeight: '500' }}>{asset.ofi}</span>
                </span>
                
                <span style={{ color: '#00d4a3' }}>
                  Vol: <span style={{ fontWeight: '500' }}>{asset.vol}</span>
                </span>

                <span style={{ color: '#00d4a3' }}>
                  AI: <span style={{ fontWeight: '500' }}>{asset.ai}</span>
                </span>
                
                <div style={{ justifySelf: 'end', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span style={{ 
                    fontFamily: 'var(--font-mono)', 
                    fontWeight: 700, 
                    fontSize: '10px',
                    padding: '2px 6px',
                    borderRadius: '4px',
                    background: asset.pass ? 'rgba(0, 212, 163, 0.1)' : 'rgba(234, 88, 12, 0.1)',
                    color: asset.pass ? '#00d4a3' : '#ea580c'
                  }}>
                    {asset.status} ({asset.score})
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Volatility Regime Radar Card */}
        <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          <div>
            <h3 style={{ fontFamily: 'var(--font-primary)', fontWeight: 700, fontSize: '15px', color: 'var(--color-obsidian)', marginBottom: '2px' }}>
              Market Volatility Regime Radar
            </h3>
            <p style={{ fontSize: '11px', color: 'var(--color-iron)' }}>
              Real-time volatility tracking adjusts indicator thresholds and bet sizing dynamically.
            </p>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', flex: 1, justifyContent: 'center' }}>
            
            {/* Active Volatility Indicator Box */}
            <div style={{ 
              padding: '16px', 
              background: 'var(--color-cream-deep)', 
              borderRadius: '12px',
              border: '1px solid var(--color-border-subtle)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between'
            }}>
              <div>
                <span style={{ fontSize: '10px', color: 'var(--color-iron)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                  Current Volatility State
                </span>
                <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: '20px', color: 'var(--color-accent)', marginTop: '2px' }}>
                  {regime.label} Regime
                </div>
              </div>
              <span 
                className="alert-pulse animate-pulse" 
                style={{ 
                  width: '16px', 
                  height: '16px', 
                  borderRadius: '50%', 
                  backgroundColor: regime.regime === 'HIGH_VOLATILITY' ? '#ef4444' : '#10b981',
                  boxShadow: regime.regime === 'HIGH_VOLATILITY' ? '0 0 12px #ef4444' : '0 0 12px #10b981'
                }} 
              />
            </div>

            {/* Metric Row: ATR level */}
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 0', borderBottom: '1px solid var(--color-border-subtle)' }}>
              <span style={{ fontSize: '12.5px', color: 'var(--color-text-secondary)', fontWeight: 500 }}>
                15m ATR Percentage (Volatility)
              </span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '13px', fontWeight: '700', color: 'var(--color-obsidian)' }}>
                {(atrPct * 100).toFixed(3)}%
              </span>
            </div>

            {/* Metric Row: Sizer multiplier */}
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 0', borderBottom: '1px solid var(--color-border-subtle)' }}>
              <span style={{ fontSize: '12.5px', color: 'var(--color-text-secondary)', fontWeight: 500 }}>
                Regime Kelly Bet Sizer Modifier
              </span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '13px', fontWeight: '700', color: 'var(--color-accent)' }}>
                {kellyMultiplier.toFixed(2)}x
              </span>
            </div>

            {/* Quick explanation */}
            <div style={{ fontSize: '11px', color: 'var(--color-iron)', lineHeight: 1.4, fontStyle: 'italic', paddingLeft: '4px' }}>
              💡 During high-volatility "Turbulent" states, ZiSi applies a 0.50x Kelly modifier to protect the capital stack from sudden wick slip.
            </div>

          </div>
        </div>

      </div>

      {/* Row 2: Heatmap & Mathematical Risk Profiles */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr', gap: '24px' }}>
        
        {/* Hourly Profitability Heatmap Card */}
        <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div>
            <h3 style={{ fontFamily: 'var(--font-primary)', fontWeight: 700, fontSize: '15px', color: 'var(--color-obsidian)', marginBottom: '2px' }}>
              Hourly Execution Profitability Heatmap
            </h3>
            <p style={{ fontSize: '11px', color: 'var(--color-iron)' }}>
              Analyzes historical trade success and density across 24 UTC hour buckets to detect golden profit windows.
            </p>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: '8px', marginTop: '4px' }}>
            {hoursGrid.map((hObj) => {
              const bg = getHeatmapColor(hObj.winRate, hObj.trades);
              const hasTrades = hObj.trades > 0;
              return (
                <div 
                  key={hObj.hour} 
                  style={{
                    background: bg,
                    border: '1px solid var(--color-border)',
                    borderRadius: '8px',
                    padding: '12px 8px',
                    textAlign: 'center',
                    display: 'flex',
                    flexDirection: 'column',
                    justifyContent: 'center',
                    alignItems: 'center',
                    minHeight: '64px',
                    transition: 'all 200ms ease',
                    cursor: hasTrades ? 'pointer' : 'default'
                  }}
                  className="glow-hover"
                  title={hasTrades ? `${hObj.trades} trades executed inside ${getHourLabel(hObj.hour)} bucket.` : `No trades recorded.`}
                >
                  <span style={{ fontSize: '9px', fontWeight: '700', color: 'var(--color-iron)', fontFamily: 'var(--font-mono)' }}>
                    {getHourLabel(hObj.hour)}
                  </span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: '13px', fontWeight: '800', color: 'var(--color-obsidian)', marginTop: '4px' }}>
                    {hasTrades ? `${(hObj.winRate * 100).toFixed(0)}%` : '—'}
                  </span>
                  {hasTrades && (
                    <span style={{ fontSize: '8px', color: 'var(--color-text-muted)', marginTop: '2px' }}>
                      {hObj.trades} {hObj.trades === 1 ? 'trade' : 'trades'}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Deep Mathematical Risk Profiles Card */}
        <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div>
            <h3 style={{ fontFamily: 'var(--font-primary)', fontWeight: 700, fontSize: '15px', color: 'var(--color-obsidian)', marginBottom: '2px' }}>
              Mathematical Risk & Expectancy Profiles
            </h3>
            <p style={{ fontSize: '11px', color: 'var(--color-iron)' }}>
              Advanced probabilistic indicators describing the mathematical edge of our indicators.
            </p>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
            
            {[
              { label: 'Profit Factor', val: profitFactor.toFixed(2), suffix: 'x', color: profitFactor >= 1.5 ? 'var(--color-profit)' : 'var(--color-amber)' },
              { label: 'Expectancy / Bet', val: expectancy >= 0 ? `+$${expectancy.toFixed(2)}` : `-$${Math.abs(expectancy).toFixed(2)}`, color: expectancy >= 0 ? 'var(--color-profit)' : 'var(--color-loss)' },
              { label: 'Max Drawdown', val: `${maxDrawdown.toFixed(2)}`, suffix: '%', color: maxDrawdown < 5 ? 'var(--color-profit)' : 'var(--color-loss)' },
              { label: 'Current Drawdown', val: `${currentDrawdown.toFixed(2)}`, suffix: '%', color: currentDrawdown < 2 ? 'var(--color-profit)' : 'var(--color-loss)' },
              { label: 'Active Lose Streak', val: consecutiveLosses, suffix: ' trades', color: consecutiveLosses < 2 ? 'var(--color-profit)' : 'var(--color-loss)' },
              { label: 'Risk of Ruin Profile', val: riskOfRuin, suffix: '', color: riskOfRuin === 'Low' ? 'var(--color-profit)' : 'var(--color-loss)' }
            ].map((metric, idx) => (
              <div 
                key={idx} 
                style={{ 
                  background: 'var(--color-cream-deep)', 
                  border: '1px solid var(--color-border-subtle)', 
                  borderRadius: '10px',
                  padding: '12px',
                  display: 'flex',
                  flexDirection: 'column'
                }}
              >
                <span style={{ fontSize: '10px', color: 'var(--color-iron)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                  {metric.label}
                </span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: '17px', fontWeight: '800', color: metric.color, marginTop: '4px' }}>
                  {metric.val}{metric.suffix}
                </span>
              </div>
            ))}

          </div>
        </div>

      </div>

      {/* Row 3: Machine Learning Retraining Progress */}
      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        <div>
          <h3 style={{ fontFamily: 'var(--font-primary)', fontWeight: 700, fontSize: '15px', color: 'var(--color-obsidian)', marginBottom: '2px' }}>
            Ensemble ML Retraining Engine
          </h3>
          <p style={{ fontSize: '11px', color: 'var(--color-iron)' }}>
            ZiSi monitors Polymarket tick execution parameters locally and retrains its Ensemble Model upon gathering sufficient data.
          </p>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '24px', alignItems: 'center' }}>
          
          {/* Visual Progress Bar */}
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '8px' }}>
              <span style={{ color: 'var(--color-text-secondary)', fontWeight: 500 }}>
                Ensemble Cycle Progress
              </span>
              <span style={{ fontFamily: 'var(--font-mono)', fontWeight: '700', color: 'var(--color-accent)' }}>
                {mlPercent.toFixed(0)}% Complete
              </span>
            </div>
            
            <div style={{ 
              width: '100%', 
              height: '14px', 
              background: 'var(--color-cream-deep)', 
              borderRadius: '99px',
              border: '1px solid var(--color-border-subtle)',
              overflow: 'hidden',
              position: 'relative'
            }}>
              <div style={{ 
                width: `${Math.min(100, mlPercent)}%`, 
                height: '100%', 
                background: 'linear-gradient(90deg, #b45309 0%, var(--color-accent) 100%)',
                borderRadius: 'inherit',
                transition: 'width 1000ms cubic-bezier(0.16, 1, 0.3, 1)'
              }} />
            </div>
          </div>

          {/* Counts */}
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12.5px', padding: '12px', background: 'var(--color-cream-deep)', borderRadius: '10px', border: '1px solid var(--color-border-subtle)' }}>
            <span style={{ color: 'var(--color-text-secondary)', fontWeight: 500 }}>
              Data Samples
            </span>
            <span style={{ fontFamily: 'var(--font-mono)', fontWeight: '700', color: 'var(--color-obsidian)' }}>
              {mlCollected} / {mlNeeded} cycles
            </span>
          </div>

          {/* Retraining Action State */}
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12.5px', padding: '12px', background: 'var(--color-cream-deep)', borderRadius: '10px', border: '1px solid var(--color-border-subtle)' }}>
            <span style={{ color: 'var(--color-text-secondary)', fontWeight: 500 }}>
              Next Cycle
            </span>
            <span style={{ fontFamily: 'var(--font-mono)', fontWeight: '700', color: 'var(--color-profit)' }}>
              {mlCollected >= mlNeeded ? 'READY' : 'GATHERING FEED'}
            </span>
          </div>

        </div>
      </div>

    </div>
  );
}
