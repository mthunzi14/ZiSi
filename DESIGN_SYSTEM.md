# FutureDesk Design System
## Complete Reference — Every Token, Component, Animation & Effect

> Copy this file to any project to reproduce the exact FutureDesk visual identity from scratch.

---

## 1. PHILOSOPHY

**Aesthetic:** Minimalist Editorial Premium. Black on cream. No gradients on text. No colour other than black, white, cream, and functional semantic colours (green for success, red for error, amber for warning). Motion is calm and purposeful — never decorative noise.

**Principles:**
- Organic softness over harsh geometry
- Depth through shadow layering, never colour contrast
- Motion decelerates (ease-out cubic-bezier) — things arrive, not snap
- Typography does heavy lifting — weight and tracking > colour
- White space is a design element, not empty space

---

## 2. COLOUR PALETTE

### Base Palette (CSS Custom Properties)

```css
:root {
  --color-cream-light:  #fafaf8;   /* page background */
  --color-cream-deep:   #f5f5f0;   /* section backgrounds, input fills */
  --color-cream-dark:   #ebebe0;   /* hover states, badges */
  --color-obsidian:     #09090b;   /* primary text, primary buttons, borders */
  --color-ink:          #111112;   /* button hover — 1 step darker than obsidian */
  --color-graphite:     #2b2b2d;   /* secondary text, captions */
  --color-iron:         #6b6b70;   /* muted text, placeholder, inactive nav */
  --color-mist:         #d1d1d6;   /* scrollbar thumb, dividers */
  --color-snow:         #ffffff;   /* card surfaces, modals */
}
```

### Semantic Aliases

```css
--color-bg:             #fafaf8;   /* body background */
--color-surface:        #ffffff;   /* card / panel surface */
--color-border:         #eaeaea;   /* default card borders */
--color-border-subtle:  #f0f0ed;   /* inner borders, section separators */
--color-text-primary:   #09090b;
--color-text-secondary: #2b2b2d;
--color-text-muted:     #6b6b70;
--color-accent:         #09090b;   /* accent = obsidian — monochrome brand */
```

### Tailwind Colour Tokens

```ts
colors: {
  obsidian: '#09090b',
  ink:      '#18181b',
  graphite: '#3f3f46',
  iron:     '#71717a',
  mist:     '#d4d4d8',
  fog:      '#f4f4f5',
  snow:     '#ffffff',
  // Aliases
  ash:    '#71717a',
  slate:  '#3f3f46',
  ghost:  '#f4f4f5',
  canvas: '#ffffff',
}
```

### Functional / Semantic Colours

| Purpose     | Background  | Text      | Border    |
|-------------|-------------|-----------|-----------|
| Success     | `#f0fdf4`   | `#16a34a` | `#bbf7d0` |
| Warning     | `#fffbeb`   | `#92400e` | `#fde68a` |
| Error       | `#fef2f2`   | `#dc2626` | `#fecaca` |
| Info        | `#eff6ff`   | `#2563eb` | `#bfdbfe` |
| Neutral     | `#f4f4f5`   | `#71717a` | `#d4d4d8` |
| Green CTA   | `#22c55e`   | `#ffffff` | —         |
| Green hover | `#16a34a`   | `#ffffff` | —         |

---

## 3. TYPOGRAPHY

### Font Stack

```html
<!-- Load these from Google Fonts in <head> -->
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800;900&family=Syne:wght@700;800&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;1,9..40,400&display=swap" rel="stylesheet" />
```

| Variable         | Value                                      | Use                          |
|------------------|--------------------------------------------|------------------------------|
| `--font-primary` | `'Outfit', 'DM Sans', system-ui, sans`    | Body, UI, labels             |
| `--font-display` | `'Syne', 'Outfit', sans`                  | Hero headlines, logo, large numbers |

> **DM Sans** is used as the dashboard UI font (sidebar, table rows, form labels) and as a fallback everywhere.

### Type Scale

```css
--text-display:     56px;   /* Hero h1 */
--text-heading-lg:  38px;   /* Section titles */
--text-heading:     30px;   /* Card headings */
--text-heading-sm:  22px;   /* Sub-headings */
--text-subheading:  18px;   /* Large body / callouts */
--text-body:        15px;   /* Default body */
--text-caption:     13px;   /* Labels, badges, meta */
```

### Heading Classes

```css
.heading-display {
  font-family: Outfit;
  font-size: 56px;
  font-weight: 700;
  line-height: 1.05;
  letter-spacing: -0.03em;
  color: #09090b;
}

.heading-lg {
  font-size: 38px; font-weight: 700;
  line-height: 1.1; letter-spacing: -0.02em;
}

.heading {
  font-size: 30px; font-weight: 700;
  line-height: 1.2; letter-spacing: -0.02em;
}

.heading-sm {
  font-size: 22px; font-weight: 600;
  line-height: 1.3; letter-spacing: -0.01em;
}
```

### Landing Page Hero Specific

```tsx
// h1 — 44px mobile / 64px desktop
className="text-[44px] md:text-[64px] font-black tracking-tight leading-[1.02] text-[#09090b] font-display"

// Italic muted word (morphing text slot)
className="text-[#6b6b70] italic"

// Section label pill
className="text-[12px] font-bold uppercase tracking-wider text-[#6b6b70] bg-[#ebebe0] px-3 py-1 rounded-full"

// Section heading
className="text-[32px] md:text-[40px] font-black tracking-tight text-[#09090b] font-display mt-4"

// Body paragraph
className="text-[16px] md:text-[18px] text-[#2b2b2d] leading-relaxed max-w-[520px] font-sans"
```

