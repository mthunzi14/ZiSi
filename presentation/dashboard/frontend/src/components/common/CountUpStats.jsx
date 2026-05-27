import React, { useEffect, useRef, useState } from 'react';

/**
 * CountUpStats - Counts up to a target number when the element scrolls into the viewport.
 * Uses requestAnimationFrame for high performance.
 */
export const CountUpStats = ({
  value, // target numerical value
  durationMs = 1500,
  decimals = 0,
  prefix = '',
  suffix = '',
  className = '',
  style = {}
}) => {
  const [displayValue, setDisplayValue] = useState(0);
  const elementRef = useRef(null);
  const hasAnimatedRef = useRef(false);
  const animationFrameIdRef = useRef(null);

  useEffect(() => {
    const target = Number(value) || 0;
    hasAnimatedRef.current = false; // Reset trigger on target change
    
    // Cancel any active animation frame from previous values
    if (animationFrameIdRef.current) {
      cancelAnimationFrame(animationFrameIdRef.current);
      animationFrameIdRef.current = null;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            hasAnimatedRef.current = true;
            let start = null;

            const animate = (timestamp) => {
              if (!start) start = timestamp;
              const progress = Math.min(1, (timestamp - start) / durationMs);
              
              // Easing out quadratic mapping
              const easeOut = progress * (2 - progress);
              const current = easeOut * target;
              setDisplayValue(current);

              if (progress < 1) {
                animationFrameIdRef.current = requestAnimationFrame(animate);
              } else {
                setDisplayValue(target);
              }
            };

            animationFrameIdRef.current = requestAnimationFrame(animate);
          }
        });
      },
      { threshold: 0.1 }
    );

    const currentEl = elementRef.current;
    if (currentEl) {
      observer.observe(currentEl);
    }

    return () => {
      if (currentEl) {
        observer.unobserve(currentEl);
      }
      if (animationFrameIdRef.current) {
        cancelAnimationFrame(animationFrameIdRef.current);
      }
    };
  }, [value, durationMs]);

  // Format with decimal count
  const formatted = displayValue.toFixed(decimals);

  return (
    <span
      ref={elementRef}
      className={className}
      style={{
        fontFamily: 'var(--font-mono)',
        fontWeight: 'bold',
        ...style
      }}
    >
      {prefix}
      {formatted}
      {suffix}
    </span>
  );
};

export default CountUpStats;
