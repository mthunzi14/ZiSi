import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const router = express.Router();

const BOT_ROOT = path.join(__dirname, '../../../../');
const PAUSE_FLAG    = path.join(BOT_ROOT, 'bot_paused.flag');
const SHADOW_CONFIG = path.join(BOT_ROOT, 'shadow_config.json');

// Mule label → internal key mapping
const MULE_MAP = { mule1: 'PBOT6', mule2: 'WALLET2' };

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

// ── Mule toggle endpoints ─────────────────────────────────────────────────────

function readShadowConfig() {
  try {
    if (fs.existsSync(SHADOW_CONFIG)) {
      return JSON.parse(fs.readFileSync(SHADOW_CONFIG, 'utf8'));
    }
  } catch (_) { /* ignore */ }
  return {};
}

function writeShadowConfig(cfg) {
  fs.writeFileSync(SHADOW_CONFIG, JSON.stringify(cfg, null, 2));
}

// GET /api/control/mules — return enabled status for all mules
router.get('/mules', (req, res) => {
  try {
    const cfg = readShadowConfig();
    const mules = {
      mule1: { name: 'Mule1', label: 'PBOT6',   enabled: cfg['PBOT6']   ? cfg['PBOT6'].enabled   : true },
      mule2: { name: 'Mule2', label: 'WALLET2',  enabled: cfg['WALLET2'] ? cfg['WALLET2'].enabled : true },
    };
    res.json(mules);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// POST /api/control/mule/:id/enable   — enable a mule  (id = mule1 | mule2)
// POST /api/control/mule/:id/disable  — disable a mule
router.post('/mule/:id/:action', (req, res) => {
  try {
    const { id, action } = req.params;
    const internalKey = MULE_MAP[id.toLowerCase()];
    if (!internalKey) return res.status(400).json({ error: `Unknown mule id: ${id}` });
    if (!['enable', 'disable'].includes(action)) return res.status(400).json({ error: `Unknown action: ${action}` });

    const enabled = action === 'enable';
    const cfg = readShadowConfig();
    cfg[internalKey] = { enabled };
    writeShadowConfig(cfg);

    console.log(`[CONTROL] ${id} (${internalKey}) ${action}d`);
    res.json({ mule: id, label: internalKey, enabled });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// POST /api/control/reset - Runs clean_slate.py to archive and reset database cleanly
router.post('/reset', (req, res) => {
  try {
    import('child_process').then(({ exec }) => {
      const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
      const startingBalance = req.body && typeof req.body.balance === 'number' ? req.body.balance : 50;
      exec(`${pythonCmd} clean_slate.py --archive --force --balance ${startingBalance}`, { cwd: BOT_ROOT }, (error, stdout, stderr) => {
        if (error) {
          console.error(`[CONTROL] Reset error: ${error}`);
          return res.status(500).json({ error: error.message, details: stderr });
        }
        console.log(`[CONTROL] Clean slate executed successfully:\n${stdout}`);
        res.json({ status: 'success', message: 'Clean slate executed successfully', output: stdout });
      });
    }).catch(err => {
      res.status(500).json({ error: err.message });
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

export default router;
