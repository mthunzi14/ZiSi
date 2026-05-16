"""
email_scheduler.py - ZiSi Bot Scheduled Email Reports
Sends startup, 8-hour update, shutdown, and alert emails via Gmail SMTP.
Reads credentials from GMAIL_SENDER_EMAIL and GMAIL_APP_PASSWORD env vars.
"""

import json
import logging
import os
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

log = logging.getLogger("zisi.email_scheduler")


class EmailScheduler:
    """
    Sends scheduled and event-driven email reports.

    Credentials are read from env vars so no secrets appear in source code:
        GMAIL_SENDER_EMAIL  — the sending Gmail address
        GMAIL_APP_PASSWORD  — Gmail App Password (not account password)
        EMAIL_INTERVAL_HOURS — hours between scheduled updates (default 8)
    """

    def __init__(self):
        self.sender_email: str = os.getenv("GMAIL_SENDER_EMAIL", "")
        self.sender_password: str = os.getenv("GMAIL_APP_PASSWORD", "")
        self.recipient_email: str = os.getenv("GMAIL_SENDER_EMAIL", "")  # self-reporting
        interval_hours = float(os.getenv("EMAIL_INTERVAL_HOURS", "8"))
        self.email_interval: float = interval_hours * 3600
        self._last_email_time: Optional[float] = None
        gmail_enabled = os.getenv("GMAIL_ENABLED", "false").strip().lower() not in ("false", "0", "no", "off")
        self._enabled: bool = bool(gmail_enabled and self.sender_email and self.sender_password)

        if not self._enabled:
            log.info("EmailScheduler disabled (GMAIL_ENABLED=false) — Telegram is the sole notification channel")

    # ── Scheduling logic ─────────────────────────────────────────────────────

    def should_send_scheduled(self) -> bool:
        """Return True once per email_interval (default 8 hours)."""
        now = time.time()
        if self._last_email_time is None:
            # First call — record time but don't send immediately
            self._last_email_time = now
            return False
        if now - self._last_email_time >= self.email_interval:
            self._last_email_time = now
            return True
        return False

    # ── Public send methods ──────────────────────────────────────────────────

    def send_startup(self, account_state: dict, market_snapshot: dict) -> None:
        subject = f"ZiSi Bot Started — {self._utcnow()}"
        body = self._startup_html(account_state, market_snapshot)
        self._send(subject, body)

    def send_scheduled_update(self, metrics: dict, account_state: dict) -> None:
        subject = f"ZiSi Bot — 8-Hour Update ({self._utcnow()})"
        body = self._update_html(metrics, account_state)
        self._send(subject, body)

    def send_shutdown(self, session_summary: dict) -> None:
        subject = f"ZiSi Bot Stopped — {self._utcnow()}"
        body = self._shutdown_html(session_summary)
        self._send(subject, body)

    def send_alert(self, alert_type: str, alert_data: dict) -> None:
        subject = f"ZiSi ALERT: {alert_type} — {self._utcnow()}"
        body = self._alert_html(alert_type, alert_data)
        self._send(subject, body)

    # ── HTML builders ────────────────────────────────────────────────────────

    def _startup_html(self, account: dict, market: dict) -> str:
        return f"""<html><body style="font-family:Segoe UI,Arial;color:#333;line-height:1.6">
<h2 style="color:#2c3e50">ZiSi Bot Started</h2>
<p style="color:#7f8c8d">{self._utcnow()}</p>

<h3>Account Status</h3>
<table style="width:100%;border-collapse:collapse">
  <tr style="background:#f8f9fa">
    <td style="padding:10px;border:1px solid #ddd">Capital Available</td>
    <td style="padding:10px;border:1px solid #ddd;font-weight:bold">${account.get('balance', 100):.2f}</td>
  </tr>
  <tr>
    <td style="padding:10px;border:1px solid #ddd">Previous P&L</td>
    <td style="padding:10px;border:1px solid #ddd;font-weight:bold">${account.get('pnl', 0):.2f}</td>
  </tr>
</table>

<h3>Market Snapshot</h3>
<table style="width:100%;border-collapse:collapse">
  <tr style="background:#f8f9fa">
    <td style="padding:10px;border:1px solid #ddd">BTC Price</td>
    <td style="padding:10px;border:1px solid #ddd">${market.get('btc_price', 'N/A'):,}</td>
  </tr>
  <tr>
    <td style="padding:10px;border:1px solid #ddd">BTC 24h Change</td>
    <td style="padding:10px;border:1px solid #ddd">{market.get('btc_24h_change', 'N/A')}</td>
  </tr>
  <tr style="background:#f8f9fa">
    <td style="padding:10px;border:1px solid #ddd">ETH Price</td>
    <td style="padding:10px;border:1px solid #ddd">${market.get('eth_price', 'N/A'):,}</td>
  </tr>
</table>

<h3>Configuration</h3>
<ul>
  <li>Signal Threshold: 7/10+</li>
  <li>Position Sizing: Kelly Criterion (dynamic)</li>
  <li>Risk Per Trade: 2% max</li>
  <li>Mode: Paper Trading</li>
  <li>Next Update: In {int(self.email_interval // 3600)} hours</li>
</ul>

<p style="color:#7f8c8d;font-size:12px;margin-top:20px">ZiSi Bot automated report. Do not reply.</p>
</body></html>"""

    def _update_html(self, metrics: dict, account: dict) -> str:
        pnl = metrics.get("session_pnl", 0)
        dd = metrics.get("current_drawdown", 0)
        pnl_color = "#27ae60" if pnl >= 0 else "#e74c3c"
        return f"""<html><body style="font-family:Segoe UI,Arial;color:#333;line-height:1.6">
<h2 style="color:#2c3e50">ZiSi Bot — {int(self.email_interval // 3600)}-Hour Update</h2>
<p style="color:#7f8c8d">{self._utcnow()}</p>

<h3>Performance</h3>
<table style="width:100%;border-collapse:collapse">
  <tr style="background:#f8f9fa">
    <td style="padding:10px;border:1px solid #ddd">Balance</td>
    <td style="padding:10px;border:1px solid #ddd;font-weight:bold;color:#27ae60">${account.get('balance', 100):.2f}</td>
  </tr>
  <tr>
    <td style="padding:10px;border:1px solid #ddd">Session P&L</td>
    <td style="padding:10px;border:1px solid #ddd;font-weight:bold;color:{pnl_color}">${pnl:.2f}</td>
  </tr>
  <tr style="background:#f8f9fa">
    <td style="padding:10px;border:1px solid #ddd">Trades Executed</td>
    <td style="padding:10px;border:1px solid #ddd;font-weight:bold">{metrics.get('trades_executed', 0)}</td>
  </tr>
  <tr>
    <td style="padding:10px;border:1px solid #ddd">Win Rate</td>
    <td style="padding:10px;border:1px solid #ddd;font-weight:bold">{metrics.get('win_rate', 0):.1%}</td>
  </tr>
  <tr style="background:#f8f9fa">
    <td style="padding:10px;border:1px solid #ddd">Profit Factor</td>
    <td style="padding:10px;border:1px solid #ddd;font-weight:bold">{metrics.get('profit_factor', 0):.2f}</td>
  </tr>
  <tr>
    <td style="padding:10px;border:1px solid #ddd">Max Drawdown</td>
    <td style="padding:10px;border:1px solid #ddd;font-weight:bold;color:#e74c3c">{metrics.get('max_drawdown', 0):.2%}</td>
  </tr>
</table>

<h3>Signal Activity</h3>
<table style="width:100%;border-collapse:collapse">
  <tr style="background:#f8f9fa">
    <td style="padding:10px;border:1px solid #ddd">Signals Evaluated</td>
    <td style="padding:10px;border:1px solid #ddd">{metrics.get('signals_evaluated', 0)}</td>
  </tr>
  <tr>
    <td style="padding:10px;border:1px solid #ddd">Polymarket Matches</td>
    <td style="padding:10px;border:1px solid #ddd">{metrics.get('polymarket_matches', 0)}</td>
  </tr>
  <tr style="background:#f8f9fa">
    <td style="padding:10px;border:1px solid #ddd">Hypothetical Trades</td>
    <td style="padding:10px;border:1px solid #ddd">{metrics.get('hypothetical_trades', 0)}</td>
  </tr>
</table>

<h3>Risk Status</h3>
<div style="background:#fff3cd;padding:15px;border-radius:5px;border-left:5px solid #ffc107;margin:15px 0">
  <p><strong>Current Drawdown:</strong> {dd:.2%}</p>
  <p><strong>Consecutive Losses:</strong> {metrics.get('consecutive_losses', 0)}</p>
  <p><strong>Risk of Ruin:</strong> {metrics.get('risk_of_ruin', 'Low')}</p>
</div>

<p style="color:#7f8c8d;font-size:12px;margin-top:20px">ZiSi Bot automated report. Do not reply.</p>
</body></html>"""

    def _shutdown_html(self, summary: dict) -> str:
        pnl = summary.get("pnl", 0)
        pnl_color = "#27ae60" if pnl >= 0 else "#e74c3c"
        return f"""<html><body style="font-family:Segoe UI,Arial;color:#333;line-height:1.6">
<h2 style="color:#e74c3c">ZiSi Bot Stopped</h2>
<p style="color:#7f8c8d">{self._utcnow()}</p>

<h3>Session Summary</h3>
<table style="width:100%;border-collapse:collapse">
  <tr style="background:#f8f9fa">
    <td style="padding:10px;border:1px solid #ddd">Duration</td>
    <td style="padding:10px;border:1px solid #ddd;font-weight:bold">{summary.get('duration', 'N/A')}</td>
  </tr>
  <tr>
    <td style="padding:10px;border:1px solid #ddd">Stop Reason</td>
    <td style="padding:10px;border:1px solid #ddd;font-weight:bold">{summary.get('stop_reason', 'User shutdown')}</td>
  </tr>
  <tr style="background:#f8f9fa">
    <td style="padding:10px;border:1px solid #ddd">Session P&L</td>
    <td style="padding:10px;border:1px solid #ddd;font-weight:bold;color:{pnl_color}">${pnl:.2f}</td>
  </tr>
  <tr>
    <td style="padding:10px;border:1px solid #ddd">Trades Executed</td>
    <td style="padding:10px;border:1px solid #ddd;font-weight:bold">{summary.get('trades_executed', 0)}</td>
  </tr>
  <tr style="background:#f8f9fa">
    <td style="padding:10px;border:1px solid #ddd">Win Rate</td>
    <td style="padding:10px;border:1px solid #ddd;font-weight:bold">{summary.get('win_rate', 0):.1%}</td>
  </tr>
</table>

<h3>Key Metrics</h3>
<ul style="background:#ecf0f1;padding:20px;border-radius:5px">
  <li>Best Trade: +${summary.get('best_trade', 0):.2f}</li>
  <li>Worst Trade: -${abs(summary.get('worst_trade', 0)):.2f}</li>
  <li>Profit Factor: {summary.get('profit_factor', 0):.2f}</li>
  <li>Max Drawdown: {summary.get('max_drawdown', 0):.2%}</li>
</ul>

<p style="color:#7f8c8d;font-size:12px;margin-top:20px">ZiSi Bot automated report. Do not reply.</p>
</body></html>"""

    def _alert_html(self, alert_type: str, alert_data: dict) -> str:
        color_map = {"WARNING": "#f39c12", "ERROR": "#e74c3c", "INFO": "#3498db"}
        color = color_map.get(alert_type.split("_")[0], "#95a5a6")
        data_html = json.dumps(alert_data, indent=2, default=str).replace("\n", "<br>").replace(" ", "&nbsp;")
        return f"""<html><body style="font-family:Segoe UI,Arial;color:#333;line-height:1.6">
<h2 style="color:{color}">ZiSi ALERT: {alert_type}</h2>
<p style="color:#7f8c8d">{self._utcnow()}</p>

<div style="background:{color}22;padding:15px;border-radius:5px;border-left:5px solid {color};margin:15px 0;font-family:monospace">
{data_html}
</div>

<p>Check the dashboard and logs immediately.</p>
<p style="color:#7f8c8d;font-size:12px;margin-top:20px">ZiSi Bot automated report. Do not reply.</p>
</body></html>"""

    # ── SMTP transport ───────────────────────────────────────────────────────

    def _send(self, subject: str, html_body: str) -> None:
        if not self._enabled:
            log.debug("EmailScheduler disabled — skipping: %s", subject)
            return

        for attempt in range(1, 4):
            try:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"] = self.sender_email
                msg["To"] = self.recipient_email
                msg.attach(MIMEText(html_body, "html", "utf-8"))

                with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
                    server.login(self.sender_email, self.sender_password)
                    server.sendmail(self.sender_email, self.recipient_email, msg.as_string())

                log.info("[EMAIL] Sent: %s", subject)
                return
            except Exception as exc:
                wait = 2 ** attempt
                log.warning("[EMAIL] Attempt %d/3 failed: %s — retry in %ds", attempt, exc, wait)
                if attempt < 3:
                    time.sleep(wait)

        log.error("[EMAIL] All 3 attempts failed for: %s", subject)

    @staticmethod
    def _utcnow() -> str:
        return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
