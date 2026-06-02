// TradeFeed.jsx — tabbed trade ledger: Open Positions + Trade History
import { useState, useEffect, useCallback } from 'react';
import SpotlightMask from './common/SpotlightMask';

// ── helpers ──────────────────────────────────────────────────────────────────

function assetFromTitle(title) {
  if (/bitcoin/i.test(title))  return 'BTC';
  if (/ethereum/i.test(title)) return 'ETH';
  if (/solana/i.test(title))   return 'SOL';
  if (/\bxrp\b/i.test(title))  return 'XRP';
  if (/doge/i.test(title))     return 'DOGE';
  if (/hype/i.test(title))     return 'HYPE';
  if (/bnb/i.test(title) || /binance/i.test(title)) return 'BNB';
  return '?';
}

function tfFromTitle(title) {
  const tagMatch = title.match(/\[(5m|15m)\]/);
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

function parseMeta(p) {
  const title = p.event_title || '';
  const aTag = title.match(/\[(BTC|ETH|SOL|XRP|DOGE|HYPE|BNB)\]/);
  const tTag = title.match(/\[(5m|15m)\]/);
  const xTag = title.match(/\[(SINGLE|DUAL_MAIN|DUAL_HEDGE|DUAL)\]/);
  return {
    asset:     aTag ? aTag[1] : assetFromTitle(title),
    timeframe: tTag ? tTag[1] : tfFromTitle(title),
    type:      xTag ? xTag[1].replace('_MAIN','').replace('_HEDGE','*') : 'SINGL',
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
  'LAT-ARB':        { label: 'LAT',  color: '#2b7fff' },
  'FAIR-VAL':       { label: 'FV',   color: '#00d4a3' },
  'REVERSAL-SNIPE': { label: 'REV',  color: '#ff007a' },
  'SIGNAL':         { label: 'SIG',  color: 'var(--color-text-muted)' },
};
function entryTypeCfg(t) { return ENTRY_TYPE_CONFIG[t] || ENTRY_TYPE_CONFIG['SIGNAL']; }

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

  const color = secs < 15 ? 'var(--color-loss)' : secs < 60 ? 'var(--color-amber)' : 'var(--color-profit)';
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

const OPEN_COLS    = ['In (Local)', 'Asset', 'TF', 'Dir', 'Src', 'Entry¢', 'Cur¢', 'Target¢', 'Stop¢', 'Unr P&L', 'Hold', 'Closes In'];
const OPEN_GRID    = '66px 55px 42px 50px 40px 48px 48px 52px 48px 68px 48px 80px';

// ── Row components ────────────────────────────────────────────────────────────

function ClosedRow({ p }) {
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
    }}>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-text-muted)' }}>{fmtLocalDT(p.entry_time)}</span>
      <span style={{ fontFamily: 'var(--font-primary)', fontWeight: 700, fontSize: 13, color: 'var(--color-text-primary)' }}>{meta.asset}</span>
      <span style={{ color: 'var(--color-text-muted)', fontFamily: 'var(--font-mono)' }}>{meta.timeframe}</span>
      <span style={{ fontWeight: 700, fontSize: 9, letterSpacing: '0.05em', color: entryTypeCfg(p.entry_type).color }}>
        {entryTypeCfg(p.entry_type).label}
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
}

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
      borderLeft: `3px solid ${isDual ? 'var(--color-accent)' : 'var(--color-text-muted)'}`,
      borderBottom: '1px solid var(--color-border-subtle)',
      fontSize: 11, fontStyle: 'italic', opacity: 0.9,
    }}>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-text-muted)' }}>{fmtLocal(p.entry_time)}</span>
      <span style={{ fontFamily: 'var(--font-display)', fontWeight: 900, fontSize: 13, color: 'var(--color-text-primary)' }}>{meta.asset}</span>
      <span style={{ color: 'var(--color-text-muted)' }}>{meta.timeframe}</span>
      <span style={{ fontWeight: 600, color: dir === 'UP' ? 'var(--color-profit)' : 'var(--color-loss)' }}>
        {dir === 'UP' ? '↑ UP' : '↓ DN'}
      </span>
      <span style={{ fontWeight: 700, fontSize: 9, letterSpacing: '0.05em', color: entryTypeCfg(p.entry_type).color }}>
        {entryTypeCfg(p.entry_type).label}
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
      {expiry > 0 ? <CountdownTimer expiry_ts={expiry} /> : <span style={{ color: 'var(--color-text-muted)' }}>—</span>}
    </div>
  );
}

