// TradeFeed.jsx — tabbed trade ledger: Open Positions + Trade History
import { useState, useEffect, useCallback, useRef, useMemo, memo } from 'react';
import SpotlightMask from './common/SpotlightMask';

// ── helpers ──────────────────────────────────────────────────────────────────

function assetFromTitle(title) {
  if (/bitcoin/i.test(title))  return 'BTC';
  if (/ethereum/i.test(title)) return 'ETH';
  if (/solana/i.test(title))   return 'SOL';
  if (/\bxrp\b/i.test(title))  return 'XRP';
  if (/doge/i.test(title))     return 'DOGE';
  if (/\blink\b/i.test(title) || /chainlink/i.test(title)) return 'LINK';
  if (/bnb/i.test(title) || /binance/i.test(title)) return 'BNB';
  return '?';
}

function tfFromTitle(title) {
  const tagMatch = title.match(/\[(5m|15m|1h)\]/);
  if (tagMatch) return tagMatch[1];
  const timeMatch = title.match(/(\d+:\d+[AP]M)-(\d+:\d+[AP]M)/i);
  if (timeMatch) {
    const toMin = (t) => {
      const m = t.match(/(\d+):(\d+)([AP]M)/i);
      if (!m) return 0;
      let h = parseInt(m[1]), mm = parseInt(m[2]);
      if (/pm/i.test(m[3]) && h !== 12) h += 12;
      if (/am/i.test(m[3]) && h === 12) h = 0;
      return h * 60 + mm;
    };
    let diff = toMin(timeMatch[2]) - toMin(timeMatch[1]);
    if (diff < 0) diff += 24 * 60;
    if (diff > 0) return `${diff}m`;
  }
  return '—';
}

function parseType(title, entryType) {
  const titleUpper = (title || '').toUpperCase();
  if (titleUpper.includes('[CLOSE_SNIPE]') || titleUpper.includes('[CLOSE-SNIPE]') || titleUpper.includes('[CLOSE-SNIPE-EARLY]') || titleUpper.includes('[NCS]')) return 'NCS';
  if (titleUpper.includes('[FAIR_VAL]') || titleUpper.includes('[FV]')) return 'FV';
  if (titleUpper.includes('[T2_SWEEPER]') || titleUpper.includes('[SWEEP]')) return 'SWEEP';
  if (titleUpper.includes('[LATENCY_ARB]') || titleUpper.includes('[LAT-ARB]') || titleUpper.includes('[ARB]') || titleUpper.includes('[LAG_ARB_FUSION]')) return 'LAT ARB';
  if (titleUpper.includes('[REVERSAL_SNIPE]') || titleUpper.includes('[REVERSAL-SNIPE]')) return 'REV SNIPE';
  if (titleUpper.includes('[REVERSAL_STREAK]') || titleUpper.includes('[REVERSAL-STREAK]')) return 'REV STREAK';
  if (titleUpper.includes('[SINGLE]') || titleUpper.includes('[SIG]')) return 'SIG';
  if (titleUpper.includes('[DUAL_MAIN]') || titleUpper.includes('[DUAL_HEDGE]') || titleUpper.includes('[DUAL]')) return 'DUAL';

  const typeUpper = (entryType || '').toUpperCase();
  if (typeUpper === 'CLOSE_SNIPE' || typeUpper === 'CLOSE-SNIPE' || typeUpper === 'CLOSE-SNIPE-EARLY' || typeUpper === 'CLOSE_SNIPE_EARLY' || typeUpper === 'NCS') return 'NCS';
  if (typeUpper === 'FAIR_VAL' || typeUpper === 'FAIR-VAL' || typeUpper === 'FV') return 'FV';
  if (typeUpper === 'SIGNAL' || typeUpper === 'SINGLE' || typeUpper === 'SIG') return 'SIG';
  if (typeUpper === 'SWEEP' || typeUpper === 'T2_SWEEPER') return 'SWEEP';
  if (typeUpper === 'LATENCY_ARB' || typeUpper === 'LAT-ARB' || typeUpper === 'ARB' || typeUpper === 'LAT ARB') return 'LAT ARB';
  if (typeUpper === 'REVERSAL-SNIPE' || typeUpper === 'REVERSAL_SNIPE' || typeUpper === 'REV SNIPE' || typeUpper === 'REV') return 'REV SNIPE';
  if (typeUpper === 'REVERSAL-STREAK' || typeUpper === 'REVERSAL_STREAK' || typeUpper === 'REV STREAK') return 'REV STREAK';
  if (typeUpper === 'DUAL' || typeUpper === 'DUAL_MAIN' || typeUpper === 'DUAL_HEDGE') return 'DUAL';

  return 'SIG';
}

function parseMeta(p) {
  const title = p.event_title || '';
  const aTag = title.match(/\[(BTC|ETH|SOL|XRP|DOGE|LINK|BNB)\]/);
  const tTag = title.match(/\[(5m|15m|1h)\]/);
  return {
    asset:     aTag ? aTag[1] : assetFromTitle(title),
    timeframe: tTag ? tTag[1] : tfFromTitle(title),
    type:      parseType(title, p.trade_type || p.entry_type),
  };
}

function fmtLocal(ts) {
  if (!ts) return '--:--:--';
  const d = new Date(ts);
  return d.getHours().toString().padStart(2,'0') + ':' +
         d.getMinutes().toString().padStart(2,'0') + ':' +
         d.getSeconds().toString().padStart(2,'0');
}

// Entry timestamp with date prepended: "05/29 10:30:05"
function fmtLocalDT(ts) {
  if (!ts) return '--/-- --:--:--';
  const d = new Date(ts);
  const mm = (d.getMonth() + 1).toString().padStart(2,'0');
  const dd = d.getDate().toString().padStart(2,'0');
  const hh = d.getHours().toString().padStart(2,'0');
  const mi = d.getMinutes().toString().padStart(2,'0');
  const ss = d.getSeconds().toString().padStart(2,'0');
  return `${mm}/${dd} ${hh}:${mi}:${ss}`;
}

const ENTRY_TYPE_CONFIG = {
  'LAT-ARB':           { label: 'LAT ARB',   color: '#2b7fff' },
  'FAIR-VAL':          { label: 'FV',        color: '#00d4a3' },
  'REVERSAL-SNIPE':    { label: 'REV SNIPE', color: '#ff007a' },
  'REVERSAL-STREAK':   { label: 'REV STREAK',color: '#e076ff' },
  'SIGNAL':            { label: 'SIG',       color: '#00e5ff' },
  'SWEEP':             { label: 'SWEEP',      color: '#eab308' },
  'CLOSE-SNIPE':       { label: 'NCS',        color: '#e27622' },
  'CLOSE-SNIPE-EARLY': { label: 'NCS',        color: '#e27622' },
  'DUAL':              { label: 'DUAL',       color: '#9945ff' },
};
function entryTypeCfg(t) {
  if (!t) return ENTRY_TYPE_CONFIG['SIGNAL'];
  const upper = t.toUpperCase();
  if (upper === 'CLOSE-SNIPE' || upper === 'CLOSE_SNIPE' || upper === 'CLOSE-SNIPE-EARLY' || upper === 'CLOSE_SNIPE_EARLY' || upper === 'NCS') {
    return ENTRY_TYPE_CONFIG['CLOSE-SNIPE'];
  }
  if (upper === 'FAIR-VAL' || upper === 'FAIR_VAL' || upper === 'FV') {
    return ENTRY_TYPE_CONFIG['FAIR-VAL'];
  }
  if (upper === 'LAT-ARB' || upper === 'LAT_ARB' || upper === 'LAT ARB' || upper === 'ARB') {
    return ENTRY_TYPE_CONFIG['LAT-ARB'];
  }
  if (upper === 'SWEEP' || upper === 'T2_SWEEPER') {
    return ENTRY_TYPE_CONFIG['SWEEP'];
  }
  if (upper === 'REVERSAL-SNIPE' || upper === 'REVERSAL_SNIPE' || upper === 'REV SNIPE') {
    return ENTRY_TYPE_CONFIG['REVERSAL-SNIPE'];
  }
  if (upper === 'REVERSAL-STREAK' || upper === 'REVERSAL_STREAK' || upper === 'REV STREAK') {
    return ENTRY_TYPE_CONFIG['REVERSAL-STREAK'];
  }
  if (upper === 'DUAL' || upper === 'DUAL_MAIN' || upper === 'DUAL_HEDGE') {
    return ENTRY_TYPE_CONFIG['DUAL'];
  }
  return ENTRY_TYPE_CONFIG['SIGNAL'];
}

// ── Market session ────────────────────────────────────────────────────────────

function getMarketSession() {
  const now = new Date();
  const day = now.getUTCDay(); // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return { label: 'Weekend', color: '#6b7280' };
  const h = now.getUTCHours() + now.getUTCMinutes() / 60;
  if (h >= 13.5 && h < 22) return { label: 'US Session',    color: '#2b7fff' };
  if (h >= 7   && h < 16)  return { label: 'EU Session',    color: '#f59e0b' };
  if (h >= 0   && h < 8)   return { label: 'Asian Session', color: '#00d4a3' };
  return { label: 'Off-Peak', color: '#6b7280' };
}

function MarketSessionPill() {
  const [session, setSession] = useState(getMarketSession());
  useEffect(() => {
    const id = setInterval(() => setSession(getMarketSession()), 30_000);
    return () => clearInterval(id);
  }, []);
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      background: `${session.color}18`,
      border: `1px solid ${session.color}55`,
      borderRadius: 8,
      padding: '3px 10px',
      fontSize: 10, fontWeight: 700, letterSpacing: '0.06em',
      color: session.color,
      flexShrink: 0,
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%',
        background: session.color,
        display: 'inline-block',
        boxShadow: `0 0 5px ${session.color}`,
      }} />
      {session.label.toUpperCase()}
    </div>
  );
}

// ── Regime pill ──────────────────────────────────────────────────────────────

