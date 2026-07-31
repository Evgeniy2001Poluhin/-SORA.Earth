# SORA Earth — RUNBOOK

Версия: 1.0  
Дата: 2026-05-16  
Автор: Evgeniy Poluhin

---

## 0. Точки входа

| Хост | Назначение | Как зайти |
|---|---|---|
| ssh.sora-earth.ru:2222 | SSH через Cloudflare Tunnel | `ssh sora-cf` с Mac |
| https://api.sora-earth.ru | FastAPI (prod) | браузер / curl |
| https://sora-earth.ru | Frontend SPA + API | браузер |
| https://grafana.sora-earth.ru | Grafana | браузер |
| https://mlflow.sora-earth.ru | MLflow | Cloudflare Access (email code) |
| 109.73.194.26:2222 | прямой SSH (fallback) | заблокирован Timeweb FW, только VNC |

---

## 1. Штатный запуск стека

```bash
ssh sora-cf
cd /opt/sora_earth
docker compose up -d
docker compose ps
curl -I http://127.0.0.1:8000/api/v1/health
```

Ждём 30 секунд → `app, postgres, scheduler` должны быть `(healthy)`.

---
```

Эскалация через Timeweb VNC (Recovery Console):
```bash
systemctl status cloudflared
systemctl restart cloudflared
journalctl -u cloudflared -n 50

systemctl status ssh
ss -tlnp | grep 2222
sshd -t
systemctl restart ssh
```

Если VNC noVNC сломан — Timeweb личный кабинет → Серверы → Recovery Console.

---

## 3. Восстановление после reboot

```bash
ssh sora-cf
cd /opt/sora_earth
systemctl status cloudflared docker ssh
docker compose ps
docker compose up -d
docker start mlflow || true
docker logs --tail 30 mlflow

for url in https://api.sora-earth.ru https://grafana.sora-earth.ru https://mlflow.sora-earth.ru; do
  echo "=== $url ==="
  curl -sI "$url" | head -3
