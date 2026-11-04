# E2E Ownership Tests

Browser-based end-to-end tests for SORA.Earth accounts and project ownership.

## What it tests

Six scenarios covering the ownership isolation model introduced in PRs #510, #511, #512:

1. **Anonymous**: Calculate works, save requires auth
2. **Specialist A**: Saves once on double-click, persists across sessions
3. **Isolation**: Specialist B cannot access A's records; admin can, with audit
4. **Context switch**: A→B clears all A's data, stops in-flight requests
5. **Revocation**: Blocking A invalidates their session immediately
6. **Error handling**: 503 shows error, keeps session, no auto-retry

## Running locally

```bash
bash e2e/ownership/run-local.sh
```

It starts a disposable PostgreSQL 16 container (`sora-e2e-integration`,
127.0.0.1:15433), runs migrations and `bootstrap_users.py --dev`, builds the
SPA with `VITE_API_BASE=/api/v1`, serves it with uvicorn on 127.0.0.1:18765
(`REDIS_URL` unset), runs every spec with retries 0, writes the report, and
removes everything it started. `E2E_VENV` names the Python venv (it needs
`requirements.txt` and `pypdf==5.1.0`).

Each person in a scenario sends its own `X-Real-IP`: the per-address rate
limit counts SPA assets too, and one loopback address is refused with 429
part-way through the suite.

## Running in CI

The `e2e-ownership` job runs these tests against:
- PostgreSQL 16 service container
- Backend with alembic migrations + bootstrap_users.py
- Production-built SPA served by uvicorn

## Structure

```
e2e/ownership/
├── tests/
│   ├── helpers.ts              # Shared utilities (admin API, sign-in, etc.)
│   ├── 01-anonymous.spec.ts    # Anonymous user scenario
│   ├── 02-specialist-a-saves.spec.ts
│   ├── 03-isolation.spec.ts    # isolation + the saved-PDF test
│   ├── 04-context-switch.spec.ts
│   ├── 05-revocation.spec.ts
│   └── 06-error-handling.spec.ts
├── pdf_text.py                 # pypdf text extraction for the PDF test
├── generate-report.ts          # node --experimental-strip-types generate-report.ts
├── playwright.config.ts
├── package.json
└── tsconfig.json
```

## Report artifact

After tests run, `generate-report.ts` produces `e2e-ownership-report.json`:

```json
{
  "timestamp": "2026-10-28T...",
  "commit_sha": "abc123...",
  "node_version": "v22.x.x",
  "playwright_version": "^1.50.0",
  "scenarios": [
    {"name": "anonymous: ...", "status": "passed", "duration_ms": 1234},
    ...
  ],
  "summary": {"total": 6, "passed": 6, "failed": 0, "skipped": 0}
}
```

Uploaded as a workflow artifact.

## Data

Synthetic only:
- Usernames: `specialist_a`, `specialist_b`
- Projects: `SpecialistA-Project-{timestamp}`, etc.
- Values: budget=100000, co2=150, social_impact=7, duration=24
