# M0 GAP REGISTER — SORA.Earth Maximum

**Created:** 2026-07-24  
**Last Updated:** 2026-07-24  
**Status:** Active

---

## Gap Categories

| Category | Code | Description |
|----------|------|-------------|
| Data Pipeline | DP | Data ingestion, persistence, quality |
| Database | DB | Schema, migrations, integrity |
| Security | SEC | Authentication, authorization, vulnerabilities |
| CI/CD | CI | Build, test, deployment pipelines |
| Operations | OPS | Monitoring, backup, recovery |
| Code Quality | CQ | Tests, coverage, dead code |

---

## P0 — Critical Blockers

### GAP-001: Missing `region_esg_scores` Table Migration
| Property | Value |
|----------|-------|
| Category | DB |
| Severity | P0 — BLOCKER |
| Status | IMPLEMENTED / CI VERIFICATION PENDING |
| Evidence | `alembic/versions/a1b2c3d4e5f6_regional_esg_snapshot_view.py:29` fails with `UndefinedTable` |
| Root Cause | Model `RegionESGScore` defined at `app/database.py:194` but no migration creates the table |
| Impact | CI fails, fresh DB cannot be provisioned, Alembic upgrade fails |
| Fix | Created migration `2e8b4493b24b` before `a1b2c3d4e5f6` that creates `region_esg_scores` table |
| Implementation | `alembic/versions/2e8b4493b24b_create_region_esg_scores_table.py` |
| Verification | PostgreSQL round-trip test: `scripts/test_alembic_bootstrap_gap001.sh` |
| Effort | 2 hours (implementation + test development + evidence) |
| Owner | TBD |

### GAP-002: Scheduler Jobs Don't Persist Signals
| Property | Value |
|----------|-------|
| Category | DP |
| Severity | P0 — BLOCKER |
| Status | OPEN |
| Evidence | `app/services/environmental/scheduler_jobs.py:131-228` — no call to `persist_environmental_observations()` |
| Root Cause | `scheduled_openaq_ingestion()` and `scheduled_openmeteo_ingestion()` fetch signals but discard them |
| Impact | `environmental_observations` table remains empty, no data for crisis detection |
| Fix | Add `persist_environmental_observations(signals, source)` call after fetch |
| Effort | 15 min |
| Owner | TBD |

### GAP-003: CI Pipeline Failing
| Property | Value |
|----------|-------|
| Category | CI |
| Severity | P0 — BLOCKER |
| Status | OPEN |
| Evidence | `gh run view 29645753745 --log-failed` — all 3 jobs failed |
| Root Cause | Cascading from GAP-001 (Alembic failure) |
| Impact | Cannot merge PR, no automated testing |
| Fix | Resolve GAP-001 first |
| Effort | Depends on GAP-001 |
| Owner | TBD |

---

## P0 — Critical Security (Non-Blocking)

### GAP-004: Path Traversal in Bulk Upload
| Property | Value |
|----------|-------|
| Category | SEC |
| Severity | P0 — CRITICAL |
| Status | OPEN |
| Evidence | `app/api/retrain.py:398-404` — `file_path` parameter used without validation |
| Root Cause | No path sanitization before `pd.read_csv(file_path)` |
| Impact | Arbitrary file read on server |
| Fix | Add `Depends(require_admin)`, validate path is within allowed directory |
| Effort | 30 min |
| Owner | TBD |

### GAP-005: Unauthenticated Sensitive Endpoints
| Property | Value |
|----------|-------|
| Category | SEC |
| Severity | P0 — CRITICAL |
| Status | OPEN |
| Evidence | `app/api/retrain.py:398` (`/model/data/bulk-upload`), `app/api/forecast.py:175,188` |
| Root Cause | Missing `Depends(require_admin)` |
| Impact | Data injection, resource exhaustion |
| Fix | Add admin authentication to endpoints |
| Effort | 15 min |
| Owner | TBD |

---

## P1 — High Priority

### GAP-006: Weak Password Hashing
| Property | Value |
|----------|-------|
| Category | SEC |
| Severity | P1 — HIGH |
| Status | OPEN |
| Evidence | `app/auth.py:72-75` — uses `hashlib.sha256` instead of bcrypt |
| Root Cause | bcrypt in requirements.txt but not used |
| Impact | Passwords vulnerable to GPU brute-force attacks |
| Fix | Migrate to `bcrypt.hashpw()` |
| Effort | 1 hour (including migration) |
| Owner | TBD |

### GAP-007: No Automated Database Backup
| Property | Value |
|----------|-------|
| Category | OPS |
| Severity | P1 — HIGH |
| Status | OPEN |
| Evidence | `backups/` contains only manual backup from Jun 14 |
| Root Cause | No cron job or systemd timer for pg_dump |
| Impact | Data loss risk on failure |
| Fix | Add cron job for daily pg_dump with 7-day retention |
| Effort | 30 min |
| Owner | TBD |

