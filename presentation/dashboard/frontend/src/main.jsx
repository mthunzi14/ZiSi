import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}

class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(err) { return { error: err }; }
  render() {
    if (this.state.error) {
      return (
        <div style={{ background: '#0c0c0e', color: '#c59b27', minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', fontFamily: 'monospace', gap: 16 }}>
          <div style={{ fontSize: 32, fontWeight: 900 }}>ZiSi.</div>
          <div style={{ fontSize: 14, color: '#71717a' }}>Dashboard render error — bot is still running</div>
          <div style={{ fontSize: 11, color: '#52525b', maxWidth: 600, textAlign: 'center' }}>{String(this.state.error)}</div>
          <button onClick={() => this.setState({ error: null })} style={{ marginTop: 8, padding: '8px 20px', background: '#c59b27', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}>Retry</button>
        </div>
      );
    }
    return this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <ErrorBoundary>
    <App />
  </ErrorBoundary>,
)
