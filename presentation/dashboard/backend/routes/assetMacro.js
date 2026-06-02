/**
 * /api/asset-macro
 * Returns the 8-candle macro direction for each active asset.
 * Fires 7 parallel Binance kline requests and computes UP/DOWN/NEUTRAL per asset.
 */
import express from 'express';

const router = express.Router();

const ASSETS = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'LINK', 'BNB'];
const SYMBOL_MAP = {
  BTC:  'BTCUSDT',
  ETH:  'ETHUSDT',
  SOL:  'SOLUSDT',
  XRP:  'XRPUSDT',
  DOGE: 'DOGEUSDT',
  LINK: 'LINKUSDT',
  BNB:  'BNBUSDT',
};

async function fetchMacro(symbol) {
  try {
    const resp = await fetch(
      `https://api.binance.com/api/v3/klines?symbol=${symbol}&interval=5m&limit=10`
    );
    const klines  = await resp.json();
    const last8   = klines.slice(0, 8);
    const upCount = last8.filter(k => parseFloat(k[4]) > parseFloat(k[1])).length;
    const direction = upCount >= 6 ? 'UP' : upCount <= 2 ? 'DOWN' : 'NEUTRAL';
    return { direction, up_count: upCount, total: 8 };
  } catch {
    return { direction: 'NEUTRAL', up_count: 4, total: 8 };
  }
}

router.get('/', async (req, res) => {
  try {
    const results = await Promise.all(
      ASSETS.map(asset => fetchMacro(SYMBOL_MAP[asset]).then(d => ({ asset, ...d })))
    );
    const map = {};
    results.forEach(r => { map[r.asset] = { direction: r.direction, up_count: r.up_count, total: r.total }; });
    res.json({ assets: map, timestamp: Date.now() });
  } catch (err) {
    console.error('[ASSET-MACRO] Error:', err.message);
    const fallback = {};
    ASSETS.forEach(a => { fallback[a] = { direction: 'NEUTRAL', up_count: 4, total: 8 }; });
    res.json({ assets: fallback, timestamp: Date.now() });
  }
});

export default router;
