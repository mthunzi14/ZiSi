const fs = require('fs').promises;
const path = require('path');

let cachedHealthResponse = null;
let lastCacheTime = 0;
const CACHE_TTL = 10000; // 10 seconds

const yieldToEventLoop = () => new Promise(resolve => setImmediate(resolve));

async function getHealthData() {
    const now = Date.now();
    if (cachedHealthResponse && (now - lastCacheTime < CACHE_TTL)) {
        return cachedHealthResponse;
    }

    try {
        const signalsPath = path.join(__dirname, '../../logs/signal_evaluations.jsonl');
        const tradesPath = path.join(__dirname, '../../logs/zisi_local_trades.jsonl');

        let rawSignals = "";
        let rawTrades = "";

        try { rawSignals = await fs.readFile(signalsPath, 'utf8'); } catch(e) {}
        try { rawTrades = await fs.readFile(tradesPath, 'utf8'); } catch(e) {}

        const signalLines = rawSignals.trim().split('\n');
        const tradeLines = rawTrades.trim().split('\n');

        let activeSignalsCount = 0;
        for (let i = 0; i < signalLines.length; i++) {
            if (i % 100 === 0) await yieldToEventLoop();
            if (signalLines[i]) activeSignalsCount++;
        }

        let totalTradesCount = 0;
        for (let i = 0; i < tradeLines.length; i++) {
            if (i % 100 === 0) await yieldToEventLoop();
            if (tradeLines[i]) totalTradesCount++;
        }

        cachedHealthResponse = {
            status: "HEALTHY",
            timestamp: new Date().toISOString(),
            metrics: {
                total_signals_evaluated: activeSignalsCount,
                total_trades_logged: totalTradesCount
            }
        };
        lastCacheTime = now;
        return cachedHealthResponse;

    } catch (error) {
        return { status: "DEGRADED", error: error.message };
    }
}

module.exports = { getHealthData };
