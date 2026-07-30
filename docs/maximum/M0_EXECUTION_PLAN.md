# M0 EXECUTION PLAN — SORA.Earth Maximum

**Created:** 2026-07-24  
**Target:** Baseline Lock before M1 Data Trust  
**Status:** Ready for Execution

---

## Objective

Close all P0 blockers so that:
1. CI passes on PR #12
2. Alembic migrations run cleanly on fresh DB
3. Environmental observations persist to PostgreSQL
4. Backup/restore drill succeeds
5. No known Critical/High security issues

---

## Sprint M0.1 — Foundation Lock

| ID | Task | Priority | Estimate | Depends On | Done When |
|----|------|----------|----------|------------|-----------|
| MAX-001 | Fix Alembic migration graph | P0 | 45 min | - | Migration creates `region_esg_scores` before VIEW |
| MAX-002 | Fix scheduler_jobs persistence | P0 | 15 min | - | `persist_environmental_observations()` called |
| MAX-003 | Verify CI green | P0 | 10 min | MAX-001, MAX-002 | All 3 jobs pass |
| MAX-004 | Path traversal fix | P0 | 30 min | - | `require_admin` + path validation |
| MAX-005 | Setup automated backup | P0 | 30 min | - | Cron job running, tested |
| MAX-006 | Deploy to production | P1 | 15 min | MAX-001-005 | Server at latest SHA |
| MAX-007 | Backup/restore drill | P0 | 30 min | MAX-005 | Fresh DB restored successfully |
| MAX-008 | Security scan | P1 | 20 min | MAX-004 | No Critical findings |
| MAX-009 | Production readiness report | P1 | 30 min | All above | Documented in `docs/maximum/` |

**Total Estimated Time:** ~4 hours

---

## PR Structure

### PR 1: `fix/alembic-clean-bootstrap`
**Scope:** MAX-001  
**Changes:**
1. Create new migration `xxxx_create_region_esg_scores_table.py` BEFORE `a1b2c3d4e5f6`
2. Update migration dependencies
3. Test with fresh SQLite + PostgreSQL

**Files:**
- `alembic/versions/xxxx_create_region_esg_scores_table.py` (new)
- Update `alembic/versions/a1b2c3d4e5f6_regional_esg_snapshot_view.py` (depends_on)

### PR 2: `fix/environmental-persistence`
**Scope:** MAX-002  
**Changes:**
1. Add `persist_environmental_observations()` call in `scheduled_openaq_ingestion()`
2. Add `persist_environmental_observations()` call in `scheduled_openmeteo_ingestion()`
3. Add integration test

**Files:**
- `app/services/environmental/scheduler_jobs.py`
- `tests/test_environmental_scheduler.py`

### PR 3: `security/path-traversal-fix`
**Scope:** MAX-004  
**Changes:**
1. Add `Depends(require_admin)` to `/model/data/bulk-upload`
2. Validate file_path within allowed directory
3. Add authentication to `/forecast/cache` and `/forecast/pretrain`

**Files:**
- `app/api/retrain.py`
- `app/api/forecast.py`
- `tests/test_retrain.py`

### PR 4: `ops/automated-backup`
**Scope:** MAX-005, MAX-007  
**Changes:**
1. Create backup script `scripts/backup_postgres.sh`
2. Add cron job configuration
3. Document restore procedure

**Files:**
- `scripts/backup_postgres.sh` (new)
- `scripts/restore_postgres.sh` (new)
- `docs/BACKUP_RESTORE.md` (new)

### PR 5: `docs/m0-evidence-pack`
**Scope:** MAX-009  
**Changes:**
1. Finalize `M0_BASELINE_AUDIT.md`
2. Update `M0_GAP_REGISTER.md` with closed items
3. Add `M0_COMPLETION_REPORT.md`

**Files:**
- `docs/maximum/M0_COMPLETION_REPORT.md` (new)
- Update existing docs

---

## Execution Sequence

