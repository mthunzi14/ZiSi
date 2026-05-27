# The ZiSi. Obsidian-Gold Design System
## Premium Design Manual & Token Specification for High-Fidelity Web Interfaces

> [!NOTE]
> Copy this file directly to any project directory to reproduce the exact **ZiSi.** high-fidelity, editorial Obsidian-Gold visual identity from scratch. This manual is tailored with specific guidelines for porting this aesthetic to a **Global Logistics & Maritime Fleet Control Suite**.

---

## 1. DESIGN PHILOSOPHY

The **ZiSi.** design system is founded on the concept of **"Minimalist Editorial Premium"**—combining the clean layout principles of luxury print magazines with high-frequency, dark-obsidian prediction interfaces. It rejects flat, boring corporate UI in favor of rich depth, glowing micro-animations, and striking high-contrast typographic layouts.

### Core Principles
1. **Obsidian & Gold Contrast:** A sleek base of dark slate and carbon blacks illuminated by premium, metallic, glowing electric gold accents (`#c59b27`).
2. **Depth Through Shadow Layering:** Layering elements through obsidian-tinted drop shadows rather than heavy borders, keeping layouts clean and organic.
3. **Glassmorphic Surfaces:** Elevating active components with satin-like frosted glass surfaces that allow under-lying ambient glows to filter through.
4. **Cinematic Motion:** Motion is deliberate, utilizing ease-out cubic bezier curves (`cubic-bezier(0.16, 1, 0.3, 1)`) to ensure elements arrive smoothly and with elegance.
5. **Bold Typography:** Bold displays, high-tracking subheadings, and distinct font weights do the heavy lifting of establishing layout hierarchy.

---

## 2. BRAND COLOR SYSTEM

The color palette consists of deep dark tones (Obsidian, Ink, Carbon) paired with soft gold metallic accents and high-fidelity glows.

### Core Color Tokens (CSS Custom Properties)
```css
:root {
  /* Dark / Background Palette */
  --color-obsidian:       #0c0c0e;   /* Base page background */
  --color-ink:            #121214;   /* Card surfaces, panels */
  --color-carbon:         #18181c;   /* Elevated panels, input fills */
  --color-graphite:       #28282d;   /* Secondary card states, hover fills */
  
  /* Text Palette */
  --color-snow:           #ffffff;   /* Primary bold text */
  --color-cream-light:    #fafaf8;   /* Default body text */
  --color-iron:           #8e8e93;   /* Secondary, muted text */
  --color-slate-grey:     #48484a;   /* Placeholders, disabled states */
  
  /* Brand Accents */
  --color-accent:         #c59b27;   /* Electric Gold */
  --color-gold-bright:    #f5b041;   /* Hover gold highlights */
  --color-gold-glow:      rgba(197, 155, 39, 0.25); /* Glow highlights */
  
  /* Borders */
  --color-border:         #242428;   /* Default card borders */
  --color-border-subtle:  #1e1e21;   /* Inner separations */
}
```

### Light Mode Adaptive Palette
When adapting to light mode, colors swap to elegant cream and paper-like texturings:
```css
[data-theme="light"] {
  --color-obsidian:       #fafaf8;   /* Paper background */
  --color-ink:            #ffffff;   /* Pure white card surfaces */
  --color-carbon:         #f5f5f0;   /* Section backgrounds, inputs */
  --color-graphite:       #ebebe0;   /* Hover states */
  
  --color-snow:           #09090b;   /* Obsidian bold text */
  --color-cream-light:    #2b2b2d;   /* Secondary graphite body */
  --color-iron:           #6b6b70;   /* Muted text */
  
  --color-accent:         #c59b27;   /* Gold accent remains constant */
  --color-border:         #eaeaea;   
  --color-border-subtle:  #f0f0ed;
}
```

### Functional / Semantic Indicators
Functional indicators are vibrant and pop against the dark canvas using distinct glow parameters:

