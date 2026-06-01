// Analytics.jsx — Deep Institutional-Grade Quantitative Insights, Confluence Radar & Hourly Heatmap
import { useState } from 'react';
import CountUpStats from './common/CountUpStats';
import BacktestHeatmap from './BacktestHeatmap';

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
    { name: 'DOGE/5m', rsi: 45.2, mom: '+0.00%', ofi: '+0.02', vol: '0.6x', ai: '51.2%', status: 'WAIT',score: '2/5', pass: false },
    { name: 'DOGE/15m', rsi: 48.1, mom: '+0.01%', ofi: '+0.11', vol: '0.9x', ai: '56.4%', status: 'WAIT',score: '3/5', pass: false },
    { name: 'HYPE/5m', rsi: 66.5, mom: '+0.12%', ofi: '+0.74', vol: '2.8x', ai: '89.1%', status: 'UP',  score: '5/5', pass: true },
    { name: 'HYPE/15m', rsi: 61.2, mom: '+0.05%', ofi: '+0.48', vol: '1.9x', ai: '78.5%', status: 'UP',  score: '4/5', pass: true },
    { name: 'BNB/5m',  rsi: 52.4, mom: '+0.01%', ofi: '+0.08', vol: '1.1x', ai: '64.2%', status: 'WAIT',score: '3/5', pass: false },
    { name: 'BNB/15m', rsi: 50.1, mom: '+0.00%', ofi: '+0.03', vol: '0.7x', ai: '51.2%', status: 'WAIT',score: '2/5', pass: false },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }} className="page-fade-enter">
      
      {/* Analytics Page Title */}
      <div className="card" style={{ padding: '16px 24px' }}>
        <h2 style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: '20px', color: 'var(--color-obsidian)', letterSpacing: '-0.02em' }}>
          ZiSi. Professional Portfolio Analytics
        </h2>
        <div style={{ fontSize: '11px', color: 'var(--color-iron)', marginTop: '2px' }}>
          Real-time confirmation gates for active trade assets and volatility regime metrics.
        </div>
      </div>

      {/* Row 1: Confluence Radar & Volatility Regime */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr', gap: '24px' }}>
        
        {/* Confluence Radar checklist card */}
        <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: '12px', height: '100%' }}>
          <div>
            <h3 style={{ fontFamily: 'var(--font-primary)', fontWeight: 700, fontSize: '15px', color: 'var(--color-obsidian)', marginBottom: '2px' }}>
              Technical Signal Confluence Radar
            </h3>
            <p style={{ fontSize: '11px', color: 'var(--color-iron)' }}>
              Real-time gated confirmation checklist per asset prior to executing orders.
            </p>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '280px', overflowY: 'auto', paddingRight: '4px' }}>
            {assetsRadar.map((asset) => (
              <div 
                key={asset.name} 
                style={{ 
                  display: 'grid', 
                  gridTemplateColumns: '1fr 1fr 1.2fr 1fr 1fr 1fr 1.4fr',
                  alignItems: 'center',
                  padding: '6px 12px',
                  background: 'var(--color-cream-deep)',
                  border: '1px solid rgba(255,255,255,0.03)',
                  borderRadius: '8px',
                  fontSize: '11.5px'
                }}
              >
                <span style={{ fontWeight: 700, color: 'var(--color-obsidian)', fontFamily: 'var(--font-mono)' }}>{asset.name}</span>
                
                <span style={{ color: asset.rsi > 60 || asset.rsi < 40 ? '#00d4a3' : 'var(--color-iron)' }}>
                  RSI: <span style={{ fontWeight: '500' }}>{asset.rsi}</span>
                </span>
                
                <span style={{ color: asset.mom !== '+0.00%' && asset.mom !== '0.00%' ? '#00d4a3' : 'var(--color-iron)' }}>
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
                    fontSize: '9.5px',
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
        <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: '16px', height: '100%' }}>
          <div>
            <h3 style={{ fontFamily: 'var(--font-primary)', fontWeight: 700, fontSize: '15px', color: 'var(--color-obsidian)', marginBottom: '2px' }}>
              Market Volatility Regime Radar
            </h3>
            <p style={{ fontSize: '11px', color: 'var(--color-iron)' }}>
              Adaptive indicator thresholds and dynamic bet sizing variables.
            </p>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', flex: 1, justifyContent: 'center' }}>
            
            {/* Active Volatility Indicator Box */}
            <div style={{ 
              padding: '12px 16px', 
              background: 'var(--color-cream-deep)', 
              borderRadius: '10px',
              border: '1px solid var(--color-border-subtle)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between'
            }}>
              <div>
                <span style={{ fontSize: '9px', color: 'var(--color-iron)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                  Current Volatility State
                </span>
                <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: '18px', color: 'var(--color-accent)', marginTop: '2px' }}>
                  {regime.label} Regime
                </div>
              </div>
              <span 
                className="alert-pulse animate-pulse" 
                style={{ 
                  width: '12px', 
                  height: '12px', 
                  borderRadius: '50%', 
                  backgroundColor: regime.regime === 'HIGH_VOLATILITY' ? '#ef4444' : '#10b981',
                  boxShadow: regime.regime === 'HIGH_VOLATILITY' ? '0 0 12px #ef4444' : '0 0 12px #10b981'
                }} 
              />
            </div>

            {/* Metric Row: ATR level */}
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid var(--color-border-subtle)' }}>
              <span style={{ fontSize: '12px', color: 'var(--color-text-secondary)', fontWeight: 500 }}>
                15m ATR Percentage
              </span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12.5px', fontWeight: '700', color: 'var(--color-obsidian)' }}>
                {(atrPct * 100).toFixed(3)}%
              </span>
            </div>

            {/* Metric Row: Sizer multiplier */}
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid var(--color-border-subtle)' }}>
              <span style={{ fontSize: '12px', color: 'var(--color-text-secondary)', fontWeight: 500 }}>
                Regime Kelly Bet Sizer Modifier
              </span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12.5px', fontWeight: '700', color: 'var(--color-accent)' }}>
                {kellyMultiplier.toFixed(2)}x
              </span>
            </div>

            {/* Quick explanation */}
            <div style={{ fontSize: '10.5px', color: 'var(--color-iron)', lineHeight: 1.4, fontStyle: 'italic', paddingLeft: '4px' }}>
              💡 During high-volatility "Turbulent" states, ZiSi applies a 0.50x Kelly modifier to protect the capital stack from sudden wick slip.
            </div>

          </div>
        </div>

      </div>

      {/* Row 2: Backtest Parameter Heatmap */}
      <BacktestHeatmap />

    </div>
  );
}
