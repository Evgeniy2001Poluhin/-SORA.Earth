# Deployment — SORA.Earth Production

## Quick start (single VPS)

```bash
git clone <repo> sora_earth_ai_platform
cd sora_earth_ai_platform
cp .env.prod.example .env.prod
# edit .env.prod — set strong POSTGRES_PASSWORD, JWT_SECRET, ADMIN_API_KEY

docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --buiinitial DB migration
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head
```

Service map:

| Service   | Port | Purpose                               |
|-----------|------|---------------------------------------|
| nginx     | 80/443 | TLS termination + reverse proxy     |
| backend   | 8000 (internal) | FastAPI on gunicorn (4 workers) |
| pgbouncer | 5432 (internal) | Transaction pool, 25 conn       |
| postgres  | 5432 (internal) | Primary DB, persistent volume   |
| redis     | 6379 (internal) | SHAP cache, predictions cache   |

## TLS

Two options:

1. **Caddy / Cloudflare Tunnel** — point at `http://server:80`, get TLS automatically.
2. **Let's Encrypt locally**:
   ```bash
   apt install certbot
   certbot certonly --standalone -d your-domain.com
   cp /etc/letsencrypt/live/your-domain.com/fullchain.pem certs/
   cp /etc/letsencrypt/live/your-domain.com/privkey.pem certs/
   ```
   Then uncomment the HTTPS block in `nginx.conf` and `docker compose restart nginx`.

## Update
```bash
git pull
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build backend nginx
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head
```

## Health checks

- `curl https://your-domain.com/health` → `{"status":"ok"}`
- `docker compose -f docker-compose.prod.yml ps` — all services `healthy`
- `docker compose -f docker-compose.prod.yml logs -f backend`

## Scaling

- `gunicorn -w 4` per container; bump `--scale backend=N` for horizontal.
- `pgbouncer` already pools — safe to add more backend workers.
- Redis is shared cache; no sharding needed at MVP scale.


## Build flow

Frontend is built locally before `docker compose build` to avoid native-deps
issues with Vite/Rolldown inside Alpine containers and keep the final image slim.

```bash
cd web && npm ci && npm run build && cd ..
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head
curl http://localhost/health
```

The SPA lands in `app/static/spa/` and is copied via `COPY app/ ./app/`.
