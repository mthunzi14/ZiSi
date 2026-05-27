import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const router = express.Router();
const BOT_ROOT = path.join(__dirname, '../../../../');

// GET /api/signal-queue — last 20 signal evaluations (newest first)
router.get('/', (req, res) => {
  const queueFile = path.join(BOT_ROOT, 'signal_queue.json');
  if (!fs.existsSync(queueFile)) return res.json([]);
  try {
    const lines = fs.readFileSync(queueFile, 'utf-8')
      .split('\n')
      .filter(Boolean);
    const items = lines
      .map(line => { try { return JSON.parse(line); } catch { return null; } })
      .filter(Boolean)
      .slice(-20)
      .reverse(); // newest first
    res.json(items);
  } catch (err) {
    console.error('[SIGNAL-QUEUE]', err.message);
    res.json([]);
  }
});

export default router;
