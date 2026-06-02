import express from 'express';

const router = express.Router();

router.get('/', async (req, res) => {
  try {
    const resp = await fetch(
      'https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=5m&limit=10'
    );
    const klines = await resp.json();
    // Use first 8 rows (newest candles), skip the still-open last candle
    const last8 = klines.slice(0, 8);
    const upCount = last8.filter(k => parseFloat(k[4]) > parseFloat(k[1])).length;
    const direction = upCount >= 6 ? 'UP' : upCount <= 2 ? 'DOWN' : 'NEUTRAL';
    res.json({ direction, up_count: upCount, total: 8 });
  } catch {
    res.json({ direction: 'NEUTRAL', up_count: 4, total: 8 });
  }
});

export default router;