### Logo Mark

```tsx
// FD. wordmark — used everywhere
<span className="font-black text-[32px] md:text-[36px] tracking-tighter text-black font-display select-none leading-none">
  FD.
</span>
```

---

## 4. SPACING SYSTEM

```css
--spacing-4:    4px;
--spacing-8:    8px;
--spacing-12:   12px;
--spacing-16:   16px;
--spacing-20:   20px;
--spacing-24:   24px;
--spacing-28:   28px;
--spacing-32:   32px;
--spacing-40:   40px;
--spacing-48:   48px;
--section-gap:  56px;   /* between major sections */
--card-padding: 32px;   /* internal card padding */
```

---

## 5. BORDER RADIUS

```css
--radius-sm:    8px;     /* small inputs, chips */
--radius-md:    14px;    /* inputs, small buttons */
--radius-lg:    20px;    /* nav items, small cards */
--radius-cards: 24px;    /* standard cards */
--radius-hero:  32px;    /* hero cards, CTAs, dark banners */
--radius-full:  9999px;  /* pills, full-round buttons, badges */
```

Tailwind extensions:
```ts
borderRadius: {
  card: '20px',
  hero: '28px',
}
```

---

## 6. SHADOW SYSTEM

```css
--shadow-xs:      0 1px 2px rgba(9,9,11,0.02);
--shadow-sm:      0 3px 12px rgba(9,9,11,0.03), 0 1px 2px rgba(9,9,11,0.02);
--shadow-md:      0 8px 24px rgba(9,9,11,0.04), 0 2px 8px rgba(9,9,11,0.02);
--shadow-lg:      0 16px 48px rgba(9,9,11,0.06), 0 4px 16px rgba(9,9,11,0.03);
--shadow-premium: 0px 30px 60px rgba(0,0,0,0.04),
                  0px 4px 10px rgba(0,0,0,0.01),
                  inset 0 1px 0 rgba(255,255,255,0.6);
```

> All shadows use `rgba(9,9,11,…)` — obsidian-tinted, never grey. Opacity stays under 0.08 for all non-modal shadows. This keeps depth organic and non-intrusive.

---

## 7. BUTTONS

### Primary Button

```css
.btn-primary {
  display: inline-flex; align-items: center; justify-content: center;
  gap: 8px; padding: 10px 20px;
  font-weight: 500; font-size: 14px;
  background: #09090b; color: #ffffff;
  border-radius: 9999px; border: none;
  transition: all 150ms ease;
  box-shadow: 0 1px 2px rgba(9,9,11,0.02);
}
.btn-primary:hover {
  background: #111112;
  transform: translateY(-1px);
  box-shadow: 0 3px 12px rgba(9,9,11,0.03), 0 1px 2px rgba(9,9,11,0.02);
}
.btn-primary:active { transform: translateY(0); }
```

### Glow Primary Button (Landing Page CTAs)

```css
.glow-btn-primary {
  /* extends btn-primary */
  background: linear-gradient(135deg, #09090b 0%, #1e1e24 100%);
  transition: all 350ms cubic-bezier(0.16, 1, 0.3, 1);
  box-shadow: 0 4px 15px rgba(9,9,11,0.15), inset 0 1px 0 rgba(255,255,255,0.1);
}
.glow-btn-primary:hover {
  background: linear-gradient(135deg, #18181b 0%, #09090b 100%);
  box-shadow: 0 8px 25px rgba(9,9,11,0.22), 0 0 8px rgba(9,9,11,0.08);
  transform: translateY(-2px);
}
```

### LightBeamButton (Spinning Conic Border CTA)

The premium animated CTA button with a continuously rotating gradient border.

**How it works:**
- Uses CSS `@property --gradient-angle` (Houdini) to animate a custom property
- A `conic-gradient` rotates 360° at `2.5s linear infinite`
- Three layers: (1) spinning gradient ring at `inset: 0`, (2) solid fill at `inset: 1.5px`, (3) hover radial glow overlay

```tsx
// Props
interface LightBeamButtonProps {
  children: React.ReactNode;
  variant?: 'dark' | 'light';        // dark = black bg, light = white bg
  gradientColors?: [string, string, string]; // default: ['#fff', '#888', '#fff']
  className?: string;
  // + all standard button HTML attrs
}

// Dark variant (on light backgrounds)
<LightBeamButton variant="dark">Get Active Sandbox</LightBeamButton>

// Light variant (on dark backgrounds like black CTA banners)
<LightBeamButton variant="light">Create Sandbox Trial</LightBeamButton>
```

**CSS internals:**

```css
@property --gradient-angle {
  syntax: "<angle>";
  initial-value: 0deg;
  inherits: false;
}
@keyframes fd-border-spin {
  from { --gradient-angle: 0deg; }
  to   { --gradient-angle: 360deg; }
}
.fd-animate-border-spin {
  animation: fd-border-spin 2.5s linear infinite;
}
```

