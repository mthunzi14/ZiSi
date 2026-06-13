import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const router    = express.Router();
const BOT_ROOT  = path.join(__dirname, '../../../../');
const REGIME_FILE = path.join(BOT_ROOT, 'regime_status.json');

router.get('/', (req, res) => {
  try {
    const data = JSON.parse(fs.readFileSync(REGIME_FILE, 'utf8'));
    res.json({
      regime:         data.regime            || 'UNKNOWN',
      label:          data.label             || data.regime || 'UNKNOWN',
      confidence:     data.regime_confidence || 0,
      atr_percentile: data.atr_percentile    || 50,
      bbw_percentile: data.bbw_percentile    || 50,
    });
  } catch {
    res.json({ regime: 'UNKNOWN', label: 'UNKNOWN', confidence: 0, atr_percentile: 50, bbw_percentile: 50 });
  }
});

export default router;
