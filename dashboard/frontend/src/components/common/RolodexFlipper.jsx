import React, { useState, useEffect, useRef } from 'react';

/**
 * RolodexFlipper animates words flipping on the X-axis (rotateX) inline.
 * Left-aligned within its container to keep flush against preceding text.
 */
export const RolodexFlipper = ({
  words = ['ALPHA', 'EDGE', 'YIELD', 'MULTITUDES'],
  intervalMs = 3000,
  className = '',
  style = {}
}) => {
  const [index, setIndex] = useState(0);
  const [containerWidth, setContainerWidth] = useState(0);
  const itemsRef = useRef([]);

  useEffect(() => {
    const handleWordTransition = setInterval(() => {
      setIndex((prev) => (prev + 1) % words.length);
    }, intervalMs);

    return () => clearInterval(handleWordTransition);
  }, [words, intervalMs]);

  // Adjust container width dynamically so flush preceding layout never breaks on different word lengths!
  useEffect(() => {
    const activeEl = itemsRef.current[index];
    if (activeEl) {
      setContainerWidth(activeEl.offsetWidth);
    }
  }, [index, words]);

  return (
    <div
      className={`t-rolodex-container ${className}`}
      style={{
        width: `${containerWidth || 100}px`,
        height: '1.2em',
        transition: 'width 0.4s cubic-bezier(0.34, 1.56, 0.64, 1)',
        ...style
      }}
    >
      <div className="t-rolodex-word">
        {words.map((word, i) => {
          const isActive = i === index;
          // Apply 3D flips: incoming rolls from bottom (rotateX(90deg)), active is flat, outgoing dips backward (rotateX(-90deg))
          const rotation = isActive
            ? 'rotateX(0deg) translate3d(0,0,0)'
            : i === (index - 1 + words.length) % words.length
            ? 'rotateX(-90deg) translate3d(0,-10px,0)'
            : 'rotateX(90deg) translate3d(0,10px,0)';
            
          const opacity = isActive ? 1 : 0;
          const pointerEvents = isActive ? 'auto' : 'none';

          return (
            <span
              key={word}
              ref={(el) => (itemsRef.current[i] = el)}
              className="t-rolodex-item"
              style={{
                transform: rotation,
                opacity: opacity,
                pointerEvents: pointerEvents,
                color: 'var(--color-ghost-white)',
                fontWeight: 'var(--font-weight-light)',
                fontFamily: 'var(--font-heading)',
                fontSize: 'inherit',
                transition: 'transform 0.6s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.4s ease'
              }}
            >
              {word}
            </span>
          );
        })}
      </div>
    </div>
  );
};

export default RolodexFlipper;
