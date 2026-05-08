# Incident: HEAD Middleware Content-Length Mismatch

**Date:** 2026-05-08
**Severity:** P2 — intermittent production failures
**User-facing downtime:** ~0 min (degraded UX via failed fetches)

## TL;DR
`head_to_get` middleware in `app/main.py` forcibly set `Content-Length: 0`
on HEAD responses while the underlying GET body_iterator still held data.
Uvicorn detected the mismatch and raised `RuntimeError: Response content
longer than Content- dropping the connection. Cloudflare edge
HEAD probes hit this path repeatedly, causing intermittent client
timeouts observed as "Сетевое соединение потеряно" in Safari.

## Timeline (MSK, 2026-05-08)
| Time  | Event |
|-------|-------|
| 03:45 | Red console errors on sora-earth.ru |
| 04:06 | All 7 containers verified healthy |
| 05:10 | curl from Mac → HTTP 200; browser still fails |
| 05:15 | POST /evaluate sometimes 000 (timeout 10s) |
| 05:24 | Logs reveal `RuntimeError: Response content longer than Content-Length` |
| 05:27 | Root cause: `app/main.py:116` forcing content-length=0 on HEAD |
| 05:35 | Fix applied, deploy pending SSH access |

## Root Cause
```python
# BROKEN
response.headers["content-length"] = "0"   # lies about body size
```

## Fix
Return empty `Response` and strip length headers; let uvicorn compute.

## Action Items
- [x] Fix `head_to_get` middleware (app/main.py)
- [x] Null guard in DriftPage.tsx
- [x] Post-mortem documented
- [ ] Deploy backend (pendingo-cache + timeouts + keepalive (pending SSH)
- [ ] Uptime Kuma install
- [ ] Document SSH port 2222 in README

## Lessons
1. Never set Content-Length manually on streaming responses.
2. TypeScript `!` is compile-time only — always runtime-check.
3. HEAD probes matter (Cloudflare health checks).
4. Intermittent 000 = server protocol violation, not network.
