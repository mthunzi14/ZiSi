import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BOT_DATA_PATH = path.join(__dirname, '../../..');

// row format: [timestamp, source, headline, sentiment, score, reasoning, coins, price, status]
function normalizeEntry(raw) {
  if (raw.type === 'signal' && Array.isArray(raw.row)) {
    const [ts, source, headline, sentiment, score, reasoning, coins, price, status] = raw.row;
    return {
      order_id: `sig_${ts}_${Math.random().toString(36).slice(2, 7)}`,
      event_title: headline,
      source,
      sentiment,
      signal_confidence: parseInt(score) || 7,
      reasoning,
      coins_mentioned: coins,
      entry_price: parseFloat(price) || 0,
      exit_price: 0,
      profit: 0,
      profit_percent: 0,
      status: status || 'SIGNAL',
      timestamp_open: ts,
      timestamp: ts,
      _type: 'signal'
    };
  }
  // Already normalized trade format
  return { ...raw, timestamp: raw.timestamp_open || raw.timestamp, _type: 'trade' };
}

export function readTradesFile() {
  try {
    const filePath = path.join(BOT_DATA_PATH, 'zisi_local_trades.jsonl');
    if (!fs.existsSync(filePath)) {
      return [];
    }

    const content = fs.readFileSync(filePath, 'utf-8');
    const trades = content
      .split('\n')
      .filter(line => line.trim())
      .map(line => {
        try {
          const raw = JSON.parse(line);
          return normalizeEntry(raw);
        } catch (e) {
          console.error('Parse error:', e);
          return null;
        }
      })
      .filter(entry => entry !== null);

    return trades;
  } catch (error) {
    console.error('Error reading trades:', error);
    return [];
  }
}

export function readMetricsFile() {
  try {
    const today = new Date().toISOString().split('T')[0];
    const filePath = path.join(BOT_DATA_PATH, `metrics_${today}.json`);

    if (!fs.existsSync(filePath)) {
      return null;
    }

    const content = fs.readFileSync(filePath, 'utf-8');
    return JSON.parse(content);
  } catch (error) {
    console.error('Error reading metrics:', error);
    return null;
  }
}

export function readAccountState() {
  try {
    const filePath = path.join(BOT_DATA_PATH, 'account_state.json');

    if (!fs.existsSync(filePath)) {
      return { balance: 100, last_updated: null };
    }

    const content = fs.readFileSync(filePath, 'utf-8');
    return JSON.parse(content);
  } catch (error) {
    console.error('Error reading account state:', error);
    return { balance: 100, last_updated: null };
  }
}

export function getBotStatus() {
  try {
    const trades = readTradesFile();
    const accountState = readAccountState();

    if (!accountState.last_updated) {
      return {
        running: false,
        status: 'offline',
        paused: false,
        last_update: null,
        last_update_minutes_ago: null,
        cycles_completed: trades.length,
        account_balance: accountState.balance,
        last_update_reason: null,
        error: 'State file never written'
      };
    }

    const lastUpdated = new Date(accountState.last_updated);
    const now = new Date();
    const minutesAgo = (now - lastUpdated) / 60000;
    const isPaused = accountState.paused === true;

    let status, running;
    if (isPaused) {
      status = 'paused';
      running = false;
    } else if (minutesAgo < 30) {
      status = 'running';
      running = true;
    } else if (minutesAgo < 45) {
      status = 'stale';
      running = false;
    } else {
      status = 'offline';
      running = false;
    }

    return {
      running,
      status,
      paused: isPaused,
      last_update: accountState.last_updated,
      last_update_minutes_ago: Math.round(minutesAgo),
      cycles_completed: trades.length,
      account_balance: accountState.balance,
      trades_executed: accountState.trades_executed || 0,
      last_update_reason: accountState.last_change_reason || accountState.last_update_reason || null,
      error: null
    };
  } catch (error) {
    console.error('Error getting bot status:', error);
    return { running: false, status: 'error', error: error.message, last_update: null };
  }
}
