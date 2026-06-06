/**
 * /api/bot-logs
 * Returns the last N lines from the pm2 bot log file.
 * Claude (and the dashboard) can call this to read live engine output.
 */
import express from 'express';
import fs from 'fs';

const router = express.Router();

const LOG_CANDIDATES = [
  '/root/.pm2/logs/zisi-bot-out.log',
  '/root/.pm2/logs/zisi-bot-error.log',
  '/root/.pm2/logs/zisi-dashboard-error.log',
  '/root/.pm2/logs/zisi-dashboard-out.log',
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