**Mouse interactions (inline JS):**
```
mouseenter → translateY(-1px)
mouseleave → translateY(0)
mousedown  → translateY(0) scale(0.98)
mouseup    → translateY(-1px)
```

**Shadow:** `0 4px 20px rgba(9,9,11,0.12), 0 1px 4px rgba(9,9,11,0.08)`

### Ghost Button

```css
.btn-ghost {
  background: #ffffff; color: #09090b;
  border: 1px solid #eaeaea;
  border-radius: 14px;
  transition: all 150ms ease;
}
.btn-ghost:hover { background: #f4f4f5; border-color: #d1d1d6; }
```

### Danger Button

```css
.btn-danger {
  background: #fef2f2; color: #dc2626;
  border: 1px solid #fecaca; border-radius: 14px;
}
.btn-danger:hover { background: #fee2e2; }
```

---

## 8. CARDS

### Standard Card

```css
.card {
  background: #ffffff;
  border-radius: 24px;
  padding: 32px;
  border: 1px solid #f0f0ed;
  box-shadow: 0 3px 12px rgba(9,9,11,0.03), 0 1px 2px rgba(9,9,11,0.02);
}

.card-sm {
  background: #ffffff;
  border-radius: 20px;
  padding: 16px 20px;
  border: 1px solid #f0f0ed;
  box-shadow: 0 1px 2px rgba(9,9,11,0.02);
}
```

### Dark Card (CTA sections)

```css
/* Used for main CTA banners and "Unlimited Pro" pricing */
background: #09090b;
border-radius: 32px;
padding: 48px;
box-shadow: 0px 30px 60px rgba(0,0,0,0.04), 0px 4px 10px rgba(0,0,0,0.01);
/* Inner radial glow overlay */
background: radial-gradient(circle, rgba(255,255,255,0.04) 0%, transparent 70%);
```

---

## 9. GLASSMORPHISM

### Light Glass (Hero visual, status badges)

```css
.glass {
  background: rgba(255, 255, 255, 0.45);
  backdrop-filter: blur(20px) saturate(190%);
  -webkit-backdrop-filter: blur(20px) saturate(190%);
  border: 1px solid rgba(255, 255, 255, 0.6);
  box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.02);
}
```

### Premium Glass (Elevated surfaces)

```css
.glass-premium {
  background: rgba(250, 250, 248, 0.7);
  backdrop-filter: blur(24px);
  border: 1px solid rgba(255, 255, 255, 0.75);
  box-shadow: 0px 30px 60px rgba(0,0,0,0.04), 0px 4px 10px rgba(0,0,0,0.01), inset 0 1px 0 rgba(255,255,255,0.6);
}
```

### Dark Glass (On dark backgrounds, demo cards)

```css
.glass-dark {
  background: rgba(9, 9, 11, 0.85);
  backdrop-filter: blur(20px);
  border: 1px solid rgba(255, 255, 255, 0.08);
  box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
}
```

---

## 10. BORDER EFFECTS

### Liquid Border (Light cards — shimmer sweep)

Animated shimmer sweeps across card borders continuously.

```css
.liquid-border {
  position: relative;
  transition: all 300ms cubic-bezier(0.16, 1, 0.3, 1);
}
.liquid-border::before {
  content: '';
  position: absolute; inset: -1px;
  border-radius: inherit; padding: 1px;
  background: linear-gradient(90deg,
    rgba(9,9,11,0.04),
    rgba(9,9,11,0.22),
    rgba(9,9,11,0.04)
  );
  background-size: 200% 200%;
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  pointer-events: none;
  animation: shimmer 5s infinite linear;
}
.liquid-border:hover { transform: translateY(-2px); box-shadow: 0 10px 25px rgba(9,9,11,0.08); }
```

### Liquid Border Dark (Dark buttons/elements — silver shimmer)

```css
.liquid-border-dark::before {
  background: linear-gradient(90deg,
    rgba(255,255,255,0.12),
    rgba(255,255,255,0.48),
    rgba(255,255,255,0.12)
  );
  background-size: 200% 200%;
  animation: shimmer 3s infinite linear;
}
```

### Metal Border (Dark premium card — Unlimited Pro)

```css
.metal-border {
  background: linear-gradient(135deg, #09090b 0%, #1c1c1f 50%, #09090b 100%);
  background-size: 200% 200%;
  box-shadow: 0 4px 15px rgba(9,9,11,0.06), inset 0 1px 0 rgba(255,255,255,0.08);
}
.metal-border::before {
  /* white shimmer border ring */
  background: linear-gradient(90deg,
    rgba(255,255,255,0.08),
    rgba(255,255,255,0.28),
    rgba(255,255,255,0.08)
  );
  background-size: 200% 200%;
  animation: shimmer 6s infinite linear;
}
.metal-border:hover {
  box-shadow: 0 12px 30px rgba(9,9,11,0.18), 0 0 10px rgba(9,9,11,0.08), inset 0 1px 0 rgba(255,255,255,0.15);
  transform: translateY(-2px);
}
```

### Border Glow (Cards — glow on hover only)

