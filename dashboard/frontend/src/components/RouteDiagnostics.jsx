// RouteDiagnostics.jsx — Premium glassmorphic latency speedometer, slippage counter, and circuit breaker status.
import React from 'react';
import ExplodedView from './common/ExplodedView';

export default function RouteDiagnostics({ diagnostics = {} }) {
  const avgLatency = parseFloat(diagnostics.avg_latency_ms ?? 0);
  const avgSlippage = parseFloat(diagnostics.avg_slippage_cents ?? 0);
  const asymmetricFills = parseInt(diagnostics.asymmetric_fills ?? 0);
  const isBreakerActive = !!diagnostics.circuit_breaker_active;

  // Latency rating and color scheme
  let latencyColor = 'var(--color-profit)';
  let latencyStatus = 'Excellent';
  if (avgLatency > 500) {
    latencyColor = 'var(--color-loss)';
    latencyStatus = 'Degraded';
  } else if (avgLatency > 300) {
    latencyColor = 'var(--color-amber)';
    latencyStatus = 'Warning';
  } else if (avgLatency > 150) {
    latencyColor = 'var(--color-accent)';
    latencyStatus = 'Good';
  }

  // Calculate needle rotation angle for the speedometer gauge (SVG)
  // Arc goes from -90 degrees (0ms) to 90 degrees (600ms+)
  const maxLatencyScale = 600;
  const clampedLatency = Math.min(avgLatency, maxLatencyScale);
  const percentage = clampedLatency / maxLatencyScale;
  const rotationAngle = -90 + percentage * 180;

  return (
    <div 
      className="glass-panel"
      style={{
        padding: 'var(--spacing-20)',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        gap: 'var(--spacing-16)',
        overflow: 'hidden'
      }}
    >
      {/* Decorative ambient background glow */}
      <div style={{
        position: 'absolute',
        top: '-50px',
        right: '-50px',
        width: '120px',
        height: '120px',
        borderRadius: '50%',
        background: isBreakerActive ? 'rgba(255, 77, 77, 0.08)' : `${latencyColor}0a`,
        filter: 'blur(40px)',
        zIndex: 0,
        pointerEvents: 'none',
        transition: 'background 0.5s ease'
      }} />

      <ExplodedView spreadMultiplier={80}>
        {/* Layer 1: Title Header */}
        <div style={{
          fontFamily: 'var(--font-heading)',
          fontWeight: 600,
          fontSize: '15px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          zIndex: 1,
          position: 'relative'
        }}>
          <span>Route Diagnostics</span>
          <span style={{
            fontSize: '10px',
            color: 'var(--color-text-muted)',
            fontFamily: 'var(--font-mono)'
          }}>
            REAL-TIME
          </span>
        </div>

        {/* Layer 2: Speedometer Gauge & Numerical Readout */}
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          margin: '10px 0 5px 0',
          position: 'relative',
          zIndex: 1
        }}>
          {/* SVG Gauge */}
          <svg width="150" height="90" viewBox="0 0 120 75" style={{ overflow: 'visible' }}>
            <defs>
              <linearGradient id="gaugeGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="var(--color-profit)" />
                <stop offset="50%" stopColor="var(--color-amber)" />
                <stop offset="100%" stopColor="var(--color-loss)" />
              </linearGradient>
              <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
                <feGaussianBlur stdDeviation="3" result="blur" />
                <feComposite in="SourceGraphic" in2="blur" operator="over" />
              </filter>
            </defs>

            {/* Background Arc */}
            <path
              d="M 15,70 A 45,45 0 0,1 105,70"
              fill="none"
              stroke="rgba(255,255,255,0.06)"
              strokeWidth="10"
              strokeLinecap="round"
            />

            {/* Colored Gradient Arc */}
            <path
              d="M 15,70 A 45,45 0 0,1 105,70"
              fill="none"
              stroke="url(#gaugeGradient)"
              strokeWidth="10"
              strokeLinecap="round"
              strokeDasharray="141"
              strokeDashoffset={141 - (percentage * 141)}
              style={{ transition: 'stroke-dashoffset 0.8s cubic-bezier(0.4, 0, 0.2, 1)' }}
            />

            {/* Center Pivot Point */}
            <circle cx="60" cy="70" r="5" fill="#ffffff" />
            <circle cx="60" cy="70" r="8" fill="none" stroke="rgba(255,255,255,0.2)" strokeWidth="1" />

            {/* Glowing Indicator Needle */}
            <line
              x1="60"
              y1="70"
              x2="60"
              y2="30"
              stroke="#ffffff"
              strokeWidth="2.5"
              strokeLinecap="round"
              transform={`rotate(${rotationAngle} 60 70)`}
              style={{
                transformOrigin: '60px 70px',
                transition: 'transform 0.8s cubic-bezier(0.34, 1.56, 0.64, 1)',
                filter: 'drop-shadow(0px 0px 3px rgba(255,255,255,0.5))'
              }}
            />
          </svg>

          {/* Central digital value */}
          <div style={{
            marginTop: '-15px',
            textAlign: 'center',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center'
          }}>
            <span style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '24px',
              fontWeight: 700,
              color: 'var(--color-text-primary)',
              textShadow: '0 0 10px rgba(255,255,255,0.1)'
            }}>
              {avgLatency > 0 ? `${avgLatency.toFixed(0)}` : '--'}
              <span style={{ fontSize: '12px', fontWeight: 400, color: 'var(--color-text-muted)', marginLeft: '2px' }}>ms</span>
            </span>
            <span style={{
              fontSize: '10px',
              fontWeight: 600,
              textTransform: 'uppercase',
              color: latencyColor,
              letterSpacing: '0.05em',
              marginTop: '2px',
              padding: '1px 6px',
              borderRadius: '4px',
              background: `${latencyColor}15`,
              border: `1px solid ${latencyColor}25`
            }}>
              {latencyStatus}
            </span>
          </div>
        </div>

        {/* Layer 3: Sub-Metrics grid */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 'var(--spacing-12)',
          zIndex: 1,
          position: 'relative'
        }}>
          {/* Slippage block */}
          <div style={{
            background: 'rgba(255, 255, 255, 0.02)',
            border: '1px solid rgba(255, 255, 255, 0.04)',
            borderRadius: '8px',
            padding: '8px 12px',
            display: 'flex',
            flexDirection: 'column',
            transition: 'background 0.2s ease'
          }}>
            <span style={{ fontSize: '10px', color: 'var(--color-text-muted)', textTransform: 'uppercase', marginBottom: '2px' }}>
              Avg Slippage
            </span>
            <span style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '16px',
              fontWeight: 600,
              color: avgSlippage > 1.5 ? 'var(--color-amber)' : 'var(--color-profit)'
            }}>
              {avgSlippage > 0 ? `${avgSlippage.toFixed(2)}¢` : '0.00¢'}
            </span>
          </div>

          {/* Asymmetric Fills block */}
          <div style={{
            background: 'rgba(255, 255, 255, 0.02)',
            border: '1px solid rgba(255, 255, 255, 0.04)',
            borderRadius: '8px',
            padding: '8px 12px',
            display: 'flex',
            flexDirection: 'column',
            transition: 'background 0.2s ease'
          }}>
            <span style={{ fontSize: '10px', color: 'var(--color-text-muted)', textTransform: 'uppercase', marginBottom: '2px' }}>
              Asym Fills
            </span>
            <span style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '16px',
              fontWeight: 600,
              color: asymmetricFills > 0 ? 'var(--color-loss)' : 'var(--color-text-muted)'
            }}>
              {asymmetricFills}
            </span>
          </div>
        </div>

        {/* Layer 4: Circuit Breaker status banner */}
        <div style={{ zIndex: 1, position: 'relative' }}>
          {isBreakerActive ? (
            <div style={{
              background: 'linear-gradient(135deg, rgba(255, 77, 77, 0.15) 0%, rgba(255, 77, 77, 0.05) 100%)',
              border: '1px solid rgba(255, 77, 77, 0.3)',
              borderRadius: 'var(--radius-buttons)',
              padding: '10px 12px',
              fontSize: '11px',
              lineHeight: '1.4',
              color: 'var(--color-loss)',
              fontWeight: 500,
              animation: 'pulse 1.5s infinite',
              display: 'flex',
              alignItems: 'flex-start',
              gap: '8px'
            }}>
              <span style={{ fontSize: '14px', flexShrink: 0 }}>🚨</span>
              <div>
                <strong style={{ display: 'block', marginBottom: '2px', textTransform: 'uppercase', letterSpacing: '0.03em' }}>
                  Circuit Breaker Active
                </strong>
                API performance degraded. HFT cross-platform scaling gated defensively.
              </div>
            </div>
          ) : (
            <div style={{
              background: 'linear-gradient(135deg, rgba(0, 212, 163, 0.08) 0%, rgba(0, 212, 163, 0.02) 100%)',
              border: '1px solid rgba(0, 212, 163, 0.2)',
              borderRadius: 'var(--radius-buttons)',
              padding: '10px 12px',
              fontSize: '11px',
              lineHeight: '1.4',
              color: 'var(--color-profit)',
              fontWeight: 500,
              display: 'flex',
              alignItems: 'center',
              gap: '8px'
            }}>
              <span style={{ fontSize: '13px' }}>🛡️</span>
              <div>
                <strong style={{ display: 'block', textTransform: 'uppercase', letterSpacing: '0.03em' }}>
                  Gateway Secure
                </strong>
                Latency nominal. Execution route active & safe.
              </div>
            </div>
          )}
        </div>
      </ExplodedView>
    </div>
  );
}
