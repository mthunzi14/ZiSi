import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const router = express.Router();

const BOT_ROOT = path.join(__dirname, '../../..');

function readJson(filePath, fallback = {}) {
  try {
    if (fs.existsSync(filePath)) return JSON.parse(fs.readFileSync(filePath, 'utf-8'));
  } catch (_) { /* non-fatal */ }
  return fallback;
}

// GET /api/system-health — ML model metadata + active alert summary
router.get('/', (req, res) => {
  try {
    // ── ML model metadata ──────────────────────────────────────────────────
    // ml_pipeline.py writes to model_meta.json (not ml_model_meta.json)
    const modelMeta = readJson(path.join(BOT_ROOT, 'data', 'model_meta.json'), null);

    let ml_status = {
      phase: 'PHASE_1_UNCALIBRATED',
      phase_label: 'Phase 1 — Deflation (0.65×)',
      model_trained: false,
      training_samples: 0,
      val_accuracy: null,
      val_roc_auc: null,
      trained_at: null,
      features: [],
    };

    // Count actual labelled examples regardless of whether model is trained yet
    let labelled_count = 0;
    try {
      const labelledFile = path.join(BOT_ROOT, 'data', 'ml_labelled_outcomes.jsonl');
      if (fs.existsSync(labelledFile)) {
        const content = fs.readFileSync(labelledFile, 'utf-8');
        labelled_count = content.split('\n').filter(l => l.trim()).length;
      }
    } catch (_) { /* non-fatal */ }

    if (modelMeta) {
      // Python train_model() writes: accuracy, auc, n_examples, n_train, phase, model_type
      const acc  = modelMeta.accuracy;
      const auc  = modelMeta.auc;
      const ts   = modelMeta.trained_at;
      // model is trained when phase is PHASE_2_CALIBRATED (set by train_model())
      const isPhase2 = (modelMeta.phase || '').startsWith('PHASE_2');

      let phase_label;
      if (!isPhase2) {
        phase_label = `Phase 1 — Deflation (0.65×) · ${labelled_count}/50 labelled`;
      } else if (acc != null && acc >= 0.60) {
        phase_label = 'Phase 2 — Logistic Regression active';
      } else {
        phase_label = 'Phase 2 — Model training (collecting accuracy)';
      }

      ml_status = {
        phase: isPhase2 ? 'PHASE_2_LOGISTIC' : 'PHASE_1_UNCALIBRATED',
        phase_label,
        model_trained: isPhase2,
        training_samples: labelled_count,   // real labelled count, not model meta
        val_accuracy: acc != null ? parseFloat((acc * 100).toFixed(1)) : null,
        val_roc_auc:  auc != null ? parseFloat(auc.toFixed(4)) : null,
        trained_at: ts ?? null,
        features: modelMeta.features ?? [],
      };
    } else {
      // No model yet — just show labelled count
      ml_status.training_samples = labelled_count;
      ml_status.phase_label = `Phase 1 — Deflation (0.65×) · ${labelled_count}/50 labelled`;
    }

    // ── Alert summary ──────────────────────────────────────────────────────
    const alertsData = readJson(path.join(BOT_ROOT, 'data', 'system_alerts.json'), { alerts: [] });
    const alerts = alertsData.alerts || [];
    const critical_count = alerts.filter(a => a.level === 'CRITICAL').length;
    const warning_count  = alerts.filter(a => a.level === 'WARNING').length;
    const recent_alerts  = alerts.slice(-10).reverse(); // last 10, newest first

    // ── Positions age summary ──────────────────────────────────────────────
    const posData = readJson(path.join(BOT_ROOT, 'data', 'positions_state.json'), { active: [] });
    const active = posData.active || [];
    const now = Date.now();
    const position_ages = active.map(p => {
      const entryStr = p.entry_time || p.open_time || '';
      if (!entryStr) return null;
      const age_h = (now - new Date(entryStr).getTime()) / 3_600_000;
      return { order_id: (p.order_id || '?').slice(0, 12), age_h: parseFloat(age_h.toFixed(2)) };
    }).filter(Boolean);

    res.json({
      ml_status,
      alerts: {
        total: alerts.length,
        critical: critical_count,
        warning: warning_count,
        recent: recent_alerts,
        last_updated: alertsData.last_updated ?? null,
      },
      positions: {
        active_count: active.length,
        ages: position_ages,
      },
      timestamp: new Date().toISOString(),
    });

  } catch (err) {
    console.error('[SYSTEM-HEALTH] Error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

export default router;