```css
.border-glow {
  position: relative;
  transition: all 300ms cubic-bezier(0.16, 1, 0.3, 1);
}
.border-glow::before {
  content: '';
  position: absolute; inset: -1px;
  border-radius: inherit;
  background: linear-gradient(135deg,
    rgba(9,9,11,0.06) 0%,
    rgba(9,9,11,0.18) 50%,
    rgba(9,9,11,0.06) 100%
  );
  background-size: 200% 200%;
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  animation: shimmer 6s infinite linear;
  opacity: 0;
  transition: opacity 300ms ease;
}
.border-glow:hover::before { opacity: 1; }
.border-glow:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 32px rgba(9,9,11,0.08);
}
```

### ElectricBorder (Canvas Perlin Noise — premium component)

Draws a procedurally animated electric border via `<canvas>` using octaved Perlin/FBM noise.

```tsx
<ElectricBorder
  color="rgba(255,255,255,0.5)"  // border stroke colour
  speed={0.6}                     // animation speed multiplier
  chaos={0.08}                    // noise amplitude (0.04–0.2)
  borderRadius={16}               // px corner radius
  className=""
  style={{}}
>
  <YourContent />
</ElectricBorder>
```

**Internals:**
- Canvas offset: `borderOffset = 60px` beyond element edges
- Samples: `perimeter / 2` points along rounded-rect path
- Octaves: 10, lacunarity: 1.6, gain: 0.7
- Draws: 1.5px stroke + 3 glow overlay divs (blur 1px, blur 3px, scale-110 opacity-0.15 background gradient)
- ResizeObserver for responsive reflow
- Uses `hexToRgba()` utility for alpha overlays

**Glow layers (CSS only, always on):**
```css
/* Layer 1 — soft static border */
border: 1.5px solid rgba(color, 0.3); filter: blur(1px);
/* Layer 2 — vivid static border */
border: 1.5px solid color; filter: blur(3px);
/* Layer 3 — ambient halo */
filter: blur(24px);
background: linear-gradient(-30deg, color, transparent, color);
scale: 1.1; opacity: 0.15;
```

---

## 11. HOVER EFFECTS

| Class | Behaviour |
|-------|-----------|
| `.glow-hover` | `translateY(-2px)` + soft shadow `0 0 25px rgba(9,9,11,0.08)` at 350ms cubic-bezier |
| `.card-lift` | `translateY(-4px)` + `0 20px 48px rgba(9,9,11,0.08)` at 300ms |
| `.liquid-border:hover` | `translateY(-2px)` + `0 10px 25px rgba(9,9,11,0.08)` |
| `.border-glow:hover` | `translateY(-2px)` + shimmer overlay fades in |
| `.metal-border:hover` | `translateY(-2px)` + deeper shadow |
| `.pill-glow:hover` | blurred glow ring fades in around pill via `::after` |
| `.LightBeamButton:hover` | `translateY(-1px)` + hover background shift |
| `.LightBeamButton:active` | `scale(0.98)` |
| Nav links (sidebar) | `cubic-bezier(0.16,1,0.3,1)` 180ms — bg → `#f4f4f5`, colour → obsidian |
| Auth form inputs `:focus` | `border-color: #09090b` + `box-shadow: 0 0 0 3px rgba(9,9,11,0.08)` |

### Transition Easing Convention

All transitions use one of three curves:
```
Fast UI:    150ms ease                          — button states, toggles
Standard:   300ms cubic-bezier(0.16, 1, 0.3, 1) — cards, links, nav
Springy:    350ms cubic-bezier(0.16, 1, 0.3, 1) — hero CTAs, marquee pause
Entrance:   0.6–0.8s cubic-bezier(0.16, 1, 0.3, 1) — reveals, page transitions
```

---

## 12. ANIMATION LIBRARY

### Keyframes

