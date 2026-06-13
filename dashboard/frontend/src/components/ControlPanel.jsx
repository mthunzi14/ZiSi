import { useState, useEffect } from 'react';

export default function ControlPanel({ state = {} }) {
  const [mules, setMules] = useState({});
  const [isPaused, setIsPaused] = useState(state.paused || false);

  useEffect(() => {
    setIsPaused(state.paused || false);
  }, [state.paused]);

  useEffect(() => {
    fetch('/api/control/mules')
      .then(res => res.json())
      .then(data => setMules(data || {}))
      .catch(() => {});
  }, []);

  const togglePause = async () => {
    const action = isPaused ? 'resume' : 'pause';
    try {
      const res = await fetch(`/api/control/${action}`, { method: 'POST' });
      if (res.ok) setIsPaused(!isPaused);
    } catch (e) {
      console.error(e);
    }
  };

  const toggleMule = async (id, currentEnabled) => {
    const action = currentEnabled ? 'disable' : 'enable';
    try {
      const res = await fetch(`/api/control/mule/${id}/${action}`, { method: 'POST' });
      if (res.ok) {
        setMules(prev => ({
          ...prev,
          [id]: { ...prev[id], enabled: !currentEnabled }
        }));
      }
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="glass-panel" style={{ padding: 'var(--spacing-20)', display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 500, fontSize: 16 }}>
        Bidirectional Control Panel
      </div>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
        <button 
          onClick={togglePause}
          style={{
            padding: '8px 16px', borderRadius: 8, border: 'none', cursor: 'pointer',
            background: isPaused ? 'var(--color-profit)' : 'var(--color-loss)',
            color: '#fff', fontWeight: 600
          }}>
          {isPaused ? '▶ Resume Bot' : '⏸ Pause Bot'}
        </button>
      </div>
    </div>
  );
}
