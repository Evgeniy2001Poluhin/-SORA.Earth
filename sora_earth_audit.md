# SORA Earth — Полный чекап проекта
**Дата:** 7 мая 2026, 13:42 MSK
**Версия:** v1.0 — финальный (сервер + Mac)

---

## 🎯 TL;DR

| Зона | Статус |
|---|---|
| Прод (HTTPS, API, Cloudflare Tunnel) | ✅ работает |
| 7 контейнеров docker compose | ✅ все healthy |
| 131 method / 128 paths в OpenAPI | ✅ |
| 149 роут-декораторов в живом коде | ✅ |
| ML-модели (RF + ensemble v2 calibrated) | ✅ retrain работает, ROC AUC 0.9123 |
| MLflow tracking 430 МБ | ✅ |
| Backups (cron 03:00 + 03:05 Selectel) | ✅ |
| Локальный git репо на Mac | ✅ 6d613b5 |
| Git на сервере | 🔴 пустой (нет коммитов) |
| GitHub remote `Evgeniy2001Poluhin/-SORA.Earth` | ⚠️ ремоут есть, push не было |
| Pytest на сервере | 🔴 не запускается (нет venv/pip) |
| Postgres/Redis/Grafana/Prom торчат в интернет | 🔴 UFW не установлен |
| Grafana errors 1867/6h | 🔴 что-то сломано |
| Текст диплома thesis | ⚠️ нет `.md/.tex`, только figures |

---

## 🖥️ ЧАСТЬ A — СЕРВЕР `109.73.194.26:2222`

### A1. Окружение
- Хост: `7771286-ex695033.twc1.net` (Timeweb)
- OS: Ubuntu, kernel 6.8.0-111
- Диск: 96 ГБ, занято 35 ГБ (37%)
- RAM: 11 ГБ (used 1.8 ГБ)
- Load avg: 0.09 — сервер свободен

### A2. Структура `/opt/sora_earth/`
| Каталог | Размер |
|---|---|
| mlruns/ | 430 MB |
| web/ | 370 MB |
| _archive_20260507_1327/ | 165 MB (твоя чистка) |
| backups/ | 156 MB |
| app/ | 102 MB |
| models/ | 26 MB |
| tests/ | 248 KB (42 файла test_*.py) |
| sora_ai_copilot/ | 460 KB (495 строк Python) |
| thesis/ | 192 KB (только figures) |

### A3. Backend
- 60 .py файлов в `app/`
- **149 роут-декораторов** (после чистки `.bak`)
- Routers подключены: auth, admin_retrain_log, admin_snapshot, admin_timeline, admin_diagnostics, admin_ai_control, drift_baseline, explainability, ai_teammate
- 204 строки в requirements.txt, 223 пакета в контейнере

### A4. Реальный стек (pip list в app)
FastAPI 0.128.8 · scikit-learn 1.6.1 · MLflow 3.1.4 · SHAP 0.49.1 · Torch 2.8.0 · XGBoost 2.1.4 · pandas 2.3.3 · numpy 2.0.2 · pydantic 2.12.5 · SQLAlchemy 2.0.48 · Redis 7.4 · uvicorn 0.39 · alembic 1.16.5 · prometheus-fastapi-instrumentator 7.1.0 · geopandas 1.0.1 · shapely 2.0.7

### A5. Модели (после чистки)
| Файл | Размер | Где грузится |
|---|---|---|
| model.pkl | 2.1 MB | retrain → main.py:168 |
| scaler.pkl | 666 b | main.py:166 |
| best_threshold.pkl | 132 b | main.py:178 |
| rf_model_cal.pkl | 2.1 MB | ab_comparison.py:41 |
| ensemble_model_v2.pkl | 2.6 MB | main.py:191 |
| ensemble_model_v2_cal.pkl | 2.6 MB | calibration.py:28,180 |
| scaler_v2.pkl | 978 b | main.py:188 |
| retrain_metrics.pkl | 433 b | retrain |

10 мёртвых моделей перенесены в `models/_unused/`.

**Активная модель (meta.json + metrics.json):**
- Algorithm: RandomForestClassifier, n_estimators=200, max_depth=10
- Features (9): budget, co2_reduction, social_impact, duration_months, budget_per_month, co2_per_dollar, efficiency_score, year, quarter
- Samples: 734 total (587 train / 147 test, +94 enrichment)
- Metrics: accuracy 0.8231 · F1 0.8375 · best F1 0.8538 · ROC AUC 0.9123 · best threshold 0.31
- Retrained: 2026-05-07 03:00

