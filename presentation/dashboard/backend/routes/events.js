/**
 * events.js — Server-Sent Events endpoint for real-time dashboard updates.
 * Balance is always derived from positions_state.json (immune to bot drift).
 */

import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const router    = express.Router();
const BOT_ROOT  = path.join(__dirname, '../../../../');

const STATE_FILE     = path.join(BOT_ROOT, 'account_state.json');
const POSITIONS_FILE = path.join(BOT_ROOT, 'infrastructure', 'exchange', 'positions_state.json');

function _getStartingBalance() {
  const state = _safeRead(STATE_FILE);
  return parseFloat(state?.starting_balance || 100.0);
}

const _clients = new Map();
let _clientId  = 0;

// Live CLOB price cache: market_id → {price, ts}
const _priceCache = new Map();
const PRICE_CACHE_TTL_MS = 3000; // 3s TTL — one fresh fetch per 5s SSE tick

async function _fetchClobPrice(marketId) {
  if (!marketId) return null;
  const cached = _priceCache.get(marketId);
  if (cached && Date.now() - cached.ts < PRICE_CACHE_TTL_MS) return cached.price;
  try {
    const r = await fetch(`https://clob.polymarket.com/book?token_id=${marketId}`, {
      signal: AbortSignal.timeout(2000),
    });
    if (!r.ok) return null;
    const d = await r.json();
    
    // The CLOB book returns multiple price levels; best bid is the highest bid, best ask is the lowest ask.
    const bidPrices = (d.bids || []).map(b => parseFloat(b.price)).filter(p => !isNaN(p));
    const askPrices = (d.asks || []).map(a => parseFloat(a.price)).filter(p => !isNaN(p));
    const bid = bidPrices.length ? Math.max(...bidPrices) : 0;
    const ask = askPrices.length ? Math.min(...askPrices) : 0;
    
    const price = (bid > 0 && ask > 0) ? (bid + ask) / 2 : 0;
    if (price > 0.01 && price < 0.99) {
      _priceCache.set(marketId, { price: Math.round(price * 10000) / 10000, ts: Date.now() });
      return _priceCache.get(marketId).price;
    }
  } catch (_) {}
  return null;
}

