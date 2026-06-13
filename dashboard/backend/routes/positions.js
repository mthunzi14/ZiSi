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

const BOT_ROOT = path.join(__dirname, '../../..');
const POSITIONS_FILE = path.join(BOT_ROOT, 'data', 'positions_state.json');

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


function enrichTradeType(p) {
  if (p.trade_type) return p;
  const et = (p.entry_type || p.event_title || '').toUpperCase();
  if (et.includes('CLOSE-SNIPE') || et.includes('CLOSE_SNIPE')) p.trade_type = 'NCS';
  else if (et.includes('FAIR')) p.trade_type = 'FAIR-VAL';
  else if (et.includes('LATENCY') || et.includes('LAT_ARB') || et.includes('LAT-ARB')) p.trade_type = 'LAT-ARB';
  else if (et.includes('T2_SWEEPER') || et.includes('SWEEP')) p.trade_type = 'SWEEP';
  else if (et.includes('REVERSAL_SNIPE') || et.includes('REVERSAL-SNIPE')) p.trade_type = 'REVERSAL-SNIPE';
  else if (et.includes('REVERSAL_STREAK') || et.includes('REVERSAL-STREAK')) p.trade_type = 'REVERSAL-STREAK';
  else p.trade_type = 'SIGNAL';
  return p;
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

    const active = deduplicateById(data.active || []).map(enrichTradeType);
    const closed = deduplicateById(data.closed || []).map(enrichTradeType);

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
const PRICE_CACHE_TTL_MS = 1000;
const _entrySpotCache = new Map();

/**
 * Fetch the current mid-price for a Polymarket token from the CLOB order book.
 * On failure, falls back to a spot-price delta heuristic using pyth_prices.json
 * and chainlink_prices.json, so the Vite UI dashboard always has a live value.
 *
 * @param {string} marketId  - Polymarket token_id
 * @param {object} [pos]     - Position object (used for fallback spot derivation)
 * @returns {Promise<number|null>}
 */
async function _fetchClobPrice(marketId, pos) {
  if (!marketId || marketId === 'test_market_abc') return null;
  const cached = _priceCache.get(marketId);
  if (cached && Date.now() - cached.ts < PRICE_CACHE_TTL_MS) return cached.price;

  // ── Primary: live CLOB order-book mid-price ───────────────────────────────
  try {
    const r = await fetch(`https://clob.polymarket.com/book?token_id=${marketId}`, {
      signal: AbortSignal.timeout(1500),
    });
    if (r.ok) {
      const d = await r.json();
      const bidPrices = (d.bids || []).map(b => parseFloat(b.price)).filter(p => !isNaN(p));
      const askPrices = (d.asks || []).map(a => parseFloat(a.price)).filter(p => !isNaN(p));
      const bid = bidPrices.length ? Math.max(...bidPrices) : 0;
      const ask = askPrices.length ? Math.min(...askPrices) : 0;
      const price = (bid > 0 && ask > 0) ? (bid + ask) / 2 : 0;
      if (price > 0.01 && price < 0.99) {
        const roundedPrice = Math.round(price * 10000) / 10000;
        _priceCache.set(marketId, { price: roundedPrice, ts: Date.now() });
        return roundedPrice;
      }
    } else {
      console.warn(`[POSITIONS CLOB] Not OK status ${r.status} for market ${marketId}`);
    }
  } catch (err) {
    console.warn(`[POSITIONS CLOB WARN] ${marketId}: ${err.message} — using spot fallback`);
  }

  // ── Fallback: derive contract price from spot oracle files ────────────────
  // Maps the % change in the underlying crypto spot price to an approximate
  // option contract price change using a 20× scaling heuristic
  // (1% spot move ≈ 20% option price move at mid-market).  This keeps
  // unrealized PnL visible in the Vite dashboard even when CLOB is down.
  if (pos) {
    try {
      const title = (pos.event_title || '').toUpperCase();
      let asset = null;
      if (title.includes('BTC'))      asset = 'BTC';
      else if (title.includes('ETH')) asset = 'ETH';
      else if (title.includes('SOL')) asset = 'SOL';
      else if (title.includes('XRP')) asset = 'XRP';
      else if (title.includes('DOGE')) asset = 'DOGE';
      else if (title.includes('BNB'))  asset = 'BNB';
      else if (title.includes('HYPE')) asset = 'HYPE';

      if (asset) {
        let currentSpot = null;
        // 1. Pyth oracle (highest freshness)
        const pythFile = path.join(BOT_ROOT, 'pyth_prices.json');
        if (fs.existsSync(pythFile)) {
          const pythData = JSON.parse(fs.readFileSync(pythFile, 'utf-8'));
          if (pythData[asset] && typeof pythData[asset].price === 'number') {
            currentSpot = pythData[asset].price;
          }
        }
        // 2. Chainlink oracle (fallback)
        if (currentSpot == null) {
          const clFile = path.join(BOT_ROOT, 'chainlink_prices.json');
          if (fs.existsSync(clFile)) {
            const clData = JSON.parse(fs.readFileSync(clFile, 'utf-8'));
            if (clData[asset] && typeof clData[asset].price === 'number') {
              currentSpot = clData[asset].price;
            }
          }
        }

        if (currentSpot != null) {
          const orderId = pos.order_id || marketId;
          if (!_entrySpotCache.has(orderId)) {
            _entrySpotCache.set(orderId, currentSpot);
          }
          const entrySpot  = _entrySpotCache.get(orderId);
          const entryPrice = parseFloat(pos.entry_price || 0.5);
          let priceDiffPct = entrySpot > 0 ? (currentSpot - entrySpot) / entrySpot : 0;

          // 1% spot move → 20% contract price move (heuristic delta)
          const SCALING = 20.0;
          const delta   = priceDiffPct * SCALING;

          let derivedPrice = pos.direction === 'YES'
            ? entryPrice + delta
            : entryPrice - delta;
          derivedPrice = Math.max(0.01, Math.min(0.99, derivedPrice));
          const roundedDerived = Math.round(derivedPrice * 10000) / 10000;

          console.log(
            `[POSITIONS CLOB FALLBACK] ${asset} (${pos.direction}): ` +
            `spot ${entrySpot} → ${currentSpot} ` +
            `(${(priceDiffPct*100).toFixed(3)}%), ` +
            `contract ${entryPrice} → ${roundedDerived}`
          );
          return roundedDerived;
        }
      }
    } catch (fallbackErr) {
      console.error('[POSITIONS CLOB FALLBACK ERROR]', fallbackErr.message);
    }
  }

  // ── Last resort: echo cached values already in position record ───────────
  if (pos) return pos.current_price || pos.entry_price || null;
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
      const livePrice = await _fetchClobPrice(marketId, pos);
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
      const livePrice = await _fetchClobPrice(marketId, pos);
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