// ── Tab button ─────────────────────────────────────────────────────────────

function Tab({ label, count, active, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        background: active ? 'var(--color-obsidian)' : 'var(--color-cream-deep)',
        border: '1px solid transparent',
        borderRadius: 'var(--radius-md)',
        padding: '6px 16px',
        fontSize: 12, fontFamily: 'var(--font-primary)', fontWeight: active ? 600 : 500,
        color: active ? 'var(--color-snow)' : 'var(--color-graphite)',
        cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8,
        transition: 'all 0.15s ease',
      }}
    >
      {label}
      <span style={{
        background: active ? 'rgba(255,255,255,0.2)' : 'var(--color-cream-dark)',
        color: active ? '#fff' : 'var(--color-obsidian)',
        borderRadius: 10, padding: '1px 7px', fontSize: 10, fontWeight: 700,
      }}>{count}</span>
    </button>
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

// ── P&L sparkline ─────────────────────────────────────────────────────────────

function PnLSparkline({ values }) {
  if (values.length < 2) return null;
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 0);
  const range = max - min || 1;
  const W = 220, H = 28;
  const toY = v => H - ((v - min) / range) * (H - 4) - 2;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * W},${toY(v)}`).join(' ');
  const zeroY = toY(0);
  const last = values[values.length - 1];
  const lineColor = last >= 0 ? '#00c853' : '#ff1744';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
      <span style={{ fontSize: 9, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', flexShrink: 0 }}>P&amp;L Trail</span>
      <svg width={W} height={H} style={{ overflow: 'visible', flexShrink: 0 }}>
        <defs>
          <linearGradient id="pnlGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor={lineColor} stopOpacity="0.4" />
            <stop offset="100%" stopColor={lineColor} stopOpacity="1" />
          </linearGradient>
        </defs>
        <line x1={0} y1={zeroY} x2={W} y2={zeroY}
              stroke="rgba(255,255,255,0.12)" strokeWidth={0.5} strokeDasharray="4,3" />
        <polyline points={pts} fill="none" stroke="url(#pnlGrad)" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" />
        <circle cx={(values.length - 1) / (values.length - 1) * W} cy={toY(last)} r={3}
                fill={lineColor} stroke="var(--color-bg-card, #111)" strokeWidth={1.5} />
      </svg>
      <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', fontWeight: 800, color: lineColor, flexShrink: 0 }}>
        {last >= 0 ? '+' : ''}${last.toFixed(2)}
      </span>
    </div>
  );
}

// ── Summary bar ──────────────────────────────────────────────────────────────

function ClosedSummary({ closed }) {
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

  // Cumulative P&L sparkline (chronological = reversed from newest-first)
  const chronoClosed = [...closed].reverse();
  const cumValues = chronoClosed.reduce((acc, p) => {
    acc.push((acc.length > 0 ? acc[acc.length - 1] : 0) + parseFloat(p.realized_pnl ?? 0));
    return acc;
  }, []);

  // P&L velocity ($/hr)
  const oldestTs = closed.length > 0
    ? (closed[closed.length - 1].entry_time || closed[closed.length - 1].timestamp || 0)
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

  // Session × Regime table data
  const SESSION_ORDER = ['Asian', 'EU', 'US', 'Off-Peak', 'Weekend'];
  const REGIME_ORDER  = ['TRENDING', 'MEAN_REVERTING', 'COMPRESSION', 'VOLATILE_CHAOS'];
  const REGIME_SHORT  = { TRENDING: 'Trend', MEAN_REVERTING: 'Mean-Rev', COMPRESSION: 'Compr', VOLATILE_CHAOS: 'Chaos' };

  function getSessionLabel(entryTs) {
    if (!entryTs) return null;
    const d   = new Date(entryTs * 1000);
    const day = d.getUTCDay();
    if (day === 0 || day === 6) return 'Weekend';
    const h = d.getUTCHours() + d.getUTCMinutes() / 60;
    if (h >= 13.5 && h < 22) return 'US';
    if (h >= 7   && h < 16)  return 'EU';
    if (h >= 0   && h < 8)   return 'Asian';
    return 'Off-Peak';
  }

  const srCells = {};
  for (const t of closed) {
    const sess   = getSessionLabel(t.entry_time || t.timestamp);
    const regime = t.regime || 'UNKNOWN';
    if (!sess || regime === 'UNKNOWN') continue;
    const key = `${sess}|${regime}`;
    if (!srCells[key]) srCells[key] = { wins: 0, total: 0, pnl: 0 };
    srCells[key].total++;
    srCells[key].pnl += parseFloat(t.realized_pnl ?? 0);
    if (parseFloat(t.realized_pnl ?? 0) > 0) srCells[key].wins++;
  }
  const activeSessions = SESSION_ORDER.filter(s => REGIME_ORDER.some(r => srCells[`${s}|${r}`]));
  const activeRegimes  = REGIME_ORDER.filter(r => SESSION_ORDER.some(s => srCells[`${s}|${r}`]));
  const showSRTable = activeSessions.length > 0 && activeRegimes.length > 0;

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
      <PnLSparkline values={cumValues} />

      {/* Session × Regime analytics table */}
      {showSRTable && (
        <div style={{ marginTop: 10, overflowX: 'auto' }}>
          <div style={{ fontSize: 9, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 4 }}>
            Session × Regime
          </div>
          <table style={{ borderCollapse: 'collapse', fontSize: 10, width: '100%' }}>
            <thead>
              <tr>
                <th style={{ textAlign: 'left', color: 'var(--color-text-muted)', fontWeight: 500, paddingRight: 10, paddingBottom: 3 }}>Session</th>
                {activeRegimes.map(r => (
                  <th key={r} style={{ textAlign: 'center', color: REGIME_COLORS[r] || '#6b7280', fontWeight: 600, paddingBottom: 3, paddingRight: 8 }}>
                    {REGIME_SHORT[r] || r}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {activeSessions.map(sess => (
                <tr key={sess}>
                  <td style={{ color: 'var(--color-text-secondary)', paddingRight: 10, paddingBottom: 2 }}>{sess}</td>
                  {activeRegimes.map(r => {
                    const cell = srCells[`${sess}|${r}`];
                    if (!cell) return <td key={r} style={{ textAlign: 'center', color: 'var(--color-text-muted)', paddingRight: 8 }}>—</td>;
                    const wr = Math.round((cell.wins / cell.total) * 100);
                    const pnlStr = `${cell.pnl >= 0 ? '+' : ''}$${cell.pnl.toFixed(1)}`;
                    const wrColor = wr >= 65 ? 'var(--color-profit)' : wr >= 50 ? 'var(--color-amber)' : 'var(--color-loss)';
                    return (
                      <td key={r} style={{ textAlign: 'center', paddingRight: 8, paddingBottom: 2 }}>
                        <span style={{ color: cell.total < 3 ? 'var(--color-text-muted)' : wrColor, fontWeight: 700 }}>{wr}%</span>
                        <span style={{ display: 'block', fontSize: 9, color: cell.pnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)', opacity: 0.8 }}>{pnlStr}</span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Source filter pills ───────────────────────────────────────────────────────

const SRC_FILTERS = ['ALL', 'LAT', 'FV', 'REV', 'SIG'];
const SRC_TO_ENTRY_TYPE = {
  LAT: 'LAT-ARB',
  FV:  'FAIR-VAL',
  REV: 'REVERSAL-SNIPE',
  SIG: 'SIGNAL',
};

function filterBySrc(trades, src) {
  if (src === 'ALL') return trades;
  const et = SRC_TO_ENTRY_TYPE[src];
  return trades.filter(p => (p.entry_type || 'SIGNAL') === et);
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

export default function TradeFeed({ positions = {} }) {
  const [tab, setTab] = useState('open');
  const [srcFilter, setSrcFilter] = useState('ALL');

  const active = positions?.active || [];
  const closed = [...(positions?.closed || [])].sort(
    (a, b) => new Date(b.exit_time || 0) - new Date(a.exit_time || 0)
  );

  // Total unrealized P&L across all open positions
  const totalUnrPnl = active.reduce((s, p) => s + parseFloat(p.unrealized_pnl || 0), 0);

  // Per-source counts + P&L for filter pills
  const srcStats = SRC_FILTERS.slice(1).map(src => {
    const filtered = filterBySrc(closed, src);
    const pnl = filtered.reduce((s, p) => s + parseFloat(p.realized_pnl ?? 0), 0);
    return { src, count: filtered.length, pnl: filtered.length > 0 ? pnl : null };
  });

  const visibleClosed = filterBySrc(closed, srcFilter);

  return (
    <SpotlightMask>
      <div
        className="glass-panel"
        style={{ padding: 'var(--spacing-20)', display: 'flex', flexDirection: 'column' }}
      >
        {/* Header: title + market session pill + tabs */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10, flexWrap: 'wrap', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 15 }}>
              Trade Ledger
            </div>
            <MarketSessionPill />
            <RegimePill />
            <MacroTrendArrow />
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <Tab label="Open"    count={active.length} active={tab === 'open'}    onClick={() => setTab('open')} />
            <Tab label="History" count={closed.length} active={tab === 'history'} onClick={() => setTab('history')} />
          </div>
        </div>

        {/* Open tab: unrealized P&L total pill */}
        {tab === 'open' && active.length > 0 && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            marginBottom: 8,
            padding: '5px 10px',
            background: 'rgba(255,255,255,0.03)',
            borderRadius: 6,
            border: '1px solid rgba(255,255,255,0.07)',
          }}>
            <span style={{ fontSize: 9, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em' }}>
              Total Unrealized P&amp;L
            </span>
            <span style={{
              fontFamily: 'var(--font-mono)', fontWeight: 800, fontSize: 14,
              color: totalUnrPnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)',
            }}>
              {totalUnrPnl >= 0 ? '+' : ''}${totalUnrPnl.toFixed(2)}
            </span>
            <span style={{ fontSize: 9, color: 'var(--color-text-muted)', marginLeft: 'auto' }}>
              {active.length} position{active.length !== 1 ? 's' : ''} open
            </span>
          </div>
        )}

        {/* History tab: source filter pills + summary */}
        {tab === 'history' && closed.length > 0 && (
          <>
            <div style={{ display: 'flex', gap: 4, marginBottom: 8, flexWrap: 'wrap' }}>
              <SrcPill
                src="ALL" active={srcFilter === 'ALL'} count={closed.length} pnl={null}
                onClick={() => setSrcFilter('ALL')}
              />
              {srcStats.map(({ src, count, pnl }) => (
                <SrcPill
                  key={src} src={src} active={srcFilter === src}
                  count={count} pnl={pnl}
                  onClick={() => setSrcFilter(src)}
                />
              ))}
            </div>
            <ClosedSummary closed={visibleClosed} />
          </>
        )}

        {/* Column headers */}
        {tab === 'history' && <ColHeaders cols={CLOSED_COLS} grid={CLOSED_GRID} />}
        {tab === 'open' && active.length > 0 && <ColHeaders cols={OPEN_COLS} grid={OPEN_GRID} />}

        {/* Rows */}
        <div style={{ overflowY: 'auto', maxHeight: 400, flex: 1 }}>
          {tab === 'history' && (
            visibleClosed.length === 0
              ? <div style={{ color: 'var(--color-text-muted)', fontSize: 13, textAlign: 'center', padding: 32 }}>
                  {srcFilter === 'ALL' ? 'No closed trades yet' : `No ${srcFilter} trades yet`}
                </div>
              : visibleClosed.map((p, i) => <ClosedRow key={p.order_id || i} p={p} />)
          )}
          {tab === 'open' && (
            active.length === 0
              ? <div style={{ color: 'var(--color-text-muted)', fontSize: 13, textAlign: 'center', padding: 32 }}>No open positions</div>
              : active.map((p, i) => <OpenRow key={p.order_id || i} p={p} />)
          )}
        </div>
      </div>
    </SpotlightMask>
  );
}
