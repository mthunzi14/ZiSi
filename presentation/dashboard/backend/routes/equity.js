/**
 * /api/equity
 * Serves the balance_history.jsonl file as a time-series array for the EquityChart.
 * Written by balance_history.py after every balance sync in main.py.
 */
import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const router    = express.Router();

const BOT_ROOT    = path.join(__dirname, '../../../../');
const HISTORY_FILE = path.join(BOT_ROOT, 'balance_history.jsonl');

/** GET /api/equity — time-series [{timestamp, balance, pnl, trades}] */
router.get('/', (req, res) => {
  if (!fs.existsSync(HISTORY_FILE)) {
    return res.json({ history: [] });
  }
  try {
    const lines   = fs.readFileSync(HISTORY_FILE, 'utf-8').split('\n').filter(Boolean);
    const history = lines.map(l => { try { return JSON.parse(l); } catch { return null; } })
                         .filter(Boolean);
    res.json({ history });
  } catch (err) {
    console.error('[EQUITY] Read error:', err.message);
    res.json({ history: [] });
  }
});

export default router;
