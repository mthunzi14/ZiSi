import { useState, useEffect, useCallback } from 'react';
import './MLStatus.css';

export default function MLStatus() {
  const [data, setData] = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/system-health');
      if (res.ok) setData(await res.json());
    } catch (_) { /* non-fatal */ }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, [load]);

  const ml = data?.ml_status;

  if (!ml) {
    return (
      <div className="ml-status card">
        <h3 className="mls-title">ML Calibration</h3>
        <p className="mls-loading">Loading...</p>
      </div>
    );
  }

  const isPhase2 = ml.model_trained;
  const phaseDot = isPhase2 ? 'dot-green' : 'dot-amber';

  return (
    <div className="ml-status card">
      <div className="mls-header">
        <h3 className="mls-title">ML Calibration</h3>
        <span className={`mls-phase-badge ${isPhase2 ? 'phase2' : 'phase1'}`}>
          {isPhase2 ? 'Phase 2' : 'Phase 1'}
        </span>
      </div>

      <p className="mls-phase-label">
        <span className={`mls-dot ${phaseDot}`} />
        {ml.phase_label}
      </p>

      <div className="mls-grid">
        <div className="mls-stat">
          <span className="mls-stat-label">Training samples</span>
          <span className="mls-stat-value">{ml.training_samples}</span>
        </div>

        {ml.val_accuracy != null && (
          <div className="mls-stat">
            <span className="mls-stat-label">Val accuracy</span>
            <span className={`mls-stat-value ${ml.val_accuracy >= 60 ? 'good' : 'warn'}`}>
              {ml.val_accuracy}%
            </span>
          </div>
        )}

        {ml.val_roc_auc != null && (
          <div className="mls-stat">
            <span className="mls-stat-label">ROC-AUC</span>
            <span className={`mls-stat-value ${ml.val_roc_auc >= 0.60 ? 'good' : 'warn'}`}>
              {ml.val_roc_auc.toFixed(3)}
            </span>
          </div>
        )}

        {!isPhase2 && (
          <div className="mls-stat">
            <span className="mls-stat-label">Confidence mult.</span>
            <span className="mls-stat-value">0.65×</span>
          </div>
        )}
      </div>

      {!isPhase2 && (
        <div className="mls-progress-wrap">
          <div className="mls-progress-bar">
            <div
              className="mls-progress-fill"
              style={{ width: `${Math.min(100, (ml.training_samples / 50) * 100)}%` }}
            />
          </div>
          <span className="mls-progress-label">
            {ml.training_samples}/50 labelled trades to activate Phase 2
          </span>
        </div>
      )}

      {ml.trained_at && (
        <p className="mls-trained-at">
          Last trained: {new Date(ml.trained_at).toLocaleString()}
        </p>
      )}
    </div>
  );
}
