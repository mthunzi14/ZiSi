import { useState, useEffect, useCallback } from 'react';
import './Positions.css';

// ── Helpers ──────────────────────────────────────────────────────────────────
function fmt$(n) {
  if (n == null) return '—';
  const v = parseFloat(n);
  const sign = v >= 0 ? '+' : '-';
  return `${sign}$${Math.abs(v).toFixed(2)}`;
}

function fmtPct(n) {
  if (n == null) return '—';
  const v = parseFloat(n);
  const sign = v >= 0 ? '+' : '-';
  return `${sign}${Math.abs(v).toFixed(1)}%`;
}

function fmtPrice(n) {
  if (n == null) return '—';
  return `$${parseFloat(n).toFixed(4)}`;
}

function holdStr(minutes) {
  if (minutes == null || isNaN(minutes) || minutes < 0) return '—';
  if (minutes < 60) return `${Math.round(minutes)}m`;
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

/**
 * Calculate live hold time from an ISO timestamp.
 * Re-evaluated every render — caller forces re-renders every 60s via tick state.
 */
function dynamicHoldStr(entryTimeStr) {
  if (!entryTimeStr) return '—';
  const opened = new Date(entryTimeStr);
  if (isNaN(opened.getTime())) return '—';
  const minutes = (Date.now() - opened.getTime()) / 60_000;
  return holdStr(Math.max(0, minutes));
}

function pnlClass(val) {
  if (val == null) return '';
  return parseFloat(val) > 0 ? 'pnl-positive' : parseFloat(val) < 0 ? 'pnl-negative' : 'pnl-zero';
}

function truncate(str, n = 48) {
  if (!str) return '—';
  return str.length > n ? str.slice(0, n) + '…' : str;
}

// ── Summary cards ─────────────────────────────────────────────────────────────
function SummaryCard({ label, value, sub, color }) {
  return (
    <div className="pos-summary-card">
      <div className="pos-summary-label">{label}</div>
      <div className="pos-summary-value" style={color ? { color } : undefined}>{value}</div>
      {sub && <div className="pos-summary-sub">{sub}</div>}
    </div>
  );
}

// ── Active positions table ────────────────────────────────────────────────────
function ActiveTable({ rows }) {
  if (!rows || rows.length === 0) {
    return (
      <div className="pos-empty">
        <span className="pos-empty-icon">📭</span>
        <span>No open positions right now — waiting for next trade signal</span>
      </div>
    );
  }

  return (
    <div className="pos-table-wrap">
      <table className="pos-table">
        <thead>
          <tr>
            <th>Market</th>
            <th>Event</th>
            <th>Side</th>
            <th>Entry</th>
            <th>Current</th>
            <th>Size</th>
            <th>Unrealized P&amp;L</th>
            <th>Held</th>
            <th>Resolves</th>
            <th>Target / Stop</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((pos) => {
            const unrealized = pos.unrealized_pnl;
            const isKalshi = pos.market === 'KALSHI';
            // entry_time (Polymarket) or open_time (Kalshi) — both are ISO strings
            const openTimeStr = pos.entry_time || pos.open_time || null;
            return (
              <tr key={pos.order_id} className={isKalshi ? 'pos-row-kalshi' : 'pos-row-poly'}>
                <td>
                  <span className={`pos-badge pos-badge-${pos.market?.toLowerCase()}`}>
                    {pos.market}
                  </span>
                </td>
                <td className="pos-title" title={pos.event_title}>
                  {truncate(pos.event_title, 44)}
                </td>
                <td>
                  <span className={`pos-side pos-side-${pos.direction?.toLowerCase()}`}>
                    {pos.direction}
                  </span>
                </td>
                <td className="pos-mono">{fmtPrice(pos.entry_price)}</td>
                <td className="pos-mono">
                  {isKalshi ? '—' : fmtPrice(pos.current_price)}
                </td>
                <td className="pos-mono">${parseFloat(pos.size || 0).toFixed(2)}</td>
                <td className={`pos-mono ${pnlClass(unrealized)}`}>
                  {unrealized != null && parseFloat(unrealized) !== 0
                    ? fmt$(unrealized)
                    : isKalshi
                      ? <span className="pos-awaiting">Pending</span>
                      : fmt$(unrealized)}
                </td>
                {/* Dynamic hold time — recalculates from real open_time each render */}
                <td className="pos-mono pos-held-live">
                  {dynamicHoldStr(openTimeStr)}
                </td>
                <td className="pos-mono pos-resolves">
                  {pos.resolution_date
                    ? new Date(pos.resolution_date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
                    : '—'}
                </td>
                <td className="pos-mono pos-targets">
                  {isKalshi ? '—' : (
                    <>
                      <span className="target-val">{fmtPrice(pos.target_price)}</span>
                      <span className="target-sep"> / </span>
                      <span className="stop-val">{fmtPrice(pos.stop_loss)}</span>
                    </>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Closed positions table ────────────────────────────────────────────────────
function ClosedTable({ rows }) {
  const [showAll, setShowAll] = useState(false);

  if (!rows || rows.length === 0) {
    return (
      <div className="pos-empty">
        <span className="pos-empty-icon">📊</span>
        <span>No closed positions yet — they will appear here once trades resolve</span>
      </div>
    );
  }

  const visible = showAll ? rows : rows.slice(0, 15);

  return (
    <>
      <div className="pos-table-wrap">
        <table className="pos-table">
          <thead>
            <tr>
              <th>Market</th>
              <th>Event</th>
              <th>Side</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>Size</th>
              <th>P&amp;L</th>
              <th>P&amp;L %</th>
              <th>Held</th>
              <th>Result</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((pos) => (
              <tr key={pos.order_id} className={pos.realized_pnl > 0 ? 'pos-row-win' : 'pos-row-loss'}>
                <td>
                  <span className={`pos-badge pos-badge-${pos.market?.toLowerCase()}`}>
                    {pos.market}
                  </span>
                </td>
                <td className="pos-title" title={pos.event_title}>
                  {truncate(pos.event_title, 44)}
                </td>
                <td>
                  <span className={`pos-side pos-side-${pos.direction?.toLowerCase()}`}>
                    {pos.direction}
                  </span>
                </td>
                <td className="pos-mono">{fmtPrice(pos.entry_price)}</td>
                <td className="pos-mono">{fmtPrice(pos.exit_price)}</td>
                <td className="pos-mono">${parseFloat(pos.size || 0).toFixed(2)}</td>
                <td className={`pos-mono ${pnlClass(pos.realized_pnl)}`}>
                  {fmt$(pos.realized_pnl)}
                </td>
                <td className={`pos-mono ${pnlClass(pos.realized_pnl_pct)}`}>
                  {fmtPct(pos.realized_pnl_pct)}
                </td>
                <td className="pos-mono">{holdStr(parseFloat(pos.hold_hours || 0) * 60)}</td>
                <td>
                  <span className={`pos-result-badge ${pos.realized_pnl > 0 ? 'badge-win' : 'badge-loss'}`}>
                    {pos.realized_pnl > 0 ? 'WIN' : 'LOSS'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > 15 && (
        <button className="pos-show-more" onClick={() => setShowAll(s => !s)}>
          {showAll ? 'Show less' : `Show all ${rows.length} trades`}
        </button>
      )}
    </>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
export default function Positions() {
  const [data, setData]           = useState(null);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState(null);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [toast, setToast]         = useState(null);

  // Tick every 15 s — forces ActiveTable to re-render so dynamicHoldStr
  // recalculates from real open_time without waiting for an API refresh.
  const [, setTick] = useState(0);
  useEffect(() => {
    const iv = setInterval(() => setTick(t => t + 1), 15_000);
    return () => clearInterval(iv);
  }, []);

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/positions');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
      setLastRefresh(new Date());
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  // Auto-refresh every 10 s
  useEffect(() => {
    load();
    const iv = setInterval(load, 10_000);
    return () => clearInterval(iv);
  }, [load]);

  const handleRefresh = useCallback(async () => {
    setLoading(true);
    await load();
    setToast('✓ Refreshed');
    setTimeout(() => setToast(null), 2500);
  }, [load]);

  const summary = data?.summary || {};
  const active  = data?.active  || [];
  const closed  = data?.closed  || [];

  const winRate = (summary.win_count + summary.loss_count) > 0
    ? Math.round((summary.win_count / (summary.win_count + summary.loss_count)) * 100)
    : null;

  const unrealizedPnl = parseFloat(summary.unrealized_pnl || 0);

  // Initial load skeleton — show spinner instead of all-zero cards
  if (loading && data === null) {
    return (
      <div className="positions-root">
        <div className="pos-header">
          <h2 className="pos-title-h2">Positions</h2>
          <div className="pos-header-right">
            <button className="pos-refresh-btn" disabled>⟳ Loading…</button>
          </div>
        </div>
        <div className="pos-loading-skeleton">
          <div className="pos-skeleton-bar" />
          <div className="pos-skeleton-bar pos-skeleton-bar--short" />
          <div className="pos-skeleton-bar" />
        </div>
      </div>
    );
  }

  return (
    <div className="positions-root">
      {/* ── Header ── */}
      <div className="pos-header">
        <h2 className="pos-title-h2">Positions</h2>
        <div className="pos-header-right">
          {lastRefresh && (
            <span className="pos-refresh-time">
              Updated {lastRefresh.toLocaleTimeString()}
            </span>
          )}
          <button className="pos-refresh-btn" onClick={handleRefresh} disabled={loading}>
            {loading ? '⟳' : '↻'} Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="pos-error">
          ⚠️ Could not load positions: {error}
        </div>
      )}

      {/* ── Summary cards ── */}
      <div className="pos-summary-row">
        <SummaryCard
          label="Open Positions"
          value={summary.active_count ?? active.length}
          sub={`${summary.poly_active ?? 0} Polymarket  ·  ${summary.kalshi_active ?? 0} Kalshi`}
          color={active.length > 0 ? 'var(--accent-blue, #3b82f6)' : undefined}
        />
        <SummaryCard
          label="Unrealized P&L"
          value={fmt$(summary.unrealized_pnl)}
          sub="All open positions (mark-to-market)"
          color={
            unrealizedPnl > 0
              ? 'var(--green, #22c55e)'
              : unrealizedPnl < 0
              ? 'var(--red, #ef4444)'
              : undefined
          }
        />
        <SummaryCard
          label="Closed Trades"
          value={summary.closed_count ?? closed.length}
          sub={winRate != null ? `${winRate}% win rate` : 'No closed trades yet'}
        />
        <SummaryCard
          label="Realized P&L"
          value={fmt$(summary.realized_pnl)}
          sub={`${summary.win_count ?? 0}W / ${summary.loss_count ?? 0}L`}
          color={
            parseFloat(summary.realized_pnl || 0) > 0
              ? 'var(--green, #22c55e)'
              : parseFloat(summary.realized_pnl || 0) < 0
              ? 'var(--red, #ef4444)'
              : undefined
          }
        />
      </div>

      {/* ── Active positions ── */}
      <div className="pos-section">
        <div className="pos-section-header">
          <h3 className="pos-section-title">
            Active Positions
            {active.length > 0 && (
              <span className="pos-count-badge">{active.length}</span>
            )}
          </h3>
          <span className="pos-section-sub">
            Live open trades — held time updates every minute · prices refresh every 30s
          </span>
        </div>
        {loading && !data ? (
          <div className="pos-loading">Loading positions…</div>
        ) : (
          <ActiveTable rows={active} />
        )}
      </div>

      {/* ── Closed positions ── */}
      <div className="pos-section">
        <div className="pos-section-header">
          <h3 className="pos-section-title">
            Closed Positions
            {closed.length > 0 && (
              <span className="pos-count-badge pos-count-closed">{closed.length}</span>
            )}
          </h3>
          <span className="pos-section-sub">Full history of resolved trades (Polymarket + Kalshi)</span>
        </div>
        {loading && !data ? (
          <div className="pos-loading">Loading history…</div>
        ) : (
          <ClosedTable rows={closed} />
        )}
      </div>

      {/* ── Toast notification ── */}
      {toast && <div className="pos-toast">{toast}</div>}
    </div>
  );
}