| Indicator | Background | Color | Border | Glow Hex |
| :--- | :--- | :--- | :--- | :--- |
| **Success / Online** | `#0a2f1d` | `#10b981` | `#065f46` | `rgba(16,185,129,0.4)` |
| **Warning / Stale** | `#451a03` | `#f59e0b` | `#78350f` | `rgba(245,158,11,0.4)` |
| **Danger / Offline** | `#4c0519` | `#f43f5e` | `#9f1239` | `rgba(244,63,94,0.4)` |

---

## 3. TYPOGRAPHY & TYPESCALE

Typography is utilized heavily to establish luxury editorial branding.

### The Font Stack
- **Display Font (`--font-display`):** `'Outfit', 'Syne', system-ui, sans-serif` — Used for titles, heavy headers, metrics, and branding assets.
- **Primary / Body Font (`--font-primary`):** `'DM Sans', 'Inter', sans-serif` — Highly readable sans-serif optimized for UI labels, dense data grids, and tables.
- **Monospace Font (`--font-mono`):** `'Fira Code', 'SF Mono', monospace` — For quantitative rates, times, values, and coordinates.

### Typescale Tokens
```css
--text-display:     56px;  /* Huge titles & key metrics */
--text-h1:          36px;  /* Section headings */
--text-h2:          24px;  /* Card titles */
--text-h3:          18px;  /* Card sub-sections */
--text-body:        14px;  /* Default body, table rows */
--text-caption:     12px;  /* Labels, small helper text */
--text-badge:       11px;  /* Pills, statuses, active state labels */
```

---

## 4. RADIUS, SHADOWS, & DEPTH

### Border Radii
- **`--radius-sm`: `6px`** — Tags, inline badges, toggle buttons.
- **`--radius-md`: `12px`** — Input fields, select panels, buttons.
- **`--radius-lg`: `18px`** — Sidebar items, inside metrics containers.
- **`--radius-card`: `24px`** — Main dashboard widgets, charts, lists.
- **`--radius-full`: `9999px`** — Round icons, status dots, pull handles.

### The Obsidian Shadow System
Shadows use a deep, obsidian-tinted tone (`rgba(0, 0, 0, 0.45)`) that is organic and highly dimensional:
```css
--shadow-xs:      0 1px 2px rgba(0, 0, 0, 0.3);
--shadow-sm:      0 4px 12px rgba(0, 0, 0, 0.4), 0 2px 4px rgba(0, 0, 0, 0.2);
--shadow-md:      0 12px 28px rgba(0, 0, 0, 0.5), 0 4px 12px rgba(0, 0, 0, 0.25);
--shadow-lg:      0 24px 50px rgba(0, 0, 0, 0.6), 0 10px 20px rgba(0, 0, 0, 0.3);
--shadow-gold:    0 0 15px var(--color-gold-glow), 0 4px 20px rgba(0, 0, 0, 0.5);
```

---

## 5. GLOWING INTERACTION & TRANSITIONS

Aesthetics in the **ZiSi.** design system are highly interactive and responsive, reacting smoothly to cursors.

### 1. Conic Rotating Metallic Border (`rotate-border`)
This creates an electric, rotating gold metallic border around elements, perfect for primary CTA buttons or active metrics.

```css
@property --gradient-angle {
  syntax: "<angle>";
  initial-value: 0deg;
  inherits: false;
}

.rotate-border {
  position: relative;
  background: var(--color-ink);
  border-radius: var(--radius-md);
  padding: 1.5px; /* Border thickness */
  z-index: 1;
}

.rotate-border::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: 1.5px;
  background: conic-gradient(
    from var(--gradient-angle),
    transparent 20%,
    var(--color-accent) 50%,
    transparent 80%
  );
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  pointer-events: none;
  animation: border-spin 3s linear infinite;
  z-index: -1;
}

@keyframes border-spin {
  from { --gradient-angle: 0deg; }
  to { --gradient-angle: 360deg; }
}
```

