import { useState, useEffect, useCallback } from 'react';
import './SystemAlerts.css';

const LEVEL_ORDER = { CRITICAL: 0, WARNING: 1, INFO: 2 };

function levelClass(level) {
  if (level === 'CRITICAL') return 'alert-critical';
  if (level === 'WARNING')  return 'alert-warning';
  return 'alert-info';
}

function timeAgo(isoStr) {
  if (!isoStr) return '';
  const diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
  if (diff < 60)   return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

export default function SystemAlerts() {
  const [data, setData]       = useState(null);
  const [clearing, setClearing] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/alerts');
      if (res.ok) setData(await res.json());
    } catch (_) { /* non-fatal */ }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 15_000);
    return () => clearInterval(id);
  }, [load]);

  const clearAlerts = async () => {
    setClearing(true);
    try {
      await fetch('/api/alerts', { method: 'DELETE' });
      setData({ alerts: [], last_updated: new Date().toISOString() });
    } finally {
      setClearing(false);
    }
  };

  const alerts = (data?.alerts ?? [])
    .slice()
    .sort((a, b) => (LEVEL_ORDER[a.level] ?? 9) - (LEVEL_ORDER[b.level] ?? 9));

  const critical = alerts.filter(a => a.level === 'CRITICAL').length;
  const warning  = alerts.filter(a => a.level === 'WARNING').length;

  if (!data) {
    return (
      <div className="system-alerts card">
        <h3 className="sa-title">System Alerts</h3>
        <p className="sa-empty">Loading...</p>
      </div>
    );
  }

  return (
    <div className={`system-alerts card ${critical > 0 ? 'has-critical' : warning > 0 ? 'has-warning' : ''}`}>
      <div className="sa-header">
        <h3 className="sa-title">
          System Alerts
          {critical > 0 && <span className="sa-badge badge-critical">{critical} CRITICAL</span>}
          {warning  > 0 && <span className="sa-badge badge-warning">{warning} WARNING</span>}
          {alerts.length === 0 && <span className="sa-badge badge-ok">ALL CLEAR</span>}
        </h3>
        {alerts.length > 0 && (
          <button
            className="sa-clear-btn"
            onClick={clearAlerts}
            disabled={clearing}
            title="Clear all alerts"
          >
            {clearing ? 'Clearing…' : 'Clear'}
          </button>
        )}
      </div>

      {alerts.length === 0 ? (
        <p className="sa-empty">No active alerts — all systems nominal.</p>
      ) : (
        <ul className="sa-list">
          {alerts.slice(0, 8).map((a, i) => (
            <li key={i} className={`sa-item ${levelClass(a.level)}`}>
              <span className="sa-level">{a.level}</span>
              <span className="sa-code">{a.code}</span>
              <span className="sa-msg">{a.message}</span>
              <span className="sa-time">{timeAgo(a.timestamp)}</span>
            </li>
          ))}
          {alerts.length > 8 && (
            <li className="sa-overflow">+{alerts.length - 8} more alerts</li>
          )}
        </ul>
      )}

      {data.last_updated && (
        <p className="sa-updated">Last updated: {timeAgo(data.last_updated)}</p>
      )}
    </div>
  );
}
