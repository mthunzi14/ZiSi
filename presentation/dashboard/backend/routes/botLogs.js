/**
 * /api/bot-logs
 * Returns the last N lines from the pm2 bot log file.
 * Claude (and the dashboard) can call this to read live engine output.
 */
import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const router = express.Router();

const BOT_ROOT = path.join(__dirname, '../../../../');

const LOG_CANDIDATES = [
  path.join(BOT_ROOT, 'zisi_bot_console.log'),
  '/root/ZiSi/zisi_bot_console.log',
  '/root/.pm2/logs/zisi-dashboard-out.log',
  '/root/.pm2/logs/zisi-bot-out.log',
  '/root/.pm2/logs/zisi-bot-error.log',
  '/root/.pm2/logs/zisi-dashboard-error.log',
];

router.get('/', (req, res) => {
  const n = Math.min(parseInt(req.query.lines || '100', 10), 500);
  const filter = (req.query.filter || '').toLowerCase();

  for (const logPath of LOG_CANDIDATES) {
    if (!fs.existsSync(logPath)) continue;
    try {
      const raw = fs.readFileSync(logPath, 'utf-8');
      let lines = raw.split('\n').filter(l => l.trim());
      if (filter) lines = lines.filter(l => l.toLowerCase().includes(filter));
      const tail = lines.slice(-n);
      return res.json({ lines: tail, total: lines.length, path: logPath });
    } catch (e) {
      return res.json({ lines: [], error: e.message });
    }
  }

  res.json({ lines: [], error: 'No log file found' });
});

export default router;
