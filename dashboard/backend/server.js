import express from 'express';
import cors from 'cors';
import dotenv from 'dotenv';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { spawn, execSync } from 'child_process';
import { WebSocketServer } from 'ws';
import tradesRouter from './routes/trades.js';
import metricsRouter from './routes/metrics.js';
import healthRouter from './routes/health.js';
import controlRouter from './routes/control.js';
import positionsRouter from './routes/positions.js';
import equityRouter from './routes/equity.js';
import alertsRouter from './routes/alerts.js';
import systemHealthRouter from './routes/systemHealth.js';
import signalQueueRouter from './routes/signalQueue.js';
import eventsRouter from './routes/events.js';
import performanceRouter from './routes/performance.js';
import backtestRouter from './routes/backtest.js';
import regimeRouter from './routes/regime.js';
import macroTrendRouter from './routes/macroTrend.js';
import gateLogRouter from './routes/gateLog.js';
import assetMacroRouter from './routes/assetMacro.js';
import engineStatusRouter from './routes/engineStatus.js';
import botLogsRouter from './routes/botLogs.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

dotenv.config();

const app = express();
const PORT = process.env.PORT || 5000;

// Bot root is two levels up from dashboard/backend/
const BOT_ROOT = path.join(__dirname, '../..');

app.use(cors());
app.use(express.json());

// ── API routes (must be registered BEFORE static file catch-all) ──────────────
app.use('/api/trades', tradesRouter);
app.use('/api/metrics', metricsRouter);
app.use('/api/health', healthRouter);
app.use('/api/regime', regimeRouter);
app.use('/api/macro-trend', macroTrendRouter);
app.use('/api/gate-log', gateLogRouter);
app.use('/api/asset-macro', assetMacroRouter);
app.use('/api/engine-status', engineStatusRouter);
app.use('/api/bot-logs', botLogsRouter);
// Intercept reset endpoint to cleanly stop bot process before clearing logs
app.post('/api/control/reset', (req, res) => {
  console.log('[CONTROL] Intercepted reset request. Stopping bot engine before resetting state...');
  try {
    // 1. Stop the bot engine process cleanly
    stopBot();
    
    // 2. Wait 2 seconds for complete process teardown
    setTimeout(() => {
      import('child_process').then(({ exec }) => {
        const pythonCmd = process.env.PYTHON_PATH || (process.platform === 'win32' ? 'C:\\Python313\\python.exe' : '/root/ZiSi/venv/bin/python');

        // 3. Execute clean_slate.py with --nuke and --archive flags
        const cmd = `${pythonCmd} miscellaneous/clean_slate.py --archive --force --balance 50 --nuke`;
        console.log(`[CONTROL] Running reset: ${cmd}`);
        
        exec(cmd, { cwd: BOT_ROOT }, (error, stdout, stderr) => {
          if (error) {
            console.error(`[CONTROL] Reset failed: ${error.message}`);
            // Restart bot to prevent system downtime if reset fails
            botStopped = false;
            startBot();
            return res.status(500).json({ error: error.message, details: stderr });
          }
          
          console.log(`[CONTROL] Clean slate executed successfully. Console output:\n${stdout}`);
          
          // 4. Safely spawn the bot daemon process again on a fresh slate
          botStopped = false;
          startBot();
          
          res.json({
            status: 'success',
            message: 'Clean slate executed successfully, old history nuked, and engine restarted.',
            output: stdout
          });
        });
      }).catch(err => {
        botStopped = false;
        startBot();
        res.status(500).json({ error: err.message });
      });
    }, 2000);
  } catch (err) {
    botStopped = false;
    startBot();
    res.status(500).json({ error: err.message });
  }
});

app.use('/api/control', controlRouter);
app.use('/api/positions', positionsRouter);
app.use('/api/equity',   equityRouter);
app.use('/api/alerts',       alertsRouter);
app.use('/api/system-health', systemHealthRouter);
app.use('/api/signal-queue',  signalQueueRouter);
app.use('/api/events',        eventsRouter);
app.use('/api/performance',   performanceRouter);
app.use('/api/backtest',      backtestRouter);

