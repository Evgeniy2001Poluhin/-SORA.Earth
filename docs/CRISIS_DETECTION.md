# Environmental Crisis Detection Module

## Overview

SORA Earth автоматически обнаруживает экологические кризисы, мониторя ESG-метрики
по 32 странам в реальном времени. Модуль встроен в фоновый scheduler и запускается
каждые 6 часов вместе с обновлением данных.

---

## Architecture

```
Scheduler (APScheduler)
└── scheduled_refresh_metrics() # every 6h
    └── CrisisDetector.analyze()
        ├── z-score anomaly detection
        ├── threshold breach check
        └── Redis alert publishing
```

---

## Detection Logic

### 1. Z-Score Anomaly Detection

Для каждой метрики вычисляется z-score за последние 30 дней:
`z = (x - μ) / σ` — аномалия при `|z| > 2.5`

### 2. Threshold Breach

Статические пороги в `config/crisis_thresholds.yaml`.
Кризис = превышение порога 3+ дня подряд.

### 3. Composite Risk Score (0–100)

| Score | Level  | Action              |
|-------|--------|---------------------|
| 0–30  | Normal | No alert            |
| 31–60 | Watch  | Log warning         |
| 61–80 | Alert  | Publish to Redis    |
| 81–100| Crisis | Webhook + Redis     |

---

## Redis Alert Format

Channel: `crisis_alerts`

```json
{
  "country": "Russia",
  "metric": "pm2_5",
  "value": 87.4,
  "threshold": 35.0,
  "z_score": 3.12,
  "risk_score": 74,
  "level": "Alert",
  "timestamp": "2026-07-17T21:59:00Z"
}
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/crisis/status` | Текущий статус по всем странам |
| GET | `/api/v1/crisis/history` | История кризисов за период |
| GET | `/api/v1/crisis/alerts` | Активные алерты |

---

## Implementation Status

| Component | Status | File |
|-----------|--------|------|
| Detector module | ✅ Done | `app/services/crisis_detector.py` |
| Scheduler integration | ✅ Done | `app/scheduler.py:744` |
| Redis publishing | ❌ TODO | — |
| Webhook dispatch | ❌ TODO | — |
| API endpoints | ❌ TODO | — |

---

## References

- [ENVIRONMENTAL_BASELINE_AUDIT.md](./ENVIRONMENTAL_BASELINE_AUDIT.md)
