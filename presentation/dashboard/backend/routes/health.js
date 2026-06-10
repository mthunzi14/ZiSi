import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { readTradesFile } from '../utils/fileReader.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const router = express.Router();

const readJSON = (fp) => JSON.parse(fs.readFileSync(fp, 'utf-8').replace(/^﻿/, ''));

router.get('/', (req, res) => {
  try {
    res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, proxy-revalidate');
    const stateFile = path.join(__dirname, '../../../../account_state.json');
    const runtimeFile = path.join(__dirname, '../../../../runtime_tracking.json');
    const pauseFlag = path.join(__dirname, '../../../../bot_paused.flag');

    const defaultRuntime = {
      hours: 0,
      days: 0,
      progressPercent: 0,
      goalHours: 336,
      status: 'tracking',
      start_time: null,
    };

    if (!fs.existsSync(stateFile)) {
      return res.json({
        status: 'offline',
        running: false,
        balance: 50.00,
        account_balance: 50.00,
        pnl: 0,
        realTrades: 0,
        totalSignals: 0,
        dailySignals: 0,
        dailyTrades: 0,
        dailyPnL: 0,
        winRate: 0,
        byCoin: [],
        byStrength: [],
        minutesAgo: null,
        last_update_minutes_ago: null,
        tradesExecuted: 0,
        trades_executed: 0,
        phase: 'phase_1',
        cycles_completed: 0,
        runtime: defaultRuntime,
        error: 'State file not found',
      });
    }

    const state = readJSON(stateFile);
    const lastUpdate = new Date(state.last_updated);
    const now = new Date();
    const minutesAgo = Math.floor((now - lastUpdate) / 60000);

    let status;
    if (fs.existsSync(pauseFlag) || state.paused) {
      status = 'paused';
    } else if (minutesAgo < 45) {
      status = 'running';
    } else if (minutesAgo < 90) {
      status = 'stale';
    } else {
      status = 'offline';
    }

    let balance = parseFloat(state.balance || 50);
    let pnl = parseFloat(state.pnl || 0);
    const tradesExecuted = state.trades_executed || 0;

    // Runtime tracking
    let runtime = defaultRuntime;
    if (fs.existsSync(runtimeFile)) {
      try {
        const rt = readJSON(runtimeFile);
        const hours = rt.runtime_hours || 0;
        runtime = {
          hours: Math.round(hours * 10) / 10,
          days: Math.floor(hours / 24),
          progressPercent: rt.progress_percent || 0,
          goalHours: rt.goal_hours || 336,
          status: rt.status || 'tracking',
          start_time: rt.start_time || null,
        };
      } catch (rtErr) {
        console.warn('[HEALTH] Could not parse runtime_tracking.json:', rtErr.message);
      }
    }

    // Signal and trade analytics from trades file
    const today = new Date().toISOString().split('T')[0];
    let totalSignals = 0;
    let dailySignals = 0;
    let realTrades = 0;
    let dailyTrades = 0;
    let dailyPnL = 0;
    let winRate = 0;
    let byCoin = [];
    let byStrength = [];
    let byUTC = [];
    let maxDrawdown = 0;
    let currentDrawdown = 0;
    let consecutiveLosses = 0;
    let riskOfRuin = 'Low';
    let profitFactor = 0;
    let expectancy = 0;

    try {
      const allEntries = readTradesFile();
      const signals = allEntries.filter(e => e._type === 'signal');
      const trades = allEntries.filter(e =>
        e._type === 'trade' && !e.order_id?.toLowerCase().includes('test')
      );

      totalSignals = signals.length;
      dailySignals = signals.filter(e => {
        const ts = e.timestamp_open || e.timestamp;
        return ts?.startsWith(today);
      }).length;

      const closedTrades = trades.filter(e =>
        e.status === 'CLOSED' || e.status === 'closed'
      );
      realTrades = closedTrades.length;

      const todayTrades = closedTrades.filter(e => {
        const ts = e.timestamp_close || e.timestamp_open || e.timestamp;
        return ts?.startsWith(today);
      });
      dailyTrades = todayTrades.length;
      dailyPnL = todayTrades.reduce((sum, e) => sum + (e.profit || 0), 0);

      const wins = closedTrades.filter(e => e.profit > 0);
      const losses = closedTrades.filter(e => e.profit <= 0);
      winRate = closedTrades.length > 0 ? wins.length / closedTrades.length : 0;

      // Profit factor & expectancy
      const grossWins = wins.reduce((s, t) => s + (t.profit || 0), 0);
      const grossLosses = Math.abs(losses.reduce((s, t) => s + (t.profit || 0), 0));
      profitFactor = grossLosses > 0 ? parseFloat((grossWins / grossLosses).toFixed(2)) : (grossWins > 0 ? 99 : 0);
      const avgWin = wins.length > 0 ? grossWins / wins.length : 0;
      const avgLoss = losses.length > 0 ? grossLosses / losses.length : 0;
      expectancy = parseFloat(((winRate * avgWin) - ((1 - winRate) * avgLoss)).toFixed(2));

      // Max drawdown & current drawdown
      let peak = 0;
      let runningPnL = 0;
      let peakAtEnd = 0;
      for (const t of closedTrades) {
        runningPnL += (t.profit || 0);
        peak = Math.max(peak, runningPnL);
        const dd = peak > 0 ? ((peak - runningPnL) / peak) * 100 : 0;
        maxDrawdown = Math.max(maxDrawdown, dd);
      }
      peakAtEnd = peak;
      currentDrawdown = peakAtEnd > 0 ? parseFloat((((peakAtEnd - runningPnL) / peakAtEnd) * 100).toFixed(2)) : 0;
      maxDrawdown = parseFloat(maxDrawdown.toFixed(2));

      // Consecutive losses (most recent streak)
      const reversed = [...closedTrades].reverse();
      for (const t of reversed) {
        if ((t.profit || 0) <= 0) consecutiveLosses++;
        else break;
      }

      // Risk of ruin
      if (consecutiveLosses >= 3 || currentDrawdown > 10) riskOfRuin = 'High';
      else if (consecutiveLosses >= 2 || currentDrawdown > 5) riskOfRuin = 'Medium';
      else riskOfRuin = 'Low';

      // By UTC hour
      const hourMap = {};
      closedTrades.forEach(t => {
        const ts = t.timestamp_open || t.timestamp;
        if (!ts) return;
        const hour = new Date(ts).getUTCHours();
        if (!hourMap[hour]) hourMap[hour] = { trades: 0, wins: 0 };
        hourMap[hour].trades++;
        if ((t.profit || 0) > 0) hourMap[hour].wins++;
      });
      byUTC = Object.entries(hourMap).map(([h, d]) => ({
        hour: parseInt(h),
        trades: d.trades,
        winRate: d.trades > 0 ? parseFloat((d.wins / d.trades).toFixed(4)) : 0,
      })).sort((a, b) => a.hour - b.hour);

      const coinMap = {};
      signals.forEach(e => {
        const text = `${e.event_title || ''} ${e.coins_mentioned || ''}`.toLowerCase();
        const coin = text.includes('btc') || text.includes('bitcoin') ? 'BTC'
                   : text.includes('eth') || text.includes('ethereum') ? 'ETH'
                   : text.includes('sol') || text.includes('solana') ? 'SOL'
                   : 'OTHER';
        coinMap[coin] = (coinMap[coin] || 0) + 1;
      });
      byCoin = Object.entries(coinMap)
        .map(([name, count]) => ({ name, count }))
        .sort((a, b) => b.count - a.count);

      const strengthMap = {};
      signals.forEach(e => {
        const level = String(e.signal_confidence || 7);
        strengthMap[level] = (strengthMap[level] || 0) + 1;
      });
      byStrength = Object.entries(strengthMap)
        .map(([level, count]) => ({ level, count }))
        .sort((a, b) => parseInt(a.level) - parseInt(b.level));
    } catch (analyticsErr) {
      console.warn('[HEALTH] Could not compute analytics:', analyticsErr.message);
    }

    // ── Signal metrics from signal_evaluations.jsonl (the real data source) ──
    let missedTrades = 0;
    let missedWinRate = 0;
    let missedPnL = 0;
    let signals_evaluated = 0;
    let avg_confidence = 0;
    let signals_by_sentiment = { bullish: 0, bearish: 0, neutral: 0 };
    let signals_by_market = { BTC: 0, ETH: 0, OTHER: 0 };
    let confidence_distribution = { '9': 0, '8': 0, '7': 0 };
    let polymarket_matches = 0;
    let kalshi_trades_recorded = 0;
    let peak_hour_signals = 0;
    let off_peak_hour_signals = 0;
    const PEAK_HOURS = new Set([22, 23, 0, 1, 2, 3, 4, 5]);

    try {
      const evalFile = path.join(__dirname, '../../../../signal_evaluations.jsonl');
      if (fs.existsSync(evalFile)) {
        const evalLines = fs.readFileSync(evalFile, 'utf-8')
          .split('\n')
          .filter(l => l.trim());

        const allEvals = evalLines
          .map(l => { try { return JSON.parse(l); } catch { return null; } })
          .filter(Boolean);

        // Count ONLY current-session Kalshi trades (have an order_id = new lifecycle format).
        // Records without order_id are the 592 pre-fix orphaned sports entries — ignore them.
        kalshi_trades_recorded = allEvals.filter(
          e => e.type === 'KALSHI_TRADE' && e.order_id
        ).length;
        const nonKalshiEvals = allEvals.filter(e => e.type !== 'KALSHI_TRADE');
        signals_evaluated = nonKalshiEvals.length;

        let confSum = 0;
        for (const e of nonKalshiEvals) {
          // Confidence: sentiment_score and confidence may be 0-10 or 0-1
          const rawConf = e.signal_source === 'EnsembleML' 
            ? ((e.sentiment_score > 1 ? e.sentiment_score / 10 : e.sentiment_score) || (e.confidence > 1 ? e.confidence / 10 : e.confidence) || 0)
            : ((e.sentiment_score || e.confidence || 0) / 10);
          confSum += rawConf;

          // Sentiment distribution
          const sent = (e.sentiment || 'neutral').toLowerCase();
          if (sent in signals_by_sentiment) signals_by_sentiment[sent]++;
          else signals_by_sentiment.neutral++;

          // Market (coin) distribution
          const coin = (e.coin || '').toUpperCase();
          if (coin.includes('BITCOIN') || coin === 'BTC') signals_by_market.BTC++;
          else if (coin.includes('ETHEREUM') || coin === 'ETH') signals_by_market.ETH++;
          else signals_by_market.OTHER++;

          // Confidence distribution (score * 10 → integer bucket)
          const bucket = Math.round(rawConf * 10);
          if (bucket >= 9) confidence_distribution['9']++;
          else if (bucket >= 8) confidence_distribution['8']++;
          else if (bucket >= 7) confidence_distribution['7']++;

          // Polymarket match
          if (e.matched_event) polymarket_matches++;

          // Peak hour classification (timestamp is unix epoch float)
          try {
            const ts = e.timestamp;
            const hour = ts > 1e10
              ? new Date(ts * 1000).getUTCHours()
              : new Date(ts).getUTCHours();
            if (PEAK_HOURS.has(hour)) peak_hour_signals++;
            else off_peak_hour_signals++;
          } catch (_) { /* ignore */ }
        }

        avg_confidence = signals_evaluated > 0
          ? parseFloat((confSum / signals_evaluated).toFixed(4))
          : 0;

        // Daily count (today's non-Kalshi signal evaluations only)
        dailySignals = nonKalshiEvals.filter(e => {
          try {
            const ts = e.timestamp;
            const d = ts > 1e10 ? new Date(ts * 1000) : new Date(ts);
            return d.toISOString().startsWith(today);
          } catch { return false; }
        }).length;

        // Missed trades (confidence > 0.55 means signal had potential)
        const missed = nonKalshiEvals.filter(e => {
          const rawConf = e.sentiment_score || (e.confidence > 1 ? e.confidence / 10 : e.confidence) || 0;
          return e.trade_type === 'MISSED' && rawConf > 0.55;
        });
        missedTrades = missed.length;
        const missedWins = missed.filter(e => {
          const rawConf = e.sentiment_score || (e.confidence > 1 ? e.confidence / 10 : e.confidence) || 0;
          return rawConf > 0.6;
        }).length;
        missedWinRate = missedTrades > 0 ? parseFloat((missedWins / missedTrades).toFixed(4)) : 0;
        missedPnL = parseFloat(((missedWins * 2) - ((missedTrades - missedWins) * 1)).toFixed(2));

        // Use signal_evaluations count as the authoritative totalSignals
        if (signals_evaluated > totalSignals) {
          totalSignals = signals_evaluated;
          dailySignals = nonKalshiEvals.filter(e => {
            try {
              const ts = e.timestamp;
              const d = ts > 1e10 ? new Date(ts * 1000) : new Date(ts);
              return d.toISOString().startsWith(today);
            } catch { return false; }
          }).length;
        }
      }
    } catch (missedErr) {
      console.warn('[HEALTH] Could not compute signal metrics:', missedErr.message);
    }

    // ── ML pipeline progress (written by ml_pipeline.py) ──────────────────
    let ml_progress = { cycles_collected: 0, cycles_needed: 50, progress_percent: 0, models: {} };
    try {
      const mlFile = path.join(__dirname, '../../../../ml_progress.json');
      if (fs.existsSync(mlFile)) {
        ml_progress = readJSON(mlFile);
      }
    } catch (_) { /* non-fatal */ }

    // ── Edge scorer stats (written by edge_scorer.py) ─────────────────────
    let edge_scorer_stats = {
      total_evaluated: 0,
      total_passed: 0,
      total_filtered: 0,
      pass_rate: 0,
      kl_threshold: 0.05,
    };
    try {
      const edgeFile = path.join(__dirname, '../../../../edge_status.json');
      if (fs.existsSync(edgeFile)) {
        edge_scorer_stats = readJSON(edgeFile);
      }
    } catch (_) { /* non-fatal */ }

    // ── Market regime (written by regime_detector.py) ─────────────────────
    let regime = { regime: 'NORMAL', label: 'Normal', atr_pct: 0, kelly_multiplier: 1.0 };
    try {
      const regimeFile = path.join(__dirname, '../../../../regime_status.json');
      if (fs.existsSync(regimeFile)) {
        regime = readJSON(regimeFile);
      }
    } catch (_) { /* non-fatal */ }

    // ── Position summary (written by trader.py after every open/close) ────
    let positions_summary = {
      active_count: 0, closed_count: 0,
      unrealized_pnl: 0, realized_pnl: 0,
      win_count: 0, loss_count: 0,
      poly_active: 0, kalshi_active: 0,
    };
    let kalshi_closed_count = 0;
    let kalshi_closed_pnl   = 0;
    let kalshi_win_count    = 0;
    let kalshi_loss_count   = 0;
    let poly_closed_count   = 0;
    let poly_closed_pnl     = 0;
    let poly_win_count      = 0;
    let poly_loss_count     = 0;
    try {
      const posFile = path.join(__dirname, '../../../../infrastructure/exchange/positions_state.json');
      if (fs.existsSync(posFile)) {
        const posData = readJSON(posFile);
        positions_summary = posData.summary || positions_summary;
        const closedList = posData.closed || [];

        // Polymarket closed stats — single source of truth
        const polyClosed = closedList.filter(p => p.market === 'POLYMARKET');
        poly_closed_count = polyClosed.length;
        poly_closed_pnl   = polyClosed.reduce((s, p) => s + (p.realized_pnl || 0), 0);
        poly_win_count    = polyClosed.filter(p => (p.realized_pnl || 0) > 0).length;
        poly_loss_count   = polyClosed.filter(p => (p.realized_pnl || 0) < 0).length;

        // Kalshi closed stats (should be 0 after cleanup)
        const kalshiClosed = closedList.filter(p => p.market === 'KALSHI');
        kalshi_closed_count = kalshiClosed.length;
        kalshi_closed_pnl   = kalshiClosed.reduce((s, p) => s + (p.realized_pnl || 0), 0);
        kalshi_win_count    = kalshiClosed.filter(p => (p.realized_pnl || 0) > 0).length;
        kalshi_loss_count   = kalshiClosed.filter(p => (p.realized_pnl || 0) < 0).length;
      }
    } catch (_) { /* non-fatal */ }

    // Derive balance from positions_state.json (single source of truth)
    if (positions_summary.realized_pnl !== undefined) {
      const _startBal = parseFloat(state.starting_balance || 50.0);
      balance = Math.round((_startBal + (positions_summary.realized_pnl || 0)) * 100) / 100;
      pnl = parseFloat((positions_summary.realized_pnl || 0).toFixed(2));
    }

    let pythPrices = {};
    try {
      const clFile = path.join(__dirname, '../../../../chainlink_prices.json');
      const pythFile = path.join(__dirname, '../../../../pyth_prices.json');
      if (fs.existsSync(clFile)) {
        pythPrices = JSON.parse(fs.readFileSync(clFile, 'utf-8'));
      } else if (fs.existsSync(pythFile)) {
        pythPrices = JSON.parse(fs.readFileSync(pythFile, 'utf-8'));
      }
    } catch (pythErr) {
      console.warn('[HEALTH] Failed to parse prices file:', pythErr.message);
    }

    res.json({
      status,
      pythPrices,
      chainlinkPrices: pythPrices,
      running: status === 'running',
      balance,
      account_balance: balance,
      pnl,
      realTrades,
      totalSignals,
      dailySignals,
      dailyTrades,
      dailyPnL: parseFloat(dailyPnL.toFixed(2)),
      winRate,
      profitFactor,
      expectancy,
      byCoin,
      byStrength,
      byUTC,
      maxDrawdown,
      currentDrawdown,
      consecutiveLosses,
      riskOfRuin,
      missedTrades,
      missedWinRate,
      missedPnL,
      minutesAgo,
      last_update_minutes_ago: minutesAgo,
      tradesExecuted,
      trades_executed: tradesExecuted,
      phase: state.phase || 'phase_1',
      last_update: state.last_updated,
      cycles_completed: tradesExecuted,
      paused: state.paused || false,
      runtime,
      // ── Signal metrics from signal_evaluations.jsonl ─────────────────
      signals_evaluated,
      daily_signals_evaluated: dailySignals,
      avg_confidence,
      signals_by_sentiment,
      signals_by_market,
      confidence_distribution,
      polymarket_matches,
      kalshi_matches: kalshi_trades_recorded,
      peak_hour_signals,
      off_peak_hour_signals,
      hypothetical_trades: missedTrades,
      hypothetical_pnl: missedPnL,
      hypothetical_win_rate: missedWinRate,
      // ── Real trade breakdown ──────────────────────────────────────────
      real_trades: realTrades,
      real_pnl: pnl,
      real_win_rate: winRate,
      // Polymarket: from positions_state.json (authoritative)
      poly_real_trades: poly_closed_count,
      poly_real_pnl: parseFloat(poly_closed_pnl.toFixed(2)),
      poly_real_win_rate: (poly_win_count + poly_loss_count) > 0
        ? parseFloat((poly_win_count / (poly_win_count + poly_loss_count)).toFixed(4))
        : 0,
      // Kalshi: use positions_state.json closed count (should be 0 after cleanup)
      kalshi_real_trades: kalshi_closed_count,
      kalshi_real_pnl: parseFloat(kalshi_closed_pnl.toFixed(4)),
      kalshi_real_win_rate: (kalshi_win_count + kalshi_loss_count) > 0
        ? parseFloat((kalshi_win_count / (kalshi_win_count + kalshi_loss_count)).toFixed(4))
        : 0,
      // ── Missed signals (insufficient liquidity) ───────────────────────
      polymarket_hypothetical_trades: missedTrades,
      polymarket_hypothetical_pnl: missedPnL,
      // Signal quality rate (high-confidence signals / total missed signals)
      // NOT a win rate — renamed to avoid confusion
      signal_quality_rate: missedWinRate,
      // kalshi_trades_recorded = open/pending Kalshi positions this session
      kalshi_hypothetical_trades: kalshi_trades_recorded,
      kalshi_hypothetical_pnl: 0,
      // ── ML pipeline ───────────────────────────────────────────────────
      ml_progress,
      // ── Edge scorer ───────────────────────────────────────────────────
      edge_scorer_stats,
      // ── Market regime ─────────────────────────────────────────────────
      regime,
      // ── Position summary ──────────────────────────────────────────────
      positions_summary,
      // ── Macro context (FRED + funding rates + compounding progress) ───
      macro_context: (() => {
        try {
          const mf = path.join(__dirname, '../../../../macro_context.json');
          return fs.existsSync(mf) ? readJSON(mf) : null;
        } catch { return null; }
      })(),
      error: null,
    });

  } catch (error) {
    console.error('[HEALTH] Error reading state:', error.message);
    res.status(500).json({
      status: 'error',
      running: false,
      balance: 50.00,
      account_balance: 50.00,
      pnl: 0,
      realTrades: 0,
      totalSignals: 0,
      dailySignals: 0,
      byCoin: [],
      byStrength: [],
      runtime: { hours: 0, days: 0, progressPercent: 0, goalHours: 336, status: 'tracking' },
      error: error.message,
    });
  }
});

