export default function ArbitrageMatrix({ positions = {} }) {
  const active = positions.active || [];
  const closed = positions.closed || [];
  const allTrades = [...active, ...closed];
  
  // Find pairs of Polymarket and Kalshi trades
  const arbTrades = allTrades.filter(t => (t.event_title || '').includes('[ARB]'));
  
  const polyArb = arbTrades.filter(t => t.market === 'POLYMARKET');
  const kalshiArb = arbTrades.filter(t => t.market === 'KALSHI');

  return (
    <div className="glass-panel" style={{ padding: 'var(--spacing-20)', display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 500, fontSize: 16 }}>
        Real-Time Arbitrage Matrix
      </div>
      
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div style={{ background: 'rgba(255,255,255,0.03)', padding: 12, borderRadius: 8, borderLeft: '2px solid #2b7fff' }}>
          <div style={{ fontSize: 10, color: 'var(--color-text-muted)', textTransform: 'uppercase' }}>Poly Arb Legs</div>
          <div style={{ fontSize: 20, color: 'var(--color-text-primary)', fontFamily: 'var(--font-mono)' }}>
            {polyArb.length}
          </div>
        </div>
        
        <div style={{ background: 'rgba(255,255,255,0.03)', padding: 12, borderRadius: 8, borderLeft: '2px solid #00d4a3' }}>
          <div style={{ fontSize: 10, color: 'var(--color-text-muted)', textTransform: 'uppercase' }}>Kalshi Arb Legs</div>
          <div style={{ fontSize: 20, color: 'var(--color-text-primary)', fontFamily: 'var(--font-mono)' }}>
            {kalshiArb.length}
          </div>
        </div>
      </div>
      
      <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-text-muted)' }}>
        {arbTrades.length === 0 ? (
          <div>Scanning for cross-exchange mispricings... (0 found)</div>
        ) : (
          <div style={{ color: 'var(--color-profit)' }}>Active Arb Executions found!</div>
        )}
      </div>
    </div>
  );
}
