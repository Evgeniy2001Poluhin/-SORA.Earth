# M0 GAP REGISTER — SORA.Earth Maximum

**Created:** 2026-07-24  
**Last Updated:** 2026-07-30  
**Status:** Active

## Standing on 2026-07-30

Every entry below was re-checked against `main@dff724f` rather than against the
pull requests that were supposed to close it. Each carries a `Verified` row with
what was actually looked at, so a reader can disagree with the conclusion without
re-deriving it.

| | count | |
|---|---|---|
| CLOSED | 9 | verified against the merged code or a green CI job |
| PARTIAL | 2 | GAP-007 backups, GAP-010 coverage — see each for what is missing |
| OPEN | 3 | GAP-008 production behind, GAP-009 refresh tokens, GAP-011 embed header |
| UNVERIFIED | 3 | GAP-013, -014, -016 — host-side, and no production access was taken |

Where an entry is no longer `OPEN`, its original `Evidence`, `Root Cause`,
`Impact` and `Fix` rows are dated rather than deleted. They describe the world as
it was on 2026-07-24 and would otherwise read as current claims sitting directly
under a `CLOSED` verdict — GAP-002 saying signals are discarded, GAP-006 saying
SHA-256 is in use. The original observation is worth keeping; it must not
contradict the line above it.

Before this pass all seventeen read `OPEN` (GAP-001 read `REASSIGNED TO PR #23`),
including nine that merged work had already closed. A register that says
everything is open is as uninformative as one that says everything is done, and
it made the remaining M0 work look larger than it is.

