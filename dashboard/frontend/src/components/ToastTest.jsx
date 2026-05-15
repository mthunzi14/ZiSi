import React, { useRef, useState } from 'react';
import ToastPortal from './ToastPortal';

/**
 * Isolated toast smoke-test.
 * Reachable via the Settings tab.
 * Verifies that ToastPortal works independently of the Header.
 */
export default function ToastTest() {
  const [msg, setMsg]   = useState('');
  const [type, setType] = useState('');
  const timerRef        = useRef(null);

  const fire = (message, toastType) => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setMsg(message);
    setType(toastType);
    timerRef.current = setTimeout(() => { setMsg(''); setType(''); }, 4000);
    console.log('[ToastTest] fired:', toastType, '→', message);
  };

  const btnStyle = (bg) => ({
    padding: '10px 20px',
    borderRadius: '8px',
    border: 'none',
    background: bg,
    color: '#fff',
    cursor: 'pointer',
    fontWeight: 600,
    fontSize: '13px',
  });

  return (
    <div style={{ padding: '32px' }}>
      <ToastPortal message={msg} type={type} isVisible={!!msg} />

      <h2 style={{ color: '#e5e5e5', marginBottom: '8px' }}>Toast Diagnostic Panel</h2>
      <p style={{ color: '#888', fontSize: '13px', marginBottom: '24px' }}>
        Click a button — a toast should appear at the top-centre of the screen for 4 seconds.
        Open the browser console (F12) to see debug logs.
      </p>

      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '32px' }}>
        <button style={btnStyle('#22c55e')} onClick={() => fire('✓ Success — portal working', 'success')}>
          Success
        </button>
        <button style={btnStyle('#6b62f2')} onClick={() => fire('⚠ Neutral — portal working', 'neutral')}>
          Neutral
        </button>
        <button style={btnStyle('#ef4444')} onClick={() => fire('✗ Error — portal working', 'error')}>
          Error
        </button>
        <button
          style={btnStyle('#f59e0b')}
          onClick={() => {
            fire('Message 1 — rapid fire test', 'success');
            setTimeout(() => fire('Message 2 — replaces 1', 'neutral'), 800);
            setTimeout(() => fire('Message 3 — final', 'error'), 1600);
          }}
        >
          Rapid-fire (×3)
        </button>
      </div>

      <div style={{
        padding: '16px',
        background: '#111',
        borderRadius: '8px',
        fontFamily: 'monospace',
        fontSize: '12px',
        color: '#aaa',
        lineHeight: '1.8',
        border: '1px solid #222',
      }}>
        <div style={{ color: '#4ade80', marginBottom: '8px' }}>Expected behaviour:</div>
        <div>✓ Toast slides in from top-centre in ~0.3s</div>
        <div>✓ Stays visible for 4 seconds</div>
        <div>✓ Rapid-fire: each click replaces the previous toast immediately</div>
        <div>✓ Console shows: [ZiSi Toast] type → message</div>
        <div style={{ marginTop: '12px', color: '#f87171' }}>If toast does NOT appear:</div>
        <div>→ Check console for errors</div>
        <div>→ Inspect DOM for #zisi-toast-root on document.body</div>
        <div>→ Check if .toast CSS is loaded (DevTools → Elements → Computed)</div>
      </div>
    </div>
  );
}