// ── SSE stream — pushes live events to frontend ───────────────────────────
const _sseClients = new Set();

router.get('/stream', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');
  res.flushHeaders();

  _sseClients.add(res);
  req.on('close', () => _sseClients.delete(res));
  req.socket?.on('error', () => _sseClients.delete(res));

  // Send heartbeat immediately
  res.write(`data: ${JSON.stringify({ type: 'heartbeat', ts: Date.now() })}\n\n`);
});

function broadcastSSE(eventObj) {
  const msg = `data: ${JSON.stringify(eventObj)}\n\n`;
  for (const client of _sseClients) {
    try { client.write(msg); } catch { _sseClients.delete(client); }
  }
}

const _priceCache = new Map();
const PRICE_CACHE_TTL_MS = 1000;

async function _fetchClobPrice(marketId) {
  if (!marketId || marketId === 'test_market_abc') return null;
  const cached = _priceCache.get(marketId);
  if (cached && Date.now() - cached.ts < PRICE_CACHE_TTL_MS) return cached.price;
  try {
    const r = await fetch(`https://clob.polymarket.com/book?token_id=${marketId}`, {
      signal: AbortSignal.timeout(5000),
    });
    if (!r.ok) {
      console.warn(`[CLOB FETCH] Not OK status ${r.status} for market ${marketId}`);
      return null;
    }
    const d = await r.json();
    
    const bidPrices = (d.bids || []).map(b => parseFloat(b.price)).filter(p => !isNaN(p));
    const askPrices = (d.asks || []).map(a => parseFloat(a.price)).filter(p => !isNaN(p));
    const bid = bidPrices.length ? Math.max(...bidPrices) : 0;
    const ask = askPrices.length ? Math.min(...askPrices) : 0;
    
    const price = (bid > 0 && ask > 0) ? (bid + ask) / 2 : 0;
    if (price > 0.01 && price < 0.99) {
      _priceCache.set(marketId, { price: Math.round(price * 10000) / 10000, ts: Date.now() });
      return _priceCache.get(marketId).price;
    }
  } catch (err) {
    console.error(`[CLOB FETCH ERROR] for market ${marketId}:`, err.message);
  }
  return null;
}

