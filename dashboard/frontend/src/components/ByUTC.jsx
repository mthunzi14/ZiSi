import React, { useState, useEffect } from 'react';
import './ByUTC.css';

export default function ByUTC({ data }) {
  const [utcData, setUtcData] = useState([]);

  useEffect(() => {
    if (data && data.byUTC) {
      setUtcData(data.byUTC);
    }
  }, [data]);

  const bestHour = utcData.length > 0
    ? utcData.reduce((best, h) => h.trades > best.trades ? h : best, utcData[0])
    : null;

  return (
    <section className="by-utc">
      <h3>Trades by Hour (UTC)</h3>

      <div className="utc-grid">
        {utcData && utcData.length > 0 ? (
          utcData.map((hour) => (
            <div key={hour.hour} className="utc-card">
              <span className="utc-hour">{String(hour.hour).padStart(2, '0')}:00</span>
              <span className="utc-count">{hour.trades} trades</span>
              {hour.winRate !== undefined && hour.trades > 0 && (
                <span className="utc-winrate">
                  {(hour.winRate * 100).toFixed(0)}% win
                </span>
              )}
            </div>
          ))
        ) : (
          <p className="utc-empty">No UTC data yet. Waiting for first trades...</p>
        )}
      </div>

      {bestHour && bestHour.trades > 0 && (
        <div className="utc-insight">
          <p className="utc-insight-text">
            Best trading hour: {String(bestHour.hour).padStart(2, '0')}:00 UTC
            ({bestHour.trades} trade{bestHour.trades !== 1 ? 's' : ''})
          </p>
        </div>
      )}
    </section>
  );
}