```css
/* Page entrance */
@keyframes pageFadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* Card/section reveal with blur */
@keyframes revealUp {
  from { opacity: 0; transform: translateY(32px); filter: blur(4px); }
  to   { opacity: 1; transform: translateY(0); filter: blur(0); }
}

/* Horizontal reveal */
@keyframes revealLeft {
  from { opacity: 0; transform: translateX(-20px); }
  to   { opacity: 1; transform: translateX(0); }
}

/* Text blur-to-focus */
@keyframes blurFocus {
  from { opacity: 0; filter: blur(8px); transform: translateY(4px); }
  to   { opacity: 1; filter: blur(0); transform: translateY(0); }
}

/* SVG neural sphere */
@keyframes neuralPulse {
  0%, 100% { transform: scale(1) rotate(0deg); opacity: 0.85; filter: drop-shadow(0 0 15px rgba(9,9,11,0.08)); }
  50%       { transform: scale(1.06) rotate(180deg); opacity: 1; filter: drop-shadow(0 0 25px rgba(9,9,11,0.16)); }
}

/* Slow float (badges, hero visual) */
@keyframes floatSlow {
  0%, 100% { transform: translateY(0px) scale(1); }
  50%       { transform: translateY(-8px) scale(1.02); }
}

/* Shimmer sweep */
@keyframes shimmer {
  0%   { background-position: 0% 50%; }
  50%  { background-position: 100% 50%; }
  100% { background-position: 0% 50%; }
}

/* Background particle drift */
@keyframes floatCalm {
  0%   { transform: translate(0, 0) rotate(0deg); }
  33%  { transform: translate(25px, 40px) rotate(60deg); }
  66%  { transform: translate(-15px, 60px) rotate(120deg); }
  100% { transform: translate(5px, -20px) rotate(180deg); }
}

/* Error shake */
@keyframes shake {
  10%, 90% { transform: translate3d(-1px, 0, 0); }
  20%, 80% { transform: translate3d(2px, 0, 0); }
  30%, 50%, 70% { transform: translate3d(-4px, 0, 0); }
  40%, 60% { transform: translate3d(4px, 0, 0); }
}

/* Fade in (dropdowns, notifications) */
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(-4px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* Modal slide up */
@keyframes modalSlideUp {
  from { opacity: 0; transform: translateY(16px) scale(0.98); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}

/* Count-up entrance */
@keyframes countUp {
  from { opacity: 0; transform: translateY(20px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* Glow breathing (box-shadow pulse) */
@keyframes glowPulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(9,9,11,0); }
  50%       { box-shadow: 0 0 20px 4px rgba(9,9,11,0.08); }
}

/* Text shadow breathing */
@keyframes textGlow {
  0%, 100% { text-shadow: 0 0 0 rgba(9,9,11,0); }
  50%       { text-shadow: 0 0 12px rgba(9,9,11,0.15); }
}

/* Alert ring pulse (red) */
@keyframes alertPulse {
  0%   { box-shadow: 0 0 0 0 rgba(220,38,38,0.3); }
  70%  { box-shadow: 0 0 0 6px rgba(220,38,38,0); }
  100% { box-shadow: 0 0 0 0 rgba(220,38,38,0); }
}

/* Spinner (sign-out overlay) */
@keyframes spinCircle {
  to { transform: rotate(360deg); }
}

/* Hyper-speed loader wobble */
@keyframes speeder {
  0%   { transform: translate(2px,1px) rotate(0deg); }
  10%  { transform: translate(-1px,-3px) rotate(-1deg); }
  20%  { transform: translate(-2px,0px) rotate(1deg); }
  ...100% repeat
}

/* SVG stroke draw */
@keyframes dashDraw {
  to { stroke-dashoffset: 0; }
}

/* Slot machine flip */
@keyframes flipIn  { from { transform: rotateX(90deg); opacity: 0; } to { transform: rotateX(0deg); opacity: 1; } }
@keyframes flipOut { from { transform: rotateX(0deg); opacity: 1; } to { transform: rotateX(-90deg); opacity: 0; } }

/* Spotlight clip-path expand */
@keyframes spotlightExpand {
  from { clip-path: circle(0% at 50% 50%); }
  to   { clip-path: circle(150% at 50% 50%); }
}

/* Progress bar sweep */
@keyframes progressSweep {
  0%   { transform: translateX(-100%); }
  50%  { transform: translateX(50%); }
  100% { transform: translateX(200%); }
}

/* Marquee infinite scroll */
@keyframes marqueeScroll {
  from { transform: translateX(0); }
  to   { transform: translateX(-50%); }
}
```

### Utility Classes

```css
.page-fade-enter    { animation: pageFadeIn 0.4s cubic-bezier(0.16,1,0.3,1) forwards; }
.reveal-up          { animation: revealUp 0.7s cubic-bezier(0.16,1,0.3,1) both; }
.reveal-left        { animation: revealLeft 0.5s cubic-bezier(0.16,1,0.3,1) both; }
.text-focus         { animation: blurFocus 0.8s cubic-bezier(0.16,1,0.3,1) both; }
.stat-counter       { animation: countUp 0.6s cubic-bezier(0.16,1,0.3,1) both; }
.glow-pulse         { animation: glowPulse 3s ease-in-out infinite; }
.text-glow-pulse    { animation: textGlow 3s ease-in-out infinite; }
.alert-pulse        { animation: alertPulse 2s ease-in-out infinite; }
.modal-enter        { animation: modalSlideUp 0.3s cubic-bezier(0.16,1,0.3,1) both; }
.spotlight-reveal   { animation: spotlightExpand 1.2s cubic-bezier(0.16,1,0.3,1) both; }
.progress-sweep     { animation: progressSweep 2.4s ease-in-out infinite; }
.svg-draw           { stroke-dasharray: 1000; stroke-dashoffset: 1000; animation: dashDraw 2s ease-in-out forwards; }
.loader-speeder     { animation: speeder 0.4s linear infinite; }
.neural-sphere-pulse { animation: neuralPulse 8s ease-in-out infinite; transform-origin: center; }
.floating-element   { animation: floatSlow 6s ease-in-out infinite; }
.animate-shake      { animation: shake 0.4s cubic-bezier(0.36,0.07,0.19,0.97) both; }
.animate-fade-in    { animation: fadeIn 0.3s cubic-bezier(0.16,1,0.3,1) forwards; }
```

### Stagger Delays

```css
.delay-100 { animation-delay: 0.1s; }
.delay-200 { animation-delay: 0.2s; }
.delay-300 { animation-delay: 0.3s; }
.delay-400 { animation-delay: 0.4s; }
.delay-500 { animation-delay: 0.5s; }
.delay-600 { animation-delay: 0.6s; }
.delay-700 { animation-delay: 0.7s; }
.delay-800 { animation-delay: 0.8s; }

/* Auto-stagger children */
.stagger-children > *:nth-child(1) { animation-delay: 0.05s; }
.stagger-children > *:nth-child(2) { animation-delay: 0.10s; }
.stagger-children > *:nth-child(3) { animation-delay: 0.15s; }
.stagger-children > *:nth-child(4) { animation-delay: 0.20s; }
.stagger-children > *:nth-child(5) { animation-delay: 0.25s; }
.stagger-children > *:nth-child(6) { animation-delay: 0.30s; }
```

