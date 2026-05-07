# 🗺️ SORA.Earth — Development Roadmap

> **Status:** Active development → v0.1.x bugfix series  
> **Updated:** 2026-05-07 (after Day 2)  
> **Current:** [v0.1.1](https://github.com/Evgeniy2001Poluhin/-SORA.Earth/releases/tag/v0.1.1) — 332/384 tests passing (86.5%)

---

## 📊 Progress Tracker

| Day | Date | Tests Passing | Failures | Release | Highlight |
|-----|------|---------------|----------|---------|-----------|
| 1   | 2026-05-06 | 327 / 384 (85.2%) | 52 | v0.1.0 | Initial public release |
| 2   | 2026-05-07 | **332 / 384 (86.5%)** | 47 | **v0.1.1** | BatchNorm fix + honest CI |
| 3   | 2026-05-08 | _target ≥337_ | _target ≤42_ | v0.1.2 | TestAuthHTTP env fixtures |
| 4   | 2026-05-09 | _target ≥345_ | _target ≤34_ | v0.1.3 | TestRetrain filesystem fixtures |
| 5   | 2026-05-10 | _target ≥360_ | _target ≤24_ | v0.1.4 | TestCalibration / TestABComparison |
| 6   | 2026-05-11 | _target ≥370_ | _target ≤14_ | v0.1.5 |
| 7   | 2026-05-12 | _target ≥380_ | _target ≤4_ | v0.2.0 | Threshold removed, full green CI |

---

## ✅ Completed (Day 1 + Day 2)

### Day 1 — v0.1.0 (Initial release)
- 327/384 tests passing
- CI workflow with `|| true` soft-fail
- Initial documentation, ml models, FastAPI backend
- 52 known failures documented as v0.1.0 backlog

### Day 2 — v0.1.1 (3.5 hours, 217 minutes)
- ✅ **Fix `_nn_forward`** with `model.eval() + torch.no_grad()`
  - Closes 5 tests (predict_neural / predict_stacct_compare ×2)
  - Resolves BatchNorm RuntimeError on `batch_size=1`
  - Speeds up inference ~15% (no autograd overhead)
- ✅ **CI honest mode** — removed `|| true`, added `--maxfail=60` threshold
- ✅ **Tag + Release v0.1.1** on GitHub with structured changelog
- ✅ Test pass rate: 85.2% → **86.5%** (+1.3pp)

**Commits:** `d150c2d` (fix), `6f9d992` (ci)

---

## 🎯 Day 3 Plan — TestAuthHTTP (target: 5 tests)

**Goal:** 332 → 337 passing (-5 failures)

### Tasks
1. **Add pytest fixture** for `JWT_SECRET`, `ADMIN_API_KEY`, `READONLY_API_KEY` env vars in `tests/conftest.py`
2. **Fix `test_me`** — needs valid JWT token in fixture
3. **Fix `test_admin_stats`** — needs admin role in token
4. **Fix `test_verify_key`** — needs API key registered in test DB
5. **Fix `test_audit_log_filter`** — needs seed audit entries
6. **Fix `test_list_users`** — needs admin auth header
7. **Tag v0.1.2** + Release on GitHub
8. **CI threshold:** 60 → 50

**Estimated time:** ~25 minutes

---

## 🎯 Day 4tests)

**Goal:** 337 → 345 passing

### Failures to address
- `test_retrain_endpoint` (RuntimeError)
- `test_data_refresh` (FileNotFoundError)
- `test_data_refresh_auto_retrain_trigger`
- `test_bulk_upload_success`
- `test_drift_no_log` / `test_drift_small_window`
- `test_feature_importance_no_key`
- `test_predict_uncertainty`

**Approach:** add `tmp_path` fixtures, mock filesystem, seed model artifacts.

---

## 🎯 Day 5 Plan — Calibration & A/B (target: ≥15 tests)

- `test_reliability_diagram`
- `test_predict_uncertainty`
- `test_ab_comparison_json`
- `test_ab_comparison_plot`
- `test_404`, `test_model_info`, `test_model_metrics_main`
- TestMainCoverage assertions

---

## 🎯 Day 6 Plan — Coverage Boost cleanup (target: ≥10 tests)

- Remaining `test_coverage_boost.py` and `test_coverage_final.py`
- Edge cases in retrain pipeline

---

## 🎯 Day 7 Plan — v0.2.0 (target: full green)

- All known failures resolved (target: <5 remaining)
- **Remove `--maxfail` threshold** from CI
- Final dohots, demo GIF
- Tag **v0.2.0** as first stable release

---

## 🚀 Beyond v0.2.0 — v0.3.0 vision

- Frontend SPA: full multi-language (EN/RU + DE/ES)
- Real-time drift dashboard with WebSocket updates
- Multi-model A/B with confidence intervals
- ESG benchmark dataset expansion (50+ countries)
- CI matrix (Python 3.9 / 3.11 / 3.12)
- Docker image on GHCR with semver tags
- Performance: latency p99 < 50ms

---

## 🔗 References

- **Latest release:** [v0.1.1](https://github.com/Evgeniy2001Poluhin/-SORA.Earth/releases/tag/v0.1.1)
- **CI Workflow:** [.github/workflows/ci.yml](.github/workflows/ci.yml)
- **Known Issues v0.1.0:** see `KNOWN_ISSUES.md`
- **Architecture:** `app/api/predict.py`, `app/main.py`

