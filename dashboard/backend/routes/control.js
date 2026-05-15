import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const router = express.Router();

// Flag file lives next to main.py in the ZiSi_Bot root
const PAUSE_FLAG = path.join(__dirname, '../../../bot_paused.flag');

router.get('/status', (req, res) => {
  try {
    const isPaused = fs.existsSync(PAUSE_FLAG);
    res.json({
      status: isPaused ? 'paused' : 'running',
      lastPauseTime: isPaused ? fs.statSync(PAUSE_FLAG).mtime : null,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.post('/pause', (req, res) => {
  try {
    fs.writeFileSync(PAUSE_FLAG, new Date().toISOString());
    console.log('[CONTROL] Bot paused');
    res.json({ status: 'paused', message: 'Bot paused successfully' });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.post('/resume', (req, res) => {
  try {
    if (fs.existsSync(PAUSE_FLAG)) {
      fs.unlinkSync(PAUSE_FLAG);
    }
    console.log('[CONTROL] Bot resumed');
    res.json({ status: 'running', message: 'Bot resumed successfully' });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

export default router;
