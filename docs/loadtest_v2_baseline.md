# Load test baseline — `/api/v2/predict` (champion @v3)

**Measured:** 2026-05-10 on macOS M-series (single node, no Redis)  
**Tool:** Locust 2.34.0 · **Target:** uvicorn + FastAPI · **Model:** XGBoost v2, MLflow Registry alias `champion`

## Horizontal scaling (uvicorn workers)

| Scenario | RPS | p50 | p95 | p99 | errors |
|---|---:|---:|---:|---:|---:|
| 1 worker, 5u, 30s   | 17.9  | 21 ms  | 65 ms  | 120 ms | 0.00% |
| 1 worker, 50u, 3m   | 71.9  | 440 ms | 730 ms | 880 ms | 0.00% |
| **4 workers, 50u, 3m** | **156.6** | **35 ms** | **200 ms** | **590 ms** | **0.00%** |

**Finding:** 4-worker scaling gives **2.2× throughput** and **3.6× lower p95**
vs. single worker. Zero errors across all profiles → server is stable,
GIL-bound for sync ML inference.

## Per-endpoint (4-worker, full run)

| Endpoint                       | Count  | p50    | p95    | p99    | RPS   |
|--------------------------------|-------:|-------:|-------:|-------:|
| POST /api/v2/predict           | 16 500 | 39 ms  | 210 ms | 610 ms | 129.5 |
| GET /api/v2/model/calibration  |  1 703 | 17 ms  | 140 ms | 510 ms |  13.4 |
| GET /api/v2/model/version      |  1 702 | 14 ms  |  95 ms | 310 ms |  13.4 |

## Reproduce

```bash
# terminal A — prod-like server
./scripts/run_server_prod.sh

# terminal B — load
./scripts/run_loadtest_v2.sh
open output/loadtest_v2_*/report.html
```

## Next optimization targets (out of scope for this thesis)

- Redis for `/model/version` + `/model/calibration` → p95 < 50 ms on GETs
- `gunicorn --preload` to avoid 4× model pickle in RAM (~240 MB savings)
- Kubernetes HPA on CPU > 60% → horizontal scale beyond single node