// Secure control middleware
const systemAuthMiddleware = (req, res, next) => {
  const apiKey = process.env.DASHBOARD_API_KEY || process.env.ZISI_API_KEY || process.env.API_KEY || '4444';
  
  let providedKey = null;
  
  // 1. Authorization: Bearer <token>
  if (req.headers.authorization) {
    const parts = req.headers.authorization.split(' ');
    if (parts.length === 2 && parts[0].toLowerCase() === 'bearer') {
      providedKey = parts[1];
    }
  }
  
  // 2. X-API-Key header
  if (!providedKey && req.headers['x-api-key']) {
    providedKey = req.headers['x-api-key'];
  }
  
  // 3. Query string parameter
  if (!providedKey && req.query.apiKey) {
    providedKey = req.query.apiKey;
  }
  
  if (providedKey === apiKey) {
    next();
  } else {
    console.warn(`[AUTH] Unauthorized control attempt to ${req.originalUrl} from ${req.ip}`);
    res.status(401).json({ error: 'Unauthorized', message: 'Invalid or missing API key.' });
  }
};

// Start / Stop Bot Engine API endpoints for Settings Tab (AUTHENTICATED)
app.get('/api/control/system/status', systemAuthMiddleware, (req, res) => {
  try {
    let isRunning = false;
    let pid = null;
    if (process.platform === 'win32') {
      isRunning = botProcess && !botProcess.killed;
      pid = botProcess ? botProcess.pid : null;
    } else {
      try {
        const stdout = execSync('pm2 jlist').toString();
        const list = JSON.parse(stdout);
        const bot = list.find(app => app.name === 'ZiSi-Core-Engine');
        isRunning = bot ? bot.pm2_env.status === 'online' : false;
        pid = bot ? bot.pid : null;
      } catch (e) {
        console.error('[PM2] Failed to check status:', e.message);
      }
    }
    res.json({
      isRunning: !!isRunning,
      botStopped: botStopped,
      pid: pid
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/control/system/start', systemAuthMiddleware, (req, res) => {
  try {
    botStopped = false;
    startBot();
    res.json({ status: 'running', message: 'Bot engine start command sent successfully.' });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/control/system/stop', systemAuthMiddleware, (req, res) => {
  try {
    stopBot();
    res.json({ status: 'stopped', message: 'Bot engine stop command sent successfully.' });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.use((err, req, res, next) => {
  console.error('Error:', err);
  res.status(500).json({ error: 'Internal server error', details: err.message });
});

// ── Serve built frontend if dist/ exists ──────────────────────────────────────
const frontendDist = path.join(__dirname, '../frontend/dist');
if (fs.existsSync(frontendDist)) {
  app.use(express.static(frontendDist));
  // SPA catch-all — don't intercept /api routes
  app.get(/^(?!\/api).*/, (req, res) => {
    res.sendFile(path.join(frontendDist, 'index.html'));
  });
  console.log(`📊 Dashboard (built): http://localhost:${PORT}`);
} else {
  console.log(`📊 Dashboard (dev):   http://localhost:3000  (run frontend dev server separately)`);
}

// ── Bot process management ────────────────────────────────────────────────────
let botProcess = null;
let botStopped = false;   // set true on intentional shutdown so we don't restart

function startBot() {
  if (botStopped) return;

  if (process.platform !== 'win32') {
    console.log('[DASHBOARD] Directing PM2 to start ZiSi-Core-Engine...');
    spawn('pm2', ['start', 'ZiSi-Core-Engine'], { stdio: 'inherit' });
    startHeartbeatWatchdog();
    return;
  }

  const pythonCmd = process.env.PYTHON_PATH || 'C:\\Python313\\python.exe';

  if (!fs.existsSync(path.join(BOT_ROOT, 'app', 'main.py'))) {
    console.error('❌  app/main.py not found at', path.join(BOT_ROOT, 'app'), '— bot not started');
    return;
  }

  console.log('');
  console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
  console.log('🚀  Spawning ZiSi Bot locally (python app/main.py)');
  console.log('    Root:', BOT_ROOT);
  console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
  console.log('');

  botProcess = spawn(pythonCmd, ['app/main.py'], {
    cwd: BOT_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
  });

  botProcess.stdout.setEncoding('utf8');
  botProcess.stdout.on('data', (chunk) => {
    process.stdout.write(chunk);
  });

  botProcess.stderr.setEncoding('utf8');
  botProcess.stderr.on('data', (chunk) => {
    process.stderr.write(chunk);
  });

  botProcess.on('error', (err) => {
    console.error('');
    console.error('❌  Failed to spawn bot:', err.message);
  });

  botProcess.on('exit', (code, signal) => {
    if (botStopped) {
      console.log('\n✅  Bot process stopped cleanly.');
      return;
    }
    const msg = signal ? `signal=${signal}` : `exit code ${code}`;
    console.log('');
    console.log(`⚠️   Bot exited unexpectedly (${msg}) — restarting in 15 s...`);
    console.log('');
    setTimeout(startBot, 15_000);
  });

  console.log(`🤖  Bot PID: ${botProcess.pid}`);
  startHeartbeatWatchdog();
}

// ── Heartbeat watchdog loop ──────────────────────────────────────────────────
let watchdogInterval = null;
function startHeartbeatWatchdog() {
  if (watchdogInterval) clearInterval(watchdogInterval);
  watchdogInterval = setInterval(() => {
    try {
      if (botStopped) return;
      if (process.platform === 'win32' && !botProcess) return;

      const stateFile = path.join(BOT_ROOT, 'data', 'account_state.json');
      if (fs.existsSync(stateFile)) {
        const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
        const lastUpdated = new Date(state.last_updated);
        const diffSeconds = (new Date() - lastUpdated) / 1000;
        if (diffSeconds > 180) { // No heartbeat for 3 minutes
          console.warn(`⚠️  [WATCHDOG WARNING] No heartbeat from bot for ${diffSeconds.toFixed(1)}s (Restart bypassed to prevent cascading disruptions).`);
        }
      }
    } catch (err) {
      console.error('⚠️  [WATCHDOG] Failed to read account_state.json:', err.message);
    }
  }, 60_000);
}

function stopBot() {
  botStopped = true;
  if (watchdogInterval) {
    clearInterval(watchdogInterval);
    watchdogInterval = null;
  }
  if (process.platform === 'win32') {
    if (botProcess && !botProcess.killed) {
      console.log('\n🛑  Stopping local bot process...');
      botProcess.kill('SIGTERM');
      setTimeout(() => {
        if (botProcess && !botProcess.killed) {
          console.warn('    Local bot did not exit in time — sending SIGKILL');
          botProcess.kill('SIGKILL');
        }
      }, 6_000);
    }
  } else {
    console.log('[DASHBOARD] Directing PM2 to stop ZiSi-Core-Engine...');
    try {
      execSync('pm2 stop ZiSi-Core-Engine');
    } catch (e) {
      console.error('[DASHBOARD] Failed to stop bot via PM2:', e.message);
    }
  }
}

// Graceful shutdown on Ctrl+C or process kill
process.on('SIGINT',  () => { console.log('\n\n🛑  ZiSi shutting down (Ctrl+C)...'); stopBot(); process.exit(0); });
process.on('SIGTERM', () => { stopBot(); process.exit(0); });

// ── Integrated health monitor ─────────────────────────────────────────────────
function startHealthMonitor() {
  const stateFile = path.join(BOT_ROOT, 'data', 'account_state.json');

  const check = () => {
    try {
      if (!fs.existsSync(stateFile)) {
        console.warn('[Health] State file not found — bot may not have started yet');
        return;
      }
      const state = JSON.parse(fs.readFileSync(stateFile, 'utf-8'));
      const minutesAgo = (Date.now() - new Date(state.last_updated)) / 60000;
      const healthy = minutesAgo < 30;
      const icon = healthy ? '✅' : minutesAgo < 45 ? '⚠️' : '❌';
      console.log(
        `${icon} [Health] ${minutesAgo.toFixed(1)}m ago | ` +
        `$${parseFloat(state.balance || 100).toFixed(2)} | ` +
        `trades: ${state.trades_executed || 0}`
      );
      if (minutesAgo > 45) {
        console.error('[Health] ALERT: Bot appears offline (>45 min since last update)');
      }
    } catch (err) {
      console.error('[Health] Check error:', err.message);
    }
  };

  setTimeout(() => { check(); setInterval(check, 30 * 60 * 1000); }, 60_000);
  console.log('🤖  Health monitor active — checks every 30 min');
}

// ── WebSocket Server and Client Management ────────────────────────────────────
const wsClients = new Set();
const accountStatePath = path.join(BOT_ROOT, 'data', 'account_state.json');
const positionsStatePath = path.join(BOT_ROOT, 'data', 'positions_state.json');
const chainlinkPricesPath = path.join(BOT_ROOT, 'data', 'chainlink_prices.json');
const pythPricesPath = path.join(BOT_ROOT, 'data', 'pyth_prices.json');

function broadcastWS(eventObj) {
  const msg = JSON.stringify(eventObj);
  for (const client of wsClients) {
    if (client.readyState === 1) { // 1 = OPEN
      try {
        client.send(msg);
      } catch (err) {
        wsClients.delete(client);
      }
    }
  }
}

const _priceCache = new Map();
const PRICE_CACHE_TTL_MS = 1000;
const _entrySpotCache = new Map();

let _spotPriceCache = {
  pyth: { data: null, ts: 0 },
  cl: { data: null, ts: 0 }
};

function getParsedSpotPrices(type) {
  const now = Date.now();
  const cache = _spotPriceCache[type];
  if (cache.data && now - cache.ts < 200) {
    return cache.data;
  }
  const filePath = type === 'pyth' ? pythPricesPath : chainlinkPricesPath;
  try {
    if (fs.existsSync(filePath)) {
      const raw = fs.readFileSync(filePath, 'utf-8');
      const data = JSON.parse(raw);
      _spotPriceCache[type] = { data, ts: now };
      return data;
    }
  } catch (err) {
    // Fail silently
  }
  return cache.data || {};
}

async function _fetchClobPrice(marketId, pos) {
  if (!marketId || marketId === 'test_market_abc') return null;
  const cached = _priceCache.get(marketId);
  // Cache hits within 8 seconds are returned instantly
  if (cached && Date.now() - cached.ts < 8000) return cached.price;

  // Return null if not in cache (rely on python bot updates in positions_state.json)
  return null;
}

async function getEnrichedPositionsPayload() {
  const fallback = { summary: {}, active: [], closed: [] };
  if (!fs.existsSync(positionsStatePath)) {
    return fallback;
  }
  try {
    const raw = fs.readFileSync(positionsStatePath, 'utf-8').trim();
    if (!raw) return fallback;
    const positions = JSON.parse(raw.replace(/^﻿/, ''));
    const active = positions.active || [];
    const closed = positions.closed || [];
    const summary = positions.summary || {};
    
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
        // price_source: 'clob' = live order-book price, 'spot_fallback' = oracle-derived
        const price_source = _priceCache.has(marketId) ? 'clob' : 'spot_fallback';
        return { ...pos, current_price: livePrice, unrealized_pnl: unrealizedPnl, price_source };
      }
      liveUnrealized += parseFloat(pos.unrealized_pnl || 0);
      return pos;
    }));
    
    return {
      summary: {
        ...summary,
        unrealized_pnl: Math.round(liveUnrealized * 100) / 100
      },
      active: enrichedActive,
      closed
    };
  } catch (err) {
    console.error('[SERVER] Failed to parse positions_state.json, returning fallback:', err.message);
    return fallback;
  }
}

function getBalancePayload() {
  const fallback = { balance: 50.00, starting_balance: 50.00, positions: { active: [], closed: [] }, running: false, minutesAgo: null, last_update_minutes_ago: null, chainlinkPrices: {}, pythPrices: {} };
  let isRunning = false;
  let pm2Uptime = null;
  try {
    if (process.platform === 'win32') {
      isRunning = !!(botProcess && !botProcess.killed);
    } else {
      try {
        const stdout = execSync('pm2 jlist').toString();
        const list = JSON.parse(stdout);
        const bot = list.find(app => app.name === 'ZiSi-Core-Engine');
        isRunning = bot ? bot.pm2_env.status === 'online' : false;
        if (bot && bot.pm2_env && bot.pm2_env.pm_uptime) {
          pm2Uptime = bot.pm2_env.pm_uptime;
        }
      } catch (e) {
        // console.error('[PM2] Failed to check status:', e.message);
      }
    }
  } catch (_) {}

  let chainlinkPrices = {};
  try {
    if (fs.existsSync(chainlinkPricesPath)) {
      chainlinkPrices = JSON.parse(fs.readFileSync(chainlinkPricesPath, 'utf-8'));
    }
  } catch (_) {}

  let pythPrices = {};
  try {
    if (fs.existsSync(pythPricesPath)) {
      pythPrices = JSON.parse(fs.readFileSync(pythPricesPath, 'utf-8'));
    }
  } catch (_) {}

  if (!fs.existsSync(accountStatePath)) {
    return { ...fallback, running: isRunning, chainlinkPrices, pythPrices };
  }
  try {
    const raw = fs.readFileSync(accountStatePath, 'utf-8').trim();
    if (!raw) return { ...fallback, running: isRunning, chainlinkPrices, pythPrices };
    const parsed = JSON.parse(raw.replace(/^﻿/, ''));
    if (parsed.balance === undefined || parsed.balance === null) {
      parsed.balance = 50.00;
    }
    if (parsed.starting_balance === undefined || parsed.starting_balance === null) {
      parsed.starting_balance = 50.00;
    }
    
    let minutesAgo = null;
    if (parsed.last_updated) {
      const lastUpdate = new Date(parsed.last_updated);
      minutesAgo = Math.floor((new Date() - lastUpdate) / 60000);
    }

    let runtime = { hours: 0, days: 0, progressPercent: 0, goalHours: 336, status: 'tracking', start_time: null };
    try {
      const runtimeFile = path.join(BOT_ROOT, 'runtime_tracking.json');
      if (fs.existsSync(runtimeFile)) {
        const rt = JSON.parse(fs.readFileSync(runtimeFile, 'utf-8').replace(/^﻿/, ''));
        const hours = rt.runtime_hours || 0;
        runtime = {
          hours: Math.round(hours * 10) / 10,
          days: Math.floor(hours / 24),
          progressPercent: rt.progress_percent || 0,
          goalHours: rt.goal_hours || 336,
          status: rt.status || 'tracking',
          start_time: rt.start_time || null,
        };
      }
    } catch (_) {}

    if (pm2Uptime) {
      runtime.start_time = new Date(pm2Uptime).toISOString();
    }

    return {
      ...parsed,
      running: isRunning,
      minutesAgo,
      last_update_minutes_ago: minutesAgo,
      runtime,
      chainlinkPrices,
      pythPrices
    };
  } catch (err) {
    console.error('[SERVER] Failed to parse account_state.json, returning fallback:', err.message);
    return { ...fallback, running: isRunning, chainlinkPrices, pythPrices };
  }
}

function getCandleBoundaryPayload() {
  const now = Math.floor(Date.now() / 1000);
  
  let potentialTrades = {};
  try {
    const ptPath = path.join(BOT_ROOT, 'data', 'potential_trades.json');
    if (fs.existsSync(ptPath)) {
      potentialTrades = JSON.parse(fs.readFileSync(ptPath, 'utf-8'));
    }
  } catch (err) {
    // Ignore read error
  }

  return [
    { asset: 'BTC', tf: '5m',  secs: 300 - (now % 300), potential: !!potentialTrades['BTC/5m'] },
    { asset: 'BTC', tf: '15m', secs: 900 - (now % 900), potential: !!potentialTrades['BTC/15m'] },
    { asset: 'ETH', tf: '5m',  secs: 300 - (now % 300), potential: !!potentialTrades['ETH/5m'] },
    { asset: 'ETH', tf: '15m', secs: 900 - (now % 900), potential: !!potentialTrades['ETH/15m'] },
    { asset: 'SOL', tf: '5m',  secs: 300 - (now % 300), potential: !!potentialTrades['SOL/5m'] },
    { asset: 'SOL', tf: '15m', secs: 900 - (now % 900), potential: !!potentialTrades['SOL/15m'] },
    { asset: 'XRP', tf: '5m',  secs: 300 - (now % 300), potential: !!potentialTrades['XRP/5m'] },
    { asset: 'XRP', tf: '15m', secs: 900 - (now % 900), potential: !!potentialTrades['XRP/15m'] },
    { asset: 'DOGE', tf: '5m',  secs: 300 - (now % 300), potential: !!potentialTrades['DOGE/5m'] },
    { asset: 'DOGE', tf: '15m', secs: 900 - (now % 900), potential: !!potentialTrades['DOGE/15m'] },
  ];
}

let _pushPositionsActive = false;
async function pushPositionsUpdate() {
  if (_pushPositionsActive) return;
  _pushPositionsActive = true;
  try {
    const payload = await getEnrichedPositionsPayload();
    if (payload) {
      broadcastWS({ type: 'position_update', payload, ts: Date.now() });
    }
  } catch (err) {
    console.error('[SERVER] Failed to push positions update:', err.message);
  } finally {
    _pushPositionsActive = false;
  }
}

async function pushBalanceUpdate() {
  try {
    const payload = getBalancePayload();
    if (payload) {
      broadcastWS({ type: 'balance_update', payload, ts: Date.now() });
    }
  } catch (err) {
    console.error('[SERVER] Failed to push balance update:', err.message);
  }
}

async function sendInitialState(ws) {
  try {
    const bal = getBalancePayload();
    if (bal) ws.send(JSON.stringify({ type: 'balance_update', payload: bal, ts: Date.now() }));
    const pos = await getEnrichedPositionsPayload();
    if (pos) ws.send(JSON.stringify({ type: 'positions_snapshot', payload: pos, ts: Date.now() }));
    ws.send(JSON.stringify({ type: 'candle_boundary', payload: getCandleBoundaryPayload(), ts: Date.now() }));
  } catch (err) {}
}

const dataDir = path.join(BOT_ROOT, 'data');
let lastDataDirEvent = 0;

if (fs.existsSync(dataDir)) {
  try {
    fs.watch(dataDir, (eventType, filename) => {
      if (!filename) return;
      const now = Date.now();
      if (now - lastDataDirEvent < 100) return;
      lastDataDirEvent = now;

      if (filename.includes('positions_state.json')) {
        setTimeout(pushPositionsUpdate, 50);
      } else if (filename.includes('account_state.json')) {
        setTimeout(pushBalanceUpdate, 50);
      } else if (filename.includes('pyth_prices.json') || filename.includes('chainlink_prices.json')) {
        setTimeout(() => {
          Promise.all([pushBalanceUpdate(), pushPositionsUpdate()]).catch(() => {});
        }, 50);
      }
    });
    console.log(`👁️  Watching data directory: ${dataDir}`);
  } catch (e) {
    console.error(`⚠️  Failed to watch data directory ${dataDir}:`, e.message);
  }
}

let lastPositionsMtime = 0;
let lastAccountMtime = 0;

// Guaranteed real-time fallback polling (optimized to stats-only non-blocking checks)
setInterval(async () => {
  if (wsClients.size === 0) return;
  
  try {
    if (fs.existsSync(positionsStatePath)) {
      const stat = fs.statSync(positionsStatePath);
      if (stat.mtimeMs !== lastPositionsMtime) {
        lastPositionsMtime = stat.mtimeMs;
        await pushPositionsUpdate();
      }
    }
  } catch (err) {
    // Fail silently
  }

  try {
    if (fs.existsSync(accountStatePath)) {
      const stat = fs.statSync(accountStatePath);
      if (stat.mtimeMs !== lastAccountMtime) {
        lastAccountMtime = stat.mtimeMs;
        await pushBalanceUpdate();
      }
    }
  } catch (err) {
    // Fail silently
  }
}, 500);

// Send ping every 5 seconds to keep WebSocket connections alive and prevent SSH tunnel timeouts
setInterval(() => {
  if (wsClients.size > 0) {
    const pingMessage = JSON.stringify({ type: 'ping', ts: Date.now() });
    for (const client of wsClients) {
      if (client.readyState === 1) { // OPEN
        client.send(pingMessage);
      }
    }
  }
}, 5000);

// Background fetcher to keep polymarket prices fresh without blocking the main event loop
async function updateClobPricesBackground() {
  if (!fs.existsSync(positionsStatePath)) return;
  try {
    const raw = fs.readFileSync(positionsStatePath, 'utf-8').trim();
    if (!raw) return;
    const positions = JSON.parse(raw.replace(/^﻿/, ''));
    const active = positions.active || [];
    let updated = false;
    for (const pos of active) {
      if (pos.market === 'POLYMARKET') {
        const marketId = pos.market_id || pos.order_id;
        if (marketId && marketId !== 'test_market_abc') {
          try {
            const r = await fetch(`https://clob.polymarket.com/book?token_id=${marketId}`, {
              headers: { 'User-Agent': 'Mozilla/5.0' },
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
                updated = true;
              }
            }
          } catch (err) {
            // Ignore background fetch error
          }
        }
      }
    }
    if (updated) {
      pushPositionsUpdate();
    }
  } catch (err) {}
}

// Run background clob price update every 5 seconds
setInterval(updateClobPricesBackground, 5000);
updateClobPricesBackground();

setInterval(() => {
  if (wsClients.size > 0) {
    broadcastWS({ type: 'candle_boundary', payload: getCandleBoundaryPayload(), ts: Date.now() });
    pushBalanceUpdate();
  }
}, 5000);

// Periodic WS positions push every 30s (safety net — complements file-watcher)
// Ensures Vite UI dashboard reflects spot-fallback prices even when
// positions_state.json is not written between CLOB outages.
setInterval(() => {
  if (wsClients.size > 0) {
    pushPositionsUpdate();
  }
}, 30_000);

// ── Start server, then launch bot ─────────────────────────────────────────────
const server = app.listen(PORT, () => {
  console.log('');
  console.log('╔══════════════════════════════════════════════════╗');
  console.log(`║  ✅  ZiSi Dashboard  →  http://localhost:${PORT}   ║`);
  console.log('╚══════════════════════════════════════════════════╝');
  console.log('');
  startHealthMonitor();

  // Initialize WebSockets Server
  const wss = new WebSocketServer({ server });
  wss.on('connection', (ws) => {
    console.log('🔌  WebSocket Handshake Confirmed: Client connected');
    wsClients.add(ws);
    sendInitialState(ws);
    ws.on('close', () => {
      console.log('🔌  WebSocket client disconnected');
      wsClients.delete(ws);
    });
    ws.on('error', () => wsClients.delete(ws));
  });
  console.log('🔌  WebSocket Server active on the dashboard port');

  // Small delay so the server is fully ready before the bot starts writing files
  setTimeout(startBot, 1_000);

  // Automatically open default browser pointing to local host on startup (Windows)
  if (process.platform === 'win32') {
    import('child_process').then(({ exec }) => {
      setTimeout(() => {
        try {
          exec('start http://localhost:5000');
          console.log('🚀  Automatically launched default browser at http://localhost:5000');
        } catch (e) {
          console.error('⚠️  Failed to auto-launch browser:', e.message);
        }
      }, 1500);
    });
  }
});
