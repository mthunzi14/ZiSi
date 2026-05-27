export default function AIInjectorHUD({ state = {} }) {
  const avgConf = state.avg_confidence || 0;
  const sigs = state.signals_evaluated || 0;
  const sent = state.signals_by_sentiment || {};
  const progress = state.ml_progress || {};

  return (
    <div className="glass-panel" style={{ padding: 'var(--spacing-20)', display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 500, fontSize: 16 }}>
        Predictive AI Injector HUD
      </div>
      
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div style={{ background: 'rgba(255,255,255,0.03)', padding: 12, borderRadius: 8 }}>
          <div style={{ fontSize: 10, color: 'var(--color-text-muted)', textTransform: 'uppercase' }}>Avg Confidence</div>
          <div style={{ fontSize: 20, color: 'var(--color-accent)', fontFamily: 'var(--font-mono)' }}>
            {(avgConf * 100).toFixed(1)}%
          </div>
        </div>
        
        <div style={{ background: 'rgba(255,255,255,0.03)', padding: 12, borderRadius: 8 }}>
          <div style={{ fontSize: 10, color: 'var(--color-text-muted)', textTransform: 'uppercase' }}>Signals Scored</div>
          <div style={{ fontSize: 20, color: 'var(--color-text-primary)', fontFamily: 'var(--font-mono)' }}>
            {sigs}
          </div>
        </div>
      </div>

      <div style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
          <span>Bullish: {sent.bullish || 0}</span>
          <span>Bearish: {sent.bearish || 0}</span>
          <span>Neutral: {sent.neutral || 0}</span>
        </div>
      </div>
      
      {progress.progress_percent !== undefined && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 10, color: 'var(--color-text-muted)', textTransform: 'uppercase', marginBottom: 4 }}>
            ML Retraining Cycle
          </div>
          <div style={{ height: 4, background: 'rgba(255,255,255,0.1)', borderRadius: 2 }}>
            <div style={{ width: `${progress.progress_percent}%`, height: '100%', background: 'var(--color-accent)', borderRadius: 2 }} />
          </div>
        </div>
      )}
    </div>
  );
}