// Poll positions_state.json and broadcast position_update with self-scheduling setTimeout to avoid pileup
async function pollPositions() {
  try {
    const posFile = path.join(__dirname, '../../../../infrastructure/exchange/positions_state.json');
    if (fs.existsSync(posFile)) {
      const positions = JSON.parse(fs.readFileSync(posFile, 'utf-8').replace(/^﻿/, ''));
      
      const active = positions.active || [];
      const closed = positions.closed || [];
      const summary = positions.summary || {};
      
      let liveUnrealized = 0;
      const enrichedActive = await Promise.all(active.map(async (pos) => {
        if (pos.market !== 'POLYMARKET') {
          liveUnrealized += parseFloat(pos.unrealized_pnl || 0);
          return pos;
        }
        const marketId = pos.market_id || pos.order_id;
        const livePrice = await _fetchClobPrice(marketId);
        if (livePrice != null) {
          const shares = parseFloat(pos.shares || 0);
          const cost = parseFloat(pos.size || 0);
          const unrealizedPnl = Math.round((shares * livePrice - cost) * 100) / 100;
          liveUnrealized += unrealizedPnl;
          return { ...pos, current_price: livePrice, unrealized_pnl: unrealizedPnl };
        }
        liveUnrealized += parseFloat(pos.unrealized_pnl || 0);
        return pos;
      }));

      broadcastSSE({
        type: 'position_update',
        payload: {
          summary: {
            ...summary,
            unrealized_pnl: Math.round(liveUnrealized * 100) / 100
          },
          active: enrichedActive,
          closed
        },
        ts: Date.now()
      });
    }
  } catch (err) {
    console.error('[SSE ERROR] Position update failed:', err.message);
  } finally {
    setTimeout(pollPositions, 1000);
  }
}
pollPositions();

