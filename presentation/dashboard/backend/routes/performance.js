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

/**
 * Wilson score 95% confidence interval for a binomial proportion.
 * Far more honest than the naive win_rate on small samples: e.g. 8/10 wins
 * yields a true 95% CI of roughly 49%–94%, not a stable "80%".
 * Returns [low, high] as percentages (0–100), or [0, 0] when n === 0.
 */
function wilsonInterval(wins, n, z = 1.96) {
  if (n <= 0) return [0, 0];
  const p = wins / n;
  const denom  = 1 + (z * z) / n;
  const center = (p + (z * z) / (2 * n)) / denom;
  const margin = (z / denom) * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n));
  const low  = Math.max(0, center - margin);
  const high = Math.min(1, center + margin);
  return [+(low * 100).toFixed(1), +(high * 100).toFixed(1)];
}

function entityStats(trades) {
  if (!trades.length) {
    return { trades: 0, wins: 0, losses: 0, win_rate: 0, pnl: 0, best: 0, worst: 0,
             wilson_low: 0, wilson_high: 0 };
  }
  const wins   = trades.filter(t => (t.realized_pnl ?? 0) > 0);
  const losses = trades.filter(t => (t.realized_pnl ?? 0) <= 0);
  const pnl    = trades.reduce((s, t) => s + (t.realized_pnl ?? 0), 0);
  const pnls   = trades.map(t => t.realized_pnl ?? 0);
  const [wlow, whigh] = wilsonInterval(wins.length, trades.length);
  return {
    trades:   trades.length,
    wins:     wins.length,
    losses:   losses.length,
    win_rate: trades.length > 0 ? +((wins.length / trades.length) * 100).toFixed(1) : 0,
    wilson_low:  wlow,   // 95% CI lower bound (%)
    wilson_high: whigh,  // 95% CI upper bound (%)
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
