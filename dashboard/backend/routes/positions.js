/**
 * /api/positions
 * Reads positions_state.json written by trader.py (Polymarket) and
 * kalshi/trader.py (Kalshi) after every open/close.
 *
 * Both Polymarket and Kalshi positions are already merged into
 * positions_state.json by the Python side — no need to read
 * signal_evaluations.jsonl here.
 */
import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const router = express.Router();

const BOT_ROOT       = path.join(__dirname, '../../..');
const POSITIONS_FILE = path.join(BOT_ROOT, 'infrastructure', 'exchange', 'positions_state.json');

const DEFAULT_SUMMARY = {
  active_count:    0,
  poly_active:     0,
  kalshi_active:   0,
  closed_count:    0,
  unrealized_pnl:  0,
  realized_pnl:    0,
  win_count:       0,
  loss_count:      0,
};

function deduplicateById(positions) {
  const seen = new Set();
  return positions.filter(p => {
    const id = p.order_id || p.trade_id;
    if (!id || seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

function readPositionsFile() {
  if (!fs.existsSync(POSITIONS_FILE)) {
    return { active: [], closed: [], summary: DEFAULT_SUMMARY, last_updated: null };
  }
  try {
    const raw  = fs.readFileSync(POSITIONS_FILE, 'utf-8');
    const data = JSON.parse(raw);

    const active = deduplicateById(data.active || []);
    const closed = deduplicateById(data.closed || []);

    const baseSummary = { ...DEFAULT_SUMMARY, ...(data.summary || {}) };
    const summary = {
      ...baseSummary,
      active_count:  active.length,
      closed_count:  closed.length,
      poly_active:   active.filter(p => p.market === 'POLYMARKET').length,
      kalshi_active: active.filter(p => p.market === 'KALSHI').length,
    };

    return { active, closed, summary, last_updated: data.last_updated || null };
  } catch (err) {
    console.warn('[POSITIONS] Parse error:', err.message);
    return { active: [], closed: [], summary: DEFAULT_SUMMARY, last_updated: null };
  }
}

/** GET /api/positions — all active + closed with summary */
router.get('/', (req, res) => {
  try {
    const { active, closed, summary, last_updated } = readPositionsFile();
    res.json({ summary, active, closed, last_updated });
  } catch (err) {
    console.error('[POSITIONS] Error:', err.message);
    res.status(500).json({ error: err.message, active: [], closed: [], summary: DEFAULT_SUMMARY });
  }
});

/** GET /api/positions/active — only open positions */
router.get('/active', (req, res) => {
  try {
    const { active } = readPositionsFile();
    res.json({ active });
  } catch (err) {
    res.status(500).json({ error: err.message, active: [] });
  }
});

/** GET /api/positions/closed — closed positions (Polymarket + resolved Kalshi) */
router.get('/closed', (req, res) => {
  try {
    const { closed } = readPositionsFile();
    res.json({ closed });
  } catch (err) {
    res.status(500).json({ error: err.message, closed: [] });
  }
});

export default router;
