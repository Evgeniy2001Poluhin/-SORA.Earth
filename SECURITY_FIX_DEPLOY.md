# Security Fix Deployment Instructions

## Critical Security Vulnerability Fixed

**Issue:** SPA catch-all routers in `app/main.py` were exposing sensitive files:
- `/.env` → 200 OK (exposed environment variables)
- `/.git/HEAD` → 200 OK (exposed git metadata)
- `/.gitignore`, `/.DS_Store` → 200 OK (exposed project structure)

**Fix:** Added security checks in two functions:
1. `_sora_spa()` (line ~662)
2. `_spa_catchall()` (line ~693)

Both now block:
- Paths starting with `.` (dotfiles)
- Paths containing `/.env`
- Exact match `.env`
- Paths containing `.git`

## Deployment to Production

### 1. On production server (45.137.60.67):

```bash
ssh root@45.137.60.67
cd /opt/sora_earth_ai_platform
git pull
```

### 2. Restart backend container:

```bash
docker compose -f docker-compose.prod.yml restart backend
```

**Note:** Only `backend` needs restart (not `scheduler`), as the fix is in app/main.py (FastAPI routes).

### 3. Verify the fix:

```bash
# Should return 404 Not Found:
curl -sI https://sora-earth.online/.env | grep "HTTP/"
curl -sI https://sora-earth.online/.git/HEAD | grep "HTTP/"
curl -sI https://sora-earth.online/.gitignore | grep "HTTP/"

# Should return 200 OK (normal paths):
curl -sI https://sora-earth.online/ | grep "HTTP/"
curl -sI https://sora-earth.online/api/v1/health | grep "HTTP/"
```

**Expected output:**
```
HTTP/2 404    # /.env
HTTP/2 404    # /.git/HEAD
HTTP/2 404    # /.gitignore
HTTP/2 200    # /
HTTP/2 200    # /api/v1/health
```

### 4. Monitor logs for errors:

```bash
docker compose -f docker-compose.prod.yml logs -f backend | head -50
```

If no errors appear within 30 seconds, the deployment is successful.

## Rollback (if needed)

If issues arise:

```bash
cd /opt/sora_earth_ai_platform
git log --oneline -5  # Find previous commit hash
git checkout <previous-commit-hash>
docker compose -f docker-compose.prod.yml restart backend
```

## Security Impact

**Before fix:**
- Attackers could access `.env` and read sensitive credentials (DATABASE_URL, JWT_SECRET, API keys)
- Git metadata exposure (`.git/HEAD`, `.git/config`) could reveal project structure
- Risk: **CRITICAL** (CWE-200: Exposure of Sensitive Information)

**After fix:**
- All dotfiles return 404 Not Found
- Environment variables protected
- Git metadata protected
- Risk: **MITIGATED**

## Commit Info

- **Commit:** 3338ff5
- **Message:** "security: block .env, .git and dotfiles in SPA routers (prevent sensitive file exposure)"
- **Files changed:** app/main.py (+5, -1)
- **Date:** 2026-07-06