---

## 13. MORPHING / SLOT-MACHINE HERO TEXT

The hero headline contains a word that cycles with blur-fade transition:

```tsx
const MORPH_WORDS = ['always on.', 'never sleeps.', '24/7 ready.', 'fluent in 11.', 'POPIA safe.'];
// State: wordIdx, wordVisible (boolean)
// Interval: every 2800ms → setWordVisible(false) → 300ms later → advance index → setWordVisible(true)

// CSS:
.morph-word {
  display: inline-block;
  transition: opacity 300ms ease, filter 300ms ease, transform 300ms ease;
}
.morph-word.hidden  { opacity: 0; filter: blur(8px); transform: translateY(6px); }
.morph-word.visible { opacity: 1; filter: blur(0);   transform: translateY(0); }
```

---

## 14. MARQUEE / INFINITE SCROLL

```css
.marquee-container {
  overflow: hidden;
  position: relative;
}
.marquee-track {
  display: flex;
  width: max-content;
  gap: 24px;
  animation: marqueeScroll 28s linear infinite;
}
.marquee-track:hover { animation-play-state: paused; }
/* Alpha edge masks */
-webkit-mask-image: linear-gradient(to right, transparent 0%, black 12%, black 88%, transparent 100%);
mask-image: linear-gradient(to right, transparent 0%, black 12%, black 88%, transparent 100%);
```

Cards inside marquee: `flex-shrink: 0; width: 300px;`
Content is **duplicated** (`[...ITEMS, ...ITEMS]`) so the loop is seamless.
GradualBlur components are positioned `left` and `right` as additional soft masks.

---

## 15. GRADUAL BLUR (Scroll Fade Component)

Multi-layer `backdrop-filter` blur that fades from 0 to full strength using `mask-image` per layer.

```tsx
<GradualBlur
  position="bottom"   // 'top' | 'bottom' | 'left' | 'right'
  strength={1.5}      // blur multiplier (px per layer)
  height="80px"       // size of blur zone
  divCount={5}        // number of blur layers
  exponential={false} // linear vs exponential falloff
  curve="linear"      // 'linear' | 'bezier' | 'ease-in' | 'ease-out' | 'ease-in-out'
  zIndex={5}
  opacity={1}
/>
```

Each layer `i` of `divCount` gets:
- `blurValue = 0.0625 * (progress * divCount + 1) * strength` rem
- `maskImage` with transparent → black → transparent band at its position
- `backdropFilter: blur(Xrem)`

---

## 16. GHOST CURSOR

Custom cursor overlay — rings and dot with lag effect.

```tsx
// Renders: outer ring (lags at 9% lerp) + inner dot (snaps)
// Ring: 40×40px, border 1.5px rgba(9,9,11,0.25), mixBlendMode multiply
// Dot: 6×6px, background #09090b
// Opacity: 0 until first mousemove, fades out on mouseleave
```

Implementation: `requestAnimationFrame` loop, `curX += (mouseX - curX) * 0.09` for lag.

**Only use on non-touch surfaces / desktop.** Apply to landing page root only.

---

## 17. COUNT-UP STATS

Triggered by `IntersectionObserver` at `threshold: 0.4`:

```tsx
function useCountUp(target: number, duration = 1800, active = false) {
  // Increments by (target / (duration / 16)) every 16ms rAF
  // Stops when count >= target
}

// Usage with StatCard component:
<StatCard value={50} suffix="+" label="SA Businesses" icon={Building} active={statsActive} />
```

Stat cards use `.reveal-up` class and an icon in a `w-12 h-12 rounded-2xl bg-[#f5f5f0] border border-[#ebebe0]` container.

---

## 18. SCROLL REVEAL

`IntersectionObserver` at `threshold: 0.15` adds `.is-visible` class:

```css
.reveal-up {
  opacity: 0;
  transform: translateY(28px);
  transition: opacity 0.6s ease, transform 0.6s ease;
}
.reveal-up.is-visible {
  opacity: 1;
  transform: translateY(0);
}
```

Apply `style={{ transitionDelay: `${idx * 0.1}s` }}` to stagger card grids.

---

## 19. FLOATING BACKGROUND SYSTEM

### Text Symbols (Fixed, full-page, z-[11])

14 symbols rendered at fixed positions with randomised animation durations (32–46s):
`{  }`, `</>`, `∞`, `AI`, `[ ]`, `⊕`, `01`, `~`, `⊗`, `( )`, `{}`, `→`, `∅`, `ML`

```css
.floating-bg-particle {
  position: absolute;
  pointer-events: none;
  opacity: 0.08;
  filter: blur(0.3px);
  color: #09090b;
  font-family: Syne, Outfit, sans; /* font-display font-black */
  animation: floatCalm 30s infinite ease-in-out alternate;
}
```

### SVG Background Shapes (Fixed, z-0)

