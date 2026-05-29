# ZiSi Bot — VPS Migration Handbook

> **Scope:** Move ZiSi from a Windows development machine to a 24/7 Ubuntu 22.04 LTS VPS so
> the dashboard is reachable from your phone and the bot runs unattended.
>
> **Architecture recap:** `npm start` (root `package.json`) builds the React frontend and starts
> `presentation/dashboard/backend/server.js` on **port 5000**. That Express process *spawns*
> `python3 app/main.py` as a child process and watchdogs it (restarts if `account_state.json` is
> >4 min stale). PM2 therefore only needs to manage the single Node process — it already owns the
> Python bot. Nginx sits in front, proxying HTTPS → port 5000, with HTTP Basic Auth guarding the
> dashboard from the open internet.

---

## 1. Provider & Sizing

| Provider | Plan | vCPU | RAM | Disk | Price |
|---|---|---|---|---|---|
| **DigitalOcean** | Basic (Regular) | 2 | 2 GB | 50 GB SSD | ~$12/mo |
| **Hetzner** | CPX11 | 2 | 2 GB | 40 GB SSD | ~€4.15/mo |

**Recommendation:** Hetzner CPX11 if cost matters most; DigitalOcean Basic if you want the
simpler UI and better US-East peering.

**Region:** For Polymarket (Polygon RPC / US APIs) and Binance data feeds choose
**US East** — DigitalOcean `nyc3` or Hetzner `ash` (Ashburn, Virginia).
Both give <25 ms round-trip to Polymarket endpoints.

**OS:** Ubuntu 22.04 LTS (select at droplet/server creation).

---

## 2. Initial Server Hardening

Run all commands as **root** immediately after first login, then switch to the new sudo user.

### 2a. Create a non-root sudo user

```bash
adduser zisi
usermod -aG sudo zisi
```

### 2b. Copy your SSH key to the new user

From your **local machine**:

```bash
ssh-copy-id zisi@<VPS_IP>
```

Or paste your public key manually on the server:

```bash
mkdir -p /home/zisi/.ssh
# paste the contents of your local ~/.ssh/id_ed25519.pub (or id_rsa.pub)
echo "ssh-ed25519 AAAA...your-public-key..." >> /home/zisi/.ssh/authorized_keys
chmod 700 /home/zisi/.ssh
chmod 600 /home/zisi/.ssh/authorized_keys
chown -R zisi:zisi /home/zisi/.ssh
```

### 2c. Disable password + root SSH login

```bash
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart sshd
```

**Log out, then reconnect as `zisi@<VPS_IP>` using your key before continuing.**

### 2d. Firewall (ufw)

Port 5000 must **never** be open to the internet — nginx handles it.

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
# Port 5000 is intentionally NOT opened here; nginx proxies it locally
sudo ufw enable
sudo ufw status verbose
```

### 2e. Automatic security updates

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure --priority=low unattended-upgrades
# Accept defaults (security updates only, automatic reboot off)
```

---

## 3. Runtime Installation

All commands as `zisi` (sudo where needed).

### 3a. System packages

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl build-essential python3.10 python3.10-venv python3-pip \
    python3.10-dev nginx certbot python3-certbot-nginx apache2-utils
```

### 3b. Node.js 18 (via NodeSource)

```bash
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt install -y nodejs
node -v   # should print v18.x.x
npm -v
```

### 3c. Clone the repo

```bash
cd /home/zisi
git clone https://github.com/<your-username>/ZiSi_Bot.git
cd ZiSi_Bot
```

### 3d. Python virtual environment + dependencies

```bash
python3.10 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
pip install vaderSentiment

deactivate
```

> `torch` and `transformers` are the heaviest packages (~1–2 GB). On 2 GB RAM the install may
> be slow; if it OOMs, add a 2 GB swap file first (see note below).

**Optional: 2 GB swap (recommended for 2 GB RAM VPS)**

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 3e. Node dependencies + frontend build

```bash
cd /home/zisi/ZiSi_Bot