### A6. База данных PostgreSQL 16
- Database: `sora_earth` (owner: `sora`)
- Таблиц: **7**
  - alembic_version, batch_results, country_indicator_history, data_refresh_log, evaluations, predictions_log, retrain_log
- Данные: evaluations=36, retrain_log=6
- ⚠️ Таблиц `users`, `countries`, `audit_log` **нет** в схеме (хотя API на них ссылается)

### A7. Docker compose — 7 сервисов
| Сервис | Статус | Порты |
|---|---|---|
| app (FastAPI) | healthy 38мин | 8000 |
| postgres:16-alpine | healthy 2ч | 5432 |
| redis:7-alpine | up 2ч | 6379 |
| nginx:alpine | up 2ч | 80 |
| prometheus | up 2ч | 9090 |
| grafana | up 2ч | 3000 |
| scheduler | healthy 2ч | — |

### A8. API проверка (localhost:8000)
| Endpoint | Status | Time |
|---|---|---|
| /api/v1/health | 200 | 5 мс |
| /api/v1/countries | 200 | 4 мс |
| /api/v1/model/feature-importance | 200 | 41 мс |
| /api/v1/data/status | 403 | — |
| /api/v1/audit/log | 401 | — |
| /api/v1/calibration/info | 404 | — |
| /api/v1/retrain/status | 404 | — |
| /docs | 200 | — |
| /openapi.json | 200 | — |
| /metrics | 200 | — |
| POST /evaluate | 200 | 357 мс (реальный JSON со скорами) |

OpenAPI: **128 paths, 131 methods**.

### A9. Внешний доступ
- HTTPS sora-earth.ru → HTTP/2 405 (allow GET)
- Cloudflare Tunnel: active, LISTEN 127.0.0.1:20241
- IP сервера: 109.73.194.26

### A10. Cron (бэкапы)
```
0 3 * * * /opt/sora_earth/scripts/backup_all.sh >> backups/backup_all.log
5 3 * * * /root/scripts/backup-to-selectel.sh >> /var/log/rclone-backup.log
```

### A11. Логи (errors за 6h)
| Сервис | Errors |
|---|---|
| postgres | 3 |
| redis | 0 |
| scheduler | 0 |
| app | 0 ✅ |
| prometheus | 1 |
| **grafana** | **1867** 🔴 |
| nginx | 8 |

### A12. Git на сервере
```
fatal: your current branch 'master' does not have any commits yet
origin: https://github.com/Evgeniy2001Poluhin/-SORA.Earth.git (нет push'ей)
```
Ремоут добавлен, но **ноль коммитов**. Все файлы untracked.

### A13. Безопасность
- ufw: **не установлен**
- LISTEN 0.0.0.0: 5432 (Postgres), 6379 (Redis), 3000 (Grafana), 9090 (Prometheus), 8000, 80, 2222, 10050 (Zabbix)
- .env: 14 ключей (ADMIN_API_KEY, JWT_SECRET, POSTGRES_*, SECRET_KEY, SENTRY_DSN, SORA_ADMIN_TOKEN и др.)
- Hardcoded секреты в коде: **не найдены** ✅

### A14. Pytest на сервере — НЕ работает
```
The virtual environment was not created successfully (ensurepip is not available)
Command 'pip' not found
Command 'pytest' not found
```
**Чинится:** `apt install -y python3-venv python3-pip`

### A15. MLflow
- mlruns/ = 430 MB, mlflow.db = 13 MB
- Эксперимент: 1 (artifacts)

### A16. Thesis на сервере
- Только `figures/` (3 файла .png/.json)
- **Нет .md / .tex** — текст диплома где-то ещё (вероятно на Mac)

---

## 💻 ЧАСТЬ B — MAC `Evgenijs-MacBook-Air`

### B1. Окружение
- macOS 15.5, ARM64 (Apple Silicon)
- Дата: 2026-05-07 13:41

### B2. SSH config (с дублем!)
```
Host github.com → ~/.ssh/github_deploy
Host sora → 109.73.194.26:2222 root  (без IdentityFile!)
Host sora → 109.73.194.26:2222 root → ~/.ssh/id_ed25519  ← дубль
Host sora-cf → ssh.sora-earth.ru через cloudflared access
```
⚠️ **Два блока `Host sora`** — первый перекрывает второй. Удали верхний.

### B3. SSH тест
`ssh sora "hostname"` → `7771286-ex695033.twc1.net` ✅ работает