6 SVG shapes drift behind all content:
1. **Drifting circle** — 300px `border border-[#09090b] rounded-full`, opacity 0.08
2. **Tech node grid** — 5-node network with dashed connecting lines, 140px
3. **Diamond/neural node** — octagonal polygon, 200px
4. **AI pulse waves** — dual sine waves with dashes, 240px
5. **Orbit rings** — circle + 2 rotated ellipses, 160px
6. **Domain icons** — Stethoscope (Healthcare), Scale (Legal), Building (Property), each ~90–100px

All use `animationDuration: 50s–70s`, `animationDelay: -6s to -25s`, `opacity: 0.08–0.12`.

---

## 20. DASHBOARD SIDEBAR

```
Width: 240px
Background: #ffffff
Border-right: 1px solid #e4e4e7
Font: DM Sans, system-ui, sans-serif
```

### Nav Items

```
Active state:
  background: #f4f4f5
  color: #09090b
  fontWeight: 600
  borderLeft: 3px solid #09090b
  boxShadow: 0 2px 8px rgba(9,9,11,0.06)

Inactive state:
  background: transparent
  color: #71717a
  fontWeight: 500
  borderLeft: 3px solid transparent

Hover (inactive):
  background: #f4f4f5
  color: #09090b
  transition: all 0.18s cubic-bezier(0.16, 1, 0.3, 1)

Icon: 16px, strokeWidth 2.5 (active) / 1.8 (inactive)
Padding: 9px 12px, borderRadius: 10px, gap: 10px, fontSize: 14px
```

### Active Nav Glow (CSS class `.nav-active-glow`)

```css
/* Left-edge glow indicator */
::after {
  position: absolute; left: 0; top: 50%;
  transform: translateY(-50%);
  width: 3px; height: 60%;
  border-radius: 0 2px 2px 0;
  background: #09090b;
  box-shadow: 0 0 8px rgba(9,9,11,0.3), 0 0 2px rgba(9,9,11,0.5);
}
```

---

## 21. SIGN-OUT OVERLAY

Full-screen cinematic sign-out animation:

```
Background: rgba(9,9,11,0.97) + backdrop-filter: blur(32px) saturate(120%)
Font: DM Sans
Animation: pageFadeIn 0.3s ease
```

**Spinner assembly (3 layers):**
1. Pulsing background orb — `64px circle`, `rgba(255,255,255,0.04)`, `signOutPulse 1.5s ease-in-out infinite` (scale 1→1.05)
2. Outer ring — `inset: 4px`, `border: 2px solid rgba(255,255,255,0.08)`, `borderTopColor: rgba(255,255,255,0.7)`, `spinCircle 0.8s linear infinite`
3. Inner ring — `inset: 12px`, `border: 1px solid rgba(255,255,255,0.04)`, `borderBottomColor: rgba(255,255,255,0.3)`, `spinCircle 1.4s linear infinite reverse`

**Progress bar:**
```
Width: 160px, height: 2px, borderRadius: 99px
Background: rgba(255,255,255,0.06)
Inner fill: width 40%, background rgba(255,255,255,0.4), .progress-sweep class
```

---

## 22. MODAL / OVERLAY PATTERN

```
Backdrop: rgba(0,0,0,0.5) fixed inset-0 z-50
Panel: bg-white, border border-[#eaeaea], rounded-[24px], padding 24px, max-width 480px
Animation: .modal-enter → modalSlideUp 0.3s cubic-bezier(0.16,1,0.3,1)
Close: X button top-right, or click backdrop
```

---

## 23. BADGES & LABELS

```css
.badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 10px; font-size: 12px; font-weight: 500;
  border-radius: 9999px;
}
.badge-green  { background: #f0fdf4; color: #16a34a; border: 1px solid #bbf7d0; }
.badge-orange { background: #fff7ed; color: #ea580c; border: 1px solid #fed7aa; }
.badge-blue   { background: #eff6ff; color: #2563eb; border: 1px solid #bfdbfe; }
.badge-red    { background: #fef2f2; color: #dc2626; border: 1px solid #fecaca; }
.badge-gray   { background: #f4f4f5; color: #71717a; border: 1px solid #d4d4d8; }
```

**Inline section labels (landing page):**
```html
<span class="text-[12px] font-bold uppercase tracking-wider text-[#6b6b70] bg-[#ebebe0] px-3 py-1 rounded-full">
  Label Text
</span>
```

---

## 24. FORM INPUTS

```css
.input {
  width: 100%; padding: 10px 16px; font-size: 13.5px;
  background: #ffffff; color: #09090b;
  border: 1px solid #eaeaea; border-radius: 12px;
  font-family: DM Sans; outline: none;
  transition: all 150ms ease;
}
.input:focus {
  border-color: #09090b;
  box-shadow: 0 0 0 3px rgba(9,9,11,0.08);
}
.input::placeholder { color: #6b6b70; }
```

**Label style:**
```html
<label class="block text-[11px] font-bold uppercase tracking-wider text-[#6b6b70] mb-2">
  Field Name
</label>
```

---

## 25. SCROLLBAR

```css
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: #f4f4f5; }
::-webkit-scrollbar-thumb { background: #d1d1d6; border-radius: 9999px; }
::-webkit-scrollbar-thumb:hover { background: #6b6b70; }
```

---

## 26. NAVIGATION (Landing)

### Fixed Header Scroll Behaviour