const REGIME_COLORS = {
  TRENDING:       '#2b7fff',
  MEAN_REVERTING: '#00d4a3',
  COMPRESSION:    '#f59e0b',
  VOLATILE_CHAOS: '#ef4444',
  UNKNOWN:        '#6b7280',
};
const REGIME_LABELS = {
  TRENDING:       'TRENDING',
  MEAN_REVERTING: 'MEAN-REV',
  COMPRESSION:    'COMPRESS',
  VOLATILE_CHAOS: 'CHAOS',
  UNKNOWN:        'UNKNOWN',
};

function RegimePill() {
  const [regime, setRegime] = useState({ regime: 'UNKNOWN', confidence: 0 });
  useEffect(() => {
    const load = () =>
      fetch('/api/regime').then(r => r.json()).then(setRegime).catch(() => {});
    load();
    const id = setInterval(load, 15_000);
    return () => clearInterval(id);
  }, []);
  const color = REGIME_COLORS[regime.regime] || REGIME_COLORS.UNKNOWN;
  const label = REGIME_LABELS[regime.regime] || regime.regime;
  const conf  = regime.confidence > 0 ? Math.round(regime.confidence * 100) : null;
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      background: `${color}18`,
      border: `1px solid ${color}55`,
      borderRadius: 8,
      padding: '3px 10px',
      fontSize: 10, fontWeight: 700, letterSpacing: '0.06em',
      color,
      flexShrink: 0,
    }}>
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: color, display: 'inline-block', boxShadow: `0 0 5px ${color}` }} />
      {label}
      {conf !== null && <span style={{ fontWeight: 500, opacity: 0.75 }}>{conf}%</span>}
    </div>
  );
}

// ── Macro trend arrow ─────────────────────────────────────────────────────────

const ARROW_GLYPH = { UP: '↑', DOWN: '↓', NEUTRAL: '→' };
const ARROW_COLOR = { UP: '#00d4a3', DOWN: '#ef4444', NEUTRAL: '#6b7280' };

function MacroTrendArrow() {
  const [trend, setTrend] = useState({ direction: 'NEUTRAL', up_count: 4, total: 8 });
  useEffect(() => {
    const load = () =>
      fetch('/api/macro-trend').then(r => r.json()).then(setTrend).catch(() => {});
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, []);
  const color = ARROW_COLOR[trend.direction] || '#6b7280';
  return (
    <span
      title={`BTC macro: ${trend.up_count}/${trend.total} UP candles`}
      style={{ fontSize: 16, fontWeight: 800, color, lineHeight: 1, cursor: 'default' }}
    >
      {ARROW_GLYPH[trend.direction] || '→'}
    </span>
  );
}

// ── Candle Countdown Bar ──────────────────────────────────────────────────────
// Shows next 5m & 15m candle boundary countdowns + per-asset macro direction dots.

function CandleCountdownBar({ assetMacro = {} }) {
  const [fiveM,    setFiveM]    = useState('');
  const [fifteenM, setFifteenM] = useState('');
  const [pct5,     setPct5]     = useState(100);
  const [pct15,    setPct15]    = useState(100);

  useEffect(() => {
    const tick = () => {
      const now = Date.now();
      const next5  = Math.ceil(now / 300_000)  * 300_000;
      const next15 = Math.ceil(now / 900_000)  * 900_000;
      const rem5   = Math.max(0, next5  - now);
      const rem15  = Math.max(0, next15 - now);
      const fmt = ms => {
        const s = Math.floor(ms / 1000);
        return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
      };
      setFiveM(fmt(rem5));
      setFifteenM(fmt(rem15));
      setPct5(Math.round((rem5 / 300_000) * 100));
      setPct15(Math.round((rem15 / 900_000) * 100));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  const ASSETS = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE'];
  const dColor  = d => d === 'UP' ? '#10b981' : d === 'DOWN' ? '#ef4444' : '#52525b';
  const dGlyph  = d => d === 'UP' ? '↑' : d === 'DOWN' ? '↓' : '→';

  const timerColor = p => p < 15 ? '#ef4444' : p < 30 ? '#f97316' : 'var(--color-logo)';

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
      padding: '5px 12px',
      marginBottom: 8,
      background: 'rgba(18,18,20,0.92)',
      borderRadius: 8,
      border: '1px solid rgba(255,255,255,0.06)',
    }}>
      {/* 5m timer */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
        <span style={{ fontSize: 8, color: '#52525b', letterSpacing: '0.08em', textTransform: 'uppercase' }}>5m</span>
        <div style={{ width: 40, height: 3, background: 'rgba(255,255,255,0.08)', borderRadius: 2, overflow: 'hidden' }}>
          <div style={{ height: '100%', width: `${pct5}%`, background: timerColor(pct5), borderRadius: 2, transition: 'width 1s linear, background 0.3s' }} />
        </div>
        <span style={{ fontFamily: 'monospace', fontSize: 11, fontWeight: 700, color: timerColor(pct5), minWidth: 34 }}>{fiveM}</span>
      </div>

      {/* 15m timer */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
        <span style={{ fontSize: 8, color: '#52525b', letterSpacing: '0.08em', textTransform: 'uppercase' }}>15m</span>
        <div style={{ width: 40, height: 3, background: 'rgba(255,255,255,0.08)', borderRadius: 2, overflow: 'hidden' }}>
          <div style={{ height: '100%', width: `${pct15}%`, background: timerColor(pct15), borderRadius: 2, transition: 'width 1s linear, background 0.3s' }} />
        </div>
        <span style={{ fontFamily: 'monospace', fontSize: 11, fontWeight: 700, color: timerColor(pct15), minWidth: 34 }}>{fifteenM}</span>
      </div>

      <div style={{ width: 1, height: 14, background: 'rgba(255,255,255,0.06)', flexShrink: 0 }} />

      {/* Per-asset macro dots */}
      {ASSETS.map(a => {
        const d = assetMacro[a]?.direction || 'NEUTRAL';
        const uc = assetMacro[a]?.up_count ?? 4;
        return (
          <div key={a} title={`${a}: ${uc}/8 up candles`}
            style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1, cursor: 'default' }}>
            <span style={{ fontSize: 7, color: '#3f3f46', letterSpacing: '0.04em' }}>{a}</span>
            <span style={{ fontSize: 12, fontWeight: 800, color: dColor(d), lineHeight: 1 }}>{dGlyph(d)}</span>
          </div>
        );
      })}
    </div>
  );
}

// ── CollapsiblePanel utility ───────────────────────────────────────────────────

function CollapsiblePanel({ title, children, defaultOpen = false, badge = null, accentColor = null }) {
  const [open, setOpen] = useState(defaultOpen);
  const accent = accentColor || 'rgba(255,255,255,0.35)';
  return (
    <div style={{
      marginBottom: 8,
      border: `1px solid ${open ? 'rgba(255,255,255,0.07)' : 'rgba(255,255,255,0.04)'}`,
      borderRadius: 8,
      overflow: 'hidden',
      transition: 'border-color 0.2s',
    }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', width: '100%', alignItems: 'center',
          padding: '6px 12px',
          background: open ? 'rgba(255,255,255,0.04)' : 'rgba(255,255,255,0.02)',
          cursor: 'pointer', border: 'none',
          transition: 'background 0.2s',
        }}
      >
        <span style={{
          color: 'var(--color-text-secondary)',
          fontSize: 9, fontWeight: 700, letterSpacing: '0.07em',
          textTransform: 'uppercase', flex: 1, textAlign: 'left',
          transition: 'color 0.2s',
        }}>{title}</span>
        {badge !== null && (
          <span style={{
            fontSize: 9, background: 'rgba(255,255,255,0.07)', borderRadius: 4,
            padding: '1px 6px', color: 'var(--color-text-muted)', marginRight: 8,
          }}>{badge}</span>
        )}
        <span style={{
          color: 'var(--color-text-muted)', fontSize: 9,
          display: 'inline-block',
          transform: open ? 'rotate(180deg)' : 'rotate(0deg)',
          transition: 'transform 0.25s cubic-bezier(0.4, 0, 0.2, 1)',
        }}>▾</span>
      </button>
      <div style={{
        maxHeight: open ? '600px' : '0px',
        overflow: 'hidden',
        transition: 'max-height 0.35s cubic-bezier(0.4, 0, 0.2, 1)',
      }}>
        <div style={{ padding: '8px 12px 12px' }}>
          {children}
        </div>
      </div>
    </div>
  );
}

// ── Mini equity sparkline ──────────────────────────────────────────────────────

function MiniSparkline({ values, color, label, width = 80, height = 24 }) {
  if (!values || values.length < 2) return (
    <div style={{ width, textAlign: 'center' }}>
      <div style={{ fontSize: 8, color: '#3f3f46', marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 10, color: '#3f3f46' }}>no data</div>
    </div>
  );
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 0);
  const range = max - min || 1;
  const toY = v => height - ((v - min) / range) * (height - 4) - 2;
  const pts  = values.map((v, i) => `${(i / (values.length - 1)) * width},${toY(v)}`).join(' ');
  const zeroY = toY(0);
  const last = values[values.length - 1];
  const lineColor = last >= 0 ? color : '#ef4444';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
      <span style={{ fontSize: 8, color: '#52525b', letterSpacing: '0.06em', textTransform: 'uppercase' }}>{label}</span>
      <svg width={width} height={height}>
        <line x1={0} y1={zeroY} x2={width} y2={zeroY} stroke="rgba(255,255,255,0.08)" strokeWidth={0.5} strokeDasharray="3,2" />
        <polyline points={pts} fill="none" stroke={lineColor} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
        <circle cx={(values.length-1)/(values.length-1)*width} cy={toY(last)} r={2.5} fill={lineColor} />
      </svg>
      <span style={{ fontSize: 9, fontFamily: 'monospace', fontWeight: 700, color: lineColor }}>
        {last >= 0 ? '+' : ''}${last.toFixed(2)}
      </span>
    </div>
  );
}

// ── Session Analytics (drawdown + per-type equity) ────────────────────────────

