import React from 'react';
import PropTypes from 'prop-types';

/**
 * StatusPill - An accessible, glowing status badge component.
 * Features semantic theme coloring, gentle pulse micro-animations, and screen-reader support.
 */
export default function StatusPill({
  label,
  statusType = 'info', // 'success' | 'danger' | 'warning' | 'info' | 'muted'
  pulse = false,
  className = '',
  style = {},
  ariaLabel = '',
}) {
  // Map semantic types to Air design variables
  const themeMap = {
    success: {
      color: 'var(--color-profit)',
      background: 'rgba(0, 212, 163, 0.08)',
      borderColor: 'rgba(0, 212, 163, 0.25)',
    },
    danger: {
      color: 'var(--color-loss)',
      background: 'rgba(255, 77, 77, 0.08)',
      borderColor: 'rgba(255, 77, 77, 0.25)',
    },
    warning: {
      color: 'var(--color-amber)',
      background: 'rgba(245, 166, 35, 0.08)',
      borderColor: 'rgba(245, 166, 35, 0.25)',
    },
    info: {
      color: 'var(--color-accent)',
      background: 'rgba(43, 127, 255, 0.08)',
      borderColor: 'rgba(43, 127, 255, 0.25)',
    },
    muted: {
      color: 'var(--color-text-muted)',
      background: 'rgba(130, 143, 159, 0.08)',
      borderColor: 'rgba(130, 143, 159, 0.2)',
    },
  };

  const currentTheme = themeMap[statusType] || themeMap.info;

  return (
    <div
      className={`status-pill ${className}`}
      role="status"
      aria-live="polite"
      aria-label={ariaLabel || `Status is ${label}`}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 'var(--spacing-8)',
        padding: 'var(--spacing-4) var(--spacing-12)',
        borderRadius: '20px',
        fontSize: '11px',
        fontWeight: '600',
        fontFamily: 'var(--font-mono)',
        letterSpacing: '0.04em',
        textTransform: 'uppercase',
        color: currentTheme.color,
        background: currentTheme.background,
        border: `1px solid ${currentTheme.borderColor}`,
        boxShadow: pulse ? `0 0 12px ${currentTheme.borderColor}` : 'none',
        transition: 'all 0.3s ease',
        ...style,
      }}
    >
      {/* Glowing Circle Indicator */}
      <span
        className={pulse ? 'status-dot-pulse' : ''}
        style={{
          width: '6px',
          height: '6px',
          borderRadius: '50%',
          backgroundColor: currentTheme.color,
          display: 'inline-block',
          boxShadow: `0 0 6px ${currentTheme.color}`,
        }}
        aria-hidden="true"
      />
      
      {/* Label Text */}
      <span>{label}</span>

      {/* Pulsing Style Injection */}
      <style>{`
        @keyframes statusPulse {
          0% {
            transform: scale(0.9);
            opacity: 0.6;
            box-shadow: 0 0 0 0 color-mix(in srgb, ${currentTheme.color} 40%, transparent);
          }
          70% {
            transform: scale(1.1);
            opacity: 1;
            box-shadow: 0 0 0 6px color-mix(in srgb, ${currentTheme.color} 0%, transparent);
          }
          100% {
            transform: scale(0.9);
            opacity: 0.6;
            box-shadow: 0 0 0 0 color-mix(in srgb, ${currentTheme.color} 0%, transparent);
          }
        }
        
        .status-dot-pulse {
          animation: statusPulse 2s infinite ease-in-out;
        }
      `}</style>
    </div>
  );
}

StatusPill.propTypes = {
  label: PropTypes.string.isRequired,
  statusType: PropTypes.oneOf(['success', 'danger', 'warning', 'info', 'muted']),
  pulse: PropTypes.bool,
  className: PropTypes.string,
  style: PropTypes.object,
  ariaLabel: PropTypes.string,
};
