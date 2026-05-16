import express from 'express';
import cors from 'cors';
import dotenv from 'dotenv';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { spawn } from 'child_process';
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
app.use('/api/control', controlRouter);
app.use('/api/positions', positionsRouter);
app.use('/api/equity',   equityRouter);
app.use('/api/alerts',       alertsRouter);
app.use('/api/system-health', systemHealthRouter);
app.use('/api/signal-queue',  signalQueueRouter);
app.use('/api/events',        eventsRouter);
app.use('/api/performance',   performanceRouter);

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

  const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';

  if (!fs.existsSync(path.join(BOT_ROOT, 'main.py'))) {
    console.error('❌  main.py not found at', BOT_ROOT, '— bot not started');
    return;
  }

  console.log('');
  console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
  console.log('🚀  Spawning ZiSi Bot  (python main.py)');
  console.log('    Root:', BOT_ROOT);
  console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
  console.log('');

  botProcess = spawn(pythonCmd, ['main.py'], {
    cwd: BOT_ROOT,
    // inherit stdin from parent so Ctrl+C propagates; pipe stdout/stderr so we
    // can stream them to this terminal directly
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env },
  });

  // Stream bot stdout directly — it already contains timestamps + log levels
  botProcess.stdout.setEncoding('utf8');
  botProcess.stdout.on('data', (chunk) => {
    process.stdout.write(chunk);
  });

  // Stream bot stderr directly (Python logs to stderr via the logging module)
  botProcess.stderr.setEncoding('utf8');
  botProcess.stderr.on('data', (chunk) => {
    process.stderr.write(chunk);
  });

  botProcess.on('error', (err) => {
    console.error('');
    console.error('❌  Failed to spawn bot:', err.message);
    if (err.code === 'ENOENT') {
      console.error('    "python" not found — install Python or check your PATH');
    }
  });

  botProcess.on('exit', (code, signal) => {
    if (botStopped) {
      console.log('\n✅  Bot process stopped cleanly.');
      return;
    }
    // Unexpected exit — auto-restart after a short delay
    const msg = signal ? `signal=${signal}` : `exit code ${code}`;
    console.log('');
    console.log(`⚠️   Bot exited unexpectedly (${msg}) — restarting in 15 s...`);
    console.log('');
    setTimeout(startBot, 15_000);
  });

  console.log(`🤖  Bot PID: ${botProcess.pid}`);
}

function stopBot() {
  botStopped = true;
  if (botProcess && !botProcess.killed) {
    console.log('\n🛑  Stopping bot process...');
    botProcess.kill('SIGTERM');
    // Force-kill if it doesn't exit within 6 s
    setTimeout(() => {
      if (botProcess && !botProcess.killed) {
        console.warn('    Bot did not exit in time — sending SIGKILL');
        botProcess.kill('SIGKILL');
      }
    }, 6_000);
  }
}

// Graceful shutdown on Ctrl+C or process kill
process.on('SIGINT',  () => { console.log('\n\n🛑  ZiSi shutting down (Ctrl+C)...'); stopBot(); process.exit(0); });
process.on('SIGTERM', () => { stopBot(); process.exit(0); });

// ── Integrated health monitor ─────────────────────────────────────────────────
function startHealthMonitor() {
  const stateFile = path.join(BOT_ROOT, 'account_state.json');

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

// ── Start server, then launch bot ─────────────────────────────────────────────
app.listen(PORT, () => {
  console.log('');
  console.log('╔══════════════════════════════════════════════════╗');
  console.log(`║  ✅  ZiSi Dashboard  →  http://localhost:${PORT}   ║`);
  console.log('╚══════════════════════════════════════════════════╝');
  console.log('');
  startHealthMonitor();

  // Small delay so the server is fully ready before the bot starts writing files
  setTimeout(startBot, 1_000);
});