const SessionAnalytics = memo(function SessionAnalytics({ closed }) {
  if (closed.length === 0) return null;

  // Build running P&L from chronological order
  const chrono = [...closed].reverse();
  let runBal = 0, peak = 0, maxDD = 0;
  const latSeries = [0], fvSeries = [0], sigSeries = [0], sweepSeries = [0];
  const ncsSeries = [0], revSnipeSeries = [0], revStreakSeries = [0], dualSeries = [0];
  let latBal = 0, fvBal = 0, sigBal = 0, sweepBal = 0;
  let ncsBal = 0, revSnipeBal = 0, revStreakBal = 0, dualBal = 0;

  chrono.forEach(t => {
    const pnl = parseFloat(t.realized_pnl || 0);
    runBal += pnl;
    if (runBal > peak) peak = runBal;
    const dd = peak - runBal;
    if (dd > maxDD) maxDD = dd;
    
    const meta = parseMeta(t);
    const et = meta.type;
    if (et === 'LAT ARB') {
      latBal += pnl;
      latSeries.push(latBal);
    } else if (et === 'FV') {
      fvBal += pnl;
      fvSeries.push(fvBal);
    } else if (et === 'NCS') {
      ncsBal += pnl;
      ncsSeries.push(ncsBal);
    } else if (et === 'REV SNIPE') {
      revSnipeBal += pnl;
      revSnipeSeries.push(revSnipeBal);
    } else if (et === 'REV STREAK') {
      revStreakBal += pnl;
      revStreakSeries.push(revStreakBal);
    } else if (et === 'SIG') {
      sigBal += pnl;
      sigSeries.push(sigBal);
    } else if (et === 'SWEEP') {
      sweepBal += pnl;
      sweepSeries.push(sweepBal);
    } else if (et === 'DUAL') {
      dualBal += pnl;
      dualSeries.push(dualBal);
    }
  });

  const currentDD = Math.max(0, peak - runBal);
  const ddColor   = currentDD > 5 ? '#ef4444' : currentDD > 2 ? '#f97316' : '#10b981';
  const maxDDColor= maxDD > 8 ? '#ef4444' : maxDD > 4 ? '#f97316' : '#10b981';

  return (
    <CollapsiblePanel title="Session Analytics" defaultOpen={true} accentColor="var(--color-logo)">
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        {/* Drawdown stats */}
        <div style={{ display: 'flex', gap: 16 }}>
          {[
            { label: 'Peak P&L', val: `+$${peak.toFixed(2)}`,        color: '#10b981' },
            { label: 'Max DD',   val: `-$${maxDD.toFixed(2)}`,        color: maxDDColor },
            { label: 'Cur DD',   val: `-$${currentDD.toFixed(2)}`,    color: ddColor },
          ].map(({ label, val, color }) => (
            <div key={label} style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 8, color: '#52525b', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 3 }}>{label}</div>
              <div style={{ fontFamily: 'monospace', fontSize: 12, fontWeight: 800, color }}>{val}</div>
            </div>
          ))}
        </div>

        {/* Per-type sparklines */}
        <div style={{ display: 'flex', gap: 16, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          {latSeries.length > 1 && <MiniSparkline values={latSeries} color="#2b7fff" label="LAT ARB" />}
          {fvSeries.length  > 1 && <MiniSparkline values={fvSeries}  color="#00d4a3" label="FV"  />}
          {ncsSeries.length > 1 && <MiniSparkline values={ncsSeries} color="#e27622" label="NCS" />}
          {revSnipeSeries.length > 1 && <MiniSparkline values={revSnipeSeries} color="#ff007a" label="REV SNIPE" />}
          {revStreakSeries.length > 1 && <MiniSparkline values={revStreakSeries} color="#e076ff" label="REV STREAK" />}
          {sigSeries.length > 1 && <MiniSparkline values={sigSeries} color="#00e5ff" label="SIG" />}
          {sweepSeries.length > 1 && <MiniSparkline values={sweepSeries} color="#eab308" label="SWEEP" />}
          {dualSeries.length > 1 && <MiniSparkline values={dualSeries} color="#9945ff" label="DUAL" />}
        </div>
      </div>
    </CollapsiblePanel>
  );
});

// ── Asset Heatmap ─────────────────────────────────────────────────────────────

