#!/usr/bin/env bash
set -e
cd /root/ZiSi

echo "=== [1/6] Creating .env ==="
cat > .env << 'ENVEOF'
BOT_MODE=paper_trading
GMAIL_ENABLED=false
LOG_TO_DRIVE=false
DAILY_REPORT_EMAIL=false
RISK_PER_TRADE_PERCENT=2
MAX_SIMULTANEOUS_TRADES=6
ENVEOF
chmod 600 .env

echo "=== [2/6] Installing Node deps ==="
npm --prefix presentation/dashboard/frontend install --silent
npm --prefix presentation/dashboard/backend install --silent

echo "=== [3/6] Building frontend ==="
npm --prefix presentation/dashboard/frontend run build

echo "=== [4/6] Clean slate — resetting to \$50 ==="
venv/bin/python3 miscellaneous/clean_slate.py --balance 50 --force

echo "=== [5/6] Creating PM2 ecosystem ==="
cat > ecosystem.config.js << 'EOF'
module.exports = {
  apps: [
    {
      name: 'zisi-bot',
      script: '/root/ZiSi/venv/bin/python3',
      args: 'app/main.py',
      cwd: '/root/ZiSi',
      interpreter: 'none',
      autorestart: true,
      watch: false,
      max_memory_restart: '800M',
      env: { PYTHONPATH: '/root/ZiSi' }
    },
    {
      name: 'zisi-dashboard',
      script: 'presentation/dashboard/backend/server.js',
      cwd: '/root/ZiSi',
      autorestart: true,
      watch: false,
      env: { PORT: '5000', NODE_ENV: 'production' }
    }
  ]
};
EOF

echo "=== [6/6] Starting with PM2 ==="
pm2 start ecosystem.config.js
pm2 save
pm2 startup | tail -1

echo ""
echo "============================================"
echo "  DONE. ZiSi is live on the VPS."
echo "  To view dashboard, run this on your laptop:"
echo "  ssh -L 5000:localhost:5000 root@204.168.222.48"
echo "  Then open: http://localhost:5000"
echo "============================================"
pm2 list
