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

function readLastLines(filePath, maxLines) {
  const stat = fs.statSync(filePath);
  const fd = fs.openSync(filePath, 'r');
  
  // Read the last 1MB or the whole file if it is smaller
  const bufferSize = Math.min(stat.size, 1024 * 1024);
  if (bufferSize <= 0) {
    fs.closeSync(fd);
    return [];
  }
  
  const buffer = Buffer.alloc(bufferSize);
  fs.readSync(fd, buffer, 0, bufferSize, stat.size - bufferSize);
  fs.closeSync(fd);
  
  const raw = buffer.toString('utf-8');
  let lines = raw.split('\n');
  
  // Discard the first line if it was cut in half
  if (stat.size > bufferSize && lines.length > 1) {
    lines.shift();
  }
  
  return lines;
}

router.get('/', (req, res) => {
  const n = Math.min(parseInt(req.query.lines || '100', 10), 500);
  const filter = (req.query.filter || '').toLowerCase();
  const fileParam = req.query.file;

  let candidates = LOG_CANDIDATES;
  if (fileParam === 'positions') {
    candidates = [path.join(BOT_ROOT, 'infrastructure', 'exchange', 'positions_state.json')];
  } else if (fileParam === 'account') {
    candidates = [path.join(BOT_ROOT, 'account_state.json')];
  } else if (fileParam === 'signals') {
    candidates = [path.join(BOT_ROOT, 'signal_evaluations.jsonl')];
  } else if (fileParam === 'gates') {
    candidates = [path.join(BOT_ROOT, 'gate_log.jsonl')];
  }

  for (const logPath of candidates) {
    if (!fs.existsSync(logPath)) continue;
    try {
      let lines = readLastLines(logPath, n);
      lines = lines.filter(l => l.trim());
      if (filter) {
        lines = lines.filter(l => l.toLowerCase().includes(filter));
      }
      const tail = lines.slice(-n);
      return res.json({ lines: tail, total: lines.length, path: logPath });
    } catch (e) {
      return res.json({ lines: [], error: e.message });
    }
  }

  res.json({ lines: [], error: 'No log file found' });
});

// GET /api/bot-logs/sizes - Returns file sizes (MB) for all log files so dashboard can show storage indicator
router.get('/sizes', (req, res) => {
  const LOG_FILES = {
    'Bot Console':        path.join(BOT_ROOT, 'zisi_bot_console.log'),
    'PM2 Out':            '/root/.pm2/logs/zisi-dashboard-out.log',
    'PM2 Error':          '/root/.pm2/logs/zisi-dashboard-error.log',
    'Signal Evaluations': path.join(BOT_ROOT, 'signal_evaluations.jsonl'),
    'Gate Log':           path.join(BOT_ROOT, 'gate_log.jsonl'),
  };
  const files = {};
  let totalBytes = 0;
  for (const [name, filePath] of Object.entries(LOG_FILES)) {
    try {
      const stat = fs.existsSync(filePath) ? fs.statSync(filePath) : null;
      const bytes = stat ? stat.size : 0;
      files[name] = { bytes, mb: Math.round(bytes / 1024 / 1024 * 100) / 100 };
      totalBytes += bytes;
    } catch {
      files[name] = { bytes: 0, mb: 0 };
    }
  }
  const totalMb = Math.round(totalBytes / 1024 / 1024 * 100) / 100;
  const recommendation = totalBytes > 100 * 1024 * 1024 ? 'Critical — clear now'
    : totalBytes > 50 * 1024 * 1024 ? 'High — clear soon'
    : totalBytes > 20 * 1024 * 1024 ? 'Moderate — monitor'
    : 'OK';
  res.json({ files, totalMb, recommendation });
});

// POST /api/bot-logs/clear - Truncates bot and PM2 log files to free up VPS space
router.post('/clear', (req, res) => {
  try {
    let clearedPaths = [];
    for (const logPath of LOG_CANDIDATES) {
      if (fs.existsSync(logPath)) {
        fs.writeFileSync(logPath, ''); // Truncate to 0 bytes
        clearedPaths.push(logPath);
      }
    }
    console.log('[LOGS] Truncated log files:', clearedPaths);
    res.json({ status: 'success', message: 'Logs cleared successfully', cleared: clearedPaths });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

export default router;
