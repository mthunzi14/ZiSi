/**
 * /api/performance
 * ZiSi vs Mule1 vs Mule2 performance comparison.
 * Reads positions_state.json closed array and splits by entity based on event_title prefix.
 */
import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const router    = express.Router();
const BOT_ROOT  = path.join(__dirname, '../../../../');

const POSITIONS_FILE = path.join(BOT_ROOT, 'infrastructure', 'exchange', 'positions_state.json');

function safeRead(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; }
}

function entityStats(trades) {
  if (!trades.length) return { trades: 0, wins: 0, losses: 0, win_rate: 0, pnl: 0, best: 0, worst: 0 };
  const wins   = trades.filter(t => (t.realized_pnl ?? 0) > 0);
  const losses = trades.filter(t => (t.realized_pnl ?? 0) <= 0);
  const pnl    = trades.reduce((s, t) => s + (t.realized_pnl ?? 0), 0);
  const pnls   = trades.map(t => t.realized_pnl ?? 0);
  return {
    trades:   trades.length,
    wins:     wins.length,
    losses:   losses.length,
    win_rate: trades.length > 0 ? +((wins.length / trades.length) * 100).toFixed(1) : 0,
    pnl:      +pnl.toFixed(4),
    best:     +(Math.max(...pnls)).toFixed(4),
    worst:    +(Math.min(...pnls)).toFixed(4),
  };
}

router.get('/', (req, res) => {
  const pos = safeRead(POSITIONS_FILE);
  const closed = (pos?.closed || []);

  // Mules are now intelligence-only — only ZiSi executes real trades
  const zisi = closed.filter(t => !/\[SHADOW:/.test(t.event_title || ''));

  res.json({
    zisi: { name: 'ZiSi', ...entityStats(zisi) },
  });
});

export default router;
