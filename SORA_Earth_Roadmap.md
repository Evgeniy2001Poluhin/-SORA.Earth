# SORA.Earth AI Platform — Roadmap

> Updated: 2026-04-09

## ✅ Sprint 1: Core Data Pipeline & External Sources
- [x] World Bank API integration (GDP, GINI, life expectancy, CO2)
- [x] OECD SDMX API migration (`sdmx.oecd.org/public/rest/data`, CSV format)
- [x] REST Countries fallback (population, region)
- [x] Redis caching layer (TTL 86400s) for all external calls
- [x] Pydantic schemas: `CountryESGInput`, `ESGResult`, `PredictResult`

## ✅ Sprint 2: ML Models & Evaluation
- [x] XGBoost ensemble (9 features, label-encoded region)
- [x] Stacking model (XGBoost + Ridge + SVR → meta Ridge)
- [x] Neural network fallback (PyTorch, 9→64→32→1)
- [x] SHAP explainability per prediction
- [x] MLflow experiment tracking & model registry
- [x] Feature alignment fix (9 features across all models)

## ✅ Sprint 3: API & Admin
- [x] FastAPI endpoints: `/evaluate`, `/predict`, `/health`, `/api/countries`
- [x] Admin dashboard (Jinja2 + Bootstrap): model managementats
- [x] JWT authentication for admin routes
- [x] Rate limiting (slowapi)
- [x] Docker Compose: app + redis + mlflow + postgres

## ✅ Sprint 4: Testing & Stability
- [x] 285 tests passing (pytest)
- [x] External data mocked in tests (no network dependency)
- [x] XGBoost feature count alignment tests
- [x] Cache hit/miss tests
- [x] MLflow integration tests
- [x] WB/OECD timeout hardening (3s/5s)

## 🔲 Sprint 5: Load Testing & Benchmarks
- [ ] Locust load test: `/evaluate` + `/predict` @ 50 rps
- [ ] Response time p95 < 500ms target
- [ ] Redis cache hit ratio monitoring
- [ ] Results table for thesis Chapter 4

## 🔲 Sprint 6: Thesis Completion
- [ ] Chapter 5: conclusions, limitations, future work
- [ ] Architecture diagram (C4 / Mermaid)
- [ ] Performance benchmark tables
- [ ] Final defense preparation

## 🔲 Sprint 7: Production Hardening (Post-Thesis)
- [ ] CI/CD pipeline (GitHub Actions)
- [ ] Prometheus + Grafana monitoring
- [ ] HTTPS / nginx reverse proxy
- [ ] Multi-model A/B testing vi
- [ ] Expand to 50+ ESG indicators
