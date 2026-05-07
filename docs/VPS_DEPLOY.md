# VPS_DEPLOY.md — SORA.Earth Production Deploy Runbook

**Цель:** развернуть SORA.Earth на Hetzner CCX13 (Ubuntu 24.04 LTS, Helsinki) от чистой машины до публичного `https://your-domain/health` за 30 минут.

**Предусловия:**
- ✅ Hetzner аккаунт верифицирован
- ✅ Сервер CCX13 создан, IP получен (далее `<SERVER_IP>`)
- ✅ SSH ключ `~/.ssh/id_ed25519` добавлен в Hetzner и привязан к серверу
- ✅ Домен куплен (например `sora.earth` или `sora-earth.ru`)
- ✅ Cloudflare аккаунт создан, домен добавлен в Cloudflare

---

## Phase 0 — Sanity check (1 мин)

С MacBook:

```bash
# 1. SSH ключ есть?
ls ~/.ssh/id_ed25519.pub && echo "✓ ssh ключ"

# 2. Ping сервера
ping -c 3 <SERVER_IP>

# 3. SSH-проверка (без захода)
ssh -o BatchMode=yes -o ConnectTimeout=5 root@<SERVER_IP> exit && echo "✓ ssh работает"
```

---

## Phase 1 — Bootstrap сервера (5 мин)

### 1.1 Первый вход

```bash
ssh root@<SERVER_IP>
# yes на fingerprint
```

### 1.2 Системное обновление + базовые пакеты

```bash
export DEBIAN_FRONTEND=noninteractive
apt update
apt -y upgrade
apt -y install \
    docker.io docker-compose-plugin \
    git curl wget htop ncdu jq \
    ufw fail2ban \
    nodejs npm \
    certbot

systemctl enable --now docker
docker --version
docker compose version
```

### 1.3 Firewall (ufw)

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP'
ufw allow 443/tcp comment 'HTTPS'
ufw --force enable
ufw status
```

### 1.4 Fail2ban (защита SSH от брутфорса)

```bash
cat > /etc/fail2ban/jail.local <<'EOF'
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
EOF

systemctl enable --now fail2ban
fail2ban-client status sshd
```

### 1.5 Non-root пользователь `sora`

```bash
adduser sora --disabled-password --gecos ""
usermod -aG docker,sudo sora

mkdir -p /home/sora/.ssh
cp /root/.ssh/authorized_keys /home/sora/.ssh/
chown -R sora:sora /home/sora/.ssh
chmod 700 /home/sora/.ssh
chmod 600 /home/sora/.ssh/authorized_keys

# Sudo без пароля для удобства (опционально)
echo "sora ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/sora
```

### 1.6 Отключить SSH по паролю + root-логин

```bash
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
systemctl restart ssh
```

### 1.7 Проверка с MacBook (НОВЫЙ терминал, не закрывая старый!)

```bash
ssh sora@<SERVER_IP> 'whoami && docker ps && sudo ufw status numbered'
```

Должно вернуть `sora`, пустой docker ps, ufw active.

✅ **Phase 1 done.** Старый терминал с root можно закрыть.

---

## Phase 2 — Deploy SORA.Earth (15 мин)

```bash
ssh sora@<SERVER_IP>
```

### 2.1 Клонирование репо

```bash
cd ~
git clone https://github.com/Evgeniy2001Poluhin/-SORA.Earth.git sora
cd sora
git status
```

### 2.2 Генерация .env.prod секретов

```bash
cp .env.prod.example .env.prod

# Сгенерим случайные секреты
PG_PASS=$(openssl rand -hex 24)
JWT_SEC=$(openssl rand -hex 32)
ADMIN_KEY=$(openssl rand -hex 24)
REDIS_PASS=$(openssl rand -hex 16)

cat <<EOF
=== СОХРАНИ В МЕНЕДЖЕРЕ ПАРОЛЕЙ ===
POSTGRES_PASSWORD=${PG_PASS}
JWT_SECRET=${JWT_SEC}
ADMIN_API_KEY=${ADMIN_KEY}
REDIS_PASSWORD=${REDIS_PASS}
====================================
EOF

# Подставить в .env.prod
sed -i "s|POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${PG_PASS}|" .env.prod
sed -i "s|JWT_SECRET=.*|JWT_SECRET=${JWT_SEC}|" .env.prod
sed -i "s|ADMIN_API_KEY=.*|ADMIN_API_KEY=${ADMIN_KEY}|" .env.prod
sed -i "s|REDIS_PASSWORD=.*|REDIS_PASSWORD=${REDIS_PASS}|" .env.prod

# Проверь, нет ли пропущенных <change-me> значений
grep -E "<.*>" .env.prod || echo "✓ нет плейсхолдеров"
chmod 600 .env.prod
```

### 2.3 Сборка фронта

```bash
cd web
npm ci
npm run build
ls dist/  # должен быть index.html + assets/
cd ..
```

### 2.4 Запуск стека

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml pull
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build

# Подождать 30 секунд пока поднимется postgres
sleep 30
docker compose -f docker-compose.prod.yml ps
```

