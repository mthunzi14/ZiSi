import React, { useState, useEffect, useRef } from 'react';
import './Header.css';

export default function Header({ metrics, onRefresh, autoRefresh = true }) {
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [toast, setToast]               = useState(null);
  const prevMetricsRef  = useRef(metrics);
  const toastTimerRef   = useRef(null);
  const lastRefreshRef  = useRef(0);

  // ── Auto-refresh: fires every 30 s but respects manual refreshes ────────
  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => {
      if (Date.now() - lastRefreshRef.current > 30_000) {
        handleRefresh();
      }
    }, 30_000);
    return () => clearInterval(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRefresh]);

  // ── Cleanup ──────────────────────────────────────────────────────────────
  useEffect(() => () => { if (toastTimerRef.current) clearTimeout(toastTimerRef.current); }, []);

  // ── Toast helper — matches Positions.jsx simple style ───────────────────
  const showToast = (msg) => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    setToast(msg);
    toastTimerRef.current = setTimeout(() => setToast(null), 2500);
  };

  // ── Refresh handler ──────────────────────────────────────────────────────
  const handleRefresh = async () => {
    lastRefreshRef.current = Date.now();
    setIsRefreshing(true);
    const before = prevMetricsRef.current || {};

    try {
      const newData = await onRefresh();

      if (!newData) {
        showToast('No data returned from backend', 'neutral');
        return;
      }

      prevMetricsRef.current = newData;

      const dSignals = (newData.signals_evaluated || 0) - (before.signals_evaluated || 0);
      const dPoly    = (newData.polymarket_matches || 0) - (before.polymarket_matches || 0);
      const dKalshi  = (newData.kalshi_matches     || 0) - (before.kalshi_matches     || 0);
      const dPnl     = (newData.pnl || 0) - (before.pnl || 0);

      console.debug('[ZiSi Refresh] deltas:', { dSignals, dPoly, dKalshi, dPnl });

      if (dSignals > 0) {
        const parts = [`+${dSignals} signals`];
        if (dPoly   > 0) parts.push(`+${dPoly} Poly`);
        if (dKalshi > 0) parts.push(`+${dKalshi} Kalshi`);
        if (Math.abs(dPnl) > 0.001) parts.push(`${dPnl > 0 ? '+' : ''}$${dPnl.toFixed(2)}`);
        showToast(`✓ ${parts.join(' | ')}`);
      } else {
        showToast('✓ Refreshed');
      }
    } catch (err) {
      console.error('[ZiSi Refresh] error:', err);
      showToast('✗ Refresh failed');
    } finally {
      setTimeout(() => setIsRefreshing(false), 400);
    }
  };

  const lastUpdateText = (() => {
    const raw = metrics?.last_update || metrics?.last_updated;
    if (!raw) return 'never';
    try { return new Date(raw).toLocaleTimeString(); } catch { return 'never'; }
  })();

  return (
    <>
      {toast && <div className="header-toast">{toast}</div>}

      <header className="page-header">
        <div className="header-logo">
          <img src="/zisi-logo.png" alt="ZiSi Logo" className="logo-icon" />
          <div className="logo-text">
            <h1>ZiSi</h1>
            <p>Intuitive Investing</p>
          </div>
        </div>

        <div className="header-controls">
          <div className="refresh-info">
            <span className="last-updated">Updated: {lastUpdateText}</span>
          </div>

          <div className="refresh-controls">
            <label className="auto-refresh-toggle">
              <input
                type="checkbox"
                defaultChecked={autoRefresh}
                aria-label="Auto-refresh every 30 seconds"
                readOnly
              />
              <span>Auto (30s)</span>
            </label>

            <button
              onClick={handleRefresh}
              className={`refresh-btn ${isRefreshing ? 'refreshing' : ''}`}
              disabled={isRefreshing}
              aria-label="Refresh metrics"
            >
              {isRefreshing ? 'Refreshing...' : 'Refresh Now'}
            </button>
          </div>
        </div>
      </header>
    </>
  );
}