export function broadcastEvent(type, data) {
  if (_clients.size === 0) return;
  const payload = `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
  for (const [id, res] of _clients) {
    try {
      res.write(payload);
    } catch (_) {
      _clients.delete(id);
    }
  }
}

let _stateWatcher     = null;
let _positionsWatcher = null;

let _lastBalance    = null;
let _lastActiveKeys = null;
let _lastClosedKeys = null;

let _stateDebounce     = null;
let _positionsDebounce = null;
const DEBOUNCE_MS = 80;

function _safeRead(filePath) {
  try { return JSON.parse(fs.readFileSync(filePath, 'utf8')); } catch (_) { return null; }
}

function _positionKeys(arr) {
  return (arr || []).map(p => p.order_id || '').filter(Boolean).sort().join(',');
}

function _balanceFromPositions() {
  const pos = _safeRead(POSITIONS_FILE);
  if (!pos) return null;
  const realizedPnl = parseFloat((pos.summary || {}).realized_pnl || 0);
  const start = _getStartingBalance();
  return Math.round((start + realizedPnl) * 100) / 100;
}

function _buildBalancePayload() {
  const state          = _safeRead(STATE_FILE);
  const startingBalance = _getStartingBalance();
  const balance        = _balanceFromPositions() ?? startingBalance;
  const pnl            = Math.round((balance - startingBalance) * 100) / 100;
  return {
    balance,
    pnl,
    starting_balance: startingBalance,
    trades: state?.trades_executed || 0,
    status: state?.status || 'running',
  };
}

function _handleStateChange() {
  const balance = _balanceFromPositions();
  if (balance !== null && balance !== _lastBalance) {
    _lastBalance = balance;
    broadcastEvent('balance_update', _buildBalancePayload());
  }
}

function _handlePositionsChange() {
  const data = _safeRead(POSITIONS_FILE);
  if (!data) return;

  const activeArr  = data.active  || [];
  const closedArr  = data.closed  || [];
  const activeKeys = _positionKeys(activeArr);
  const closedKeys = _positionKeys(closedArr);

  if (activeKeys !== _lastActiveKeys && _lastActiveKeys !== null) {
    const prevSet = new Set((_lastActiveKeys || '').split(',').filter(Boolean));
    activeArr
      .filter(p => p.order_id && !prevSet.has(p.order_id))
      .forEach(pos => broadcastEvent('trade_opened', pos));
  }
  _lastActiveKeys = activeKeys;

  if (closedKeys !== _lastClosedKeys && _lastClosedKeys !== null) {
    const prevSet = new Set((_lastClosedKeys || '').split(',').filter(Boolean));
    closedArr
      .filter(p => p.order_id && !prevSet.has(p.order_id))
      .forEach(pos => broadcastEvent('trade_closed', {
        ...pos,
        profit: pos.realized_pnl ?? pos.profit ?? 0,
      }));
  }
  _lastClosedKeys = closedKeys;

  // Also broadcast updated balance when positions change
  broadcastEvent('balance_update', _buildBalancePayload());
}

function _watchStateFile() {
  if (_stateWatcher) { try { _stateWatcher.close(); } catch (_) {} }
  if (!fs.existsSync(STATE_FILE)) { _stateWatcher = null; return; }
  try {
    _stateWatcher = fs.watch(STATE_FILE, { persistent: false }, () => {
      clearTimeout(_stateDebounce);
      _stateDebounce = setTimeout(_handleStateChange, DEBOUNCE_MS);
    });
  } catch (_) { _stateWatcher = null; }
}

function _watchPositionsFile() {
  if (_positionsWatcher) { try { _positionsWatcher.close(); } catch (_) {} }
  if (!fs.existsSync(POSITIONS_FILE)) { _positionsWatcher = null; return; }
  try {
    _positionsWatcher = fs.watch(POSITIONS_FILE, { persistent: false }, () => {
      clearTimeout(_positionsDebounce);
      _positionsDebounce = setTimeout(_handlePositionsChange, DEBOUNCE_MS);
    });
  } catch (_) { _positionsWatcher = null; }
}

function _seedInitialState() {
  const balance = _balanceFromPositions() ?? _getStartingBalance();
  _lastBalance = balance;
  const positions = _safeRead(POSITIONS_FILE);
  if (positions) {
    _lastActiveKeys = _positionKeys(positions.active);
    _lastClosedKeys = _positionKeys(positions.closed);
  }
}

_seedInitialState();
_watchStateFile();
_watchPositionsFile();

// Re-attach watchers every 30s
setInterval(() => {
  _watchStateFile();
  _watchPositionsFile();
}, 30_000);

// Heartbeat every 5s
setInterval(() => {
  broadcastEvent('heartbeat', { ts: Date.now() });
}, 5_000);

// Full re-sync every 5s — enrich active Polymarket positions with live CLOB prices
setInterval(async () => {
  const positions = _safeRead(POSITIONS_FILE);
  broadcastEvent('balance_update', _buildBalancePayload());
  _lastBalance = _balanceFromPositions() ?? _getStartingBalance();

  if (positions) {
    const activeArr = positions.active  || [];
    const closedArr = positions.closed  || [];
    const summary   = positions.summary || {};

    // Enrich each active Polymarket position with a live CLOB price (3s cache)
    let liveUnrealized = 0;
    const enrichedActive = await Promise.all(activeArr.map(async (pos) => {
      if (pos.market !== 'POLYMARKET') {
        liveUnrealized += parseFloat(pos.unrealized_pnl || 0);
        return pos;
      }
      const marketId = pos.market_id || pos.conditionId || pos.order_id;
      const livePrice = await _fetchClobPrice(marketId);
      if (livePrice != null) {
        const shares  = parseFloat(pos.shares || pos.shares_acquired || 0);
        const cost    = parseFloat(pos.size   || pos.amount_spent    || 0);
        const unrealizedPnl = Math.round((shares * livePrice - cost) * 100) / 100;
        liveUnrealized += unrealizedPnl;
        return { ...pos, current_price: livePrice, unrealized_pnl: unrealizedPnl };
      }
      liveUnrealized += parseFloat(pos.unrealized_pnl || 0);
      return pos;
    }));

    broadcastEvent('positions_snapshot', {
      active:         enrichedActive,
      active_count:   enrichedActive.length,
      closed_count:   closedArr.length,
      win_count:      summary.win_count  || 0,
      loss_count:     summary.loss_count || 0,
      realized_pnl:   summary.realized_pnl  || 0,
      unrealized_pnl: liveUnrealized,
    });
    _lastActiveKeys = _positionKeys(activeArr);
    _lastClosedKeys = _positionKeys(closedArr);
  }
}, 5_000);

router.get('/', (req, res) => {
  res.setHeader('Content-Type',      'text/event-stream');
  res.setHeader('Cache-Control',     'no-cache');
  res.setHeader('Connection',        'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');
  res.flushHeaders();

  const id = ++_clientId;
  _clients.set(id, res);

  res.write(`event: balance_update\ndata: ${JSON.stringify(_buildBalancePayload())}\n\n`);

  const positions = _safeRead(POSITIONS_FILE);
  if (positions) {
    const summary = positions.summary || {};
    res.write(`event: positions_snapshot\ndata: ${JSON.stringify({
      active_count:   (positions.active  || []).length,
      closed_count:   (positions.closed  || []).length,
      win_count:      summary.win_count   || 0,
      loss_count:     summary.loss_count  || 0,
      realized_pnl:   summary.realized_pnl  || 0,
      unrealized_pnl: summary.unrealized_pnl || 0,
    })}\n\n`);
  }

  req.on('close', () => { _clients.delete(id); });
});

export default router;