done
```

---

## 4. Частые проблемы

### A) `Could not resolve hostname sora-cf`
Ты не на Mac. Алиас живёт в `~/.ssh/config` Mac. На сервере используй `ssh user@host` напрямую.

### B) `apt` зависает на `dpkg-reconfigure openssh-server`
Стрелка вверх → `keep the local version` → Tab → Enter.  
Или: `dpkg --configure -a`.

### C) `cloudflared` не видит порт после рестарта docker
```bash
systemctl restart cloudflared
```

### D) Сломал `sshd_config`
Через VNC:
```bash
cp /etc/ssh/sshd_config.bak.<ts> /etc/ssh/sshd_config
sshd -t
systemctl restart ssh
```

### E) Случайно удалил Timeweb FW правила
Должны быть открыты: `80/tcp`, `443/tcp`, `443/udp`, `ICMP`.  
Всё остальное закрыто. SSH идёт через Cloudflare — ему правила не нужны.

### F) Cloudflare Tunnel — UUID и config
- Tunnel ID: `209344ca-428a-4357-8b68-e5702c556086`
- Config: `/etc/cloudflared/coress validate
  ```

### G) MLflow требует логин при своём же email
Открой https://mlflow.sora-earth.ru в приватном окне → email → 6-значный код на почту.

### H) Grafana заваливает логи `data source not found`
Проблема: alert rules ссылаются на удалённый datasource UID.  
Открой https://grafana.sora-earth.ru/alerting/list → у каждого правила (sora-app-down, sora-auc-degradation, sora-drift-detected, sora-high-latency, sora-retrain-failed) → Query A → выбери актуальный Prometheus → Save.

---

## 5. Конфиги и пути
/etc/ssh/sshd_config — порт 2222, pubkey only
/etc/ssh/sshd_config.d/00-sora.conf — overrides (НЕ Port!)
/etc/cloudflared/config.yml — ingress 5 хостов
/root/.cloudflared/<UUID>.json — credentials
/opt/sora_earth/ — docker-compose проект
/opt/sora_earth/docker-compose.yml — основной compose
/opt/sora_earth/grafana/provisioning/ — datasources + dashboards
/opt/mlflow/db/mlflow.db — БД MLflow
/opt/mlflow/artifacts/ — артефакты MLflow
~/.ssh/authorized_keys — твой публичный ключ
/etc/ssh/sshd_config.bak.<ts>
/etc/cloudflared/config.yml.bak.<ts>
/root/backup-ssh-2026-05-08.tgz
/opt/sora_earth/backups/
---

## 6. Восстановление с нуля

1. **Timeweb VNC / Recovery Console** → root
2. **Firewall Timeweb:** 80, 443 TCP/UDP, ICMP
3. **SSH:**
   ```bash
   ss -tlnp | grep 2222
   systemctl restart ssh
   ```
4. **Docker:**
   ```bash
   systemctl start docker
   cd /opt/sora_earth && docker compose up -d
   ```
5. **Cloudflare Tunnel:**
   ```bash
   systemctl restart cloudflared
   cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate
   ```
6. **MLflow:**
   ```bash
   docker start mlflow || docker run -d --name mlflow --restart unless-stopped \
     -p 127.0.0.1:5000:5000 \
     -v /opt/mlflow/db:/mlflow/db \
     -v /opt/mlflow/artifacts:/mlflow/artifacts \
     ghcr.io/mlflow/mlflow:v2.18.0 \
     mlflow server --host 0.0.0.0 --port 5000 \
     --backend-store-uri sqlite:////mlflow/db/mlflow.db \
     --artifacts-destination file:///mlflow/artifacts \
     --serve-artifacts
   ```
7. **Smoke-проверка:** curl 5 хостов из раздела 0.

---

imeweb FW: открыть `2222/tcp` временно
2. С Mac: `ssh -p 2222 root@109.73.194.26`
3. Восстанови Cloudflare → **закрой `2222/tcp` обратно**

---

## 8. Работа с MLflow с Mac

Однократно:
```bash
cd ~/sora_earth_ai_platform
python3 -m venv .venv
source .venv/bin/activate
pip install mlflow xgboost scikit-learn pandas numpy
```

Каждую сессию:
```bash
# Окно 1 — туннель (5555 чтобы не конфликтовать с macOS AirTunes на 5000)
ssh -N -L 5555:127.0.0.1:5000 sora-cf

# Окно 2 — работа
source .venv/bin/activate
export MLFLOW_TRACKING_URI=http://127.0.0.1:5555
python train_model_v2.py
```

UI: https://mlflow.sora-earth.ru (Cloudflare Access → email code)  
Experiment: `esg-success-classification-v2`  
Champion model: alias `@champion`, challenger: `@challenger`

---

## 9. Алиасы на Mac (`~/.zshrc`)

```bash
alias sora='ssh sora-cf'
alias sora-logs='ssh sora-cf "cd /opt/sora_earth && docker lared"'
alias sora-mlflow='ssh -N -L 5555:127.0.0.1:5000 sora-cf'
alias sora-health='for u in https://api.sora-earth.ru https://grafana.sora-earth.ru https://mlflow.sora-earth.ru; do echo "=== $u ==="; curl -sI "$u" | head -1; done'
```

---

## 10. Inventory сервисов на сервере

| Container | Image | Port (host) | Healthcheck |
|---|---|---|---|
| sora_earth-app-1 | sora_earth-app | 0.0.0.0:8000 | ✅ |
| sora_earth-postgres-1 | postgres:16-alpine | 127.0.0.1:5432 | ✅ |
| sora_earth-redis-1 | redis:7-alpine | 127.0.0.1:6379 | — |
| sora_earth-nginx-1 | nginx:alpine | 0.0.0.0:80 | — |
| sora_earth-prometheus-1 | prom/prometheus:latest | 127.0.0.1:9090 | — |
| sora_earth-grafana-1 | grafana/grafana:latest | 127.0.0.1:3000 | — |
| sora_earth-scheduler-1 | sora_earth-scheduler | — | ✅ |
| mlflow (standalone) | ghcr.io/mlflow/mlflow:v2.18.0 | 127.0.0.1:5000 | — |

DB tables (postgres, db `sora_earth`, user `sora`):
`alembic_version, batch_results, country_indicator_history, dataons_log, retrain_log`

---

## 11. Откат релиза с миграцией `f2c9a1d47b30`

`alembic downgrade` **откажется** — это сделано намеренно, а не сломалось.

Ревизия создаёт `batch_results`, `forecast_history`, `region_signals` и
`retrain_log` только там, где их нет. Отличить таблицы, которые создала она, от
тех, что раньше сделал `Base.metadata.create_all()`, она не может — а в этих
лежат данные. Поэтому вместо того, чтобы отмотать `alembic_version` и оставить
таблицы на месте (запись в БД говорила бы одно, схема — другое), она падает и
объясняет почему.

Откат делается вперёд или из бэкапа, в порядке предпочтения:

```bash
# 1. Откатить только приложение — обычный случай.
#    Ревизия лишь добавляет таблицы, поэтому предыдущая версия кода
#    работает на новой схеме без изменений.
docker compose -f docker-compose.prod.yml up -d --build backend   # с предыдущего тега

