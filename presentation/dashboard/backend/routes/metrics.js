import express from 'express';
import { readTradesFile, readAccountState } from '../utils/fileReader.js';

const router = express.Router();

function calculateMetrics(trades) {
  if (trades.length === 0) {
    return {
      total_trades: 0,
      profitable: 0,
      win_rate: 0,
      profit_factor: 0,
      total_pnl: 0,
      avg_win: 0,
      avg_loss: 0,
      sharpe_ratio: 0,
      max_drawdown: 0
    };
  }

  const profitable = trades.filter(t => t.profit > 0).length;
  const totalProfit = trades.reduce((sum, t) => sum + (t.profit || 0), 0);

  const wins = trades.filter(t => t.profit > 0).map(t => t.profit);
  const losses = trades.filter(t => t.profit < 0).map(t => Math.abs(t.profit));

  const avgWin = wins.length > 0 ? wins.reduce((a, b) => a + b) / wins.length : 0;
  const avgLoss = losses.length > 0 ? losses.reduce((a, b) => a + b) / losses.length : 0;

  const profitFactor = avgLoss > 0 ? (avgWin / avgLoss) : (avgWin > 0 ? Infinity : 0);

  const sharpe = trades.length > 1
    ? totalProfit / (Math.sqrt(trades.length) * Math.max(avgLoss, 1))
    : 0;

  return {
    total_trades: trades.length,
    profitable: profitable,
    win_rate: ((profitable / trades.length) * 100).toFixed(1),
    profit_factor: profitFactor.toFixed(2),
    total_pnl: totalProfit.toFixed(2),
    avg_win: avgWin.toFixed(2),
    avg_loss: avgLoss.toFixed(2),
    sharpe_ratio: sharpe.toFixed(2),
    max_drawdown: calculateMaxDrawdown(trades)
  };
}

function calculateMaxDrawdown(trades) {
  let peak = 0;
  let maxDD = 0;
  let runningPnL = 0;

  for (const trade of trades) {
    runningPnL += trade.profit || 0;
    peak = Math.max(peak, runningPnL);
    maxDD = Math.max(maxDD, peak - runningPnL);
  }

  return maxDD.toFixed(2);
}

router.get('/overall', (req, res) => {
  try {
    const trades = readTradesFile();
    const metrics = calculateMetrics(trades);
    const accountState = readAccountState();

    res.json({
      ...metrics,
      account_balance: accountState.balance,
      last_updated: accountState.last_updated
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

router.get('/daily', (req, res) => {
  try {
    const trades = readTradesFile();
    const today = new Date().toISOString().split('T')[0];

    const todayTrades = trades.filter(t => {
      const ts = t.timestamp_open || t.timestamp;
      const tradeDate = ts?.split('T')[0];
      return tradeDate === today;
    });

    const metrics = calculateMetrics(todayTrades);

    res.json({ date: today, ...metrics });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

router.get('/by-coin', (req, res) => {
  try {
    const trades = readTradesFile();
    const coinMetrics = {};

    trades.forEach(trade => {
      const haystack = `${trade.event_title || ''} ${trade.coins_mentioned || ''}`.toLowerCase()
      const coin = haystack.includes('btc') || haystack.includes('bitcoin') ? 'BTC'
                 : haystack.includes('eth') || haystack.includes('ethereum') ? 'ETH'
                 : haystack.includes('sol') || haystack.includes('solana') ? 'SOL'
                 : 'OTHER';

      if (!coinMetrics[coin]) coinMetrics[coin] = [];
      coinMetrics[coin].push(trade);
    });

    const results = {};
    for (const [coin, coinTrades] of Object.entries(coinMetrics)) {
      results[coin] = calculateMetrics(coinTrades);
    }

    res.json(results);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

router.get('/by-signal', (req, res) => {
  try {
    const trades = readTradesFile();
    const signalMetrics = { 7: [], 8: [], 9: [], 10: [] };

    trades.forEach(trade => {
      const signal = trade.signal_confidence || trade.sentiment_score || 7;
      if (signalMetrics[signal]) {
        signalMetrics[signal].push(trade);
      }
    });

    const results = {};
    for (const [signal, signalTrades] of Object.entries(signalMetrics)) {
      results[signal] = calculateMetrics(signalTrades);
    }

    res.json(results);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

router.get('/by-hour', (req, res) => {
  try {
    const trades = readTradesFile();
    const hourMetrics = {};

    trades.forEach(trade => {
      const ts = trade.timestamp_open || trade.timestamp;
      const hour = new Date(ts).getUTCHours();
      if (!hourMetrics[hour]) hourMetrics[hour] = [];
      hourMetrics[hour].push(trade);
    });

    const results = {};
    for (let h = 0; h < 24; h++) {
      results[h] = calculateMetrics(hourMetrics[h] || []);
    }

    res.json(results);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

export default router;
