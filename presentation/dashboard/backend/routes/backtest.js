/**
 * /api/backtest
 * Serves the most recent backtest result JSON from tools/backtest/results/
 * for the heatmap widget on the dashboard.
 */
import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const router    = express.Router();

// Four levels up from routes/ -> backend/ -> dashboard/ -> presentation/ -> repo root
const BOT_ROOT    = path.join(__dirname, '../../../../');
const RESULTS_DIR = path.join(BOT_ROOT, 'tools', 'backtest', 'results');

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
 * Response: { cells: [...], note: string, generated_at: number }
 */
router.get('/heatmap', (req, res) => {
  const latest = latestResultFile();
  if (!latest) {
    return res.json({ cells: [], note: 'no backtest results yet', generated_at: 0 });
  }
  try {
    const raw    = fs.readFileSync(latest.fullPath, 'utf-8');
    const report = JSON.parse(raw);
    res.json({
      cells:        report.sweep_results  || [],
      note:         report.note           || '',
      generated_at: latest.mtime,
    });
  } catch (err) {
    console.error('[BACKTEST] Failed to read result file:', err.message);
    res.json({ cells: [], note: 'error reading backtest results', generated_at: 0 });
  }
});

export default router;
