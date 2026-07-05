# Production Deployment Checklist

## Changes Ready for Deployment

### 1. Security Fix (CRITICAL) ✅
**Commit:** 3338ff5  
**Issue:** Exposed sensitive files (.env, .git/HEAD) via SPA catch-all router  
**Fix:** Added blocking for dotfiles in `_sora_spa()` and `_spa_catchall()`  

### 2. MLflow Graceful Failure ✅
**Commit:** 7d3775d  
**Issue:** App crashes on restart when MLflow unavailable (5 retries × 9s = 45s delay)  
**Fix:** Added `SORA_OFFLINE=1` to docker-compose.yml + wrapped MLflow init in try/except  

### 3. Scheduler Status via Redis ✅
**Commits:** 5ed04de, e579192, 1367d01, 7d24e3c  
**Issue:** `/api/v1/scheduler/status` returns empty jobs (reads from wrong container)  
**Fix:** Scheduler publishes status to Redis every 60s, API reads from Redis  

### 4. Models Volume Permissions ✅
**Commit:** 568fbfc  
**Issue:** Backend/scheduler can't read models (wrong ownership)  
**Fix:** Added `user: "1000:1000"` to docker-compose.prod.yml  

## Deployment Steps

### On Production Server (45.137.60.67):

```bash
ssh root@45.137.60.67
cd /opt/sora_earth_ai_platform

# 1. Pull latest changes
git pull

# 2. Fix models directory permissions (one-time, if not done)
chown -R 1000:1000 ./models

# 3. Restart backend (includes security fix + MLflow fix)
docker compose -f docker-compose.prod.yml restart backend

# 4. Restart scheduler (includes Redis status publishing)
docker compose -f docker-compose.prod.yml restart scheduler
```

## Verification Tests

### 1. Security Fix Verification (CRITICAL)

```bash
# All should return 404:
curl -sI https://sora-earth.online/.env | grep "HTTP/"
curl -sI https://sora-earth.online/.git/HEAD | grep "HTTP/"
curl -sI https://sora-earth.online/.gitignore | grep "HTTP/"
curl -sI https://sora-earth.online/.DS_Store | grep "HTTP/"

# Should return 200:
curl -sI https://sora-earth.online/api/v1/health | grep "HTTP/"
curl -sI https://sora-earth.online/ | grep "HTTP/"
```

**Expected:**
```
HTTP/2 404    ← .env (MUST BE 404!)
HTTP/2 404    ← .git/HEAD
HTTP/2 404    ← .gitignore
HTTP/2 404    ← .DS_Store
HTTP/2 200    ← /api/v1/health
HTTP/2 200    ← /
```

### 2. MLflow Graceful Failure Verification

```bash
# Check backend logs - should NOT see MlflowException crashes:
docker compose -f docker-compose.prod.yml logs backend | grep -i mlflow | tail -10

# Should see warning instead of crash:
# "MLflow init failed: [Errno 111] Connection refused"
```

### 3. Scheduler Status Verification

```bash
# Should return jobs array with 5 jobs:
curl -s https://sora-earth.online/api/v1/scheduler/status | jq '.jobs_count'
# Expected: 5

# Should see source="scheduler_container":
curl -s https://sora-earth.online/api/v1/scheduler/status | jq '.source'
# Expected: "scheduler_container"
```

### 4. Check Scheduler Logs

```bash
# Should see "Publishing scheduler status to Redis..." every 60s:
docker compose -f docker-compose.prod.yml logs -f scheduler | grep "Publishing"
```

## Health Monitoring

After deployment, monitor for 5 minutes:

```bash
# Watch backend logs for errors:
docker compose -f docker-compose.prod.yml logs -f backend

# Watch scheduler logs:
docker compose -f docker-compose.prod.yml logs -f scheduler

# Check container status:
docker compose -f docker-compose.prod.yml ps

# All services should be "Up"
```

## Rollback (if needed)

If any issues arise:

```bash
cd /opt/sora_earth_ai_platform
git log --oneline -10  # Find commit hash before changes
git checkout <previous-hash>
docker compose -f docker-compose.prod.yml restart backend scheduler
```

**Previous stable commit:** 535f8d7 (before these changes)

## Post-Deployment

### Update CLAUDE.md

Add to Production Server section:

```markdown
### Recent Fixes Applied:
- Security: Dotfiles blocked in SPA routers (.env, .git exposure fixed)
- MLflow: Graceful failure with SORA_OFFLINE=1 (no crashes on unreachable MLflow)
- Scheduler: Status published to Redis (API reads from scheduler container)
- Permissions: Models volume owned by uid:gid 1000:1000
```

## Summary

**Total commits to deploy:** 8  
**Critical security fix:** YES ✅  
**Breaking changes:** NO  
**Requires downtime:** NO (rolling restart)  
**Estimated deployment time:** 5 minutes  

**Order of operations:**
1. git pull
2. chown models (if not done)
3. restart backend
4. restart scheduler
5. verify security fix (MUST CHECK!)
6. monitor logs for 5 min
