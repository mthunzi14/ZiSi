import React, { useEffect, useRef } from 'react';

/**
 * Simple 2D Perlin-like noise helper for organic electric bolts
 */
class SimpleNoise {
  constructor() {
    this.grad3 = [
      [1,1,0],[-1,1,0],[1,-1,0],[-1,-1,0],
      [1,0,1],[-1,0,1],[1,0,-1],[-1,0,-1],
      [0,1,1],[0,-1,1],[0,1,-1],[0,-1,-1]
    ];
    this.p = Array.from({ length: 256 }, () => Math.floor(Math.random() * 256));
    this.permutation = [...this.p, ...this.p];
  }

  dot(g, x, y) {
    return g[0] * x + g[1] * y;
  }

  noise(xin, yin) {
    let n0, n1, n2;
    const F2 = 0.5 * (Math.sqrt(3.0) - 1.0);
    const s = (xin + yin) * F2;
    const i = Math.floor(xin + s);
    const j = Math.floor(yin + s);
    const G2 = (3.0 - Math.sqrt(3.0)) / 6.0;
    const t = (i + j) * G2;
    const X0 = i - t;
    const Y0 = j - t;
    const x0 = xin - X0;
    const y0 = yin - Y0;
    
    let i1, j1;
    if (x0 > y0) {
      i1 = 1; j1 = 0;
    } else {
      i1 = 0; j1 = 1;
    }
    
    const x1 = x0 - i1 + G2;
    const y1 = y0 - j1 + G2;
    const x2 = x0 - 1.0 + 2.0 * G2;
    const y2 = y0 - 1.0 + 2.0 * G2;
    
    const ii = i & 255;
    const jj = j & 255;
    
    const gi0 = this.permutation[ii + this.permutation[jj]] % 12;
    const gi1 = this.permutation[ii + i1 + this.permutation[jj + j1]] % 12;
    const gi2 = this.permutation[ii + 1 + this.permutation[jj + 1]] % 12;
    
    let t0 = 0.5 - x0 * x0 - y0 * y0;
    if (t0 < 0) n0 = 0.0;
    else {
      t0 *= t0;
      n0 = t0 * t0 * this.dot(this.grad3[gi0], x0, y0);
    }
    
    let t1 = 0.5 - x1 * x1 - y1 * y1;
    if (t1 < 0) n1 = 0.0;
    else {
      t1 *= t1;
      n1 = t1 * t1 * this.dot(this.grad3[gi1], x1, y1);
    }
    
    let t2 = 0.5 - x2 * x2 - y2 * y2;
    if (t2 < 0) n2 = 0.0;
    else {
      t2 *= t2;
      n2 = t2 * t2 * this.dot(this.grad3[gi2], x2, y2);
    }
    
    return 70.0 * (n0 + n1 + n2);
  }
}

/**
 * ElectricBorder component wraps any content with an animated, glowing procedural canvas border.
 */
export const ElectricBorder = ({
  children,
  color = '#bbdef2', // Midnight Command iridescent default
  speed = 1.0,
  chaos = 0.15,
  borderRadius = 8,
  className = '',
  style = {}
}) => {
  const containerRef = useRef(null);
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const ctx = canvas.getContext('2d');
    const simpleNoise = new SimpleNoise();
    let animId = null;
    let t = 0;

    const resize = () => {
      const rect = container.getBoundingClientRect();
      canvas.width = rect.width;
      canvas.height = rect.height;
    };

    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(container);

    const drawBolt = (x1, y1, x2, y2, time) => {
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      
      const dx = x2 - x1;
      const dy = y2 - y1;
      const dist = Math.sqrt(dx * dx + dy * dy);
      const steps = Math.max(10, Math.floor(dist / 6));
      
      for (let i = 1; i < steps; i++) {
        const k = i / steps;
        const cx = x1 + dx * k;
        const cy = y1 + dy * k;
        
        // Compute perpendicular offset using Perlin-like noise
        const nx = -dy / dist;
        const ny = dx / dist;
        
        const noiseVal = simpleNoise.noise(cx * 0.03, cy * 0.03 + time * 0.005 * speed);
        const offset = noiseVal * dist * chaos;
        
        ctx.lineTo(cx + nx * offset, cy + ny * offset);
      }
      
      ctx.lineTo(x2, y2);
      ctx.stroke();
    };

    const draw = () => {
      t += 1;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      
      const w = canvas.width;
      const h = canvas.height;
      const r = borderRadius;

      if (w === 0 || h === 0) {
        animId = requestAnimationFrame(draw);
        return;
      }

      ctx.save();
      ctx.lineWidth = 1.8;
      ctx.strokeStyle = color;
      
      // Shadow glow path
      ctx.shadowBlur = 10;
      ctx.shadowColor = color;
      
      // Define path segments wrapping the container
      // Segment 1: Top-Left to Top-Right
      drawBolt(r, 0, w - r, 0, t);
      
      // Segment 2: Top-Right to Bottom-Right
      drawBolt(w, r, w, h - r, t);
      
      // Segment 3: Bottom-Right to Bottom-Left
      drawBolt(w - r, h, r, h, t);
      
      // Segment 4: Bottom-Left to Top-Left
      drawBolt(0, h - r, 0, r, t);
      
      ctx.restore();
      animId = requestAnimationFrame(draw);
    };

    draw();

    return () => {
      if (animId) cancelAnimationFrame(animId);
      ro.disconnect();
    };
  }, [color, speed, chaos, borderRadius]);

  return (
    <div
      ref={containerRef}
      className={`relative inline-block ${className}`}
      style={{ borderRadius: `${borderRadius}px`, ...style }}
    >
      {/* Canvas electrical backdrop */}
      <canvas
        ref={canvasRef}
        style={{
          position: 'absolute',
          inset: 0,
          pointerEvents: 'none',
          zIndex: 0,
          borderRadius: `${borderRadius}px`
        }}
      />
      {/* Children content wrapper */}
      <div style={{ position: 'relative', zIndex: 1 }}>
        {children}
      </div>
    </div>
  );
};

export default ElectricBorder;
