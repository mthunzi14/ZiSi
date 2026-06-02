/**
 * /api/engine-status
 * Reads engine_status.json written by the Python engine each scan cycle.
 * Returns the current engine state (why no trade) for the dashboard pill.
 */
import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const router    = express.Router();
const BOT_ROOT  = path.join(__dirname, '../../../../');
const STATUS_FILE = path.join(BOT_ROOT, 'engine_status.json');

router.get('/', (req, res) => {
  try {
    if (!fs.existsSync(STATUS_FILE)) {
      return res.json({ status: 'SCANNING', detail: 'Bot initializing', ts: 0, asset_states: {} });
    }
    const raw = fs.readFileSync(STATUS_FILE, 'utf-8');
    res.json(JSON.parse(raw));
  } catch (e) {
    res.json({ status: 'ERROR', detail: e.message, ts: 0, asset_states: {} });
  }
});

export default router;