# Install both workspaces
npm --prefix presentation/dashboard/frontend install
npm --prefix presentation/dashboard/backend install

# Build the React frontend (output goes to presentation/dashboard/frontend/dist/)
npm --prefix presentation/dashboard/frontend run build
```

---

## 4. Secrets

The bot reads secrets from `.env` at the **repo root**. Never commit this file.

```bash
cd /home/zisi/ZiSi_Bot

# Copy the template
cp .env.example .env

# Open and fill in real values
nano .env
```

Minimum required fields (see `.env.example` for the full list):

```
POLYMARKET_GAMMA_API_URL=https://gamma-api.polymarket.com
POLYMARKET_DATA_API_URL=https://data-api.polymarket.com
POLYMARKET_CLOB_API_URL=https://clob.polymarket.com
TELEGRAM_BOT_TOKEN=<your-token>
TELEGRAM_CHAT_ID=<your-chat-id>
BOT_MODE=paper_trading
```

Lock down the file:

```bash
chmod 600 /home/zisi/ZiSi_Bot/.env
```

> **Key rotation:** If `TELEGRAM_BOT_TOKEN`, `PMXT_API_KEY`, `GMAIL_APP_PASSWORD`, or any
> `POLYMARKET_PRIVATE_KEY` was ever shared in a chat, screenshot, or commit — **revoke and
> reissue it before this server goes live.** Treat any key that touched a chat window as
> compromised.

Also create `.env` inside the backend directory so `dotenv.config()` inside `server.js` can
find it (or symlink):

```bash
ln -s /home/zisi/ZiSi_Bot/.env /home/zisi/ZiSi_Bot/presentation/dashboard/backend/.env
```

---

## 5. Process Management with PM2

### Why PM2 manages only the Node process

`server.js` already:
- Spawns `python3 app/main.py` as a child process.
- Restarts it automatically on unexpected exit (15 s delay).
- Runs a 60-second heartbeat watchdog that force-kills and restarts the Python bot if
  `account_state.json` is >4 min stale.

If you also added the Python bot to PM2, you would have **two independent supervisors** fighting
over the same process — they would create duplicate bot instances or fight over restarts.
**Do not add `app/main.py` to PM2 directly.** Let PM2 own Node; Node owns Python.

> **Alternative (not recommended):** Run Node and Python as separate PM2 apps, disable the
> `startBot()` logic inside `server.js`, and rely entirely on PM2 for restarts. This is more
> work and removes the watchdog. Stick with the default single-process approach.

### 5a. Install PM2 globally

```bash
sudo npm install -g pm2
```

### 5b. Create `ecosystem.config.js`

Create this file at `/home/zisi/ZiSi_Bot/ecosystem.config.js`:

```js
module.exports = {
  apps: [
    {
      name: 'zisi-dashboard',
      // server.js must be started from the backend directory so relative
      // paths (BOT_ROOT = ../../..) resolve back to the repo root correctly
      cwd: '/home/zisi/ZiSi_Bot/presentation/dashboard/backend',
      script: 'server.js',
      interpreter: 'node',
      // Pass the venv python3 so server.js picks it up via $PATH
      env: {
        NODE_ENV: 'production',
        PORT: 5000,
        PATH: '/home/zisi/ZiSi_Bot/venv/bin:' + process.env.PATH,
      },
      // Restart if memory exceeds 1.2 GB (safety net for transformer models)
      max_memory_restart: '1200M',
      // Restart on crash, wait 5 s before retrying
      restart_delay: 5000,
      autorestart: true,
      // Keep the last 30 days of logs
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      error_file: '/home/zisi/ZiSi_Bot/logs/pm2-error.log',
      out_file:   '/home/zisi/ZiSi_Bot/logs/pm2-out.log',
      merge_logs: true,
    },
  ],
};
```

Create the logs directory:

```bash
mkdir -p /home/zisi/ZiSi_Bot/logs
```

### 5c. Start and verify

```bash
cd /home/zisi/ZiSi_Bot
pm2 start ecosystem.config.js
pm2 status
pm2 logs zisi-dashboard --lines 50
```

You should see `server.js` boot on port 5000, then the line `Spawning ZiSi Bot (python
app/main.py)` followed by Python startup logs.

### 5d. Persist across reboots

```bash
pm2 startup systemd
# PM2 prints a sudo command — run it exactly as printed, e.g.:
sudo env PATH=$PATH:/usr/bin /usr/lib/node_modules/pm2/bin/pm2 startup systemd -u zisi --hp /home/zisi

