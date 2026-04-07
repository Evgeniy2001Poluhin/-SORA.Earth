"""Audit logging for SORA.Earth"""
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("sora.audit")
_audit_log: deque = deque(maxlen=10000)

def record_audit(user: Optional[str], action: str, endpoint: str, method: str = "GET",
                 ip: str = "unknown", details: Optional[str] = None, status_code: int = 200):
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "user": user or "anonymous",
             "action": action, "endpoint": endpoint, "method": method, "ip": ip,
             "details": details, "status_code": status_code}
    _audit_log.appendleft(entry)
    logger.info(f"AUDIT: {entry['user']} {method} {endpoint} -> {status_code}")
    return entry

def get_audit_log(limit: int = 100, user: Optional[str] = None) -> list:
    logs = list(_audit_log)
    if user:
        logs = [e for e in logs if e["user"] == user]
    return logs[:limit]