### 2. Glassmorphism Backdrops (`glass-surface`)
Satin frosted-glass backing allows dynamic canvas shapes and glows to drift beneath elements.
```css
.glass-surface {
  background: rgba(18, 18, 20, 0.75);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.05);
  box-shadow: var(--shadow-md);
}
```

### 3. Active Gold Navigation Glow (`nav-active-glow`)
Puts a warm gold breath under active navigation items:
```css
.nav-active-glow {
  background: rgba(197, 155, 39, 0.08) !important;
  color: var(--color-snow) !important;
  box-shadow: inset 0 0 10px rgba(197, 155, 39, 0.1), 0 0 15px rgba(197, 155, 39, 0.05);
  border-left: 3px solid var(--color-accent);
}
```

### 4. Transition Curves
Transitions utilize smooth, decelerating ease curves:
- **Fast Interactive:** `all 150ms cubic-bezier(0.2, 0.8, 0.2, 1)` — Toggles, sliders, button taps.
- **Smooth Dashboard:** `all 300ms cubic-bezier(0.16, 1, 0.3, 1)` — Expanding cards, sidebars, panels.
- **Heavy Transitions:** `all 600ms cubic-bezier(0.16, 1, 0.3, 1)` — Tab switching, modal sliders, data refreshes.

---

## 6. LOGISTICS SYSTEM PORTING INDEX
### Direct Mapping Strategy: Porting to a Global Logistics Web App

To adapt the **ZiSi.** Obsidian-Gold design system for a global logistics, container tracking, and fleet operations dashboard, map the quantitative prediction assets directly to physical supply chain components:

```
┌───────────────────────────────────────┐
│           ZiSi. SYSTEM                │
└──────────────────┬────────────────────┘
                   │
                   ▼
┌───────────────────────────────────────┐
│     Global Logistics & Fleet          │
└───────────────────────────────────────┘
  ├── Assets (BTC/ETH/SOL)       ──► Ships & Cargo Vessels
  ├── Candles / Trade Intervals  ──► Port Schedules & Route Windows
  ├── Confidence (Score 1-10)    ──► Transit Delay Probability Index
  ├── Buy / Sell Signals         ──► Route Divergence / Fuel Save Action
  ├── Volatility ATR Regime      ──► Severe Weather Sea Regimes
  ├── Kelly Sizing Cap ($2)      ──► Fuel Optimization Speed Caps
  └── Retraining Pipeline Progress──► Vessel Telemetry Ingest Queue
```

### UI Implementation Mappings

#### 1. Vessel Overview Cards (Asset Card Analogy)
Use the glowing grid cards designed for ticker symbols (BTC/ETH) to represent **Active Cargo Vessels**:
- **Title Block:** `Vessel IMO Number` (e.g., `OOCL GERMANY`) in Syne bold display font.
- **Performance Sparkline:** Dynamic rolling fuel efficiency levels or sea swell frequencies.
- **Top Metrics Badge:** Replace active price metrics with the ETA countdown (e.g., `4d 12h to Rotterdam`) in Monospace format.
- **Dynamic Outer Borders:** Apply the `rotate-border` animation when a vessel enters a **Critical Delayed state** or high-swell severe weather zones.

#### 2. Maritime Transit Route Timers (Candle Boundary Analogy)
The ticking candle timers (5m/15m) are mapped to **Port Schedule Deadlines & Tide Windows**:
- Represent lock-closure windows, unloading berth availabilities, or customs clearance lockups.
- Circular ticking indicator counts down remaining minutes until the berth slot expires.
- **Status:** Amber glow if the ship is stale/late, red pulse if the tide window is missed completely.

#### 3. Transit Delay Probability (AI Confidence Score Analogy)
The AI Sentiment Cascade (Score 1-10) translates into a **Real-Time Route Delay Probability Indicator**:
- Instead of showing Bullish/Bearish trade sentiments, display the likelihood of sea congestion or labor disruption.
- A score of `8.5` indicates an 85% probability of route delay.
- The indicator is surrounded by a soft golden halo (`box-shadow: 0 0 15px rgba(197, 155, 39, 0.15)`) representing the high priority of shipping managers to re-route immediately.

