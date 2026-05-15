import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const router = express.Router();

const BOT_ROOT = path.join(__dirname, '../../..');

// GET /api/alerts — returns alerts from system_alerts.json
router.get('/', (req, res) => {
  try {
    const alertsFile = path.join(BOT_ROOT, 'system_alerts.json');
    if (!fs.existsSync(alertsFile)) {
      return res.json({ alerts: [], last_updated: null });
    }
    const raw = JSON.parse(fs.readFileSync(alertsFile, 'utf-8'));
    res.json(raw);
  } catch (err) {
    console.warn('[ALERTS] Could not read system_alerts.json:', err.message);
    res.json({ alerts: [], last_updated: null, error: err.message });
  }
});

// DELETE /api/alerts — clear all alerts (operator action from dashboard)
router.delete('/', (req, res) => {
  try {
    const alertsFile = path.join(BOT_ROOT, 'system_alerts.json');
    const empty = { alerts: [], last_updated: new Date().toISOString() };
    fs.writeFileSync(alertsFile, JSON.stringify(empty, null, 2), 'utf-8');
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

export default router;
