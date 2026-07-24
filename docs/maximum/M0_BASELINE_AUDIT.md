# M0 BASELINE AUDIT — SORA.Earth Maximum

**Audit Date:** 2026-07-24  
**Auditor:** Claude Opus 4.5  
**Branch:** `fix/environmental-ingestion-persistence`  
**HEAD SHA:** `63395f7b301f958e01ea9b635e3e37529542832d`  
**Scope:** Phase M0 / Baseline Lock  
**Status:** 🔴 BLOCKED — Critical issues prevent PR merge

---

## 1. GIT STATE

### 1.1 Current Branch
| Property | Value | Evidence |
|----------|-------|----------|
| Branch | `fix/environmental-ingestion-persistence` | `git branch --show-current` |
| HEAD SHA | `63395f7b301f958e01ea9b635e3e37529542832d` | `git rev-parse HEAD` |
| Working tree | 5 modified files (data/models) | `git status --short` |
| Commits ahead of main | 5 | `git log main..HEAD --oneline` |
| origin/main HEAD | `9525bf0efb4c84967ee3b80564365b8d1ec37878` | `git rev-parse origin/main` |

### 1.2 Branch Commits (5)
```
63395f7 ci: apply migrations before PostgreSQL persistence tests
961624f test: isolate PostgreSQL persistence tests from app startup
e0a64e5 fix: add MLFLOW_TRACKING_URI to environmental-postgres-tests
b352568 ci: add environmental-postgres-tests job for PR validation
b351671 feat: add environmental ingestion persistence with upsert
```

### 1.3 PR Status
| Property | Value | Evidence |
|----------|-------|----------|
| PR Number | #12 | `gh pr list --head fix/environmental-ingestion-persistence` |
| State | OPEN | GitHub API |
| Mergeable | MERGEABLE | GitHub API |
| CI Status | 🔴 ALL FAILED | statusCheckRollup |

---

## 2. CI STATUS

### 2.1 Last CI Run (2026-07-18T13:13:22Z)
| Job | Status | Failure Reason |
|-----|--------|----------------|
| backend-tests | ❌ FAILURE | 32 collection errors (app import failures) |
| frontend-checks | ❌ FAILURE | Build/type errors |
| environmental-postgres-tests | ❌ FAILURE | Alembic migration fails |
| integration-tests | ⏭️ SKIPPED | Blocked by prior jobs |

### 2.2 Root Cause: Alembic Migration Failure

**Error Location:** `alembic/versions/a1b2c3d4e5f6_regional_esg_snapshot_view.py:29`

```
psycopg2.errors.UndefinedTable: relation "region_esg_scores" does not exist
LINE 11: FROM region_esg_scores;
```

**Analysis:**
- Migration `a1b2c3d4e5f6` creates VIEW `regional_esg_snapshot` that references table `region_esg_scores`
- Table `region_esg_scores` is defined in `app/database.py:194` but **NO MIGRATION CREATES IT**
- Production works only because table was created manually with SQL

**Evidence:**
- `grep -rn "region_esg_scores" alembic/versions/*.py` — No CREATE TABLE found
- `alembic/versions/cb5a035aed2f_*.py:22-25` — ALTER TABLE assumes table exists

---

## 3. TESTS

### 3.1 Local Test Results (macOS)

```bash
# Environmental persistence tests
python3 -m pytest tests/test_environmental_persist.py -v
# Result: 11 passed, 1 skipped in 0.31s
```

| Test Suite | Status | Notes |
|------------|--------|-------|
| test_environmental_persist.py | ✅ 11/12 | 1 PostgreSQL-only skipped |
| test_environmental_scheduler.py | NOT RUN | Requires full app context |
| Frontend build | ✅ PASS | `npm run build` succeeds |

### 3.2 CI Test Failures

**Blocked by:** App import failures due to broken migration graph

```
collected 233 items / 32 errors
ERROR collecting tests/test_ab.py
```

---

## 4. ALEMBIC MIGRATIONS

### 4.1 Migration Graph
```
                              ┌─ a8d9becb25de (forecast_model_metrics)
                              │
94d781c78478 → 963bec0d1503 → 0afef9ea21d5 → 31e5cc432377 → a1b2c3d4e5f6 → 0d88c3e3c633 → cb5a035aed2f ─┤
                                                                                                         │
                                                                                                         └─ f7a8b9c0d1e2 (environmental_observations)
                                                                                                         
                                                                                              013bbc52a33f (merge) → 0b0ff6d1594e (head)
```

### 4.2 Critical Dependency Issue

| Migration | Problem | Severity |
|-----------|---------|----------|
| `a1b2c3d4e5f6` | Creates VIEW on non-existent `region_esg_scores` table | CRITICAL |
| `cb5a035aed2f` | ALTER TABLE on non-existent `region_esg_scores` | CRITICAL |

### 4.3 Missing Table Creation

**Table:** `region_esg_scores`  
**Model defined at:** `app/database.py:194-207`  
**Migration to create:** MISSING

```python
class RegionESGScore(Base):
    __tablename__ = "region_esg_scores"
    id = Column(Integer, primary_key=True, autoincrement=True)
    region_code = Column(String(10), index=True, unique=True, nullable=False)
    env_score = Column(Float, nullable=True)
    social_score = Column(Float, nullable=True)
    gov_score = Column(Float, nullable=True)
    total_score = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    sources_count = Column(Integer, nullable=True)
    signals_used = Column(Integer, nullable=True)
    updated_at = Column(DateTime(timezone=True), ...)
```

