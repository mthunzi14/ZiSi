import React from 'react';
import './MLProgress.css';

export default function MLProgress({ data }) {
  const ml = data?.ml_progress;

  if (!ml) {
    return (
      <div className="ml-progress card">
        <h3>🤖 ML Model Training</h3>
        <p className="ml-info">Waiting for first cycle data...</p>
      </div>
    );
  }

  const { cycles_collected = 0, cycles_needed = 50, progress_percent = 0, models = {} } = ml;

  return (
    <div className="ml-progress card">
      <h3>🤖 ML Model Training Progress</h3>
      <p className="ml-info">
        Models train after {cycles_needed} cycles (~2 weeks). Currently{' '}
        <strong>{cycles_collected}</strong> cycles collected.
      </p>

      <div className="ml-overall-bar">
        <div className="ml-overall-fill" style={{ width: `${progress_percent}%` }} />
      </div>
      <p className="ml-overall-pct">{progress_percent.toFixed(1)}% complete</p>

      <div className="ml-models">
        {Object.entries(models).map(([key, cfg]) => (
          <div key={key} className="ml-model">
            <div className="ml-model-header">
              <span className="ml-model-name">{cfg.description}</span>
              <span className="ml-model-count">
                {cfg.cycles_collected}/{cfg.cycles_needed}
              </span>
            </div>
            <div className="ml-bar">
              <div
                className="ml-fill"
                style={{ width: `${cfg.progress_percent}%` }}
              />
            </div>
            <span className={`ml-status ${cfg.ready ? 'ready' : 'collecting'}`}>
              {cfg.ready ? '✅ Ready for training' : '⏳ Collecting data...'}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