// Poll account_state.json and broadcast balance_update every 5s
setInterval(() => {
  try {
    const stateFile = path.join(__dirname, '../../../../account_state.json');
    if (!fs.existsSync(stateFile)) return;
    const state = JSON.parse(fs.readFileSync(stateFile, 'utf-8').replace(/^﻿/, ''));
    broadcastSSE({ type: 'balance_update', payload: state, ts: Date.now() });
  } catch { /* ignore */ }
}, 5000);

// Poll candle boundary timers every 10s
setInterval(() => {
  if (_sseClients.size === 0) return;
  const now = Math.floor(Date.now() / 1000);
  const boundaries = [
    { asset: 'BTC', tf: '5m',  secs: 300 - (now % 300) },
    { asset: 'BTC', tf: '15m', secs: 900 - (now % 900) },
    { asset: 'ETH', tf: '5m',  secs: 300 - (now % 300) },
    { asset: 'ETH', tf: '15m', secs: 900 - (now % 900) },
    { asset: 'SOL', tf: '5m',  secs: 300 - (now % 300) },
    { asset: 'SOL', tf: '15m', secs: 900 - (now % 900) },
    { asset: 'XRP', tf: '5m',  secs: 300 - (now % 300) },
    { asset: 'XRP', tf: '15m', secs: 900 - (now % 900) },
    { asset: 'DOGE', tf: '5m',  secs: 300 - (now % 300) },
    { asset: 'DOGE', tf: '15m', secs: 900 - (now % 900) },
    { asset: 'HYPE', tf: '5m',  secs: 300 - (now % 300) },
    { asset: 'HYPE', tf: '15m', secs: 900 - (now % 900) },
    { asset: 'BNB', tf: '5m',  secs: 300 - (now % 300) },
    { asset: 'BNB', tf: '15m', secs: 900 - (now % 900) },
  ];
  broadcastSSE({ type: 'candle_boundary', payload: boundaries, ts: Date.now() });
}, 10000);

export { broadcastSSE };

export default router;
