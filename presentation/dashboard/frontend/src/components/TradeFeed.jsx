// TradeFeed.jsx — tabbed trade ledger: Open Positions + Trade History
import { useState, useEffect } from 'react';
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

// ── Summary bar ──────────────────────────────────────────────────────────────

function ClosedSummary({ closed }) {
  const wins   = closed.filter(p => parseFloat(p.realized_pnl ?? 0) > 0).length;
  const losses = closed.filter(p => parseFloat(p.realized_pnl ?? 0) < 0).length;
  const evens  = closed.length - wins - losses;
  const totalPnl = closed.reduce((s, p) => s + parseFloat(p.realized_pnl ?? 0), 0);
  const wrN = wins + losses;
  const wr = wrN > 0 ? ((wins / wrN) * 100).toFixed(1) : '—';
  const [ciLo, ciHi] = wilson95(wins, wrN);
  const gross = closed.reduce((s, p) => {
    const pnl = parseFloat(p.realized_pnl ?? 0);
    return { w: s.w + (pnl > 0 ? pnl : 0), l: s.l + (pnl < 0 ? Math.abs(pnl) : 0) };
  }, { w: 0, l: 0 });
  const pf = gross.l > 0 ? (gross.w / gross.l).toFixed(2) : '∞';

  return (
    <div style={{
      display: 'flex', gap: 20, padding: '8px 0 12px 0',
      borderBottom: '1px solid rgba(255,255,255,0.06)',
      marginBottom: 8, flexWrap: 'wrap',
    }}>
      {[
        { label: 'Trades', val: closed.length, color: 'var(--color-text-primary)' },
        { label: 'Win Rate', val: (
            <>{wr}%{wrN > 0 && (
              <span style={{ fontSize: 9, fontWeight: 500, color: 'var(--color-text-muted)', marginLeft: 4 }}>
                95% CI {ciLo.toFixed(0)}–{ciHi.toFixed(0)}%
              </span>
            )}</>
          ), color: parseFloat(wr) >= 62 ? 'var(--color-profit)' : parseFloat(wr) >= 45 ? 'var(--color-amber)' : 'var(--color-loss)' },
        { label: 'W / L / E', val: `${wins} / ${losses} / ${evens}`, color: 'var(--color-text-secondary)' },
        { label: 'Total P&L', val: `${totalPnl >= 0 ? '+' : ''}$${totalPnl.toFixed(2)}`, color: totalPnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)' },
        { label: 'Profit Factor', val: pf, color: parseFloat(pf) >= 1.5 ? 'var(--color-profit)' : parseFloat(pf) >= 1 ? 'var(--color-amber)' : 'var(--color-loss)' },
      ].map(({ label, val, color }) => (
        <div key={label}>
          <div style={{ fontSize: 9, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 2 }}>{label}</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, color }}>{val}</div>
        </div>
      ))}
    </div>
  );
}

// ── Main export ───────────────────────────────────────────────────────────────

export default function TradeFeed({ positions = {} }) {
  const [tab, setTab] = useState('open');

  const active = positions?.active || [];
  const closed = [...(positions?.closed || [])].sort((a, b) => new Date(b.exit_time || 0) - new Date(a.exit_time || 0)); // newest first

  return (
    <SpotlightMask>
      <div 
        className="glass-panel"
        style={{
          padding: 'var(--spacing-20)',
          display: 'flex', flexDirection: 'column',
        }}
      >
        {/* Header + tabs */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 15 }}>
            Trade Ledger
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <Tab label="Open"    count={active.length} active={tab === 'open'}    onClick={() => setTab('open')} />
            <Tab label="History" count={closed.length} active={tab === 'history'} onClick={() => setTab('history')} />
          </div>
        </div>

        {/* Closed summary strip */}
        {tab === 'history' && closed.length > 0 && <ClosedSummary closed={closed} />}

        {/* Column headers */}
        {tab === 'history' && <ColHeaders cols={CLOSED_COLS} grid={CLOSED_GRID} />}
        {tab === 'open'    && active.length > 0 && <ColHeaders cols={OPEN_COLS} grid={OPEN_GRID} />}

        {/* Rows */}
        <div style={{ overflowY: 'auto', maxHeight: 400, flex: 1 }}>
          {tab === 'history' && (
            closed.length === 0
              ? <div style={{ color: 'var(--color-text-muted)', fontSize: 13, textAlign: 'center', padding: 32 }}>No closed trades yet</div>
              : closed.map((p, i) => <ClosedRow key={p.order_id || i} p={p} />)
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
