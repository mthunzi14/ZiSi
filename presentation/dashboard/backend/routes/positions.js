/**
 * /api/positions
 * Reads positions_state.json written by trader.py (Polymarket) and
 * kalshi/trader.py (Kalshi) after every open/close.
 *
 * Both Polymarket and Kalshi positions are already merged into
 * positions_state.json by the Python side — no need to read
 * signal_evaluations.jsonl here.
 */
import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const router = express.Router();

const BOT_ROOT       = path.join(__dirname, '../../../../');
const POSITIONS_FILE = path.join(BOT_ROOT, 'infrastructure', 'exchange', 'positions_state.json');

const DEFAULT_SUMMARY = {
  active_count:    0,
  poly_active:     0,
  kalshi_active:   0,
  closed_count:    0,
  unrealized_pnl:  0,
  realized_pnl:    0,
  win_count:       0,
  loss_count:      0,
};

function deduplicateById(positions) {
  const seen = new Set();
  return positions.filter(p => {
    const id = p.order_id || p.trade_id;
    if (!id || seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

function readPositionsFile() {
  if (!fs.existsSync(POSITIONS_FILE)) {
    return { active: [], closed: [], summary: DEFAULT_SUMMARY, last_updated: null };
  }
  try {
    const raw  = fs.readFileSync(POSITIONS_FILE, 'utf-8');
    if (!raw.trim()) {
      throw new Error('Positions file is empty');
    }
    const data = JSON.parse(raw);
    if (!data.active && !data.closed) {
      throw new Error('Positions file JSON is invalid or incomplete');
    }

    const active = deduplicateById(data.active || []);
    const closed = deduplicateById(data.closed || []);

    const baseSummary = { ...DEFAULT_SUMMARY, ...(data.summary || {}) };
    const summary = {
      ...baseSummary,
      active_count:  active.length,
      closed_count:  closed.length,
      poly_active:   active.filter(p => p.market === 'POLYMARKET').length,
      kalshi_active: active.filter(p => p.market === 'KALSHI').length,
    };

    return { active, closed, summary, last_updated: data.last_updated || null };
  } catch (err) {
    console.warn('[POSITIONS] Parse error:', err.message);
    throw err;
  }
}

const _priceCache = new Map();
const PRICE_CACHE_TTL_MS = 2500;

async function _fetchClobPrice(marketId) {
  if (!marketId || marketId === 'test_market_abc') return null;
  const cached = _priceCache.get(marketId);
  if (cached && Date.now() - cached.ts < PRICE_CACHE_TTL_MS) return cached.price;
  try {
    const r = await fetch(`https://clob.polymarket.com/book?token_id=${marketId}`, {
      signal: AbortSignal.timeout(5000),
    });
    if (!r.ok) {
      console.warn(`[CLOB FETCH] Not OK status ${r.status} for market ${marketId}`);
      return null;
    }
    const d = await r.json();
    
    const bidPrices = (d.bids || []).map(b => parseFloat(b.price)).filter(p => !isNaN(p));
    const askPrices = (d.asks || []).map(a => parseFloat(a.price)).filter(p => !isNaN(p));
    const bid = bidPrices.length ? Math.max(...bidPrices) : 0;
    const ask = askPrices.length ? Math.min(...askPrices) : 0;
    
    const price = (bid > 0 && ask > 0) ? (bid + ask) / 2 : 0;
    if (price > 0.01 && price < 0.99) {
      _priceCache.set(marketId, { price: Math.round(price * 10000) / 10000, ts: Date.now() });
      return _priceCache.get(marketId).price;
    }
  } catch (err) {
    console.error(`[CLOB FETCH ERROR] for market ${marketId}:`, err.message);
  }
  return null;
}

/** GET /api/positions — all active + closed with summary */
router.get('/', async (req, res) => {
  try {
    res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, proxy-revalidate');
    const { active, closed, summary, last_updated } = readPositionsFile();
    
    let liveUnrealized = 0;
    const enrichedActive = await Promise.all(active.map(async (pos) => {
      if (pos.market !== 'POLYMARKET') {
        liveUnrealized += parseFloat(pos.unrealized_pnl || 0);
        return pos;
      }
      const marketId = pos.market_id || pos.order_id;
      const livePrice = await _fetchClobPrice(marketId);
      if (livePrice != null) {
        const shares = parseFloat(pos.shares || 0);
        const cost = parseFloat(pos.size || 0);
        const unrealizedPnl = Math.round((shares * livePrice - cost) * 100) / 100;
        liveUnrealized += unrealizedPnl;
        return { ...pos, current_price: livePrice, unrealized_pnl: unrealizedPnl };
      }
      liveUnrealized += parseFloat(pos.unrealized_pnl || 0);
      return pos;
    }));

    res.json({
      summary: {
        ...summary,
        unrealized_pnl: Math.round(liveUnrealized * 100) / 100
      },
      active: enrichedActive,
      closed,
      last_updated
    });
  } catch (err) {
    console.error('[POSITIONS] Error:', err.message);
    res.status(500).json({ error: err.message, active: [], closed: [], summary: DEFAULT_SUMMARY });
  }
});

/** GET /api/positions/active — only open positions */
router.get('/active', async (req, res) => {
  try {
    res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, proxy-revalidate');
    const { active } = readPositionsFile();
    
    const enrichedActive = await Promise.all(active.map(async (pos) => {
      if (pos.market !== 'POLYMARKET') return pos;
      const marketId = pos.market_id || pos.order_id;
      const livePrice = await _fetchClobPrice(marketId);
      if (livePrice != null) {
        const shares = parseFloat(pos.shares || 0);
        const cost = parseFloat(pos.size || 0);
        const unrealizedPnl = Math.round((shares * livePrice - cost) * 100) / 100;
        return { ...pos, current_price: livePrice, unrealized_pnl: unrealizedPnl };
      }
      return pos;
    }));
    
    res.json({ active: enrichedActive });
  } catch (err) {
    res.status(500).json({ error: err.message, active: [] });
  }
});

/** GET /api/positions/closed — closed positions (Polymarket + resolved Kalshi) */
router.get('/closed', (req, res) => {
  try {
    res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, proxy-revalidate');
    const { closed } = readPositionsFile();
    res.json({ closed });
  } catch (err) {
    res.status(500).json({ error: err.message, closed: [] });
  }
});

export default router;