const AssetHeatmap = memo(function AssetHeatmap({ closed }) {
  if (closed.length === 0) return null;

  const ASSETS = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE'];

  const stats = ASSETS.map(asset => {
    const trades = closed.filter(t => {
      const tag = (t.event_title || '').match(/\[(BTC|ETH|SOL|XRP|DOGE)\]/);
      return tag && tag[1] === asset;
    });
    if (trades.length === 0) return null;
    const wins = trades.filter(t => parseFloat(t.realized_pnl || 0) > 0).length;
    const wr   = trades.length > 0 ? wins / trades.length * 100 : 0;
    const net  = trades.reduce((s, t) => s + parseFloat(t.realized_pnl || 0), 0);
    const avgH = trades.reduce((s, t) => s + parseFloat(t.hold_hours || 0) * 60, 0) / trades.length;
    return { asset, count: trades.length, wr, net, avgH };
  }).filter(Boolean);

  if (stats.length === 0) return null;

  const wrColor  = wr  => wr  >= 65 ? '#10b981' : wr  >= 50 ? '#f97316' : '#ef4444';
  const netColor = net => net >= 0  ? '#10b981' : '#ef4444';

  return (
    <CollapsiblePanel title="Asset Heatmap" defaultOpen={false} badge={`${stats.length} assets`} accentColor="#00d4a3">
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 10 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.07)' }}>
              {['Asset', 'Trades', 'WR%', 'Net P&L', 'Avg Hold'].map(h => (
                <th key={h} style={{ padding: '3px 8px', textAlign: 'left', color: '#52525b', fontWeight: 600, fontSize: 8, textTransform: 'uppercase', letterSpacing: '0.07em' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {stats.map(({ asset, count, wr, net, avgH }) => (
              <tr key={asset} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                <td style={{ padding: '4px 8px', fontWeight: 700, fontSize: 11, color: 'var(--color-text-primary)' }}>{asset}</td>
                <td style={{ padding: '4px 8px', fontFamily: 'monospace', color: '#a1a1aa' }}>{count}</td>
                <td style={{ padding: '4px 8px' }}>
                  <span style={{
                    background: `${wrColor(wr)}18`,
                    color: wrColor(wr),
                    borderRadius: 4, padding: '1px 6px',
                    fontWeight: 700, fontFamily: 'monospace', fontSize: 10,
                  }}>{wr.toFixed(0)}%</span>
                </td>
                <td style={{ padding: '4px 8px', fontFamily: 'monospace', fontWeight: 700, color: netColor(net) }}>
                  {net >= 0 ? '+' : ''}${net.toFixed(2)}
                </td>
                <td style={{ padding: '4px 8px', fontFamily: 'monospace', color: '#71717a' }}>
                  {avgH < 60 ? `${avgH.toFixed(0)}m` : `${(avgH/60).toFixed(1)}h`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </CollapsiblePanel>
  );
});

// ── Source Heatmap ────────────────────────────────────────────────────────────

const SourceHeatmap = memo(function SourceHeatmap({ closed }) {
  if (closed.length === 0) return null;

  const SOURCES = ['LAT ARB', 'FV', 'NCS', 'REV SNIPE', 'REV STREAK', 'SIG', 'SWEEP', 'DUAL'];

  const stats = SOURCES.map(src => {
    const trades = closed.filter(t => {
      const meta = parseMeta(t);
      return meta.type === src;
    });
    if (trades.length === 0) return null;
    const wins = trades.filter(t => parseFloat(t.realized_pnl || 0) > 0).length;
    const wr   = trades.length > 0 ? wins / trades.length * 100 : 0;
    const net  = trades.reduce((s, t) => s + parseFloat(t.realized_pnl || 0), 0);
    const avgH = trades.reduce((s, t) => s + parseFloat(t.hold_hours || 0) * 60, 0) / trades.length;
    return { src, count: trades.length, wr, net, avgH };
  }).filter(Boolean);

  if (stats.length === 0) return null;

  const wrColor  = wr  => wr  >= 65 ? '#10b981' : wr  >= 50 ? '#f97316' : '#ef4444';
  const netColor = net => net >= 0  ? '#10b981' : '#ef4444';

  const SRC_LABELS = {
    'LAT ARB': 'Latency Arb',
    'FV': 'Fair Value',
    'NCS': 'Near Close Sniper (NCS)',
    'REV SNIPE': 'Reversal Snipe',
    'REV STREAK': 'Streak Reversal',
    'SIG': 'Signal',
    'SWEEP': 'Sweeper',
    'DUAL': 'Dual Arb'
  };

  return (
    <CollapsiblePanel title="Source Heatmap" defaultOpen={false} badge={`${stats.length} sources`} accentColor="#2b7fff">
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 10 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.07)' }}>
              {['Source', 'Trades', 'WR%', 'Net P&L', 'Avg Hold'].map(h => (
                <th key={h} style={{ padding: '3px 8px', textAlign: 'left', color: '#52525b', fontWeight: 600, fontSize: 8, textTransform: 'uppercase', letterSpacing: '0.07em' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {stats.map(({ src, count, wr, net, avgH }) => (
              <tr key={src} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                <td style={{ padding: '4px 8px', fontWeight: 700, fontSize: 11, color: 'var(--color-text-primary)' }}>{SRC_LABELS[src] || src}</td>
                <td style={{ padding: '4px 8px', fontFamily: 'monospace', color: '#a1a1aa' }}>{count}</td>
                <td style={{ padding: '4px 8px' }}>
                  <span style={{
                    background: `${wrColor(wr)}18`,
                    color: wrColor(wr),
                    borderRadius: 4, padding: '1px 6px',
                    fontWeight: 700, fontFamily: 'monospace', fontSize: 10,
                  }}>{wr.toFixed(0)}%</span>
                </td>
                <td style={{ padding: '4px 8px', fontFamily: 'monospace', fontWeight: 700, color: netColor(net) }}>
                  {net >= 0 ? '+' : ''}${net.toFixed(2)}
                </td>
                <td style={{ padding: '4px 8px', fontFamily: 'monospace', color: '#71717a' }}>
                  {avgH < 60 ? `${avgH.toFixed(0)}m` : `${(avgH/60).toFixed(1)}h`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </CollapsiblePanel>
  );
});

// ── Timeframe Heatmap ─────────────────────────────────────────────────────────

const TimeframeHeatmap = memo(function TimeframeHeatmap({ closed }) {
  if (closed.length === 0) return null;

  const TIMEFRAMES = ['5m', '15m', '1h'];

  const stats = TIMEFRAMES.map(tf => {
    const trades = closed.filter(t => {
      const parsed = parseMeta(t);
      return parsed.timeframe === tf;
    });
    if (trades.length === 0) return null;
    const wins = trades.filter(t => parseFloat(t.realized_pnl || 0) > 0).length;
    const wr   = trades.length > 0 ? wins / trades.length * 100 : 0;
    const net  = trades.reduce((s, t) => s + parseFloat(t.realized_pnl || 0), 0);
    const avgH = trades.reduce((s, t) => s + parseFloat(t.hold_hours || 0) * 60, 0) / trades.length;
    return { tf, count: trades.length, wr, net, avgH };
  }).filter(Boolean);

  if (stats.length === 0) return null;

  const wrColor  = wr  => wr  >= 65 ? '#10b981' : wr  >= 50 ? '#f97316' : '#ef4444';
  const netColor = net => net >= 0  ? '#10b981' : '#ef4444';

  return (
    <CollapsiblePanel title="Timeframe Heatmap" defaultOpen={false} badge={`${stats.length} intervals`} accentColor="#f59e0b">
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 10 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.07)' }}>
              {['Timeframe', 'Trades', 'WR%', 'Net P&L', 'Avg Hold'].map(h => (
                <th key={h} style={{ padding: '3px 8px', textAlign: 'left', color: '#52525b', fontWeight: 600, fontSize: 8, textTransform: 'uppercase', letterSpacing: '0.07em' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {stats.map(({ tf, count, wr, net, avgH }) => (
              <tr key={tf} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                <td style={{ padding: '4px 8px', fontWeight: 700, fontSize: 11, color: 'var(--color-text-primary)' }}>{tf}</td>
                <td style={{ padding: '4px 8px', fontFamily: 'monospace', color: '#a1a1aa' }}>{count}</td>
                <td style={{ padding: '4px 8px' }}>
                  <span style={{
                    background: `${wrColor(wr)}18`,
                    color: wrColor(wr),
                    borderRadius: 4, padding: '1px 6px',
                    fontWeight: 700, fontFamily: 'monospace', fontSize: 10,
                  }}>{wr.toFixed(0)}%</span>
                </td>
                <td style={{ padding: '4px 8px', fontFamily: 'monospace', fontWeight: 700, color: netColor(net) }}>
                  {net >= 0 ? '+' : ''}${net.toFixed(2)}
                </td>
                <td style={{ padding: '4px 8px', fontFamily: 'monospace', color: '#71717a' }}>
                  {avgH < 60 ? `${avgH.toFixed(0)}m` : `${(avgH/60).toFixed(1)}h`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </CollapsiblePanel>
  );
});

// ── Assets vs Timeframe Heatmap ────────────────────────────────────────────────
const AssetVsTimeframeHeatmap = memo(function AssetVsTimeframeHeatmap({ closed }) {
  if (closed.length === 0) return null;

  const ASSETS = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE'];
  const TIMEFRAMES = ['5m', '15m', '1h'];

  const wrColor  = wr  => wr  >= 65 ? '#10b981' : wr  >= 50 ? '#f97316' : '#ef4444';
  const netColor = net => net >= 0  ? '#10b981' : '#ef4444';

  // Compute stats per (asset, tf) combination
  const matrix = {};
  ASSETS.forEach(asset => {
    matrix[asset] = {};
    TIMEFRAMES.forEach(tf => {
      const trades = closed.filter(t => {
        const meta = parseMeta(t);
        return meta.asset === asset && meta.timeframe === tf;
      });
      if (trades.length === 0) {
        matrix[asset][tf] = null;
      } else {
        const wins = trades.filter(t => parseFloat(t.realized_pnl || 0) > 0).length;
        const wr = wins / trades.length * 100;
        const net = trades.reduce((s, t) => s + parseFloat(t.realized_pnl || 0), 0);
        matrix[asset][tf] = { count: trades.length, wr, net };
      }
    });
    // Add asset row totals
    const assetTrades = closed.filter(t => {
      const meta = parseMeta(t);
      return meta.asset === asset && TIMEFRAMES.includes(meta.timeframe);
    });
    if (assetTrades.length === 0) {
      matrix[asset]['Total'] = null;
    } else {
      const wins = assetTrades.filter(t => parseFloat(t.realized_pnl || 0) > 0).length;
      const wr = wins / assetTrades.length * 100;
      const net = assetTrades.reduce((s, t) => s + parseFloat(t.realized_pnl || 0), 0);
      matrix[asset]['Total'] = { count: assetTrades.length, wr, net };
    }
  });

  return (
    <CollapsiblePanel title="Assets vs Timeframe" defaultOpen={false} badge="5 assets × 3 TFs" accentColor="#00e5ff">
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 10 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.07)' }}>
              <th style={{ padding: '4px 8px', textAlign: 'left', color: '#52525b', fontWeight: 600, fontSize: 8, textTransform: 'uppercase', letterSpacing: '0.07em' }}>Asset</th>
              {TIMEFRAMES.map(tf => (
                <th key={tf} style={{ padding: '4px 8px', textAlign: 'center', color: '#52525b', fontWeight: 600, fontSize: 8, textTransform: 'uppercase', letterSpacing: '0.07em' }}>{tf}</th>
              ))}
              <th style={{ padding: '4px 8px', textAlign: 'center', color: '#52525b', fontWeight: 600, fontSize: 8, textTransform: 'uppercase', letterSpacing: '0.07em' }}>Total</th>
            </tr>
          </thead>
          <tbody>
            {ASSETS.map(asset => (
              <tr key={asset} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                <td style={{ padding: '6px 8px', fontWeight: 700, fontSize: 11, color: 'var(--color-text-primary)' }}>{asset}</td>
                {TIMEFRAMES.map(tf => {
                  const cell = matrix[asset][tf];
                  if (!cell) {
                    return (
                      <td key={tf} style={{ padding: '6px 8px', textAlign: 'center', color: 'var(--color-text-muted)', fontFamily: 'monospace' }}>
                        —
                      </td>
                    );
                  }
                  return (
                    <td key={tf} style={{ padding: '6px 8px', textAlign: 'center' }}>
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
                        <span style={{
                          background: `${wrColor(cell.wr)}18`,
                          color: wrColor(cell.wr),
                          borderRadius: 4, padding: '1px 4px',
                          fontWeight: 700, fontFamily: 'monospace', fontSize: 9,
                        }}>
                          {cell.wr.toFixed(0)}% ({cell.count})
                        </span>
                        <span style={{ color: netColor(cell.net), fontFamily: 'monospace', fontWeight: 600, fontSize: 9 }}>
                          {cell.net >= 0 ? '+' : ''}${cell.net.toFixed(1)}
                        </span>
                      </div>
                    </td>
                  );
                })}
                {/* Row Total */}
                {(() => {
                  const cell = matrix[asset]['Total'];
                  if (!cell) {
                    return (
                      <td style={{ padding: '6px 8px', textAlign: 'center', color: 'var(--color-text-muted)', fontFamily: 'monospace' }}>
                        —
                      </td>
                    );
                  }
                  return (
                    <td style={{ padding: '6px 8px', textAlign: 'center', borderLeft: '1px dashed rgba(255,255,255,0.1)' }}>
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
                        <span style={{
                          background: `${wrColor(cell.wr)}24`,
                          color: wrColor(cell.wr),
                          borderRadius: 4, padding: '1px 5px',
                          fontWeight: 800, fontFamily: 'monospace', fontSize: 9,
                        }}>
                          {cell.wr.toFixed(0)}% ({cell.count})
                        </span>
                        <span style={{ color: netColor(cell.net), fontFamily: 'monospace', fontWeight: 700, fontSize: 9 }}>
                          {cell.net >= 0 ? '+' : ''}${cell.net.toFixed(1)}
                        </span>
                      </div>
                    </td>
                  );
                })()}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </CollapsiblePanel>
  );
});

// ── Gate Event Log ─────────────────────────────────────────────────────────────

const GATE_META = {
  'MACRO-GATE':   { color: '#f97316', label: 'MACRO' },
  'FV-MACRO':     { color: '#00d4a3', label: 'FV-MACRO' },
  'DIR-COOLDOWN': { color: '#2b7fff', label: 'COOLDOWN' },
  'TREND-CONFIRM':{ color: '#a855f7', label: 'TREND-CF' },
  'TREND-GATE':   { color: '#ef4444', label: 'TREND' },
  'FV-EDGE-GATE': { color: 'var(--color-logo)', label: 'FV-EDGE' },
  'CORROBORATE':  { color: '#6b7280', label: 'CORR' },
  'VOL-SURGE':    { color: '#ec4899', label: 'VOL-SURGE' },
};

function GateEventLog({ events = [] }) {
  if (events.length === 0) return null;

  return (
    <CollapsiblePanel title="Gate Events" defaultOpen={false} badge={events.length} accentColor="#f97316">
      <div style={{ maxHeight: 180, overflowY: 'auto' }}>
        {events.map((e, i) => {
          const meta  = GATE_META[e.gate] || { color: '#6b7280', label: e.gate };
          const ts    = new Date(e.ts * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
          const dirC  = e.direction === 'UP' ? '#10b981' : '#ef4444';
          return (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '3px 0',
              borderBottom: '1px solid rgba(255,255,255,0.03)',
              fontSize: 9, lineHeight: 1.4,
            }}>
              <span style={{ fontFamily: 'monospace', color: '#3f3f46', minWidth: 54, flexShrink: 0 }}>{ts}</span>
              <span style={{
                background: `${meta.color}20`, color: meta.color,
                borderRadius: 3, padding: '1px 5px',
                fontWeight: 700, fontSize: 8, letterSpacing: '0.05em',
                flexShrink: 0, minWidth: 58, textAlign: 'center',
              }}>{meta.label}</span>
              <span style={{ fontWeight: 700, color: 'var(--color-text-secondary)', minWidth: 50, flexShrink: 0 }}>{e.asset}/{e.tf}</span>
              <span style={{ fontWeight: 700, color: dirC, minWidth: 28, flexShrink: 0 }}>{e.direction}</span>
              <span style={{ color: '#52525b', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{e.reason}</span>
            </div>
          );
        })}
      </div>
    </CollapsiblePanel>
  );
}

// Wilson score 95% CI for a binomial proportion -> [low%, high%].
// Honest small-sample band on the win rate (matches dashboard/backend performance.js).
function wilson95(wins, n) {
  if (n <= 0) return [0, 0];
  const z = 1.96, p = wins / n;
  const denom  = 1 + (z * z) / n;
  const center = (p + (z * z) / (2 * n)) / denom;
  const margin = (z / denom) * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n));
  return [Math.max(0, (center - margin) * 100), Math.min(100, (center + margin) * 100)];
}

function fmtHold(hours) {
  if (hours == null || isNaN(hours)) return '—';
  const mins = Math.round(hours * 60);
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h ${(mins % 60).toString().padStart(2,'0')}m`;
}

function fmtHoldMins(mins) {
  if (mins == null || isNaN(mins)) return '—';
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h ${(mins % 60).toString().padStart(2,'0')}m`;
}

function reasonBadge(reason) {
  if (!reason) return { label: '—', color: 'var(--color-text-muted)' };
  if (reason === 'TARGET_HIT')  return { label: 'TARGET', color: 'var(--color-profit)' };
  if (reason === 'STOP_HIT')    return { label: 'STOP',   color: 'var(--color-loss)' };
  if (reason === 'TIME_EXPIRED') return { label: 'EXPIRY', color: 'var(--color-amber)' };
  return { label: reason.replace(/_/g, ' '), color: 'var(--color-text-muted)' };
}

function dirStr(dir) {
  if (dir === 'YES' || dir === 'UP') return 'UP';
  return 'DOWN';
}

// ── CountdownTimer ────────────────────────────────────────────────────────────

function CountdownTimer({ expiry_ts }) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const tick = () => setSecs(Math.max(0, expiry_ts - Math.floor(Date.now() / 1000)));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [expiry_ts]);

  const color = secs < 15 ? 'var(--color-loss)' : secs < 60 ? 'var(--color-amber)' : 'var(--color-text-secondary)';
  const pulse = secs < 15 ? { animation: 'pulse 0.8s infinite' } : {};
  return (
    <span style={{ fontFamily: 'var(--font-mono)', color, fontSize: 11, ...pulse }}>
      {Math.floor(secs / 60)}m {(secs % 60).toString().padStart(2,'0')}s
    </span>
  );
}

// ── Column configs ─────────────────────────────────────────────────────────

const CLOSED_COLS  = ['In (Local)', 'Asset', 'TF', 'Src', 'Dir', 'Size ($)', 'Entry¢', 'Exit¢', 'Hold', 'Reason', 'P&L / %', 'Exit (Local)', 'Result'];
const CLOSED_GRID  = '92px 52px 38px 38px 50px 50px 48px 48px 48px 58px 88px 62px 40px';

function VirtualList({ items, renderRow, itemHeight = 38, containerHeight = 380 }) {
  const [scrollTop, setScrollTop] = useState(0);
  const rafRef = useRef(null);

  // Throttle scroll state updates to rAF cadence to avoid thrashing the UI thread
  const onScroll = (e) => {
    const top = e.currentTarget.scrollTop;
    if (rafRef.current) return;
    rafRef.current = requestAnimationFrame(() => {
      setScrollTop(top);
      rafRef.current = null;
    });
  };

  const totalHeight = items.length * itemHeight;
  const startIndex = Math.max(0, Math.floor(scrollTop / itemHeight) - 2);
  const endIndex = Math.min(items.length, Math.floor((scrollTop + containerHeight) / itemHeight) + 2);

  const visibleItems = items.slice(startIndex, endIndex);
  const offsetY = startIndex * itemHeight;

  return (
    <div
      onScroll={onScroll}
      style={{
        overflowY: 'auto',
        maxHeight: containerHeight,
        position: 'relative',
        flex: 1,
        borderBottom: '1px solid var(--color-border-subtle)'
      }}
    >
      <div style={{ height: totalHeight, width: '100%', position: 'relative' }}>
        {/* will-change: transform promotes this layer to the GPU compositor — zero-cost scrolling */}
        <div style={{ transform: `translateY(${offsetY}px)`, left: 0, right: 0, position: 'absolute', willChange: 'transform' }}>
          {visibleItems.map((item, index) => renderRow(item, startIndex + index))}
        </div>
      </div>
    </div>
  );
}

const OPEN_COLS    = ['In (Local)', 'Asset', 'TF', 'Dir', 'Src', 'Entry¢', 'Cur¢', 'Target¢', 'Stop¢', 'Unr P&L', 'Hold', 'Closes In'];
const OPEN_GRID    = '66px 55px 42px 50px 40px 48px 48px 52px 48px 68px 48px 80px';

// ── Row components ────────────────────────────────────────────────────────────

const ClosedRow = memo(function ClosedRow({ p }) {
  const meta   = parseMeta(p);
  const dir    = dirStr(p.direction);
  const pnl    = parseFloat(p.realized_pnl ?? 0);
  const pct    = parseFloat(p.realized_pnl_pct ?? 0);
  const size   = parseFloat(p.size ?? 0);
  const result = pnl > 0 ? 'WIN' : pnl < 0 ? 'LOSS' : 'EVEN';
  const rColor = result === 'WIN' ? 'var(--color-profit)' : result === 'LOSS' ? 'var(--color-loss)' : 'rgba(9,9,11,0.25)';
  const rb     = reasonBadge(p.exit_reason);
  const pnlColor = pnl > 0 ? 'var(--color-profit)' : pnl < 0 ? 'var(--color-loss)' : 'var(--color-text-muted)';

  return (
    <div style={{
      display: 'grid', gridTemplateColumns: CLOSED_GRID, gap: 8, alignItems: 'center',
      padding: '7px 0 7px 8px',
      borderLeft: `3px solid ${rColor}`,
      borderBottom: '1px solid var(--color-border-subtle)',
      fontSize: 11,
      height: 38,
      boxSizing: 'border-box'
    }}>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-text-muted)' }}>{fmtLocalDT(p.entry_time)}</span>
      <span style={{ fontFamily: 'var(--font-primary)', fontWeight: 700, fontSize: 13, color: 'var(--color-text-primary)' }}>{meta.asset}</span>
      <span style={{ color: 'var(--color-text-muted)', fontFamily: 'var(--font-mono)' }}>{meta.timeframe}</span>
      <span style={{ fontWeight: 700, fontSize: 9, letterSpacing: '0.05em', color: entryTypeCfg(meta.type || p.entry_type).color }}>
        {entryTypeCfg(meta.type || p.entry_type).label}
      </span>
      <span style={{ fontWeight: 600, color: dir === 'UP' ? 'var(--color-profit)' : 'var(--color-loss)' }}>
        {dir === 'UP' ? '↑ UP' : '↓ DN'}
      </span>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-text-secondary)' }}>${size.toFixed(2)}</span>
      <span style={{ fontFamily: 'var(--font-mono)' }}>{(parseFloat(p.entry_price) * 100).toFixed(0)}¢</span>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-text-muted)' }}>
        {p.exit_price ? `${(parseFloat(p.exit_price) * 100).toFixed(0)}¢` : '—'}
      </span>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-text-muted)' }}>{fmtHold(p.hold_hours)}</span>
      <span style={{ fontWeight: 700, fontSize: 10, letterSpacing: '0.04em', color: rb.color }}>{rb.label}</span>
      <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: pnlColor }}>
        {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
        <span style={{ fontSize: 9, opacity: 0.85 }}> [{pct >= 0 ? '+' : ''}{pct.toFixed(1)}%]</span>
      </span>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-text-muted)' }}>{fmtLocal(p.exit_time)}</span>
      <span style={{ fontWeight: 800, fontSize: 10, letterSpacing: '0.06em', color: rColor }}>{result}</span>
    </div>
  );
});

function OpenRow({ p }) {
  const meta    = parseMeta(p);
  const dir     = dirStr(p.direction);
  const entry   = parseFloat(p.entry_price || 0);
  const cur     = parseFloat(p.current_price || entry);
  const target  = parseFloat(p.target_price || 0);
  const stop    = parseFloat(p.stop_loss || 0);
  const unrPnl  = parseFloat(p.unrealized_pnl || 0);
  const holdMin = parseInt(p.hold_minutes || 0);
  const expiry  = parseInt(p.expiry_ts || '0');
  const isDual  = meta.type.includes('DUAL');

  return (
    <div style={{
      display: 'grid', gridTemplateColumns: OPEN_GRID, gap: 8, alignItems: 'center',
      padding: '7px 0 7px 8px',
      borderLeft: `3px solid ${isDual ? 'var(--color-logo)' : 'var(--color-text-muted)'}`,
      borderBottom: '1px solid var(--color-border-subtle)',
      fontSize: 11, fontStyle: 'italic', opacity: 0.9,
    }}>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-text-muted)' }}>{fmtLocal(p.entry_time)}</span>
      <span style={{ fontFamily: 'var(--font-display)', fontWeight: 900, fontSize: 13, color: 'var(--color-text-primary)' }}>{meta.asset}</span>
      <span style={{ color: 'var(--color-text-muted)' }}>{meta.timeframe}</span>
      <span style={{ fontWeight: 600, color: dir === 'UP' ? 'var(--color-profit)' : 'var(--color-loss)' }}>
        {dir === 'UP' ? '↑ UP' : '↓ DN'}
      </span>
      <span style={{ fontWeight: 700, fontSize: 9, letterSpacing: '0.05em', color: entryTypeCfg(meta.type || p.entry_type).color }}>
        {entryTypeCfg(meta.type || p.entry_type).label}
      </span>
      <span style={{ fontFamily: 'var(--font-mono)' }}>{(entry * 100).toFixed(0)}¢</span>
      <span style={{ fontFamily: 'var(--font-mono)', color: cur >= entry ? 'var(--color-profit)' : 'var(--color-loss)' }}>
        {(cur * 100).toFixed(0)}¢
      </span>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-profit)', fontSize: 10 }}>
        {target > 0 ? `${(target * 100).toFixed(0)}¢` : '—'}
      </span>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-loss)', fontSize: 10 }}>
        {stop > 0 ? `${(stop * 100).toFixed(0)}¢` : '—'}
      </span>
      <span style={{
        fontFamily: 'var(--font-mono)', fontWeight: 700,
        color: unrPnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)',
      }}>{unrPnl >= 0 ? '+' : ''}${unrPnl.toFixed(2)}</span>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-text-muted)' }}>{fmtHoldMins(holdMin)}</span>
      {p.status === 'RESOLVING' ? (
        <span style={{ color: 'var(--color-logo)', fontWeight: 700, animation: 'pulse 1.5s infinite' }}>Resolving...</span>
      ) : expiry > 0 ? (
        <CountdownTimer expiry_ts={expiry} />
      ) : (
        <span style={{ color: 'var(--color-text-muted)' }}>—</span>
      )}
    </div>
  );
}

// ── Tab button ─────────────────────────────────────────────────────────────

function SlidingTabs({ activeTab, setActiveTab, activeCount, historyCount }) {
  const containerRef = useRef(null);
  const [pillStyle, setPillStyle] = useState({ transform: 'translateX(0px)', width: '0px' });

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    
    // Find the button with aria-selected="true"
    const activeBtn = container.querySelector('[aria-selected="true"]');
    if (activeBtn) {
      setPillStyle({
        transform: `translateX(${activeBtn.offsetLeft}px)`,
        width: `${activeBtn.offsetWidth}px`
      });
    }
  }, [activeTab, activeCount, historyCount]);

  return (
    <div className="t-tabs" ref={containerRef} role="tablist" style={{ position: 'relative' }}>
      <span className="t-tabs-pill" style={{
        ...pillStyle,
        position: 'absolute',
        top: '3px',
        height: '24px',
        background: 'var(--color-cream-dark)',
        borderRadius: '48px',
        transition: 'transform 200ms cubic-bezier(0.22, 1, 0.36, 1), width 200ms cubic-bezier(0.22, 1, 0.36, 1)',
        zIndex: 0,
        pointerEvents: 'none'
      }} />
      <button
        className="t-tab"
        role="tab"
        aria-selected={activeTab === 'open'}
        onClick={() => setActiveTab('open')}
        style={{
          position: 'relative',
          background: 'transparent',
          border: 'none',
          color: activeTab === 'open' ? 'var(--color-obsidian)' : 'var(--color-iron)',
          fontSize: '12px',
          fontWeight: activeTab === 'open' ? 700 : 500,
          cursor: 'pointer',
          padding: '4px 14px',
          borderRadius: '48px',
          zIndex: 1,
          transition: 'color 200ms cubic-bezier(0.22, 1, 0.36, 1)',
          display: 'inline-flex',
          alignItems: 'center',
          gap: '6px'
        }}
      >
        Open
        <span style={{
          background: activeTab === 'open' ? 'var(--color-logo)' : 'var(--color-cream-dark)',
          color: activeTab === 'open' ? '#fff' : 'var(--color-text-secondary)',
          borderRadius: '10px',
          padding: '1px 6px',
          fontSize: '9px',
          fontWeight: 700
        }}>
          {activeCount}
        </span>
      </button>
      <button
        className="t-tab"
        role="tab"
        aria-selected={activeTab === 'history'}
        onClick={() => setActiveTab('history')}
        style={{
          position: 'relative',
          background: 'transparent',
          border: 'none',
          color: activeTab === 'history' ? 'var(--color-obsidian)' : 'var(--color-iron)',
          fontSize: '12px',
          fontWeight: activeTab === 'history' ? 700 : 500,
          cursor: 'pointer',
          padding: '4px 14px',
          borderRadius: '48px',
          zIndex: 1,
          transition: 'color 200ms cubic-bezier(0.22, 1, 0.36, 1)',
          display: 'inline-flex',
          alignItems: 'center',
          gap: '6px'
        }}
      >
        History
        <span style={{
          background: activeTab === 'history' ? 'var(--color-logo)' : 'var(--color-cream-dark)',
          color: activeTab === 'history' ? '#fff' : 'var(--color-text-secondary)',
          borderRadius: '10px',
          padding: '1px 6px',
          fontSize: '9px',
          fontWeight: 700
        }}>
          {historyCount}
        </span>
      </button>
    </div>
  );
}

function SlidingPills({ activeFilter, setActiveFilter, srcStats, closedCount }) {
  const containerRef = useRef(null);
  const [pillStyle, setPillStyle] = useState({ transform: 'translateX(0px)', width: '0px' });

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const activeBtn = container.querySelector('[aria-selected="true"]');
    if (activeBtn) {
      setPillStyle({
        transform: `translateX(${activeBtn.offsetLeft}px)`,
        width: `${activeBtn.offsetWidth}px`,
      });
    }
  }, [activeFilter, srcStats, closedCount]);

  return (
    <div className="t-tabs" ref={containerRef} role="tablist" style={{ position: 'relative', overflow: 'visible' }}>
      <span className="t-tabs-pill" style={{
        ...pillStyle,
        position: 'absolute',
        top: '3px',
        height: '24px',
        background: 'var(--color-cream-dark)',
        borderRadius: '48px',
        transition: 'transform 200ms cubic-bezier(0.22, 1, 0.36, 1), width 200ms cubic-bezier(0.22, 1, 0.36, 1)',
        zIndex: 0,
        pointerEvents: 'none'
      }} />
      <button
        role="tab"
        aria-selected={activeFilter === 'ALL'}
        onClick={() => setActiveFilter('ALL')}
        style={{
          position: 'relative',
          background: 'transparent',
          border: 'none',
          borderRadius: '48px',
          padding: '4px 14px',
          fontSize: '11px',
          fontWeight: activeFilter === 'ALL' ? 700 : 500,
          color: activeFilter === 'ALL' ? 'var(--color-obsidian)' : 'var(--color-iron)',
          cursor: 'pointer',
          display: 'flex', alignItems: 'center', gap: 6,
          transition: 'color 200ms cubic-bezier(0.22, 1, 0.36, 1)',
          whiteSpace: 'nowrap',
          zIndex: 1,
        }}
      >
        ALL
        {closedCount > 0 && (
          <span style={{
            background: activeFilter === 'ALL' ? 'var(--color-logo)' : 'var(--color-cream-dark)',
            color: activeFilter === 'ALL' ? '#fff' : 'var(--color-text-secondary)',
            borderRadius: '10px',
            padding: '1px 6px',
            fontSize: '9px',
            fontWeight: 700
          }}>
            {closedCount}
          </span>
        )}
      </button>
      {srcStats.map(({ src, count, pnl }) => {
        const isActive = activeFilter === src;
        return (
          <button
            key={src}
            role="tab"
            aria-selected={isActive}
            onClick={() => setActiveFilter(src)}
            style={{
              position: 'relative',
              background: 'transparent',
              border: 'none',
              borderRadius: '48px',
              padding: '4px 14px',
              fontSize: '11px',
              fontWeight: isActive ? 700 : 500,
              color: isActive ? 'var(--color-obsidian)' : 'var(--color-iron)',
              cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 6,
              transition: 'color 200ms cubic-bezier(0.22, 1, 0.36, 1)',
              whiteSpace: 'nowrap',
              zIndex: 1,
            }}
          >
            {src}
            {count > 0 && (
              <span style={{
                background: isActive ? 'var(--color-logo)' : 'var(--color-cream-dark)',
                color: isActive ? '#fff' : 'var(--color-text-secondary)',
                borderRadius: '10px',
                padding: '1px 6px',
                fontSize: '9px',
                fontWeight: 700,
                display: 'inline-flex',
                alignItems: 'center',
              }}>
                {count}
                {pnl !== null && (
                  <span style={{ marginLeft: 3, color: isActive ? '#fff' : (pnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)') }}>
                    {pnl >= 0 ? '+' : ''}${pnl.toFixed(1)}
                  </span>
                )}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

// ── Column header row ─────────────────────────────────────────────────────────

function ColHeaders({ cols, grid }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: grid, gap: 8,
      paddingLeft: 11, marginBottom: 4,
      fontSize: 9, color: 'var(--color-text-muted)',
      textTransform: 'uppercase', letterSpacing: '0.08em',
    }}>
      {cols.map(h => <span key={h}>{h}</span>)}
    </div>
  );
}

// ── Expected Value sparkline ──────────────────────────────────────────────────

function RunningEVSparkline({ values }) {
  if (values.length < 2) return null;
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 0);
  const range = max - min || 1;
  const W = 220, H = 28;
  const toY = v => H - ((v - min) / range) * (H - 4) - 2;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * W},${toY(v)}`).join(' ');
  const zeroY = toY(0);
  const last = values[values.length - 1];
  const lineColor = 'var(--color-text-secondary)';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6, marginBottom: 6 }}>
      <span style={{ fontSize: 9, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', flexShrink: 0 }}>Running EV</span>
      <svg width={W} height={H} style={{ overflow: 'visible', flexShrink: 0 }}>
        <defs>
          <linearGradient id="evGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor={lineColor} stopOpacity="0.4" />
            <stop offset="100%" stopColor={lineColor} stopOpacity="1" />
          </linearGradient>
        </defs>
        <line x1={0} y1={zeroY} x2={W} y2={zeroY}
              stroke="rgba(255,255,255,0.12)" strokeWidth={0.5} strokeDasharray="4,3" />
        <polyline points={pts} fill="none" stroke="url(#evGrad)" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" />
        <circle cx={W} cy={toY(last)} r={3}
                fill={lineColor} stroke="var(--color-bg-card, #111)" strokeWidth={1.5} />
      </svg>
      <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', fontWeight: 800, color: lineColor, flexShrink: 0 }}>
        {last >= 0 ? '+' : ''}${last.toFixed(2)}
      </span>
    </div>
  );
}

// ── Summary bar ──────────────────────────────────────────────────────────────

const ClosedSummary = memo(function ClosedSummary({ closed }) {
  if (closed.length === 0) return null;

  const winTrades  = closed.filter(p => parseFloat(p.realized_pnl ?? 0) > 0);
  const lossTrades = closed.filter(p => parseFloat(p.realized_pnl ?? 0) < 0);
  const wins   = winTrades.length;
  const losses = lossTrades.length;
  const evens  = closed.length - wins - losses;
  const totalPnl = closed.reduce((s, p) => s + parseFloat(p.realized_pnl ?? 0), 0);
  const wrN = wins + losses;
  const wr = wrN > 0 ? ((wins / wrN) * 100).toFixed(1) : '—';
  const [ciLo, ciHi] = wilson95(wins, wrN);

  const grossW = winTrades.reduce((s, p) => s + parseFloat(p.realized_pnl ?? 0), 0);
  const grossL = lossTrades.reduce((s, p) => s + Math.abs(parseFloat(p.realized_pnl ?? 0)), 0);
  const pf = grossL > 0 ? (grossW / grossL).toFixed(2) : '∞';

  const avgWin  = wins > 0 ? grossW / wins : 0;
  const avgLoss = losses > 0 ? grossL / losses : 0;

  // Best and worst trade
  const sorted = [...closed].sort((a, b) => parseFloat(b.realized_pnl ?? 0) - parseFloat(a.realized_pnl ?? 0));
  const bestPnl = parseFloat(sorted[0]?.realized_pnl ?? 0);
  const worstPnl = parseFloat(sorted[sorted.length - 1]?.realized_pnl ?? 0);

  // Current streak from most recent trades (chronological → newest first already from parent sort)
  let streak = 0, streakWin = null;
  for (const p of closed) {
    const isW = parseFloat(p.realized_pnl ?? 0) > 0;
    if (streakWin === null) { streakWin = isW; streak = 1; }
    else if (isW === streakWin) streak++;
    else break;
  }

  // Running Expected Value (EV) calculation chronologically
  const chronoClosed = [...closed].reverse();
  const evValues = [];
  let cumWinPnl = 0;
  let cumLossPnl = 0;
  let winsCount = 0;
  let lossesCount = 0;

  chronoClosed.forEach(p => {
    const pnl = parseFloat(p.realized_pnl ?? 0);
    if (pnl > 0.001) {
      cumWinPnl += pnl;
      winsCount++;
    } else if (pnl < -0.001) {
      cumLossPnl += Math.abs(pnl);
      lossesCount++;
    }
    const totalWL = winsCount + lossesCount;
    if (totalWL === 0) {
      evValues.push(0);
    } else {
      const wr = winsCount / totalWL;
      const lr = lossesCount / totalWL;
      const avgW = winsCount > 0 ? cumWinPnl / winsCount : 0;
      const avgL = lossesCount > 0 ? cumLossPnl / lossesCount : 0;
      const ev = (wr * avgW) - (lr * avgL);
      evValues.push(ev);
    }
  });

  // P&L velocity ($/hr) — parse ISO string or unix int from entry_time
  const _rawOldest = closed.length > 0
    ? (closed[closed.length - 1].entry_time || closed[closed.length - 1].timestamp || 0)
    : 0;
  const oldestTs = _rawOldest
    ? (typeof _rawOldest === 'string' ? new Date(_rawOldest).getTime() / 1000 : Number(_rawOldest))
    : 0;
  const hoursElapsed = oldestTs > 0 ? (Date.now() / 1000 - oldestTs) / 3600 : 0;
  const pnlVelocity  = hoursElapsed > 0.05 ? totalPnl / hoursElapsed : null;
  const velStr = pnlVelocity !== null
    ? `${pnlVelocity >= 0 ? '+' : ''}$${pnlVelocity.toFixed(2)}/hr`
    : '—';

  // Loss cluster alert: 3+ trades settled ≤10¢ in last 20 min
  const now20min = Date.now() - 20 * 60 * 1000;
  const recentFullLosses = closed.filter(t => {
    const exitTs = (t.exit_time || t.closed_at || 0) * 1000;
    const exitPrice = parseFloat(t.exit_price ?? 1.0);
    return exitTs >= now20min && exitPrice <= 0.10;
  }).length;


  const statCols = [
    { label: 'Trades',   val: closed.length, color: 'var(--color-text-primary)' },
    { label: 'Win Rate', val: (
        <>{wr}%<span style={{ fontSize: 9, fontWeight: 500, color: 'var(--color-text-muted)', marginLeft: 4 }}>
          {wrN > 4 ? `${ciLo.toFixed(0)}–${ciHi.toFixed(0)}%` : 'n<5'}
        </span></>
      ), color: parseFloat(wr) >= 62 ? 'var(--color-profit)' : parseFloat(wr) >= 45 ? 'var(--color-amber)' : 'var(--color-loss)' },
    { label: 'W / L / E', val: `${wins} / ${losses} / ${evens}`, color: 'var(--color-text-secondary)' },
    { label: 'Total P&L', val: `${totalPnl >= 0 ? '+' : ''}$${totalPnl.toFixed(2)}`, color: totalPnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)' },
    { label: 'P&L Rate',  val: velStr, color: pnlVelocity !== null ? (pnlVelocity >= 0 ? 'var(--color-profit)' : 'var(--color-loss)') : 'var(--color-text-muted)' },
    { label: 'Profit Factor', val: pf, color: parseFloat(pf) >= 1.5 ? 'var(--color-profit)' : parseFloat(pf) >= 1 ? 'var(--color-amber)' : 'var(--color-loss)' },
    { label: 'Avg Win',   val: avgWin > 0 ? `+$${avgWin.toFixed(2)}` : '—', color: 'var(--color-profit)' },
    { label: 'Avg Loss',  val: avgLoss > 0 ? `-$${avgLoss.toFixed(2)}` : '—', color: 'var(--color-loss)' },
    { label: 'Best',  val: bestPnl > 0 ? `+$${bestPnl.toFixed(2)}` : '—', color: 'var(--color-profit)' },
    { label: 'Worst', val: worstPnl < 0 ? `-$${Math.abs(worstPnl).toFixed(2)}` : '—', color: 'var(--color-loss)' },
    { label: 'Streak', val: streak > 1
        ? <span style={{ letterSpacing: 0 }}>{streakWin ? '🔥' : '❄️'} {streak}{streakWin ? 'W' : 'L'}</span>
        : `${streakWin ? 'W' : 'L'}`,
      color: streakWin ? 'var(--color-profit)' : 'var(--color-loss)' },
  ];

  return (
    <div style={{
      padding: '8px 0 12px 0',
      borderBottom: '1px solid rgba(255,255,255,0.06)',
      marginBottom: 8,
    }}>
      {/* Loss cluster alert strip */}
      {recentFullLosses >= 3 && (
        <div style={{
          background: 'rgba(127,29,29,0.55)',
          border: '1px solid #ef4444aa',
          borderRadius: 6,
          padding: '5px 10px',
          marginBottom: 8,
          fontSize: 10,
          fontWeight: 700,
          color: '#fca5a5',
          letterSpacing: '0.04em',
        }}>
          ⚠ {recentFullLosses} FULL LOSSES IN LAST 20 MIN — MACRO REVERSAL RISK — HIGH-CONFIDENCE ENTRIES ONLY
        </div>
      )}

      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 4 }}>
        {statCols.map(({ label, val, color }) => (
          <div key={label}>
            <div style={{ fontSize: 9, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 2 }}>{label}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, color }}>{val}</div>
          </div>
        ))}
      </div>

      <RunningEVSparkline values={evValues} />

    </div>
  );
});

// ── Engine Status Pill ────────────────────────────────────────────────────────

function EngineStatusPill({ status, detail, lastTradeAgo }) {
  if (!status || status === 'SCANNING' || lastTradeAgo < 5) return null;

  const COLOR = {
    LOW_EDGE:      '#f97316',
    CHOPPY:        '#f97316',
    LAT_COOLDOWN:  '#f97316',
    NO_MARKET:     '#ef4444',
    PRICE_FLOOR:   '#f97316',
    MACRO_BLOCK:   '#f97316',
    CIRCUIT_BREAK: '#ef4444',
    UNKNOWN:       '#52525b',
    ERROR:         '#ef4444',
  };
  const ICON = {
    LOW_EDGE:      '📉',
    CHOPPY:        '🌀',
    LAT_COOLDOWN:  '🔒',
    NO_MARKET:     '🚫',
    MACRO_BLOCK:   '📊',
    CIRCUIT_BREAK: '⛔',
    UNKNOWN:       '❓',
    ERROR:         '⚠️',
  };
  const color = COLOR[status] || '#52525b';

  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      background: `${color}18`, border: `1px solid ${color}44`,
      borderRadius: 8, padding: '4px 10px',
      fontSize: 10, fontWeight: 700, color, fontFamily: 'monospace',
      marginBottom: 8,
    }}>
      <span>{ICON[status] || '⏳'}</span>
      <span>{status.replace(/_/g, ' ')} — {detail || `last trade ${lastTradeAgo}m ago`}</span>
    </div>
  );
}

// ── Source filter pills ───────────────────────────────────────────────────────

const SRC_FILTERS = ['ALL', 'NCS', 'REV SNIPE', 'SIG', 'SWEEP'];
const SRC_TO_ENTRY_TYPE = {
  'LAT ARB':    'LAT-ARB',
  'FV':         'FAIR-VAL',
  'NCS':        'CLOSE-SNIPE',
  'REV SNIPE':  'REVERSAL-SNIPE',
  'REV STREAK': 'REVERSAL-STREAK',
  'SIG':        'SIGNAL',
  'SWEEP':      'SWEEP',
};

function filterBySrc(trades, src) {
  if (src === 'ALL') return trades;
  return trades.filter(p => {
    const meta = parseMeta(p);
    return meta.type === src;
  });
}

function SrcPill({ src, active, count, pnl, onClick }) {
  const cfg = src === 'ALL'
    ? { color: '#fff' }
    : entryTypeCfg(SRC_TO_ENTRY_TYPE[src]);
  const color = cfg.color;
  const isActive = active;
  return (
    <button
      onClick={onClick}
      className="metal-fx"
      style={{
        background: isActive ? `${color}22` : 'transparent',
        border: `1px solid ${isActive ? color : 'rgba(255,255,255,0.1)'}`,
        borderRadius: 6,
        padding: '2px 8px',
        fontSize: 10, fontWeight: 700, letterSpacing: '0.05em',
        color: isActive ? color : 'var(--color-text-muted)',
        cursor: 'pointer',
        display: 'flex', alignItems: 'center', gap: 4,
        transition: 'all 0.15s',
        whiteSpace: 'nowrap',
      }}
    >
      {src}
      {count > 0 && (
        <span style={{
          background: isActive ? `${color}33` : 'rgba(255,255,255,0.07)',
          borderRadius: 4,
          padding: '0 4px',
          fontSize: 9,
          color: isActive ? color : 'var(--color-text-muted)',
        }}>
          {count}
          {src !== 'ALL' && pnl !== null && (
            <span style={{ marginLeft: 3, color: pnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)' }}>
              {pnl >= 0 ? '+' : ''}${Math.abs(pnl).toFixed(1)}
            </span>
          )}
        </span>
      )}
    </button>
  );
}

// ── Main export ───────────────────────────────────────────────────────────────

export default function TradeFeed({ positions = {}, gateLog = [], assetMacro = {} }) {
  const [tab, setTab] = useState('open');
  const [srcFilter, setSrcFilter] = useState('ALL');
  const [limit, setLimit] = useState(10);
  const [engineStatus, setEngineStatus] = useState({ status: 'SCANNING', detail: '' });
  const [isLedgerCollapsed, setIsLedgerCollapsed] = useState(false);
  const [btnHovered, setBtnHovered] = useState(false);

  // Reset limit to 10 when the filter source or tab changes
  useEffect(() => {
    setLimit(10);
  }, [srcFilter, tab]);

  useEffect(() => {
    const poll = () =>
      fetch('/api/engine-status').then(r => r.json()).then(setEngineStatus).catch(() => {});
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, []);

  const active = positions?.active || [];
  
  const closed = useMemo(() => {
    return [...(positions?.closed || [])].sort(
      (a, b) => new Date(b.exit_time || 0) - new Date(a.exit_time || 0)
    );
  }, [positions?.closed?.length]);

  // Total unrealized P&L across all open positions
  const totalUnrPnl = active.reduce((s, p) => s + parseFloat(p.unrealized_pnl || 0), 0);
  const totalUnrColor = totalUnrPnl > 0 ? 'var(--color-profit)' : totalUnrPnl < 0 ? 'var(--color-loss)' : 'var(--color-text-muted)';

  // Per-source counts + P&L for filter pills
  const srcStats = useMemo(() => {
    return SRC_FILTERS.slice(1).map(src => {
      const filtered = filterBySrc(closed, src);
      const pnl = filtered.reduce((s, p) => s + parseFloat(p.realized_pnl ?? 0), 0);
      return { src, count: filtered.length, pnl: filtered.length > 0 ? pnl : null };
    });
  }, [closed]);

  const filteredClosed = useMemo(() => {
    return filterBySrc(closed, srcFilter);
  }, [closed, srcFilter]);

  const visibleClosed = useMemo(() => {
    return filteredClosed.slice(0, limit);
  }, [filteredClosed, limit]);

  // Stable row renderer — prevents VirtualList from discarding and re-creating
  // DOM nodes on every parent render (critical for 120Hz smooth scrolling).
  const renderClosedRow = useCallback((p, idx) => (
    <ClosedRow key={p.order_id || idx} p={p} />
  ), []);

  return (
    <SpotlightMask>
      <div
        className="glass-panel"
        style={{ padding: 'var(--spacing-20)', display: 'flex', flexDirection: 'column' }}
      >
        {/* Header: title + pills + tabs */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: isLedgerCollapsed ? 0 : 10, flexWrap: 'wrap', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 15, letterSpacing: '-0.01em' }}>
              Trade Ledger
            </div>
            <MarketSessionPill />
            <RegimePill />
            <MacroTrendArrow />
            <button
              onClick={() => setIsLedgerCollapsed(c => !c)}
              onMouseEnter={() => setBtnHovered(true)}
              onMouseLeave={() => setBtnHovered(false)}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 4,
                background: btnHovered ? 'rgba(192,192,215,0.08)' : 'rgba(255,255,255,0.04)',
                border: `1px solid ${btnHovered ? 'rgba(192,192,215,0.35)' : 'rgba(255,255,255,0.08)'}`,
                boxShadow: btnHovered ? '0 0 10px rgba(192,192,215,0.15), inset 0 1px 0 rgba(255,255,255,0.08)' : 'none',
                borderRadius: 6, padding: '2px 8px', cursor: 'pointer',
                fontSize: 9, fontWeight: 700,
                color: btnHovered ? '#d4d4e8' : '#a1a1aa',
                transition: 'all 0.2s',
                marginLeft: 4
              }}
            >
              <span style={{ display: 'inline-block', transform: isLedgerCollapsed ? 'rotate(0deg)' : 'rotate(180deg)', transition: 'transform 0.25s' }}>▾</span>
              {isLedgerCollapsed ? 'Expand' : 'Collapse'}
            </button>
          </div>
          {!isLedgerCollapsed && (
            <SlidingTabs activeTab={tab} setActiveTab={setTab} activeCount={active.length} historyCount={closed.length} />
          )}
        </div>

        {!isLedgerCollapsed && (
          <>
            {/* Candle countdown + per-asset macro bar */}
            <CandleCountdownBar assetMacro={assetMacro} />

        {/* Gate Event Log lives in Analytics tab (not here) */}

        {/* Open tab: unrealized P&L live strip + positions */}
        {tab === 'open' && (
          <>
            {active.length > 0 && (
              <div style={{
                display: 'flex', alignItems: 'center', gap: 8,
                marginBottom: 8, padding: '5px 10px',
                background: `${totalUnrPnl >= 0 ? 'rgba(16,185,129,0.05)' : 'rgba(239,68,68,0.05)'}`,
                borderRadius: 6,
                border: `1px solid ${totalUnrPnl >= 0 ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)'}`,
                transition: 'all 0.3s',
              }}>
                <span style={{ fontSize: 9, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em' }}>
                  Unrealized P&amp;L
                </span>
                <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 800, fontSize: 14, color: totalUnrColor }}>
                  {totalUnrPnl >= 0 ? '+' : ''}${totalUnrPnl.toFixed(2)}
                </span>
                <span style={{ fontSize: 9, color: 'var(--color-text-muted)', marginLeft: 'auto' }}>
                  {active.length} position{active.length !== 1 ? 's' : ''} open
                </span>
              </div>
            )}
            {active.length > 0 && <ColHeaders cols={OPEN_COLS} grid={OPEN_GRID} />}
            <div style={{ overflowY: 'auto', maxHeight: 300 }}>
              {active.length === 0
                ? <div style={{ color: 'var(--color-text-muted)', fontSize: 13, textAlign: 'center', padding: 32 }}>No open positions</div>
                : active.map((p, i) => <OpenRow key={p.order_id || i} p={p} />)
              }
            </div>
          </>
        )}

        {/* History tab */}
        {tab === 'history' && (
          <>
            {/* Source filter + summary stats */}
            {closed.length > 0 && (
              <>
                <div style={{ marginBottom: 12 }}>
                  <SlidingPills
                    activeFilter={srcFilter}
                    setActiveFilter={setSrcFilter}
                    srcStats={srcStats}
                    closedCount={closed.length}
                  />
                </div>
                <ClosedSummary closed={visibleClosed} />

                {/* Session Analytics (drawdown + per-type sparklines) */}
                <SessionAnalytics closed={closed} />

                {/* Asset Heatmap */}
                <AssetHeatmap closed={closed} />

                {/* Source Heatmap */}
                <SourceHeatmap closed={closed} />

                {/* Timeframe Heatmap */}
                <TimeframeHeatmap closed={closed} />

                {/* Assets vs Timeframe Heatmap */}
                <AssetVsTimeframeHeatmap closed={closed} />
              </>
            )}

            {/* Engine status pill — shown when bot is idle for 5+ minutes */}
            <EngineStatusPill
              status={engineStatus.status}
              detail={engineStatus.detail}
              lastTradeAgo={closed.length > 0 ? Math.floor((Date.now() / 1000 - (typeof closed[0].exit_time === 'string' ? new Date(closed[0].exit_time).getTime() / 1000 : Number(closed[0].exit_time || 0))) / 60) : 999}
            />

            {/* Trade rows */}
            <ColHeaders cols={CLOSED_COLS} grid={CLOSED_GRID} />
            {filteredClosed.length === 0 ? (
              <div style={{ color: 'var(--color-text-muted)', fontSize: 13, textAlign: 'center', padding: 32 }}>
                {srcFilter === 'ALL' ? 'No closed trades yet' : `No ${srcFilter} trades yet`}
              </div>
            ) : (
              <VirtualList
                items={filteredClosed}
                itemHeight={38}
                containerHeight={380}
                renderRow={renderClosedRow}
              />
            )}
          </>
        )}
      </>
    )}
      </div>
    </SpotlightMask>
  );
}
