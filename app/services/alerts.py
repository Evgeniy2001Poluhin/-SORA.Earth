"""SORA.Earth alerts dispatcher."""
import os, json, logging, smtplib
from datetime import datetime
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)
LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs", "drift_alerts.jsonl")
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

def _log_jsonl(payload):
    rec = {"ts": datetime.utcnow().isoformat() + "Z", **payload}
    try:
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:
        logger.warning(f"jsonl log failed: {e}")

def send_slack_alert(message, severity="warning", title=None):
    url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    color = {"info": "#36a64f", "warning": "#ff9900", "critical": "#d50000"}.get(severity, "#cccccc")
    payload = {"channel": "slack", "severity": severity, "title": title or "SORA.Earth Alert", "message": message}
    if not url:
        _log_jsonl({"channel": "slack_fallback", **payload})
        return {"ok": False, "channel": "slack", "fallback": "jsonl", "reason": "SLACK_WEBHOOK_URL not set"}
    try:
        import requests
        body = {"attachments": [{"color": color, "title": payload["title"], "text": message, "fields": [{"title": "Severity", "value": severity.upper(), "short": True}], "ts": int(datetime.utcnow().timestamp())}]}
        r = requests.post(url, json=body, timeout=5)
        ok = 200 <= r.status_code < 300
        _log_jsonl({"channel": "slack", "ok": ok, "status": r.status_code, **payload})
        return {"ok": ok, "channel": "slack", "status": r.status_code}
    except Exception as e:
        _log_jsonl({"channel": "slack_error", "error": str(e), **payload})
        return {"ok": False, "channel": "slack", "error": str(e)}

def send_telegram_alert(message, severity="warning", title=None):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    payload = {"channel": "telegram", "severity": severity, "title": title or "SORA.Earth Alert", "message": message}
    if not (token and chat):
        _log_jsonl({"channel": "telegram_fallback", **payload})
        return {"ok": False, "channel": "telegram", "fallback": "jsonl", "reason": "TELEGRAM_BOT_TOKEN/CHAT_ID not set"}
    try:
        import requests
        emoji = {"info": "INFO", "warning": "WARN", "critical": "CRITICAL"}.get(severity, "ALERT")
        text = f"[{emoji}] {payload['title']}\n{message}"
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": text}, timeout=5)
        ok = 200 <= r.status_code < 300
        _log_jsonl({"channel": "telegram", "ok": ok, "status": r.status_code, **payload})
        return {"ok": ok, "channel": "telegram", "status": r.status_code}
    except Exception as e:
        _log_jsonl({"channel": "telegram_error", "error": str(e), **payload})
        return {"ok": False, "channel": "telegram", "error": str(e)}

def send_email_alert(subject, message, severity="warning"):
    to_addr = os.getenv("ALERT_EMAIL_TO", "").strip()
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASSWORD", "").strip()
    payload = {"channel": "email", "severity": severity, "subject": subject, "message": message, "to": to_addr}
    if not (to_addr and smtp_host and smtp_user and smtp_pass):
        _log_jsonl({"channel": "email_fallback", **payload})
        return {"ok": False, "channel": "email", "fallback": "jsonl", "reason": "SMTP not fully configured"}
    try:
        msg = MIMEText(message)
        msg["Subject"] = f"[{severity.upper()}] {subject}"
        msg["From"] = smtp_user
        msg["To"] = to_addr
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.send_message(msg)
        _log_jsonl({"ok": True, **payload})
        return {"ok": True, "channel": "email"}
    except Exception as e:
        _log_jsonl({"channel": "email_error", "error": str(e), **payload})
        return {"ok": False, "channel": "email", "error": str(e)}

def send_alert(message, severity="warning", title=None, channels=None):
    channels = channels or ["slack", "telegram", "email"]
    results = {}
    if "slack" in channels:
        results["slack"] = send_slack_alert(message, severity, title)
    if "telegram" in channels:
        results["telegram"] = send_telegram_alert(message, severity, title)
    if "email" in channels:
        results["email"] = send_email_alert(title or "SORA.Earth Alert", message, severity)
    return {"severity": severity, "results": results}