# 2. Forward-fix: выпустить ревизию, которая чинит проблему.
#    Аддитивную схему не нужно разматывать, чтобы исправить.

# 3. Восстановление из бэкапа, снятого ДО апгрейда.
#    См. docs/BACKUP_RESTORE.md.
```

**Снимать бэкап до апгрейда обязательно** — именно он делает третий вариант
возможным.

### DROP таблиц — не шаг отката

На любом развёртывании, существовавшем до этой ревизии, четыре таблицы создал
`Base.metadata.create_all()`, и в них могут лежать production-данные, которых
ревизия не касалась. Ровно поэтому `downgrade` отказывается, а не удаляет их за
вас.

Если убрать их действительно нужно — это отдельное решение, и начинается оно с
установления происхождения и содержимого:

```bash
# 1. Пусто ли? Все четыре, а не одна.
for t in batch_results forecast_history region_signals retrain_log; do
  docker compose -f docker-compose.prod.yml exec postgres \
    psql -U sora -d sora_earth -c "SELECT '$t', count(*) FROM public.$t"
done
```

**В PostgreSQL нет встроенной записи о времени создания таблицы.** Прежняя версия этого
runbook предлагала `pg_stat_get_last_analyze_time` — он отдаёт время последнего
`ANALYZE` и ничего не говорит ни о моменте создания таблицы, ни о том, какая
ревизия её создала. Происхождение может существовать — event trigger или таблица аудита DDL его
несли бы, — но лишь если на этом развёртывании их завели. Если нет, оно берётся
вне базы: из записи о развёртывании или журнала аудита с перечнем выполненных
ревизий. Если такой записи
для ровно этой схемы нет — **восстанавливайте из бэкапа, а не удаляйте**.

Только когда **все четыре** подтверждённо пусты **и** внешняя запись
подтверждает, что эта база выполняла создавшую их ревизию, удаление с последующим `alembic stamp e3f8a7c15d92` становится
разумным действием. Если хоть одно неизвестно — восстанавливайте из бэкапа.

Если ревизия отказывается на **апгрейде**, она сообщает, что эти таблицы уже
существуют в форме, расходящейся с моделями, и перечисляет каждое расхождение.
Это находка о состоянии базы, а не поломка миграции: см. issue #51.

---

## TL;DR

- Зайти: `ssh sora-cf` с Mac
- Если не пускает: Timeweb VNC → `systemctl restart ssh cloudflared docker`
- Всё наружу — через Cloudflare. Прямые IP — заблокированы Timeweb FW.
- Бэкапы конфигов: `/etc/ssh/sshd_config.bak.*`, `/etc/cloudflared/config.yml.bak.*`
- Champion ML model: MLflow alias `@champion` (Stacking v2, AUC 0.9875)
