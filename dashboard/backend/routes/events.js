/**
 * events.js — Server-Sent Events endpoint for real-time dashboard updates.
 *
 * Clients connect to GET /api/events and receive a stream of JSON-encoded
 * events whenever key bot state files change on disk.
 *
 * Event types emitted:
 *   trade_opened     — new position detected in positions_state.json
 *   trade_closed     — position moved to closed section
 *   balance_update   — account_state.json balance changed
 *   heartbeat        — sent every 30s so browsers don't time out the connection
 *
 * No extra npm packages required — pure Node.js + Express.
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
 * Called by the file watchers below.
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

let _lastBalance    = null;
let _lastActiveKeys = null;
let _lastClosedKeys = null;

function _safeRead(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch (_) {
    return null;
  }
}

function _watchStateFile() {
  if (!fs.existsSync(STATE_FILE)) return;
  fs.watch(STATE_FILE, { persistent: false }, () => {
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
  });
}

function _watchPositionsFile() {
  if (!fs.existsSync(POSITIONS_FILE)) return;
  fs.watch(POSITIONS_FILE, { persistent: false }, () => {
    const data = _safeRead(POSITIONS_FILE);
    if (!data) return;

    const activeKeys = Object.keys(data.active || {}).sort().join(',');
    const closedKeys = Object.keys(data.closed || {}).sort().join(',');

    if (activeKeys !== _lastActiveKeys && _lastActiveKeys !== null) {
      const newKeys    = activeKeys.split(',').filter(k => k && !(_lastActiveKeys || '').split(',').includes(k));
      const removedKeys = (_lastActiveKeys || '').split(',').filter(k => k && !activeKeys.split(',').includes(k));
      if (newKeys.length > 0) {
        newKeys.forEach(k => {
          const pos = data.active[k];
          if (pos) broadcastEvent('trade_opened', pos);
        });
      }
    }
    _lastActiveKeys = activeKeys;

    if (closedKeys !== _lastClosedKeys && _lastClosedKeys !== null) {
      const newClosed = closedKeys.split(',').filter(k => k && !(_lastClosedKeys || '').split(',').includes(k));
      if (newClosed.length > 0) {
        newClosed.forEach(k => {
          const pos = data.closed[k];
          if (pos) broadcastEvent('trade_closed', pos);
        });
      }
    }
    _lastClosedKeys = closedKeys;
  });
}

// Seed initial state so first-change detection works correctly
function _seedInitialState() {
  const state = _safeRead(STATE_FILE);
  if (state) _lastBalance = parseFloat(state.balance || 100);

  const positions = _safeRead(POSITIONS_FILE);
  if (positions) {
    _lastActiveKeys = Object.keys(positions.active || {}).sort().join(',');
    _lastClosedKeys = Object.keys(positions.closed || {}).sort().join(',');
  }
}

_seedInitialState();
_watchStateFile();
_watchPositionsFile();

// Re-attach watchers every 60s in case files are replaced (not modified in-place)
setInterval(() => {
  _watchStateFile();
  _watchPositionsFile();
}, 60_000);

// Heartbeat: keep SSE connections alive
setInterval(() => {
  broadcastEvent('heartbeat', { ts: Date.now() });
}, 30_000);

// ── SSE endpoint ─────────────────────────────────────────────────────────────

router.get('/', (req, res) => {
  res.setHeader('Content-Type',  'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection',    'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');  // disable nginx buffering if behind proxy
  res.flushHeaders();

  const id = ++_clientId;
  _clients.set(id, res);

  // Send immediate snapshot so client has data before first file-change event
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
      active_count: Object.keys(positions.active || {}).length,
      closed_count: Object.keys(positions.closed || {}).length,
      win_count:    summary.win_count  || 0,
      loss_count:   summary.loss_count || 0,
      realized_pnl: summary.realized_pnl || 0,
    })}\n\n`);
  }

  req.on('close', () => {
    _clients.delete(id);
  });
});

export default router;
