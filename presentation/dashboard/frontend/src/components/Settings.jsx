import { useState, useEffect } from 'react';

export default function Settings() {
  const [password, setPassword] = useState('');
  const [isUnlocked, setIsUnlocked] = useState(false);
  const [error, setError] = useState('');
  const [systemStatus, setSystemStatus] = useState({ isRunning: false, botStopped: true, pid: null });
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');

  // Fetch bot engine status from Express API
  const fetchStatus = async () => {
    const token = sessionStorage.getItem('zisi_admin_token');
    if (!token) return;
    try {
      const r = await fetch('/api/control/system/status', {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (r.status === 200) {
        const d = await r.json();
        setSystemStatus(d);
      }
    } catch (err) {
      console.error('Failed to fetch system status:', err);
    }
  };

  useEffect(() => {
    const token = sessionStorage.getItem('zisi_admin_token');
    if (token) {
      setIsUnlocked(true);
      fetch('/api/control/system/status', {
        headers: { 'Authorization': `Bearer ${token}` }
      }).then(r => {
        if (r.status === 200) {
          r.json().then(d => setSystemStatus(d));
        } else {
          sessionStorage.removeItem('zisi_admin_token');
          setIsUnlocked(false);
        }
      }).catch(() => {});
    }

    const id = setInterval(() => {
      const activeToken = sessionStorage.getItem('zisi_admin_token');
      if (activeToken) {
        fetchStatus();
      }
    }, 3000);
    return () => clearInterval(id);
  }, []);

  const handleUnlock = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const r = await fetch('/api/control/system/status', {
        headers: { 'Authorization': `Bearer ${password}` }
      });
      if (r.status === 200) {
        setIsUnlocked(true);
        sessionStorage.setItem('zisi_admin_token', password);
        setError('');
        const d = await r.json();
        setSystemStatus(d);
      } else if (r.status === 401) {
        setError('Invalid Access Key. Access Denied.');
        setPassword('');
      } else {
        setError(`Access Denied (HTTP ${r.status})`);
        setPassword('');
      }
    } catch (err) {
      setError(`Auth Connection Error: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleStart = async () => {
    setLoading(true);
    setMessage('');
    const token = sessionStorage.getItem('zisi_admin_token') || password;
    try {
      const r = await fetch('/api/control/system/start', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      });
      const d = await r.json();
      if (d.status === 'running') {
        setMessage('System Engine started successfully.');
        fetchStatus();
      } else {
        setMessage(d.message || 'Failed to start system.');
      }
    } catch (err) {
      setMessage(`Connection Error: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleStop = async () => {
    setLoading(true);
    setMessage('');
    const token = sessionStorage.getItem('zisi_admin_token') || password;
    try {
      const r = await fetch('/api/control/system/stop', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      });
      const d = await r.json();
      if (d.status === 'stopped') {
        setMessage('System Engine stopped forcefully.');
        fetchStatus();
      } else {
        setMessage(d.message || 'Failed to stop system.');
      }
    } catch (err) {
      setMessage(`Connection Error: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  if (!isUnlocked) {
    return (
      <div 
        className="glass-surface page-fade-enter"
        style={{
          maxWidth: '450px',
          margin: '100px auto',
          padding: '40px 32px',
          borderRadius: '24px',
          border: '1px solid var(--color-border)',
          boxShadow: 'var(--shadow-lg)',
          textAlign: 'center',
          background: 'rgba(18, 18, 20, 0.85)'
        }}
      >
        <span 
          style={{ 
            fontFamily: 'var(--font-display)', 
            fontWeight: 900, 
            fontSize: '32px', 
            letterSpacing: '-0.04em', 
            color: 'var(--color-accent)',
            display: 'block',
            marginBottom: '8px'
          }}
        >
          ZiSi.
        </span>
        <div style={{ fontSize: '13px', color: 'var(--color-iron)', marginBottom: '32px' }}>
          Enter Admin Access Key to open Settings panel
        </div>

        <form onSubmit={handleUnlock}>
          <input 
            type="password" 
            placeholder="••••"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            maxLength={6}
            style={{
              width: '100%',
              padding: '14px',
              borderRadius: '12px',
              background: 'var(--color-carbon)',
              border: '1px solid var(--color-border)',
              color: 'var(--color-snow)',
              textAlign: 'center',
              fontSize: '20px',
              letterSpacing: '8px',
              fontFamily: 'monospace',
              outline: 'none',
              marginBottom: '16px',
              transition: 'all 200ms ease'
            }}
            autoFocus
          />
          {error && (
            <div style={{ color: '#ff1744', fontSize: '12px', fontWeight: '600', marginBottom: '16px' }}>
              {error}
            </div>
          )}
          <button 
            type="submit"
            className="rotate-border"
            style={{
              width: '100%',
              padding: '14px',
              borderRadius: '12px',
              background: 'var(--color-accent)',
              color: 'var(--color-obsidian)',
              fontWeight: '700',
              fontSize: '14px',
              border: 'none',
              cursor: 'pointer',
              boxShadow: 'var(--shadow-gold)',
              transition: 'all 200ms ease'
            }}
          >
            Unlock Control Centre
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="page-fade-enter" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {/* Bento Widget 1: System Control */}
      <div 
        className="card glass-surface"
        style={{
          padding: '32px',
          borderRadius: '24px',
          border: '1px solid var(--color-border)',
          boxShadow: 'var(--shadow-md)',
          background: 'rgba(18, 18, 20, 0.75)'
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
          <div>
            <h2 style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: '24px', color: 'var(--color-snow)', margin: 0, letterSpacing: '-0.02em' }}>
              System Control Centre
            </h2>
            <p style={{ fontSize: '13px', color: 'var(--color-iron)', margin: '4px 0 0 0' }}>
              Start and stop the quantitative trading engine process tree
            </p>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span 
              className={systemStatus.isRunning ? 'alert-pulse' : ''} 
              style={{
                width: '12px',
                height: '12px',
                borderRadius: '50%',
                backgroundColor: systemStatus.isRunning ? '#10b981' : '#ff1744',
                display: 'inline-block'
              }}
            />
            <span style={{ fontSize: '13px', fontWeight: '700', color: systemStatus.isRunning ? '#10b981' : '#ff1744', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              {systemStatus.isRunning ? 'Engine Active' : 'Engine Offline'}
            </span>
          </div>
        </div>

        <div 
          style={{
            background: 'var(--color-carbon)',
            borderRadius: '16px',
            padding: '20px',
            border: '1px solid var(--color-border)',
            marginBottom: '24px',
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: '16px'
          }}
        >
          <div>
            <div style={{ fontSize: '11px', color: 'var(--color-iron)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Active Process PID
            </div>
            <div style={{ fontFamily: 'monospace', fontSize: '16px', color: 'var(--color-snow)', marginTop: '4px', fontWeight: '700' }}>
              {systemStatus.pid ? systemStatus.pid : '—'}
            </div>
          </div>
          <div>
            <div style={{ fontSize: '11px', color: 'var(--color-iron)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Lifecycle Intent
            </div>
            <div style={{ fontSize: '16px', color: systemStatus.botStopped ? '#ff1744' : '#10b981', marginTop: '4px', fontWeight: '700' }}>
              {systemStatus.botStopped ? 'STOPPED' : 'RUNNING'}
            </div>
          </div>
        </div>

        {message && (
          <div 
            style={{
              padding: '14px',
              borderRadius: '12px',
              background: 'rgba(197, 155, 39, 0.1)',
              border: '1px solid rgba(197, 155, 39, 0.2)',
              color: 'var(--color-accent)',
              fontSize: '13px',
              fontWeight: '600',
              marginBottom: '24px'
            }}
          >
            {message}
          </div>
        )}

        <div style={{ display: 'flex', gap: '16px' }}>
          {systemStatus.isRunning ? (
            <button
              onClick={handleStop}
              disabled={loading}
              style={{
                flex: 1,
                padding: '16px',
                borderRadius: '12px',
                background: '#ff1744',
                color: '#ffffff',
                fontWeight: '700',
                fontSize: '15px',
                border: 'none',
                cursor: loading ? 'not-allowed' : 'pointer',
                boxShadow: '0 0 15px rgba(255, 23, 68, 0.3)',
                transition: 'all 200ms cubic-bezier(0.16, 1, 0.3, 1)',
                transform: loading ? 'scale(0.98)' : 'scale(1)'
              }}
            >
              {loading ? 'Terminating Engine Process...' : 'STOP ZiSi ENGINE'}
            </button>
          ) : (
            <button
              onClick={handleStart}
              disabled={loading}
              className="rotate-border"
              style={{
                flex: 1,
                padding: '16px',
                borderRadius: '12px',
                background: 'var(--color-accent)',
                color: 'var(--color-obsidian)',
                fontWeight: '700',
                fontSize: '15px',
                border: 'none',
                cursor: loading ? 'not-allowed' : 'pointer',
                boxShadow: 'var(--shadow-gold)',
                transition: 'all 200ms cubic-bezier(0.16, 1, 0.3, 1)',
                transform: loading ? 'scale(0.98)' : 'scale(1)'
              }}
            >
              {loading ? 'Spawning Engine Process...' : 'START ZiSi ENGINE'}
            </button>
          )}
        </div>
      </div>

      {/* Info Widget */}
      <div 
        className="card glass-surface"
        style={{
          padding: '24px',
          borderRadius: '24px',
          border: '1px solid var(--color-border)',
          background: 'rgba(18, 18, 20, 0.5)'
        }}
      >
        <h3 style={{ margin: '0 0 8px 0', fontSize: '15px', color: 'var(--color-snow)', fontWeight: '700' }}>
          System Telemetry & Protection
        </h3>
        <p style={{ margin: 0, fontSize: '12.5px', color: 'var(--color-iron)', lineHeight: '1.6' }}>
          Stopping the engine terminates the background Python execution tree entirely, halting order checks, API fetching, and websocket streams immediately. The dashboard remains online as a read-only shell, allowing single-click deployment when ready to resume trading.
        </p>
      </div>
    </div>
  );
}
