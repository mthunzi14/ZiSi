/**
 * events.js — Server-Sent Events endpoint for real-time dashboard updates.
 *
 * Clients connect to GET /api/events and receive a stream of JSON-encoded
 * events whenever key bot state files change on disk.
 *
 * Event types emitted:
 *   trade_opened       — new position detected in positions_state.json
 *   trade_closed       — position moved to closed section
 *   balance_update     — account_state.json balance changed
 *   positions_snapshot — periodic full re-sync of position counts
 *   heartbeat          — sent every 5s so browsers don't time out
 */

import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const router    = express.Router();
const BOT_ROOT  = path.join(__dirname, '../../..');

const STATE_FILE     = path.join(BOT_ROOT, 'account_state.json');
const POSITIONS_FILE = path.join(BOT_ROOT, 'positions_state.json');

// Connected SSE clients: Map<id, res>
const _clients = new Map();
let _clientId  = 0;

/**
 * Broadcast a typed event to all connected SSE clients.
 */
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

// ── File watchers ─────────────────────────────────────────────────────────────
// Watcher instances — we close these before re-attaching to prevent accumulation.
let _stateWatcher     = null;
let _positionsWatcher = null;

let _lastBalance    = null;
let _lastActiveKeys = null;
let _lastClosedKeys = null;

// Debounce timers — Windows fires 2-3 watch events per single file write.
// Coalesce rapid-fire events into one handler invocation.
let _stateDebounce     = null;
let _positionsDebounce = null;
const DEBOUNCE_MS = 80;

function _safeRead(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch (_) {
    return null;
  }
}

function _positionKeys(arr) {
  return (arr || []).map(p => p.order_id || '').filter(Boolean).sort().join(',');
}

function _handleStateChange() {
  const state = _safeRead(STATE_FILE);
  if (!state) return;
  const balance = parseFloat(state.balance || 100);
  if (balance !== _lastBalance) {
    _lastBalance = balance;
    broadcastEvent('balance_update', {
      balance,
      pnl:    parseFloat(state.pnl  || 0),
      trades: state.trades_executed || 0,
      status: state.status          || 'running',
    });
  }
}

function _handlePositionsChange() {
  const data = _safeRead(POSITIONS_FILE);
  if (!data) return;

  const activeArr = data.active || [];
  const closedArr = data.closed || [];
  const activeKeys = _positionKeys(activeArr);
  const closedKeys = _positionKeys(closedArr);

  // Trade opened: new order_id in active that wasn't there before
  if (activeKeys !== _lastActiveKeys && _lastActiveKeys !== null) {
    const prevSet = new Set((_lastActiveKeys || '').split(',').filter(Boolean));
    activeArr
      .filter(p => p.order_id && !prevSet.has(p.order_id))
      .forEach(pos => broadcastEvent('trade_opened', pos));
  }
  _lastActiveKeys = activeKeys;

  // Trade closed: new order_id in closed that wasn't there before
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

// Seed initial snapshot so first-change detection works
function _seedInitialState() {
  const state = _safeRead(STATE_FILE);
  if (state) _lastBalance = parseFloat(state.balance || 100);

  const positions = _safeRead(POSITIONS_FILE);
  if (positions) {
    _lastActiveKeys = _positionKeys(positions.active);
    _lastClosedKeys = _positionKeys(positions.closed);
  }
}

_seedInitialState();
_watchStateFile();
_watchPositionsFile();

// Re-attach watchers every 30s — fs.watch can silently stop on Windows.
// Closing old watchers first prevents accumulation.
setInterval(() => {
  _watchStateFile();
  _watchPositionsFile();
}, 30_000);

// Heartbeat every 5s — keeps SSE connections alive; also serves as a
// liveness signal so the frontend can detect connection drops fast.
setInterval(() => {
  broadcastEvent('heartbeat', { ts: Date.now() });
}, 5_000);

// Full re-sync every 15s — corrects any drift from missed events.
setInterval(() => {
  const state     = _safeRead(STATE_FILE);
  const positions = _safeRead(POSITIONS_FILE);

  if (state) {
    const balance = parseFloat(state.balance || 100);
    broadcastEvent('balance_update', {
      balance,
      pnl:    parseFloat(state.pnl ?? (balance - parseFloat(state.starting_balance || 100))),
      trades: state.trades_executed || 0,
      status: state.status || 'running',
    });
    _lastBalance = balance;
  }

  if (positions) {
    const summary = positions.summary || {};
    broadcastEvent('positions_snapshot', {
      active_count:  (positions.active  || []).length,
      closed_count:  (positions.closed  || []).length,
      win_count:     summary.win_count   || 0,
      loss_count:    summary.loss_count  || 0,
      realized_pnl:  summary.realized_pnl || 0,
      unrealized_pnl: summary.unrealized_pnl || 0,
    });
    _lastActiveKeys = _positionKeys(positions.active);
    _lastClosedKeys = _positionKeys(positions.closed);
  }
}, 15_000);

// ── SSE endpoint ─────────────────────────────────────────────────────────────

router.get('/', (req, res) => {
  res.setHeader('Content-Type',      'text/event-stream');
  res.setHeader('Cache-Control',     'no-cache');
  res.setHeader('Connection',        'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');
  res.flushHeaders();

  const id = ++_clientId;
  _clients.set(id, res);

  // Immediately send current snapshot to new subscriber
  const state     = _safeRead(STATE_FILE);
  const positions = _safeRead(POSITIONS_FILE);

  if (state) {
    res.write(`event: balance_update\ndata: ${JSON.stringify({
      balance: parseFloat(state.balance || 100),
      pnl:     parseFloat(state.pnl     || 0),
      trades:  state.trades_executed || 0,
      status:  state.status          || 'running',
    })}\n\n`);
  }

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

  req.on('close', () => {
    _clients.delete(id);
  });
});

export default router;
