# M0 Evidence: region_esg_scores Schema

**Collection date:** 2026-07-24  
**Timezone:** UTC+04  
**Method:** Read-only PostgreSQL `\d+` query  
**Scope:** `public.region_esg_scores` table only  
**Status:** Observed production schema (not normative)

---

## Production Schema Observed

**Object:** `public.region_esg_scores`

### Column Definitions

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| region_code | text | NOT NULL | - | PRIMARY KEY |
| env_score | real | YES | - | 4-byte float |
| social_score | real | YES | - | 4-byte float |
| gov_score | real | YES | - | 4-byte float |
| total_score | real | YES | - | 4-byte float |
| confidence | real | YES | - | 4-byte float |
| updated_at | timestamp with time zone | NOT NULL | now() | Server default |
| id | bigint | NOT NULL | GENERATED ALWAYS AS IDENTITY | Added by cb5a035aed2f |
| sources_count | integer | YES | - | Added by cb5a035aed2f |
| signals_used | integer | YES | - | Added by cb5a035aed2f |

### Constraints

- **PRIMARY KEY:** `region_esg_scores_pkey` on column `region_code`

### Indexes

1. `region_esg_scores_pkey` — PRIMARY KEY, btree (region_code)
2. `ix_region_esg_scores_id` — UNIQUE, btree (id)

### Storage

- **Access method:** heap

---

## Three-Way Schema Drift Analysis

### 1. Alembic Historical Chain (Before Fix)

**Status:** ❌ **No CREATE TABLE migration exists**

- Migration `a1b2c3d4e5f6` creates VIEW referencing table → assumes table exists
- Migration `cb5a035aed2f` does `ALTER TABLE ADD COLUMN` → assumes table exists
- **Gap before implementation:** Table creation was not defined in migration history

**Expected minimal schema (before cb5a035aed2f):**
```sql
region_code   TEXT/VARCHAR  NOT NULL
env_score     FLOAT/REAL    NULL
social_score  FLOAT/REAL    NULL
gov_score     FLOAT/REAL    NULL
total_score   FLOAT/REAL    NULL
confidence    FLOAT/REAL    NULL
updated_at    TIMESTAMPTZ   NOT NULL DEFAULT now()
```

**Missing from base schema:**
- id (added by cb5a035aed2f:22)
- sources_count (added by cb5a035aed2f:23)
- signals_used (added by cb5a035aed2f:24)

---

### 2. SQLAlchemy ORM Model

**Location:** `app/database.py:194-208`

```python
class RegionESGScore(Base):
    __tablename__ = "region_esg_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)  # ← PK on id
    region_code = Column(String(10), index=True, unique=True, nullable=False)
    env_score = Column(Float, nullable=True)
    # ... other columns
```

**ORM Expectations:**
- Primary key: `id` (Integer, autoincrement)
- Unique constraint: `region_code`
- Type: `String(10)` for region_code
- Type: `Float` for score columns (compiles to DOUBLE PRECISION)

**Drift from Production:**
- ❌ ORM expects `id` as PRIMARY KEY
- ✓ Production has `region_code` as PRIMARY KEY
- ⚠️ ORM expects `Float` (8-byte) → Production has `real` (4-byte)
- ⚠️ ORM expects `String(10)` → Production has `text` (unlimited)

---

### 3. Production Reality

**Primary Key:** `region_code`  
**Unique Identity Column:** `id` (bigint IDENTITY)

**Origin:** Unknown. Manual table creation or alteration is one unverified hypothesis.

**Evidence for region_code as natural key:**
1. Application code usage (`app/services/esg_aggregator.py:66`):
   ```python
   row = db.query(RegionESGScore).filter_by(region_code=region_code).first()
   ```
   → Application logic assumes one row per `region_code`; a uniqueness constraint enforces that invariant

2. No code usage of `Session.get(RegionESGScore, id)` was found during the initial audit

---

## Classification

| Issue | Category | Severity | Resolution |
|-------|----------|----------|------------|
| Missing CREATE TABLE migration | Migration Bootstrap Defect | **P0 BLOCKER** | GAP-001 (this fix) |
| ORM expects id as PK | ORM/Schema Drift | **P1 HIGH** | GAP-017 (deferred) |
| Unknown PK origin | Requires investigation | Unclassified | Document observations only |
| Type mismatch (Float vs REAL) | Schema drift | Unclassified | Evaluate during GAP-017 analysis |

---

## Implementation: GAP-001

**Migration:** `2e8b4493b24b_create_region_esg_scores_table.py`

**Approach:**
1. Create prerequisite migration inserting between `31e5cc432377` and `a1b2c3d4e5f6`
2. Define minimal base schema (7 columns, NO id/sources_count/signals_used)
3. Use `region_code` as PRIMARY KEY (matches production and usage pattern)
4. Use PostgreSQL `REAL` type explicitly (matches production)
5. Allow cb5a035aed2f to add id/sources_count/signals_used later (no duplicates)

**ORM Drift (GAP-017):** Deferred to separate analysis requiring:
- Full audit of RegionESGScore query patterns
- Check for Session.get() usage
- Relationship/foreign key impact analysis
- Serializer/API response impact
- Migration path for PRIMARY KEY change (if needed)

---

## Verification

**Status:** PostgreSQL round-trip test executed against a real PostgreSQL 16 instance in CI and **PASSED**.

**CI Reference:**
- PR: https://github.com/Evgeniy2001Poluhin/-SORA.Earth/pull/12
- Workflow run: `30123691206`
- Job: `environmental-postgres-tests` (job ID `89582031242`)
- Step: `Run GAP-001 Alembic bootstrap round-trip test`
- PostgreSQL version: 16
- Verified: 2026-07-24 UTC / 2026-07-25 UTC+04

**Verified stages (all passed):**
1. Fresh upgrade — empty `public` schema to Alembic head
2. Schema assertions against `region_esg_scores` (columns, types, nullability, primary key, unique index) and `regional_esg_snapshot` (view kind, queryable)
3. Destructive downgrade to `31e5cc432377` — confirms `region_esg_scores` and `regional_esg_snapshot` are fully removed
4. Re-upgrade to head — schema assertions re-pass after downgrade/upgrade cycle
5. Idempotent upgrade — no-op, current revision unchanged
6. Single-head / current-revision consistency check

**Final Alembic head:** `0b0ff6d1594e`

**Scope note:** This result covers only the `Run GAP-001 Alembic bootstrap round-trip test` step. The `environmental-postgres-tests` job, and the overall PR #12 CI run, remained **FAILED** because a later, unrelated step (`Run environmental persistence tests against PostgreSQL`) and other independent checks (`backend-tests`, `frontend-checks`) failed for reasons unrelated to this migration. This document does not claim the PR or the full CI run passed — only the GAP-001 round-trip step described above.

---

## Notes

- This evidence document is **observational**, not normative
- Production schema reflects deployed state as of collection date
- Schema may evolve through subsequent migrations
- ORM drift tracked separately and does not block GAP-001 resolution
- Fresh database round-trip behavior (upgrade → downgrade → re-upgrade → idempotent upgrade) confirmed PASSED in CI (PR #12, workflow run `30123691206`, job `89582031242`); see "Verification" section above for scope and limitations
- This confirms only the GAP-001 migration step; it does not imply the overall PR or CI run passed
- Existing production databases require no action from this migration if already stamped beyond the inserted revision; production rollout behavior remains subject to deployment review