### GAP-008: Production Server Behind
| Property | Value |
|----------|-------|
| Category | OPS |
| Severity | P1 — HIGH |
| Status | OPEN |
| Evidence | Production at `9525bf0`, local at `63395f7` (5 commits behind) |
| Root Cause | Manual deployment process |
| Impact | Production missing environmental persistence fixes |
| Fix | Deploy after PR merge |
| Effort | 15 min |
| Owner | TBD |

### GAP-009: In-Memory Refresh Token Storage
| Property | Value |
|----------|-------|
| Category | SEC |
| Severity | P1 — HIGH |
| Status | OPEN |
| Evidence | `app/auth.py:118` — `_refresh_tokens: set = set()` |
| Root Cause | Tokens stored in Python process memory |
| Impact | Tokens lost on restart, not distributed |
| Fix | Store refresh tokens in Redis with TTL |
| Effort | 2 hours |
| Owner | TBD |

### GAP-010: 0% Test Coverage for Critical Modules
| Property | Value |
|----------|-------|
| Category | CQ |
| Severity | P1 — HIGH |
| Status | OPEN |
| Evidence | `app/shap_explainer.py` (0%), `app/training.py` (0%) |
| Root Cause | No tests written |
| Impact | Regressions undetected |
| Fix | Add unit tests for training pipeline |
| Effort | 4 hours |
| Owner | TBD |

### GAP-017: ORM/Production Schema Drift (region_esg_scores PRIMARY KEY)
| Property | Value |
|----------|-------|
| Category | DB |
| Severity | P1 — HIGH |
| Status | OPEN |
| Evidence | Production has `PRIMARY KEY (region_code)`, ORM expects `PRIMARY KEY (id)` |
| Root Cause | Origin unknown; production schema and ORM metadata diverge |
| Impact | Potential ORM identity-map, merge, refresh, delete, and write-correctness risks |
| Discovered | GAP-001 investigation (2026-07-24) |
| Analysis Required | Audit all `RegionESGScore` query patterns, check `Session.get()` usage, relationship/FK impact |
| Fix Options | (1) Update ORM to match production (region_code PK), OR (2) Migrate production to id PK |
| Recommendation | Decision pending full impact analysis |
| Effort | 3 hours (audit + ORM change + tests) |
| Owner | TBD |
| Evidence Doc | `docs/maximum/evidence/M0_REGION_ESG_SCHEMA.md` |

---

## P2 — Medium Priority

### GAP-011: X-Frame-Options ALLOWALL on Embed
| Property | Value |
|----------|-------|
| Category | SEC |
| Severity | P2 — MEDIUM |
| Status | OPEN |
| Evidence | `app/api/embed/api.py:10` |
| Fix | Restrict to known origins or remove |

### GAP-012: Port 8000 Exposed to Internet
| Property | Value |
|----------|-------|
| Category | OPS |
| Severity | P2 — MEDIUM |
| Status | OPEN |
| Evidence | Production `ss -tlnp` shows `0.0.0.0:8000` |
| Fix | Bind to `127.0.0.1:8000` in docker-compose.prod.yml |

### GAP-013: No Swap Configured
| Property | Value |
|----------|-------|
| Category | OPS |
| Severity | P2 — MEDIUM |
| Status | OPEN |
| Evidence | `free -h` shows 0B swap, 62% RAM used |
| Fix | Add 2GB swapfile |

### GAP-014: fail2ban Not Installed
| Property | Value |
|----------|-------|
| Category | SEC |
| Severity | P2 — MEDIUM |
| Status | OPEN |
| Evidence | `fail2ban-client status sshd` returns "not installed" |
| Fix | `apt install fail2ban` |

### GAP-015: Backup Files in Repository
| Property | Value |
|----------|-------|
| Category | CQ |
| Severity | P2 — MEDIUM |
| Status | OPEN |
| Evidence | 30+ `.bak` files found via `find -name "*.bak*"` |
| Fix | Clean up or add to .gitignore |

### GAP-016: Docker Build Cache 37GB
| Property | Value |
|----------|-------|
| Category | OPS |
| Severity | P2 — MEDIUM |
| Status | OPEN |
| Evidence | `docker system df` on production |
| Fix | `docker system prune -a` |

---

## Summary

| Priority | Open | In Progress | Closed | Total |
|----------|------|-------------|--------|-------|
| P0 Blocker | 2 | 1 | 0 | 3 |
| P0 Security | 2 | 0 | 0 | 2 |
| P1 | 6 | 0 | 0 | 6 |
| P2 | 6 | 0 | 0 | 6 |
| **Total** | **16** | **1** | **0** | **17** |

---

## Resolution Order

1. **GAP-001** — Migration fix (unlocks CI)
2. **GAP-002** — Persistence fix (unlocks data pipeline)
3. **GAP-003** — CI green (automatic after 001+002)
4. **GAP-004, GAP-005** — Security hardening
5. **GAP-007** — Backup setup
6. **GAP-008** — Production deploy
7. Remaining P1/P2 in follow-up sprints
