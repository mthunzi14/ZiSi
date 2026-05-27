import React, { useEffect, useRef, useState } from 'react';

/**
 * ExplodedView - Renders container panels and "explodes" inner components outward
 * along distinct vectors based on scroll position, locking them back together as scroll continues.
 */
export const ExplodedView = ({
  children,
  className = '',
  style = {},
  spreadMultiplier = 150 // maximum pixel drift
}) => {
  const containerRef = useRef(null);
  const [spread, setSpread] = useState(1); // 1 = fully exploded, 0 = locked

  useEffect(() => {
    const handleScroll = () => {
      const el = containerRef.current;
      if (!el) return;

      const rect = el.getBoundingClientRect();
      const viewHeight = window.innerHeight;
      
      // Calculate how close the center of the card is to the center of the viewport
      const cardCenter = rect.top + rect.height / 2;
      const viewCenter = viewHeight / 2;
      const distance = Math.abs(cardCenter - viewCenter);
      
      // If within 500px of center, spring to locked position. Otherwise explode.
      const maxDistance = 400;
      const factor = Math.min(1, distance / maxDistance);
      
      // Curve factor using ease-out shape
      const spreadFactor = Math.pow(factor, 2);
      setSpread(spreadFactor);
    };

    window.addEventListener('scroll', handleScroll, { passive: true });
    handleScroll();

    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  // Map distinct directional coordinates on children
  // Inside ExplodedView, children are rendered as layers with distinct offset vectors:
  // Layer 0: shifts Top-Left
  // Layer 1: shifts Top-Right
  // Layer 2: shifts Bottom-Left
  // Layer 3: shifts Bottom-Right
  const vectors = [
    [-1, -1], // Layer 0: Top-Left
    [1, -1],  // Layer 1: Top-Right
    [-1, 1],  // Layer 2: Bottom-Left
    [1, 1],   // Layer 3: Bottom-Right
  ];

  return (
    <div
      ref={containerRef}
      className={`t-exploded-panel ${className}`}
      style={{ ...style }}
    >
      {React.Children.map(children, (child, index) => {
        if (!React.isValidElement(child)) return child;

        const vector = vectors[index % vectors.length];
        const tx = vector[0] * spread * spreadMultiplier;
        const ty = vector[1] * spread * spreadMultiplier;
        const opacity = Math.max(0.6, 1 - spread * 0.45);

        return (
          <div
            className="t-exploded-layer"
            style={{
              transform: `translate3d(${tx}px, ${ty}px, 0)`,
              opacity: opacity,
              transition: 'transform 0.2s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.2s ease-out',
            }}
          >
            {child}
          </div>
        );
      })}
    </div>
  );
};

export default ExplodedView;
