# DEMO-сценарий SORA.Earth AI Platform

## Цель

Показать, что SORA.Earth AI Platform — это production-grade AI/ML платформа, а не только ML-модель.

## Подготовка

Запуск стека:

```bash
docker compose up -d
```

Проверка:
- FastAPI Docs: `http://localhost:8000/docs`
- App: `http://localhost:8000`
- Grafana: `http://localhost:3000`

## Сценарий на 5–7 минут

### 1. Swagger / API обзор
Открыть:
- `http://localhost:8000/docs`

Показать:
- evaluate endpoint
- predict/explain endpoints
- analytics endpoints
- mlops endpoints
- admin endpoints

### 2. ESG Evaluate
Показать форму оценки проекта.

Пример:
- name: Solar Farm Alpha
- budget: 100000
- co2_reduction: 60
- social_impact: 8
- duration_months: 24
- region: Germany

Показать:
- total ESG score
- environment / social / economic score
- success probability
- risk level
- recommendations

### 3. Predict / Explain

> **Тело раздела утрачено.** Заголовок читался как `### 3. Predic Germany` —
> оборванное «Predict» и затёкший хвост от следующего раздела. Тема
> восстановлена по списку скриншотов ниже (`03-predict-explain.png`), само
> содержание — нет (#296).

### 4. Country benchmark

> **Раздел 4 утрачен целиком**, заголовок и текст, в первом коммите `997de75`
> (7 мая). Тема восстановлена по `04-country-benchmark.png` и по слову
> «Germany», уцелевшему в заголовке раздела 3; содержание восстановить нечем
> (#296).

### 5. AI Teammate
Показать:
- status
- observe mode
- recommendations

### 6. Admin / Timeline
Показать:
- admin snapshot
- timeline
- retrain logs
- diagnostics

### 7. Grafana / Metrics
Показать:
- request metrics
- latency
- model metrics
- alerts

## Скриншоты

Заголовок этого раздела тоже был уничтожен — от него осталось « в
`assets/screenshots/`:». Все семь файлов на месте:

- `01-swagger.png`
- `02-evaluate.png`
- `03-predict-explain.png`
- `04-country-benchmark.png`
- `05-ai-teammate.png`
- `06-admin-dashboard.png`
- `07-grafana.png`