The M0 Definition of Done in `M0_EXECUTION_PLAN.md` is a separate list and is
**not** met: three of its eight criteria hold. Backups are not scheduled by
anything, production is not deployed at the current SHA, no P0 security issue may
remain open while GAP-011 does, and `M0_COMPLETION_REPORT.md` does not exist.

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
| Status | CLOSED |
| Verified 2026-07-30 | `migration-bootstrap` is green on `main@dff724f`: a clean PostgreSQL 16 reaches head from migrations alone, plus the catch-up scenario against a database already recorded at `0b0ff6d1594e`. |
| Evidence (as raised 2026-07-24) | `alembic/versions/a1b2c3d4e5f6_regional_esg_snapshot_view.py:29` fails with `UndefinedTable` |
| Root Cause (as raised) | Model `RegionESGScore` defined at `app/database.py:194` but no migration creates the table |
| Impact (as raised) | CI fails, fresh DB cannot be provisioned, Alembic upgrade fails |
| Fix (as proposed) | **Reassigned to PR #23.** This PR no longer carries a migration for GAP-001 — see note below |
| Implementation | `alembic/versions/b7c1e4a92f30_create_region_esg_scores.py` (PR #23) |
| Verification | *Superseded — describes the removed migration, kept as history.* PostgreSQL round-trip test `scripts/test_alembic_bootstrap_gap001.sh` (since removed) executed against real PostgreSQL 16 in CI and **PASSED**. PR [#12](https://github.com/Evgeniy2001Poluhin/-SORA.Earth/pull/12), workflow run [30123691206](https://github.com/Evgeniy2001Poluhin/-SORA.Earth/actions/runs/30123691206), job `environmental-postgres-tests` (job ID `89582031242`), step `Run GAP-001 Alembic bootstrap round-trip test`. Verified stages: fresh upgrade → schema assertions → downgrade to `31e5cc432377` → re-upgrade → idempotent upgrade → single-head/current-revision checks. Final Alembic head: `0b0ff6d1594e`. This step passed; the `environmental-postgres-tests` job and the overall PR run remained FAILED due to a later, unrelated step (`Run environmental persistence tests against PostgreSQL`) and other independent checks (`backend-tests`, `frontend-checks`) — full details in `docs/maximum/evidence/M0_REGION_ESG_SCHEMA.md`. Current verification for GAP-001 lives in PR #23 (`migration-bootstrap`, 38 convergence scenarios). |
| Verified | 2026-07-24 UTC / 2026-07-25 UTC+04 |
| Effort | 2 hours (implementation + test development + evidence) |
| Owner | TBD |


> **Reassignment note (2026-07-28).** GAP-001 was fixed independently in PR #23
> with migration `b7c1e4a92f30`, inserted at the same point in the chain. Two
> migrations creating one table cannot coexist: whichever one loses the
> `down_revision` conflict is left with no descendant and becomes a second
> head, after which `alembic upgrade head` refuses to run at all. `2e8b4493b24b`
> and `scripts/test_alembic_bootstrap_gap001.sh` were therefore removed from
> this PR, and it no longer modifies the Alembic chain.
>
> `b7c1e4a92f30` was kept because it converges onto `RegionESGScore` rather
> than freezing the divergence, is idempotent on a database where the table
> already exists, and refuses a destructive downgrade over populated data.
> Convergence for deployments already past the head is handled separately by
> PR #27.

### GAP-002: Scheduler Jobs Don't Persist Signals
| Property | Value |
|----------|-------|
| Category | DP |
| Severity | P0 — BLOCKER |
| Status | CLOSED |
| Verified 2026-07-30 | `app/services/environmental/scheduler_jobs.py:215` (openaq) and `:335` (openmeteo) call `persist_environmental_observations`. `environmental-postgres-tests` green on `main@dff724f`. |
| Evidence (as raised 2026-07-24) | `app/services/environmental/scheduler_jobs.py:131-228` — no call to `persist_environmental_observations()` |
| Root Cause (as raised) | `scheduled_openaq_ingestion()` and `scheduled_openmeteo_ingestion()` fetch signals but discard them |
| Impact (as raised) | `environmental_observations` table remains empty, no data for crisis detection |
| Fix (as proposed) | Add `persist_environmental_observations(signals, source)` call after fetch |
| Effort | 15 min |
| Owner | TBD |

### GAP-003: CI Pipeline Failing
| Property | Value |
|----------|-------|
| Category | CI |
| Severity | P0 — BLOCKER |
| Status | CLOSED |
| Verified 2026-07-30 | `main@dff724f`: CI `completed/success` — all five required contexts plus `integration-tests`. The first workflow on `main` to be green in its entirety; the previous run on `94e3d49` was `failure`. |
| Evidence (as raised 2026-07-24) | `gh run view 29645753745 --log-failed` — all 3 jobs failed |
| Root Cause (as raised) | Cascading from GAP-001 (Alembic failure) |
| Impact (as raised) | Cannot merge PR, no automated testing |
| Fix (as proposed) | Resolve GAP-001 first |
| Effort | Depends on GAP-001 |
| Owner | TBD |

---

## P0 — Critical Security (Non-Blocking)

### GAP-004: Path Traversal in Bulk Upload
| Property | Value |
|----------|-------|
| Category | SEC |
| Severity | P0 — CRITICAL |
| Status | CLOSED |
| Verified 2026-07-30 | PR #24 requires admin and confines the path. The remaining architectural change — replacing a server-side `file_path` with a multipart upload — is tracked separately as issue #26 and is not this gap. |
| Evidence (as raised 2026-07-24) | `app/api/retrain.py:398-404` — `file_path` parameter used without validation |
| Root Cause (as raised) | No path sanitization before `pd.read_csv(file_path)` |
| Impact (as raised) | Arbitrary file read on server |
| Fix (as proposed) | Add `Depends(require_admin)`, validate path is within allowed directory |
| Effort | 30 min |
| Owner | TBD |

### GAP-005: Unauthenticated Sensitive Endpoints
| Property | Value |
|----------|-------|
| Category | SEC |
| Severity | P0 — CRITICAL |
| Status | CLOSED |
| Verified 2026-07-30 | PR #24 put `require_admin` on the sensitive endpoints. |
| Evidence (as raised 2026-07-24) | `app/api/retrain.py:398` (`/model/data/bulk-upload`), `app/api/forecast.py:175,188` |
| Root Cause (as raised) | Missing `Depends(require_admin)` |
| Impact (as raised) | Data injection, resource exhaustion |
| Fix (as proposed) | Add admin authentication to endpoints |
| Effort | 15 min |
| Owner | TBD |

---

## P1 — High Priority

### GAP-006: Weak Password Hashing
| Property | Value |
|----------|-------|
| Category | SEC |
| Severity | P1 — HIGH |
| Status | CLOSED |
| Verified 2026-07-30 | `app/auth.py:9` uses `argon2.PasswordHasher` (PR #34), with migration of the legacy formats on verify. |
| Evidence (as raised 2026-07-24) | `app/auth.py:72-75` — uses `hashlib.sha256` instead of bcrypt |
| Root Cause (as raised) | bcrypt in requirements.txt but not used |
| Impact (as raised) | Passwords vulnerable to GPU brute-force attacks |
| Fix (as proposed) | Migrate to `bcrypt.hashpw()` |
| Effort | 1 hour (including migration) |
| Owner | TBD |

### GAP-007: No Automated Database Backup
| Property | Value |
|----------|-------|
| Category | OPS |
| Severity | P1 — HIGH |
| Status | PARTIAL |
| Verified 2026-07-30 | The scripts and their tests are merged (#37, #31) and cover encryption, retention, restore and locking. **Nothing schedules them.** No image copies `scripts/` — `Dockerfile.prod` ships `app/`, `data/`, `alembic/`, `run_scheduler.py` and `entrypoint.sh` only — and the repository contains no systemd unit or cron entry. Until that exists there is no backup running, so the M0 criterion 'verified by cron output' cannot be met. |
| Evidence (as raised 2026-07-24) | `backups/` contains only manual backup from Jun 14 |
| Root Cause (as raised) | No cron job or systemd timer for pg_dump |
| Impact (as raised) | Data loss risk on failure |
| Fix (as proposed) | Add cron job for daily pg_dump with 7-day retention |
| Effort | 30 min |
| Owner | TBD |

### GAP-008: Production Server Behind
| Property | Value |
|----------|-------|
| Category | OPS |
| Severity | P1 — HIGH |
| Status | OPEN |
| Verified 2026-07-30 | Production has not been deployed at `dff724f`. No production action was taken in this work. |
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
| Verified 2026-07-30 | `app/auth.py:397` holds `_refresh_tokens: set = set()` — a plain in-process set. Three consequences: every restart invalidates every refresh token, `backend` and `scheduler` do not share it, and the set grows without bound because entries are removed only by explicit revocation. |
| Evidence | `app/auth.py:397` — `_refresh_tokens: set = set()`. The original citation of line 118 no longer resolves; that line is now `ARGON2_CEILING`. |
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
| Status | PARTIAL |
| Verified 2026-07-30 | Coverage on `main@dff724f` is 65% overall (8742 statements, 3052 missing), against 744 backend tests plus 47 forecasting. The gap as written names *critical modules* specifically, which the aggregate does not answer — it needs re-scoping to per-module figures before it can be closed or kept. |
| Evidence (as raised 2026-07-24) | `app/shap_explainer.py` (0%), `app/training.py` (0%) |
| Root Cause (as raised) | No tests written |
| Impact (as raised) | Regressions undetected |
| Fix (as proposed) | Add unit tests for training pipeline |
| Effort | 4 hours |
| Owner | TBD |

### GAP-017: ORM/Production Schema Drift (region_esg_scores PRIMARY KEY)
| Property | Value |
|----------|-------|
| Category | DB |
| Severity | P1 — HIGH |
| Status | CLOSED |
| Verified 2026-07-30 | The converge migrations classify `id` from `pg_attribute.attidentity` and `pg_attrdef` and refuse an unrecognised default by name; 24 tests against PostgreSQL 16, and `migration-bootstrap`'s catch-up scenario converges an 85-row legacy database with the view intact. |
| Evidence (as raised 2026-07-24) | Production has `PRIMARY KEY (region_code)`, ORM expects `PRIMARY KEY (id)` |
| Root Cause (as raised) | Origin unknown; production schema and ORM metadata diverge |
| Impact (as raised) | Potential ORM identity-map, merge, refresh, delete, and write-correctness risks |
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
| Verified 2026-07-30 | `app/api/embed/api.py:10` still returns `headers={"X-Frame-Options": "ALLOWALL"}`. |
| Evidence | `app/api/embed/api.py:10` |
| Fix | Restrict to known origins or remove |

### GAP-012: Port 8000 Exposed to Internet
| Property | Value |
|----------|-------|
| Category | OPS |
| Severity | P2 — MEDIUM |
| Status | CLOSED |
| Verified 2026-07-30 | `docker-compose.prod.yml` publishes ports only for `nginx` (80, 443); `backend`, `scheduler`, `postgres`, `pgbouncer`, `redis` and `mlflow` publish none, and Prometheus and Grafana bind to `127.0.0.1`. |
| Evidence (as raised 2026-07-24) | Production `ss -tlnp` shows `0.0.0.0:8000` |
| Fix (as proposed) | Bind to `127.0.0.1:8000` in docker-compose.prod.yml |

### GAP-013: No Swap Configured
| Property | Value |
|----------|-------|
| Category | OPS |
| Severity | P2 — MEDIUM |
| Status | UNVERIFIED |
| Verified 2026-07-30 | Host-side. Verifying it requires production access, which was not taken in this work. |
| Evidence (as raised 2026-07-24) | `free -h` shows 0B swap, 62% RAM used |
| Fix (as proposed) | Add 2GB swapfile |

### GAP-014: fail2ban Not Installed
| Property | Value |
|----------|-------|
| Category | SEC |
| Severity | P2 — MEDIUM |
| Status | UNVERIFIED |
| Verified 2026-07-30 | Host-side. Verifying it requires production access, which was not taken in this work. |
| Evidence (as raised 2026-07-24) | `fail2ban-client status sshd` returns "not installed" |
| Fix (as proposed) | `apt install fail2ban` |

### GAP-015: Backup Files in Repository
| Property | Value |
|----------|-------|
| Category | CQ |
| Severity | P2 — MEDIUM |
| Status | CLOSED |
| Verified 2026-07-30 | The only tracked `.sql` files are genuine migrations (`migrations/004_esg_pipeline.sql`, `migrations/2026_06_07_regional_esg_snapshot_view.sql`). `.gitignore` covers `backups/`, `*.bak.*` and `*.backup`. |
| Evidence (as raised 2026-07-24) | 30+ `.bak` files found via `find -name "*.bak*"` |
| Fix (as proposed) | Clean up or add to .gitignore |

### GAP-016: Docker Build Cache 37GB
| Property | Value |
|----------|-------|
| Category | OPS |
| Severity | P2 — MEDIUM |
| Status | UNVERIFIED |
| Verified 2026-07-30 | Host-side. Verifying it requires production access, which was not taken in this work. |
| Evidence (as raised 2026-07-24) | `docker system df` on production |
| Fix (as proposed) | `docker system prune -a` |

---

## Summary

| Priority | Open | In Progress | Closed | Total |
|----------|------|-------------|--------|-------|
| P0 Blocker | 2 | 0 | 1 | 3 |
| P0 Security | 2 | 0 | 0 | 2 |
| P1 | 6 | 0 | 0 | 6 |
| P2 | 6 | 0 | 0 | 6 |
| **Total** | **16** | **0** | **1** | **17** |

---

## Resolution Order

1. **GAP-001** — Migration fix (unlocks CI)
2. **GAP-002** — Persistence fix (unlocks data pipeline)
3. **GAP-003** — CI green (automatic after 001+002)
4. **GAP-004, GAP-005** — Security hardening
5. **GAP-007** — Backup setup
6. **GAP-008** — Production deploy
7. Remaining P1/P2 in follow-up sprints