```
Day 1 (Today)
│
├─ [09:00] PR 1: fix/alembic-clean-bootstrap
│  ├─ Create migration for region_esg_scores
│  ├─ Test locally with SQLite
│  ├─ Push and verify CI
│  └─ Merge when green
│
├─ [10:00] PR 2: fix/environmental-persistence
│  ├─ Add persist calls to scheduler_jobs.py
│  ├─ Add integration test
│  ├─ Push and verify CI
│  └─ Merge when green
│
├─ [11:00] PR 3: security/path-traversal-fix
│  ├─ Add authentication
│  ├─ Add path validation
│  ├─ Test and merge
│  └─ 
│
├─ [12:00] PR 4: ops/automated-backup
│  ├─ Create backup script
│  ├─ Test locally
│  └─ Deploy to production
│
├─ [13:00] MAX-006: Production Deploy
│  ├─ git pull on server
│  ├─ docker compose up -d --build
│  ├─ Verify health endpoint
│  └─ Run alembic upgrade head
│
├─ [14:00] MAX-007: Backup/Restore Drill
│  ├─ Run backup script
│  ├─ Create test database
│  ├─ Restore backup
│  ├─ Verify data integrity
│  └─ Document results
│
└─ [15:00] PR 5: docs/m0-evidence-pack
   ├─ Update gap register
   ├─ Create completion report
   └─ Tag v0.3.0-m0
```

---

## Validation Criteria

### MAX-001: Alembic Migration Fix
```bash
# Fresh database test
rm -f test.db
DATABASE_URL=sqlite:///./test.db python3 -m alembic upgrade head
# Expected: All migrations pass, no errors

# PostgreSQL test (CI)
docker compose exec postgres psql -U sora -d sora_earth -c "\dt"
# Expected: region_esg_scores table exists
```

### MAX-002: Persistence Fix
```bash
# Trigger ingestion
curl -X POST http://localhost:8000/api/v1/scheduler/trigger/openaq_ingestion

# Check observations
psql -c "SELECT COUNT(*) FROM environmental_observations"
# Expected: count > 0 after job runs
```

### MAX-003: CI Green
```bash
gh run list --limit 1 --json conclusion
# Expected: {"conclusion": "success"}
```

### MAX-007: Backup/Restore
```bash
# Create backup
./scripts/backup_postgres.sh

# Restore to test DB
createdb sora_earth_test
./scripts/restore_postgres.sh sora_earth_test backup.sql.gz

# Verify
psql sora_earth_test -c "SELECT COUNT(*) FROM country_indicator_history"
# Expected: ~80887 rows (matching production)
```

---

## Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Migration breaks production | Medium | High | Test in CI PostgreSQL first, backup before deploy |
| OpenAQ API rate limits | Low | Medium | Implement backoff, cache responses |
| CI remains red after fixes | Low | Medium | Run tests locally first, incremental fixes |
| Restore drill fails | Low | High | Test on local Docker before production |

---

## Definition of Done for M0

- [ ] Alembic `upgrade head` runs on fresh DB without errors
- [ ] CI pipeline passes all jobs (backend, frontend, postgres)
- [ ] `environmental_observations` table has data after job run
- [ ] No P0 security issues open
- [ ] Automated backup running (verified by cron output)
- [ ] Restore drill documented with evidence
- [ ] Production deployed at latest SHA
- [ ] `docs/maximum/M0_COMPLETION_REPORT.md` exists

---

## Not In Scope for M0

- CatBoost integration
- Crisis detection models
- LLM/RAG enhancements
- BRICS expansion
- UI/UX improvements
- Performance optimization

These items require stable data pipeline and are planned for M1+.

---

## Post-M0 Roadmap

| Phase | Focus | Prerequisites |
|-------|-------|---------------|
| M1 | Data Trust | M0 complete, observations persisting |
| M2 | Crisis Detection | M1 complete, 30 days of data |
| M3 | Advanced ML | M2 complete, validated alerts |
| M4 | BRICS Expansion | M3 complete, scalable architecture |

---

**Approval Required Before Execution:**
- [ ] Engineering Lead
- [ ] Security Review (for SEC changes)
- [ ] Ops Lead (for production deploy)