```
Unscrolled: py-6, transparent background
Scrolled (>40px): py-3, bg-[rgba(250,250,248,0.85)] backdrop-blur-md
                  border-b border-[rgba(9,9,11,0.04)] shadow-xs
Transition: duration-300
```

### Nav Links

```
font-size: 14px; font-weight: 500; color: #2b2b2d
hover: color: #09090b; transition-colors
```

---

## 27. SELECTION COLOUR

```css
::selection { background: #09090b; color: #ffffff; }
```

---

## 28. HERO VISUAL — Neural Sphere

Animated SVG sphere with concentric circles, cross-lines, and pulsing nodes using `linearGradient id="neuralGlow"` from obsidian → iron → cream. Wrapped in a glass `rounded-full` container with:
- `animate-spin` dashed ring (30s duration)
- Radial gradient glow pulse (`animate-pulse`)
- `.neural-sphere-pulse` class (8s ease-in-out scale+rotate)

**Status badge on hero:**
```html
<!-- Floating ping badge -->
<div class="flex items-center gap-2 px-3 py-1.5 rounded-full bg-white border border-[#eaeaea] shadow-md floating-element">
  <span class="w-2.5 h-2.5 rounded-full bg-green-500 animate-ping" />
  <span class="w-2.5 h-2.5 rounded-full bg-green-500 absolute" />
  <span class="text-[11px] font-bold text-[#09090b] uppercase tracking-wider">Aria: Active</span>
</div>
```

---

## 29. PRICING CARD SHINE EFFECT

```css
.pricing-shine {
  position: relative;
  overflow: hidden;
}
.pricing-shine::before {
  content: '';
  position: absolute;
  top: -50%; left: -75%; width: 50%; height: 200%;
  background: linear-gradient(to right, transparent, rgba(255,255,255,0.06), transparent);
  transform: skewX(-20deg);
  transition: left 700ms ease;
  pointer-events: none;
}
.pricing-shine:hover::before { left: 125%; }
```

---

## 30. FAVICONS & METADATA

```tsx
// SVG favicon inline (text-based, no image file needed)
icon: 'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-family=%22sans-serif%22 font-weight=%22900%22 font-size=%2275%22 fill=%22black%22>FD.</text></svg>'

// OpenGraph image: /logo.png
// Base URL: https://yourdomain.com
```

---

## 31. PAGE TRANSITION WRAPPER

Every page is wrapped in:

```tsx
<div className="page-fade-enter relative z-10">{children}</div>
// pageFadeIn: 0.4s cubic-bezier(0.16,1,0.3,1) — opacity 0→1 + translateY 4px→0
```

---

## 32. PILL GLOW EFFECT

Animated glow ring around pill/badge elements on hover:

```css
.pill-glow {
  position: relative; border-radius: 9999px;
  transition: all 300ms cubic-bezier(0.16,1,0.3,1);
}
.pill-glow::after {
  content: '';
  position: absolute; inset: -2px; border-radius: inherit; z-index: -1;
  background: linear-gradient(90deg, rgba(9,9,11,0.08), rgba(9,9,11,0.25), rgba(9,9,11,0.08));
  background-size: 200% 200%;
  animation: shimmer 4s infinite linear;
  filter: blur(4px);
  opacity: 0;
  transition: opacity 300ms ease;
}
.pill-glow:hover::after { opacity: 1; }
```

---

## 33. GLOBAL BODY SETUP

```css
html { scroll-behavior: smooth; }
html, body {
  background-color: #fafaf8;
  color: #09090b;
  font-family: 'Outfit', 'DM Sans', -apple-system, sans-serif;
  font-size: 15px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
```

---

## 34. DEPENDENCY CHECKLIST

To reproduce this design system in a new project:

```
Google Fonts:
  - Outfit (weights 300, 400, 500, 600, 700, 800, 900)
  - Syne (weights 700, 800)
  - DM Sans (ital, opsz 9..40, weights 300, 400, 500, 600)

npm packages (frontend):
  - tailwindcss + postcss + autoprefixer
  - lucide-react (icons)
  - next (App Router, for icon.tsx favicon generation)

No external animation libraries needed.
All animations are pure CSS keyframes + React state.
ElectricBorder uses canvas API (no WebGL/Three.js).
GhostCursor uses requestAnimationFrame (no libraries).
```

---

## 35. FILE LOCATIONS (Reference Project)

| File | Contents |
|------|----------|
| `frontend/src/styles/globals.css` | Full CSS design system, tokens, all keyframes, all utility classes |
| `frontend/tailwind.config.ts` | Colour tokens, font family, border radius extensions |
| `frontend/src/app/layout.tsx` | Font loading, FloatingBackground SVGs, FloatingSymbols, page wrapper |
| `frontend/src/app/page.tsx` | Landing page — all section patterns, marquee, stats, morph text |
| `frontend/src/app/dashboard/layout.tsx` | Dashboard sidebar, sign-out overlay, trial banner |
| `frontend/src/components/ui/LightBeamButton.tsx` | Spinning conic-gradient CTA button |
| `frontend/src/components/ui/ElectricBorder.tsx` | Canvas Perlin noise animated border |
| `frontend/src/components/ui/GradualBlur.tsx` | Multi-layer backdrop-filter blur fade |
| `frontend/src/components/ui/GhostCursor.tsx` | Lagging ring + dot cursor trail |
