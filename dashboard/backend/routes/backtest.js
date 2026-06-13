/**
 * /api/backtest
 * Serves the most recent backtest result JSON from tools/backtest/results/
 * for the heatmap widget on the dashboard.
 */
import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { spawn } from 'child_process';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const router    = express.Router();

// Four levels up from routes/ -> backend/ -> dashboard/ -> presentation/ -> repo root
const BOT_ROOT    = path.join(__dirname, '../../..');
const RESULTS_DIR = path.join(BOT_ROOT, 'backtest', 'results');

let backtestProcess = null;
let isBacktestRunning = false;

function triggerBackgroundBacktest() {
  if (isBacktestRunning) return;
  
  isBacktestRunning = true;
  const pythonCmd = process.platform === 'win32' 
    ? (fs.existsSync('C:\\Python313\\python.exe') ? 'C:\\Python313\\python.exe' : 'python')
    : 'python3';
  
  console.log('[BACKTEST-TRIGGER] Starting background parameter sweep...');
  
  backtestProcess = spawn(pythonCmd, ['-m', 'backtest.historical_backtest', '--days', '7'], {
    cwd: BOT_ROOT,
    stdio: 'ignore', // run completely silently in background
    env: { ...process.env, PYTHONUNBUFFERED: '1' }
  });
  
  backtestProcess.on('exit', (code) => {
    isBacktestRunning = false;
    backtestProcess = null;
    console.log(`[BACKTEST-TRIGGER] Background backtest finished with code ${code}`);
  });
  
  backtestProcess.on('error', (err) => {
    isBacktestRunning = false;
    backtestProcess = null;
    console.error(`[BACKTEST-TRIGGER] Background backtest failed to start: ${err.message}`);
  });
}

/**
 * Return the absolute path to the most recently modified .json file in RESULTS_DIR,
 * or null if the directory does not exist / is empty.
 */
function latestResultFile() {
  if (!fs.existsSync(RESULTS_DIR)) return null;
  const files = fs.readdirSync(RESULTS_DIR)
    .filter(f => f.endsWith('.json'))
    .map(f => ({ name: f, mtime: fs.statSync(path.join(RESULTS_DIR, f)).mtimeMs }))
    .sort((a, b) => b.mtime - a.mtime);   // newest first
  if (!files.length) return null;
  return { fullPath: path.join(RESULTS_DIR, files[0].name), mtime: files[0].mtime };
}

/**
 * GET /api/backtest/heatmap
 * Response: { cells: [...], note: string, generated_at: number, isGenerating: boolean }
 */
router.get('/heatmap', (req, res) => {
  const latest = latestResultFile();
  
  // Auto-trigger if no results exist OR if they are older than 2 hours
  const force = req.query.force === 'true';
  const ageLimitMs = 2 * 60 * 60 * 1000; // 2 hours
  const isStale = latest && (Date.now() - latest.mtime > ageLimitMs);
  
  if (!latest || isStale || force) {
    triggerBackgroundBacktest();
  }
  
  if (!latest) {
    return res.json({ 
      cells: [], 
      note: 'Generating backtest parameter heatmap in background. This may take a minute...', 
      generated_at: 0,
      isGenerating: isBacktestRunning
    });
  }
  
  try {
    const raw    = fs.readFileSync(latest.fullPath, 'utf-8');
    const report = JSON.parse(raw);
    res.json({
      cells:        report.sweep_results  || [],
      note:         report.note           || '',
      generated_at: latest.mtime,
      isGenerating: isBacktestRunning
    });
  } catch (err) {
    console.error('[BACKTEST] Failed to read result file:', err.message);
    res.json({ 
      cells: [], 
      note: 'error reading backtest results', 
      generated_at: 0,
      isGenerating: isBacktestRunning
    });
  }
});

/**
 * POST /api/backtest/heatmap/trigger
 * Manually trigger backtest sweep in the background
 */
router.post('/heatmap/trigger', (req, res) => {
  if (isBacktestRunning) {
    return res.json({ status: 'running', message: 'Backtest is already running.' });
  }
  triggerBackgroundBacktest();
  res.json({ status: 'started', message: 'Backtest successfully triggered in the background.' });
});

export default router;
