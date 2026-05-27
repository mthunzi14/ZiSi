import React, { useEffect, useRef, useState } from 'react';

/**
 * SpotlightMask - Creates a circular spotlight clip-path mask over its child content
 * that grows from 0% to 150% as the operator scrolls down the page.
 */
export const SpotlightMask = ({
  children,
  className = '',
  style = {}
}) => {
  const containerRef = useRef(null);
  const [radius, setRadius] = useState(0);

  useEffect(() => {
    const handleScroll = () => {
      const el = containerRef.current;
      if (!el) return;

      const rect = el.getBoundingClientRect();
      const viewHeight = window.innerHeight;

      // Scroll factor: 0 when element enters screen at bottom, 1 when it exits at top
      const entry = rect.top - viewHeight;
      const totalDist = -viewHeight - rect.height;
      const scrollFactor = Math.min(1, Math.max(0, entry / totalDist));

      // Spotlight grows from 0% to 150% in size
      const targetRadius = scrollFactor * 150;
      setRadius(targetRadius);
    };

    window.addEventListener('scroll', handleScroll, { passive: true });
    handleScroll(); // Initial call

    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  return (
    <div
      ref={containerRef}
      className={`t-spotlight-section ${className}`}
      style={{ ...style }}
    >
      {/* Spotlight Overlay Layer */}
      <div
        className="t-spotlight-mask"
        style={{
          clipPath: `circle(${radius}% at 50% 50%)`,
        }}
      />
      {/* Glowing contents reveal backdrop */}
      <div style={{ position: 'relative', zIndex: 1 }}>
        {children}
      </div>
    </div>
  );
};

export default SpotlightMask;
