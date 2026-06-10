# Claude Code Handover Guide: VPS Command Execution & Clean Slate Reset

Dear Claude,

This guide outlines exactly how the ZiSi Bot's VPS deployment, health checks, logging, and clean slate resets are executed from our local environment. Since you do not have direct SSH terminal access, you must execute all remote VPS operations using the local SSH tunnel port-forwarding mechanism.

---

## 1. Core Architecture & Connection Channel

The VPS runs the `zisi-dashboard` daemon on port `5000` via PM2. 
To communicate with it, the user keeps an SSH tunnel active on local port **`9090`**:
```bash
ssh -L 9090:localhost:5000 root@204.168.222.48
```

> [!IMPORTANT]
> - **Primary Endpoint**: All API requests and remote commands must be sent to `http://127.0.0.1:9090` locally.
> - **Authentication**: Control endpoints require authentication. Use the default API key `4444` if prompted or pass it in parameters, though the local control endpoints do not enforce it for command executions via loopback.

---

## 2. How to Execute Commands on the VPS

To run a shell command on the VPS, you must send a POST request to `http://127.0.0.1:9090/api/control/exec` with a JSON payload containing `{"command": "<your_command>"}`.

Here is the exact Python pattern to run VPS commands. You can create a scratch script or execute this directly:

```python
import urllib.request
import json
import sys

def run_vps_cmd(cmd: str):
    url = "http://127.0.0.1:9090/api/control/exec"
    headers = {"Content-Type": "application/json"}
    payload = {"command": cmd}
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            if res.get("status") == "success":
                print(res.get("stdout", ""))
                if res.get("stderr"):
                    print("STDERR:", res.get("stderr"), file=sys.stderr)
            else:
                print("Error:", res.get("error"))
    except Exception as e:
        print(f"Request failed: {e}")

# Example: Check PM2 status
run_vps_cmd("pm2 status")
```

---

## 3. How to Inspect Live PM2 Logs

To check the bot's health or scan logs, query PM2 logs on the VPS:
- **Tailing Logs**:
  ```python
  run_vps_cmd("pm2 logs zisi-dashboard --lines 100 --no-color")
  ```
- **Live Output Stream**:
  The bot logs to standard output and writes files under `/root/ZiSi`. You can view the live console log file:
  ```python
  run_vps_cmd("tail -n 100 /root/ZiSi/zisi_bot_console.log")
  ```

---

## 4. How to Execute a Clean Slate Reset

A "Clean Slate" reset archives the current session, deletes local trade database logs, and resets the paper trading balance (usually to `$50.00` or `$100.00`).

### ⚠️ Pre-Conditions (CRITICAL)
1. **No Active Trades**: You **MUST** ensure there are no active positions before resetting. Cleaning slate with open positions will cause the bot to lose track of real exposure, resulting in unmanaged financial risk.
   - Check active trades by querying `http://localhost:9090/api/positions`.
   - Verify that `active_count` is `0` (or `len(active)` is `0`).
2. **SSH Tunnel Open**: If the request to `127.0.0.1:9090` fails with a connection error, the SSH tunnel is closed. Tell the user to open it.

### Reset Execution Method A: The HTTP Endpoint (Recommended)
The dashboard exposes an automated reset API `/api/control/reset`. When triggered, it automatically:
1. Stops the running bot engine.
2. Runs `clean_slate.py` with archiving and nuking options.
3. Restarts the bot engine on the new slate.

Send a POST request to `http://127.0.0.1:9090/api/control/reset` with JSON payload `{"balance": 50}`:

```python
import urllib.request
import json

def clean_slate_api(starting_balance: float = 50.0):
    url = "http://127.0.0.1:9090/api/control/reset"
    headers = {"Content-Type": "application/json"}
    payload = {"balance": starting_balance}
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            print("Reset Response:", res)
    except Exception as e:
        # Note: A socket disconnect/timeout is normal here since the server restarts PM2
        print(f"Request status (disconnect expected on success): {e}")
```

### Reset Execution Method B: Manual Command Execution
If the HTTP endpoint fails or you need to run it manually:
```python
# 1. Stop bot
run_vps_cmd("pm2 stop zisi-dashboard")

# 2. Execute clean slate script
run_vps_cmd("python3 miscellaneous/clean_slate.py --archive --force --balance 50 --nuke")

# 3. Start bot
run_vps_cmd("pm2 start zisi-dashboard")
```

---

## 5. Troubleshooting & Failsafes

- **Connection Refused / Timeout**: The SSH tunnel is not open. Stop and ask the user:
  > Please make sure the SSH tunnel is active on port 9090:
  > `ssh -L 9090:localhost:5000 root@204.168.222.48`
- **Expected Socket Disconnects**: When you run `pm2 restart` or hit the `/reset` API, the dashboard server restarts. The connection will drop immediately. Do not treat this as a failure; wait 5-10 seconds and check `pm2 status` to confirm it is up.
- **Double Check Price Cache**: The bot writes `chainlink_prices.json` and `pyth_prices.json` in the root `/root/ZiSi`. Ensure both files exist and are regularly updated (size > 0 bytes).