### B4. Локальный репо `~/sora_earth_ai_platform/`
- Git **с историей** ✅
- HEAD: `6d613b5 fix(deploy): exclude macOS resource forks + bind mount override + pdf_report service`
- Последние 5 коммитов (PDF report, SHAP i18n, batch fallback)
- Status: 5 удалённых файлов (40, 50, 70, 75, REАDME.md), 5 модифицированных (Pitch Deck, requirements.txt, .env.production)
- Remote: `https://github.com/Evgeniy2001Poluhin/-SORA.Earth.git`

### B5. Расхождение прод vs локалка
| | Hash |
|---|---|
| Mac | `6d613b58fb62f5d21729a53c4fb47d8b028dc3df` |
| Server | пусто (git пуст) |

⚠️ **Сервер не отслеживает git**. Деплой делается через `scp` без версии, поэтому невозможно сказать, какой коммит сейчас в проде. Скорее всего — то что было `scp`-нуто последний раз.

### B6. Прод снаружи (с Mac)
| URL | Status |
|---|---|
| https://sora-earth.ru/ | 200 |
| https://sora-earth.ru/api/v1/health | 200 |
| https://sora-earth.ru/docs | 200 |
| https://grafana.sora-earth.ru/ | 302 (login redirect) |

### B7. Thesis на Mac
`zsh: no matches found: thesis/*.md` — папка `thesis/` есть, но **нет .md/.tex** и здесь.
Скорее всего диплом в другой папке (`thesis/`, `Documents/`, `Dropbox/`?).

---

## 🔴 КРИТИЧНОЕ К ЗАЩИТЕ

| # | Проблема | Как фиксить |
|---|---|---|
| 1 | Git на сервере пуст, нет CI/CD pipeline | Initial commit + push с Mac в `-SORA.Earth`, потом `git pull` на сервере вместо scp |
| 2 | Pytest не запускается на сервере | `apt install -y python3-venv python3-pip` |
| 3 | Postgres/Redis/Grafana/Prom в интернете | В docker-compose.yml: `5432:5432` → `127.0.0.1:5432:5432` (то же 6379, 3000, 9090) |
| 4 | Grafana 1867 ошибок/6ч | `docker compose logs grafana --since 6h \| grep -i error \| head` |
| 5 | Текст диплома thesis нигде нет | Найти на Mac/iCloud, положить в `thesis/` |
| 6 | Дубль `Host sora` в `~/.ssh/config` | Удалить первый блок (без IdentityFile) |
| 7 | API: 4 эндпойнта 401/403/404 | Проверить расхождение фронт vs бэк |

## 🟡 НЕКРИТИЧНОЕ

- В таблицах БД нет `users`, `countries`, `audit_log` — либо удалить эти routes, либо создать миграцию
- `htmlcov/` 3.9 MB на сервере — старое coverage, удалить
- `.DS_Store` в корне — добавить в .gitignore (он уже есть)
- `Untitled` (39 байт) в `models/` — мусор

## ✅ ЧТО МОЖНО УВЕРЕННО ГОВОРИТЬ НА ЗАЩИТЕ

- Production deployment на Cloudflare Tunnel + HTTPS, аптайм 2+ часа
- 7-сервисный стек (FastAPI + Postgres + Redis + Nginx + Prometheus + Grafana + Scheduler)
- 131 endpoint в OpenAPI спецификации
- ML pipeline: RandomForest (calibrated) + Ensemble v2 (calibrated), ROC AUC 0.9123, F1 0.85, retrain автоматический в 03:00
- MLflow tracking 430 MB истории экспериментов
- 42 теста, 7 таблиц БД с alembic миграциями
- SHAP explainability (есть в коде и в зависимостях)
- 9 фичей с реальной семантикой (CO2, бюджет, social impact)
- AI helper CLI (495 строк) — **называть честно: «LLM-based codebase assistant»**, не «autonomous agent»

---

## 📋 ПЛАН ДО ЗАЩИТЫ (приоритеты)

### Сегодня (1 час)
1. На Mac: убрать дубль SSH-конфига
2. На Mac: `git push origin main` (через PAT)
3. На сервере: установить python3-venv, прогнать pytest
4. На сервере: посмотреть grafana errors, починить datasource

### Завтра (2 часа)
5. Закрыть порты Postgres/Redis/Grafana/Prom через docker-compose binding
6. Найти текст диплома, положить в thesis/
7. Решить проблему с 4 эндпойнтами (401/403/404)

### На неделе (4 часа)
8. Добавить таблицы users/countries/audit_log через alembic, либо удалить роуты
9. Заменить scp-деплой на git pull
10. Обновить README.md и API_CATALOG.md под реальные 131 method

---

**Этот файл сохранён локально как `sora_earth_audit.md`.
В любой момент пришли его мне — я подхвачу контекст за 1 секунду.**