pm2 save
```

After this, `zisi-dashboard` starts automatically on every boot.

---

## 6. Log Rotation

### 6a. PM2 log rotation plugin

```bash
pm2 install pm2-logrotate

pm2 set pm2-logrotate:max_size 50M
pm2 set pm2-logrotate:retain 14
pm2 set pm2-logrotate:compress true
pm2 set pm2-logrotate:dateFormat YYYY-MM-DD_HH-mm-ss
pm2 set pm2-logrotate:rotateInterval '0 0 * * *'   # daily at midnight

pm2 save
```

### 6b. System logrotate for bot file logs

If the bot writes any additional `.log` or `.jsonl` files to the repo root, add a system
logrotate config:

```bash
sudo tee /etc/logrotate.d/zisi << 'EOF'
/home/zisi/ZiSi_Bot/*.log
/home/zisi/ZiSi_Bot/*.jsonl {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
EOF
```

Test the config:

```bash
sudo logrotate --debug /etc/logrotate.d/zisi
```

---

## 7. Nginx Reverse Proxy + SSL

### 7a. Server block

Replace `<YOUR_DOMAIN>` with your domain or DuckDNS subdomain (e.g. `zisi.duckdns.org`).
If you have no domain yet, get a free one at [duckdns.org](https://www.duckdns.org/).

```bash
sudo nano /etc/nginx/sites-available/zisi
```

Paste (HTTP-only first; certbot upgrades to HTTPS automatically):

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name <YOUR_DOMAIN>;

    # Increase buffer sizes for dashboard JSON payloads
    proxy_buffer_size          128k;
    proxy_buffers              4 256k;
    proxy_busy_buffers_size    256k;

    location / {
        # HTTP Basic Auth (added in Section 8)
        # auth_basic "ZiSi Dashboard";
        # auth_basic_user_file /etc/nginx/.htpasswd;

        proxy_pass         http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection 'upgrade';
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 120s;
    }
}
```

Enable and test:

```bash
sudo ln -s /etc/nginx/sites-available/zisi /etc/nginx/sites-enabled/zisi
sudo nginx -t
sudo systemctl reload nginx
```

### 7b. Free SSL with Let's Encrypt (certbot)

Point your domain's A record to `<VPS_IP>` first (DNS propagation can take a few minutes),
then:

```bash
sudo certbot --nginx -d <YOUR_DOMAIN>
# Follow prompts: enter email, agree to ToS, choose to redirect HTTP→HTTPS (option 2)
```

Certbot rewrites the nginx config automatically to include the SSL block and HTTP→HTTPS
redirect. Auto-renewal is pre-configured via a systemd timer:

```bash
sudo systemctl status certbot.timer   # should show active
# Test renewal dry-run:
sudo certbot renew --dry-run
```

### 7c. DuckDNS auto-update (if using DuckDNS)

Your VPS IP may change on reboot on cheaper plans. Keep the DNS record current:

```bash
crontab -e
# Add:
*/5 * * * * curl -s "https://www.duckdns.org/update?domains=<SUBDOMAIN>&token=<DUCKDNS_TOKEN>&ip=" > /dev/null 2>&1
```

---

## 8. Phone Access & HTTP Basic Auth

> **Warning:** The `/api/control/system/start`, `/api/control/system/stop`, and other control
> endpoints in `server.js` have **no server-side authentication**. Anyone who can reach port 80/443
> can start, stop, or query the bot. Add HTTP Basic Auth in nginx before sharing the URL.

### 8a. Create the password file

```bash
# Replace 'mthunzi' with your preferred username
sudo htpasswd -c /etc/nginx/.htpasswd mthunzi
# Enter and confirm a strong password at the prompts
sudo chmod 640 /etc/nginx/.htpasswd
```

### 8b. Enable auth in the nginx config

```bash
sudo nano /etc/nginx/sites-available/zisi
```

Uncomment the two `auth_basic` lines inside `location /`:

```nginx
        auth_basic "ZiSi Dashboard";
        auth_basic_user_file /etc/nginx/.htpasswd;
