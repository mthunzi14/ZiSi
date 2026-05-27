import React from 'react';
import PropTypes from 'prop-types';

/**
 * InteractiveButton - An accessible, highly responsive trading button.
 * Supports loading states, custom size presets, icon prefixes/suffixes, and micro-interactions.
 */
export default function InteractiveButton({
  children,
  onClick,
  type = 'button',
  variant = 'primary', // 'primary' | 'secondary' | 'danger' | 'success' | 'outline'
  size = 'md', // 'sm' | 'md' | 'lg'
  disabled = false,
  isLoading = false,
  className = '',
  style = {},
  ariaLabel = '',
  iconPrefix = null,
  iconSuffix = null,
}) {
  const getStyles = () => {
    // Sizing presets
    const paddingMap = {
      sm: 'var(--spacing-8) var(--spacing-16)',
      md: 'var(--spacing-12) var(--spacing-24)',
      lg: 'var(--spacing-16) var(--spacing-32)',
    };
    const fontMap = {
      sm: '12px',
      md: '13px',
      lg: '15px',
    };

    // Variant presets matching the Air Design System
    const variantStyles = {
      primary: {
        background: 'var(--color-accent)',
        color: '#ffffff',
        border: '1px solid transparent',
        boxShadow: '0 4px 14px 0 rgba(43, 127, 255, 0.4)',
      },
      secondary: {
        background: 'rgba(255, 255, 255, 0.08)',
        color: 'var(--color-text-secondary)',
        border: '1px solid rgba(255, 255, 255, 0.1)',
        boxShadow: 'none',
      },
      danger: {
        background: 'var(--color-loss)',
        color: '#ffffff',
        border: '1px solid transparent',
        boxShadow: '0 4px 14px 0 rgba(255, 77, 77, 0.4)',
      },
      success: {
        background: 'var(--color-profit)',
        color: '#06060c',
        border: '1px solid transparent',
        boxShadow: '0 4px 14px 0 rgba(0, 212, 163, 0.4)',
        fontWeight: '700',
      },
      outline: {
        background: 'transparent',
        color: 'var(--color-text-primary)',
        border: '1px solid rgba(255, 255, 255, 0.25)',
        boxShadow: 'none',
      },
    };

    const activeVariant = variantStyles[variant] || variantStyles.primary;

    return {
      padding: paddingMap[size] || paddingMap.md,
      fontSize: fontMap[size] || fontMap.md,
      cursor: disabled || isLoading ? 'not-allowed' : 'pointer',
      opacity: disabled || isLoading ? 0.5 : 1,
      fontFamily: 'var(--font-heading)',
      fontWeight: '600',
      borderRadius: 'var(--radius-buttons)',
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 'var(--spacing-8)',
      transition: 'all 0.15s cubic-bezier(0.4, 0, 0.2, 1)',
      outline: 'none',
      ...activeVariant,
      ...style,
    };
  };

  return (
    <button
      type={type}
      onClick={!disabled && !isLoading ? onClick : undefined}
      disabled={disabled || isLoading}
      className={`interactive-button btn-${variant} ${className}`}
      aria-label={ariaLabel || undefined}
      aria-busy={isLoading}
      style={getStyles()}
    >
      {/* Loading Spinner */}
      {isLoading && (
        <span
          className="btn-spinner"
          style={{
            width: '1em',
            height: '1em',
            border: '2px solid currentColor',
            borderRightColor: 'transparent',
            borderRadius: '50%',
            display: 'inline-block',
            animation: 'btnRotate 0.75s infinite linear',
          }}
          aria-hidden="true"
        />
      )}

      {/* Prefix Icon */}
      {!isLoading && iconPrefix && <span aria-hidden="true">{iconPrefix}</span>}

      {/* Content */}
      <span style={{ display: 'inline-flex', alignItems: 'center' }}>
        {children}
      </span>

      {/* Suffix Icon */}
      {!isLoading && iconSuffix && <span aria-hidden="true">{iconSuffix}</span>}

      {/* Styles Injection */}
      <style>{`
        @keyframes btnRotate {
          to { transform: rotate(360deg); }
        }
        
        .interactive-button:focus-visible {
          box-shadow: 0 0 0 2px var(--color-bg-base), 0 0 0 4px var(--color-accent) !important;
          transform: translateY(-1px);
        }
        
        .interactive-button:hover:not(:disabled) {
          transform: translateY(-2px);
          filter: brightness(1.15);
        }
        
        .interactive-button:active:not(:disabled) {
          transform: translateY(0);
          filter: brightness(0.95);
        }
      `}</style>
    </button>
  );
}

InteractiveButton.propTypes = {
  children: PropTypes.node.isRequired,
  onClick: PropTypes.func,
  type: PropTypes.string,
  variant: PropTypes.oneOf(['primary', 'secondary', 'danger', 'success', 'outline']),
  size: PropTypes.oneOf(['sm', 'md', 'lg']),
  disabled: PropTypes.bool,
  isLoading: PropTypes.bool,
  className: PropTypes.string,
  style: PropTypes.object,
  ariaLabel: PropTypes.string,
  iconPrefix: PropTypes.node,
  iconSuffix: PropTypes.node,
};