### 2.5 Миграции базы

```bash
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head

# Сид-данные если есть
docker compose -f docker-compose.prod.yml exec backend python -m scripts.seed || true
```

### 2.6 Health check локально

```bash
curl -s http://localhost/health | jq .
curl -s http://localhost/api/health | jq .
docker compose -f docker-compose.prod.yml logs --tail=50 backend
```

Должно быть `{"status":"ok"}` и логи без ERROR.

✅ **Phase 2 done.** Стек крутится на сервере, доступен по IP.

---

## Phase 3 — TLS через Cloudflare (5 мин)

### 3.1 Cloudflare DNS

```
Cloudflare dashboard → твой домен → DNS → Add record:
  Type:    A
  Name:    @                     (или sora-earth)
  IPv4:    <SERVER_IP>
  Proxy:   ✓ Proxied (orange cloud)
  TTL:     Auto

  Type:    A
  Name:    www
  IPv4:    <SERVER_IP>
  Proxy:   ✓ Proxied
  TTL:     Auto
```

### 3.2 Cloudflare SSL/TLS режим

```
Cloudflare → SSL/TLS → Overview → Encryption mode:
  ⭕ Full (strict)        ← если у тебя на сервере есть Let's Encrypt
  ⭕ Full                 ← если self-signed серт
  🔘 Flexible             ← старт: Cloudflare ↔ Browser HTTPS, Cloudflare ↔ Сервер HTTP. ПРОСТЕЙШИЙ ВАРИАНТ.
```

**Старт с Flexible** — никаких сертификатов на сервере не нужно. Cloudflare сам делает HTTPS для пользователя, к серверу идёт по HTTP. Для дипломного демо — ОК.

**Позже апгрейдни до Full (strict)** через certbot:
```bash
sudo certbot --nginx -d sora.earth -d www.sora.earth
```

### 3.3 Always Use HTTPS

```
Cloudflare → SSL/TLS → Edge Certificates:
  ✓ Always Use HTTPS
  ✓ Automatic HTTPS Rewrites
  Minimum TLS Version: TLS 1.2
```

### 3.4 Smoke test

```bash
# С MacBook
curl -sI https://sora.earth/health
curl -s  https://sora.earth/health | jq .
curl -sI https://sora.earth/api/health
```

Должно быть `HTTP/2 200` + `{"status":"ok"}`.

✅ **Phase 3 done.** Публичный HTTPS работает.

---

## Phase 4 — Мониторинг и обслуживание

### 4.1 Логи

```bash
# Все контейнеры
docker compose -f docker-compose.prod.yml logs -f

# Только backend
docker compose -f docker-compose.prod.yml logs -f backend

# Размер логов
docker system df
```

### 4.2 Backup БД (cron, ежедневно в 3:00)

```bash
mkdir -p ~/backups
cat > ~/backup_db.sh <<'EOF'
#!/bin/bash
cd ~/sora
TS=$(date +%Y%m%d_%H%M%S)
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U sora sora_prod | gzip > ~/backups/sora_${TS}.sql.gz
# Хранить только последние 7 дней
find ~/backups -name "sora_*.sql.gz" -mtime +7 -delete
EOF
chmod +x ~/backup_db.sh

(crontab -l 2>/dev/null; echo "0 3 * * * /home/sora/backup_db.sh") | crontab -
crontab -l
```

### 4.3 Полезные команды

```bash
# Рестарт стека
cd ~/sora && docker compose -f docker-compose.prod.yml restart

# Обновить код и пересобрать
cd ~/sora
git pull
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head

# Чистка старых docker-образов
docker system prune -af --volumes

# Использование ресурсов
docker stats --no-stream
free -h
df -h /
```

---

## Phase 5 — Rollback при беде

```bash
cd ~/sora

# Откат коммита
git log --oneline -10
git checkout <previous-good-sha>

# Откат стека
docker compose -f docker-compose.prod.yml down
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build

# Восстановление БД из бэкапа
gunzip -c ~/backups/sora_20260501_030000.sql.gz | \
  docker compose -f docker-compose.prod.yml exec -T postgres \
  psql -U sora sora_prod
```

---

## Чек-лист

- [ ] Phase 0 — SSH работает, IP пингуется
- [ ] Phase 1 — Сервер обновлён, Docker, ufw, fail2ban, sora user
- [ ] Phase 2 — Репо склонирован, .env.prod заполнен, стек запущен, миграции прошли
- [ ] Phase 3 — Cloudflare DNS, SSL Flexible, https://sora.earth/health = 200
- [ ] Phase 4 — Бэкап cron настроен
- [ ] Tag и пуш: `git tag v1.0.0-prod && git push --tags`
- [ ] Demo URL отправлен комиссии/инвестору 🎉
