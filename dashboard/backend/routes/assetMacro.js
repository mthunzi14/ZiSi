/**
 * /api/asset-macro
 * Returns the 8-candle macro direction for each active asset.
 * Fires 7 parallel Binance kline requests and computes UP/DOWN/NEUTRAL per asset.
 */
import express from 'express';

const router = express.Router();

const ASSETS = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE'];
const SYMBOL_MAP = {
  BTC:  'BTCUSDT',
  ETH:  'ETHUSDT',
  SOL:  'SOLUSDT',
  XRP:  'XRPUSDT',
  DOGE: 'DOGEUSDT',
};

async function fetchMacro(symbol) {
  try {
    const resp = await fetch(
      `https://api.binance.com/api/v3/klines?symbol=${symbol}&interval=5m&limit=10`
    );
    const klines  = await resp.json();
    const last8   = klines.slice(-9, -1);
    const upCount = last8.filter(k => parseFloat(k[4]) > parseFloat(k[1])).length;
    const direction = upCount >= 6 ? 'UP' : upCount <= 2 ? 'DOWN' : 'NEUTRAL';
    return { direction, up_count: upCount, total: 8 };
  } catch {
    return { direction: 'NEUTRAL', up_count: 4, total: 8 };
  }
}

let macroCache = null;
let lastFetchTime = 0;
const CACHE_TTL_MS = 15000; // 15 seconds cache

router.get('/', async (req, res) => {
  const now = Date.now();
  if (macroCache && now - lastFetchTime < CACHE_TTL_MS) {
    return res.json({ assets: macroCache, timestamp: lastFetchTime, cached: true });
  }

  try {
    const results = await Promise.all(
      ASSETS.map(asset => fetchMacro(SYMBOL_MAP[asset]).then(d => ({ asset, ...d })))
    );
    const map = {};
    results.forEach(r => { map[r.asset] = { direction: r.direction, up_count: r.up_count, total: r.total }; });
    macroCache = map;
    lastFetchTime = now;
    res.json({ assets: map, timestamp: now });
  } catch (err) {
    console.error('[ASSET-MACRO] Error:', err.message);
    const fallback = {};
    ASSETS.forEach(a => { fallback[a] = { direction: 'NEUTRAL', up_count: 4, total: 8 }; });
    res.json({ assets: fallback, timestamp: now });
  }
});

export default router;