```

Reload nginx:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### 8c. Phone access

Navigate to `https://<YOUR_DOMAIN>` on your phone. The browser will prompt for username +
password once; most mobile browsers remember it. The glassmorphism dashboard is mobile-responsive.

---

## 9. Verification & Ops

### 9a. Confirm the bot is trading

```bash
# Live PM2 logs (Python bot output streams through Node)
pm2 logs zisi-dashboard --lines 100

# Check account_state.json freshness (should be <2 min old)
python3 -c "
import json, datetime
with open('/home/zisi/ZiSi_Bot/account_state.json') as f:
    d = json.load(f)
last = datetime.datetime.fromisoformat(d['last_updated'])
age  = (datetime.datetime.utcnow() - last).total_seconds()
print(f'last_updated: {d[\"last_updated\"]}  ({age:.0f}s ago)')
"

# Quick healthcheck via curl
curl -u mthunzi:<password> https://<YOUR_DOMAIN>/api/health
```

### 9b. Update procedure

```bash
cd /home/zisi/ZiSi_Bot

# 1. Pull latest code
git pull origin main

# 2. Install any new Python dependencies
source venv/bin/activate
pip install -r requirements.txt
pip install vaderSentiment
deactivate

# 3. Install any new Node dependencies
npm --prefix presentation/dashboard/frontend install
npm --prefix presentation/dashboard/backend install

# 4. Rebuild the React frontend
npm --prefix presentation/dashboard/frontend run build

# 5. Restart the managed process (Node restarts; it respawns Python automatically)
pm2 restart zisi-dashboard

pm2 logs zisi-dashboard --lines 50
```

### 9c. State file backups

The bot's critical runtime state lives in three files at the repo root:

| File | Contents |
|---|---|
| `account_state.json` | Balance, P&L, last heartbeat |
| `positions_state.json` | Open positions |
| `balance_history.jsonl` | Running equity curve |

Back them up daily with a cron job:

```bash
crontab -e
# Add (creates a timestamped tar in ~/backups/):
0 3 * * * mkdir -p /home/zisi/backups && tar -czf /home/zisi/backups/zisi-state-$(date +\%Y\%m\%d).tar.gz -C /home/zisi/ZiSi_Bot account_state.json positions_state.json balance_history.jsonl 2>/dev/null; find /home/zisi/backups -name 'zisi-state-*.tar.gz' -mtime +30 -delete
```

This keeps 30 days of daily state snapshots.

### 9d. PM2 cheat-sheet

```bash
pm2 status                          # process list + uptime
pm2 logs zisi-dashboard             # stream live logs
pm2 logs zisi-dashboard --lines 200 # recent history
pm2 restart zisi-dashboard          # graceful restart
pm2 stop    zisi-dashboard          # stop (won't auto-restart until pm2 start)
pm2 delete  zisi-dashboard          # remove from PM2 registry
pm2 monit                           # interactive CPU/mem dashboard
```

---

*Handbook last updated: 2026-05-29*