#### 4. Weather Sea Regime (Volatility ATR Analogy)
The Volatility Regime radar (`NORMAL` / `TURBULENT`) translates into **Sea Condition Alert HUDs**:
- **NORMAL State (Green Glow):** Calm sea swells under 2.0 meters, standard fuel-saving speeds.
- **TURBULENT State (Vibrant Amber/Gold Blink):** Severe offshore weather swell exceeding 6.0 meters. The Kelly Bet modifier adjusts into a **Vessel Throttle Sizer** to scale vessel RPM to 60%, preventing machinery stress and cargo damage.

#### 5. Fleet Porting Ledger (Trade Ledger Analogy)
The scrolling Trade ledger is ported into a **Global Cargo Shipping Manifest**:
- Active rows represent container cargo entries.
- Columns map: `Carrier Line`, `Container ID` (in gold Monospace), `Origin`, `Destination`, `Status` (Frosted success badges for `Delivered`, pulsing amber warning badges for `Customs Hold`).
- Rows enter with the smooth `.page-fade-enter` layout fade, establishing the premium flow.

---

## 7. CODE SNIPPETS FOR LOGISTICS DASHBOARDS

Below is a copy-pasteable React card structure utilizing this design system for a shipping vessel card:

```jsx
import React from 'react';
import './design.css'; // containing the above CSS tokens

export default function CargoVesselCard({ vessel }) {
  const { name, eta, fuelStatus, status, riskLevel, route } = vessel;
  const isDelayed = status === 'delayed';

  return (
    <div className={`card ${isDelayed ? 'rotate-border' : 'glass-surface'}`} style={{ padding: '24px', borderRadius: '24px', position: 'relative' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
        <div>
          <span style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: '20px', color: 'var(--color-snow)', letterSpacing: '-0.02em' }}>
            {name}
          </span>
          <div style={{ fontSize: '11px', color: 'var(--color-iron)', letterSpacing: '0.05em', textTransform: 'uppercase', marginTop: '2px' }}>
            Route: {route}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span 
            className={status === 'active' ? 'alert-pulse' : ''} 
            style={{
              width: '10px',
              height: '10px',
              borderRadius: '50%',
              backgroundColor: status === 'active' ? '#10b981' : '#f43f5e',
              display: 'inline-block'
            }} 
          />
          <span style={{ fontSize: '12px', fontWeight: '600', color: status === 'active' ? '#10b981' : '#f43f5e', textTransform: 'uppercase' }}>
            {status}
          </span>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginTop: '20px' }}>
        <div style={{ borderRight: '1px solid var(--color-border)', paddingRight: '12px' }}>
          <div style={{ fontSize: '11px', color: 'var(--color-iron)', textTransform: 'uppercase' }}>ETA Window</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '14px', fontWeight: '700', color: 'var(--color-accent)', marginTop: '4px' }}>
            {eta}
          </div>
        </div>
        <div style={{ paddingLeft: '4px' }}>
          <div style={{ fontSize: '11px', color: 'var(--color-iron)', textTransform: 'uppercase' }}>Fuel Swell</div>
          <div style={{ fontSize: '14px', fontWeight: '600', color: 'var(--color-snow)', marginTop: '4px' }}>
            {fuelStatus}
          </div>
        </div>
      </div>

      {isDelayed && (
        <div style={{ marginTop: '16px', padding: '10px', background: 'rgba(244, 63, 94, 0.1)', border: '1px solid rgba(244, 63, 94, 0.2)', borderRadius: '12px', fontSize: '12px', color: '#f43f5e' }}>
          <strong>Delay Risk Detected:</strong> Storm swell of 7.2m near route coordinate.
        </div>
      )}
    </div>
  );
}
```
