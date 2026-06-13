import React from 'react';
import PropTypes from 'prop-types';

/**
 * GlassCard - A premium glassmorphic card component optimized for high-performance dashboards.
 * Fully accessible with support for keyboard focus, interactive click events, and loading states.
 */
export default function GlassCard({
  children,
  onClick,
  className = '',
  style = {},
  glowColor = 'rgba(43, 127, 255, 0.15)',
  isLoading = false,
  interactive = false,
  ariaLabel = '',
  role = '',
  tabIndex = 0,
}) {
  const isClickable = interactive || !!onClick;
  
  // Interactive style additions
  const interactiveStyles = isClickable
    ? {
        cursor: 'pointer',
        userSelect: 'none',
      }
    : {};

  const handleKeyDown = (e) => {
    if (onClick && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      onClick(e);
    }
  };

  return (
    <div
      className={`glass-panel ${isClickable ? 'interactive-glass-panel' : ''} ${className}`}
      onClick={isClickable && !isLoading ? onClick : undefined}
      onKeyDown={isClickable && !isLoading ? handleKeyDown : undefined}
      role={isClickable ? (role || 'button') : (role || undefined)}
      tabIndex={isClickable && !isLoading ? tabIndex : undefined}
      aria-label={ariaLabel || undefined}
      aria-busy={isLoading}
      style={{
        padding: 'var(--spacing-20)',
        display: 'flex',
        flexDirection: 'column',
        position: 'relative',
        outline: 'none',
        ...interactiveStyles,
        ...style,
      }}
    >
      {/* Loading State Overlay */}
      {isLoading && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            borderRadius: 'inherit',
            background: 'linear-gradient(90deg, rgba(32, 32, 48, 0.1) 25%, rgba(43, 127, 255, 0.05) 50%, rgba(32, 32, 48, 0.1) 75%)',
            backgroundSize: '200% 100%',
            animation: 'shimmer 1.5s infinite linear',
            pointerEvents: 'none',
            zIndex: 2,
          }}
          aria-hidden="true"
        />
      )}

      {/* Focus & Interactive Style Stylesheet (Injected once for simplicity and standalone purity) */}
      <style>{`
        @keyframes shimmer {
          0% { background-position: 200% 0; }
          100% { background-position: -200% 0; }
        }
        
        .glass-panel:focus-visible {
          box-shadow: 0 0 0 2px var(--color-accent), 0 8px 32px 0 rgba(0, 0, 0, 0.45) !important;
          border-color: var(--color-accent) !important;
        }

        .interactive-glass-panel:hover {
          transform: translate3d(0, -3px, 0);
          box-shadow: 0 12px 40px 0 ${glowColor} !important;
          border-color: rgba(255, 255, 255, 0.2) !important;
        }
        
        .interactive-glass-panel:active {
          transform: translate3d(0, -1px, 0);
          box-shadow: 0 4px 16px 0 ${glowColor} !important;
          transition: all 0.08s ease;
        }
      `}</style>

      {/* Children Wrapper with Loading Opacity Fade */}
      <div style={{ opacity: isLoading ? 0.35 : 1, transition: 'opacity 0.25s ease', width: '100%', height: '100%', display: 'flex', flexDirection: 'column' }}>
        {children}
      </div>
    </div>
  );
}

GlassCard.propTypes = {
  children: PropTypes.node.isRequired,
  onClick: PropTypes.func,
  className: PropTypes.string,
  style: PropTypes.object,
  glowColor: PropTypes.string,
  isLoading: PropTypes.bool,
  interactive: PropTypes.bool,
  ariaLabel: PropTypes.string,
  role: PropTypes.string,
  tabIndex: PropTypes.number,
};
