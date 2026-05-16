/**
 * useSSE — React hook for Server-Sent Events.
 *
 * Usage:
 *   const { balance, pnl, trades, status } = useSSE();
 *
 * Connects to GET /api/events on mount, reconnects automatically on disconnect,
 * and cleans up on unmount.
 *
 * Exported state slices:
 *   balance          number   — current account balance
 *   pnl              number   — session P&L
 *   trades           number   — trades executed count
 *   botStatus        string   — 'running' | 'paused' | 'offline'
 *   activeCount      number   — open positions count
 *   closedCount      number   — closed positions count
 *   winCount         number
 *   lossCount        number
 *   realizedPnl      number
 *   lastTradeClosed  object   — most recent closed trade payload (null initially)
 *   lastTradeOpened  object   — most recent opened trade payload (null initially)
 *   connected        boolean  — SSE connection is live
 */

import { useState, useEffect, useRef, useCallback } from 'react';

export function useSSE() {
  const [balance,         setBalance]         = useState(100);
  const [pnl,             setPnl]             = useState(0);
  const [trades,          setTrades]          = useState(0);
  const [botStatus,       setBotStatus]       = useState('loading');
  const [activeCount,     setActiveCount]     = useState(0);
  const [closedCount,     setClosedCount]     = useState(0);
  const [winCount,        setWinCount]        = useState(0);
  const [lossCount,       setLossCount]       = useState(0);
  const [realizedPnl,     setRealizedPnl]     = useState(0);
  const [lastTradeClosed, setLastTradeClosed] = useState(null);
  const [lastTradeOpened, setLastTradeOpened] = useState(null);
  const [connected,       setConnected]       = useState(false);

  const esRef      = useRef(null);
  const retryTimer = useRef(null);

  const connect = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
    }

    const es = new EventSource('/api/events');
    esRef.current = es;

    es.addEventListener('balance_update', (e) => {
      try {
        const d = JSON.parse(e.data);
        setBalance(parseFloat(d.balance ?? 100));
        setPnl(parseFloat(d.pnl ?? 0));
        setTrades(d.trades ?? 0);
        setBotStatus(d.status || 'running');
      } catch (_) {}
    });

    es.addEventListener('positions_snapshot', (e) => {
      try {
        const d = JSON.parse(e.data);
        setActiveCount(d.active_count ?? 0);
        setClosedCount(d.closed_count ?? 0);
        setWinCount(d.win_count      ?? 0);
        setLossCount(d.loss_count    ?? 0);
        setRealizedPnl(d.realized_pnl ?? 0);
      } catch (_) {}
    });

    es.addEventListener('trade_opened', (e) => {
      try {
        const d = JSON.parse(e.data);
        setLastTradeOpened(d);
        setActiveCount(prev => prev + 1);
      } catch (_) {}
    });

    es.addEventListener('trade_closed', (e) => {
      try {
        const d = JSON.parse(e.data);
        setLastTradeClosed(d);
        setActiveCount(prev => Math.max(0, prev - 1));
        setClosedCount(prev => prev + 1);
        const profit = parseFloat(d.profit ?? 0);
        if (profit > 0) setWinCount(prev => prev + 1);
        else            setLossCount(prev => prev + 1);
        setRealizedPnl(prev => parseFloat((prev + profit).toFixed(4)));
      } catch (_) {}
    });

    // heartbeat keeps the connection alive silently
    es.addEventListener('heartbeat', () => {});

    es.onopen = () => {
      setConnected(true);
      setBotStatus(prev => prev === 'loading' ? 'running' : prev);
    };

    es.onerror = () => {
      setConnected(false);
      es.close();
      esRef.current = null;
      // Reconnect after 5s
      retryTimer.current = setTimeout(connect, 5_000);
    };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      if (esRef.current)      esRef.current.close();
      if (retryTimer.current) clearTimeout(retryTimer.current);
    };
  }, [connect]);

  return {
    balance, pnl, trades, botStatus,
    activeCount, closedCount, winCount, lossCount, realizedPnl,
    lastTradeClosed, lastTradeOpened, connected,
  };
}
