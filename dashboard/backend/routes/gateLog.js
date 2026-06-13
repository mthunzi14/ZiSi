/**
 * /api/gate-log
 * Reads gate_log.jsonl written by the Python engine whenever a gate blocks a signal.
 * Returns the last N entries in reverse-chronological order for the dashboard event log.
 */
import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const router    = express.Router();
const BOT_ROOT  = path.join(__dirname, '../../..');
const GATE_LOG  = path.join(BOT_ROOT, 'data', 'gate_log.jsonl');

router.get('/', (req, res) => {
  const limit = parseInt(req.query.limit || '50', 10);
  if (!fs.existsSync(GATE_LOG)) {
    return res.json({ events: [] });
  }
  try {
    const lines  = fs.readFileSync(GATE_LOG, 'utf-8').split('\n').filter(Boolean);
    const events = lines
      .map(l => { try { return JSON.parse(l); } catch { return null; } })
      .filter(Boolean)
      .reverse()
      .slice(0, limit);
    res.json({ events });
  } catch (err) {
    console.error('[GATE-LOG] Read error:', err.message);
    res.json({ events: [] });
  }
});

export default router;