---

## 5. ENVIRONMENTAL PIPELINE

### 5.1 Critical Bug: Signals Not Persisted

**Location:** `app/services/environmental/scheduler_jobs.py:131-228`

**Problem:**
- `scheduled_openaq_ingestion()` calls `ingester.fetch()` to get signals (line 161)
- Signals are used for metrics calculation (lines 164-179)
- **`persist_environmental_observations()` is NEVER called**
- Same issue in `scheduled_openmeteo_ingestion()` (lines 259, no persist call)

**Evidence:**
```bash
grep -n "persist_environmental_observations" app/services/environmental/scheduler_jobs.py
# Result: NO MATCHES
```

**vs. Correct Implementation in `app/ingesters/runner.py:102-103`:**
```python
persist_result = _persist_signals(signals, ing.name)  # Actually persists!
```

### 5.2 Production Evidence

From production server audit:
```
environmental_observations: 0 rows
environmental_job_log: 489 rows (jobs run but don't persist)
```

Scheduler logs show:
```
"OpenAQ ingestion completed: 0 signals (0 valid, 0 rejected) in 11.61s"
```

### 5.3 Data Flow Analysis

**Expected Flow:**
```
HTTP Response → Ingester.fetch() → signals[] → persist_environmental_observations() → PostgreSQL
```

**Actual Flow (scheduler_jobs.py):**
```
HTTP Response → Ingester.fetch() → signals[] → metrics update → LOST
```

**Correct Flow (runner.py):**
```
HTTP Response → Ingester.fetch() → signals[] → _persist_signals() → PostgreSQL ✓
```

---

## 6. PRODUCTION CONFIGURATION

### 6.1 Server Status
| Property | Value | Status |
|----------|-------|--------|
| Host | 45.137.60.67 | ✅ |
| Uptime | 7 days | ✅ |
| Containers | 9 running | ✅ |
| Health endpoint | 200 OK | ✅ |
| SSL valid until | Oct 3, 2026 | ✅ |

### 6.2 Git Version Mismatch
| Location | HEAD | Delta |
|----------|------|-------|
| Local | `63395f7` | - |
| origin/main | `9525bf0` | -5 commits |
| Production | `9525bf0` | -5 commits |

### 6.3 Missing Production Features
- ❌ Automated PostgreSQL backup
- ❌ Swap configured
- ❌ fail2ban installed
- ❌ Port 8000 bound to localhost only

---

## 7. SECURITY

### 7.1 Path Traversal (P0)
**File:** `app/api/retrain.py:398-404`
```python
def data_bulk_upload(file_path: str, auto_retrain: bool = False):
    if not os.path.exists(file_path):
        raise HTTPException(400, f"File not found: {file_path}")
    df_new = pd.read_csv(file_path)  # Arbitrary file read!
```
**Severity:** CRITICAL — No path validation

### 7.2 Password Hashing (P1)
**File:** `app/auth.py:72-75`
- Uses SHA-256 instead of bcrypt
- bcrypt is in requirements.txt but not used

### 7.3 Hardcoded Credentials
| File | Line | Type | Status |
|------|------|------|--------|
| `app/auth.py` | 170-171 | API keys | [REDACTED] |
| `scripts/add_historical_evaluations.py` | 19 | DB connection | [REDACTED] |

---

## 8. COMMANDS EXECUTED (Read-Only)

```bash
# Git state
git branch --show-current
git rev-parse HEAD
git status --short
git remote -v
git log --oneline -5
git log main..HEAD --oneline
git fetch origin
git rev-parse origin/main

# GitHub API
gh pr list --state all --head fix/environmental-ingestion-persistence
gh run list --limit 10
gh run view 29645753745 --log-failed

# Alembic
python3 -m alembic heads
python3 -m alembic history --verbose

# Tests (local)
python3 -m pytest tests/test_environmental_persist.py -v --tb=short

# Frontend
cd web && npm run build

# Production SSH (read-only)
ssh root@45.137.60.67 "docker compose ps"
ssh root@45.137.60.67 "psql ... SELECT * FROM environmental_observations"
```

---

## 9. CONFIRMATION

✅ **No code was modified**  
✅ **No commits were created**  
✅ **No files were written except this documentation**  
✅ **No database changes were made**  
✅ **No production deployment was performed**

---

## 10. SUMMARY

### Blocking Issues (Must Fix Before Merge)

| ID | Issue | Severity | Owner |
|----|-------|----------|-------|
| M0-001 | Missing `region_esg_scores` table migration | P0 | Backend |
| M0-002 | Signals not persisted in scheduler_jobs.py | P0 | Backend |
| M0-003 | CI failing on all jobs | P0 | DevOps |

### Non-Blocking Issues (Fix in Follow-up PRs)

| ID | Issue | Severity | Owner |
|----|-------|----------|-------|
| M0-004 | Path traversal in bulk_upload | P0 | Security |
| M0-005 | SHA-256 password hashing | P1 | Security |
| M0-006 | Missing automated backups | P1 | Ops |
| M0-007 | Production 5 commits behind | P1 | Ops |

---

**Next Action:** Create PR `fix/alembic-clean-bootstrap` to add missing `region_esg_scores` table migration and fix scheduler_jobs.py persistence.
