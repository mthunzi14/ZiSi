"""
backup_and_rotate_logs.py - Local Archive and VPS Log Rotation Tool for ZiSi.

This script runs locally on your Windows machine. It connects via the SSH tunnel (http://localhost:9090)
to download your full trade history and console logs, saves them safely in your local archive directory,
and then triggers the VPS API to clear the remote logs, ensuring your VPS stays clean and light.

Usage:
    python tools/backup_and_rotate_logs.py
"""

import os
import sys
import json
import datetime
import requests

API_URL = "http://localhost:9090"
LOCAL_ARCHIVE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "archive")
LOCAL_LOGS_FILE = os.path.join(LOCAL_ARCHIVE_DIR, "local_vps_console_history.log")
LOCAL_TRADES_FILE = os.path.join(LOCAL_ARCHIVE_DIR, "local_vps_trades_archive.json")

def initialize_directories():
    os.makedirs(LOCAL_ARCHIVE_DIR, exist_ok=True)

def pull_and_archive_trades():
    print("Fetching trade history from VPS...")
    try:
        r = requests.get(f"{API_URL}/api/positions", timeout=10)
        if r.status_code != 200:
            print(f"[ERROR] Failed to fetch positions: {r.status_code} - {r.text}")
            return False
        
        vps_data = r.json()
        closed_trades = vps_data.get("closed", [])
        active_trades = vps_data.get("active", [])
        print(f"Downloaded {len(closed_trades)} closed and {len(active_trades)} active trades.")

        # Read existing local archive
        local_archive = {}
        if os.path.exists(LOCAL_TRADES_FILE):
            try:
                with open(LOCAL_TRADES_FILE, "r", encoding="utf-8") as f:
                    local_archive = json.load(f)
            except Exception as e:
                print(f"[WARNING] Local trades archive exists but failed to parse ({e}). Starting fresh.")
        
        # Merge closed trades by order_id to prevent duplicates
        archived_closed = local_archive.get("closed", [])
        archived_ids = {t["order_id"] for t in archived_closed if "order_id" in t}
        
        new_count = 0
        for trade in closed_trades:
            if "order_id" in trade and trade["order_id"] not in archived_ids:
                archived_closed.append(trade)
                archived_ids.add(trade["order_id"])
                new_count += 1
        
        # Sort chronologically by exit time
        archived_closed.sort(key=lambda x: x.get("exit_time", ""))
        
        # Update archive dict
        local_archive["last_updated"] = datetime.datetime.now().isoformat()
        local_archive["closed"] = archived_closed
        local_archive["active"] = active_trades # overwrite active list with current state
        local_archive["summary"] = {
            "total_archived_closed": len(archived_closed),
            "realized_pnl": sum(t.get("realized_pnl", 0.0) for t in archived_closed),
            "win_count": sum(1 for t in archived_closed if t.get("realized_pnl", 0.0) > 0.0),
            "loss_count": sum(1 for t in archived_closed if t.get("realized_pnl", 0.0) < 0.0)
        }

        # Write merged archive back to disk
        with open(LOCAL_TRADES_FILE, "w", encoding="utf-8") as f:
            json.dump(local_archive, f, indent=2, default=str)
        
        print(f"[OK] Archived {new_count} new closed trades (Total local closed: {len(archived_closed)}).")
        print(f"     Realized PnL of local archive: ${local_archive['summary']['realized_pnl']:.2f}")
        return True
    except Exception as e:
        print("[ERROR] Failed to archive trades:", e)
        return False

def pull_and_archive_logs():
    print("\nFetching console logs from VPS...")
    try:
        # Request maximum lines available (up to 500)
        r = requests.get(f"{API_URL}/api/bot-logs", params={"lines": 500}, timeout=10)
        if r.status_code != 200:
            print(f"[ERROR] Failed to fetch logs: {r.status_code} - {r.text}")
            return False
        
        data = r.json()
        lines = data.get("lines", [])
        if not lines:
            print("[INFO] No log lines fetched from VPS.")
            return True
        
        # Append lines to the local history file with UTF-8 encoding
        with open(LOCAL_LOGS_FILE, "a", encoding="utf-8") as f:
            timestamp_header = f"\n--- BACKUP LOG SNAPSHOT: {datetime.datetime.now().isoformat()} ---\n"
            f.write(timestamp_header)
            for line in lines:
                f.write(line.strip() + "\n")
        
        print(f"[OK] Appended {len(lines)} log lines to local history: {LOCAL_LOGS_FILE}")
        return True
    except Exception as e:
        print("[ERROR] Failed to archive logs:", e)
        return False

def clear_vps_logs():
    print("\nTriggering log truncation on VPS...")
    try:
        r = requests.post(f"{API_URL}/api/bot-logs/clear", timeout=10)
        if r.status_code == 200:
            print("[OK] VPS logs successfully truncated to 0 bytes.")
            print("Cleared paths:", r.json().get("cleared", []))
            return True
        else:
            print(f"[ERROR] VPS failed to clear logs: {r.status_code} - {r.text}")
            return False
    except Exception as e:
        print("[ERROR] Failed to clear VPS logs:", e)
        return False

def main():
    print("======================================================================")
    print("ZiSi VPS BACKUP & ROTATE UTILITY")
    print("======================================================================")
    
    # Test connection to the API
    try:
        r = requests.get(f"{API_URL}/api/control/status", timeout=5)
        if r.status_code != 200:
            raise Exception("API status unhealthy")
    except Exception as e:
        print("[CRITICAL] Could not connect to the VPS dashboard API.")
        print("Please verify that:")
        print("  1. The bot dashboard is running on the VPS.")
        print("  2. Your SSH tunnel is active on port 9090 (ssh -L 9090:localhost:5000 root@204.168.222.48).")
        sys.exit(1)

    initialize_directories()
    
    trades_ok = pull_and_archive_trades()
    logs_ok = pull_and_archive_logs()
    
    if trades_ok and logs_ok:
        clear_vps_logs()
    else:
        print("\n[WARNING] VPS log truncation aborted because some backups failed.")
        
    print("\n======================================================================")
    print("BACKUP PROCESS COMPLETED")
    print("======================================================================")

if __name__ == "__main__":
    main()
