# How production actually serves traffic

**Established:** 2026-07-31, read-only. Written because not knowing this produced
three wrong conclusions in a row, in opposite directions, within one hour.

## The routing

```
nginx.conf:
    upstream sora_backend {
        server app:8000;
    }
```

**All traffic goes to the `app` container.** The `backend` container runs, is
healthy, responds identically on `/health`, and serves nothing.

Both are defined in `docker-compose.prod.yml`. Only one is wired to nginx.

## Why `app` being "13 days old" means nothing

`docker compose ps` shows `app` as `Up 13 days`. That is the **process**, not the
code. `app` mounts the project directory as a volume rather than baking it into
the image, so `git pull` on the host updates what the container runs
immediately — no rebuild, no restart, and the uptime figure never moves.

Verified: `assert_schema_ready` — added in PR #53, merged the same day — is
present in `/app/app/main.py` inside both containers.

```bash
docker compose -f docker-compose.prod.yml exec -T app \
  grep -c 'assert_schema_ready' /app/app/main.py     # 2
```

## The three wrong conclusions this caused

Recorded because the reasoning is the useful part, not the outcome.

1. **"M0 is deployed"** — after rebuilding `backend`. Never checked which
   container nginx points at. The migrations *were* applied, because those reach
   the shared database, but the claim about the serving code was unfounded.
2. **"Traffic goes to 13-day-old code"** — after reading the nginx upstream.
   Inferred age of code from age of process.
3. Both were corrected only by looking at the file inside the container, which is
   the one thing that answers the question directly.

Each step inferred from an indirect signal — the container I happened to rebuild,
then its uptime — rather than from content. The signal was available in one
`grep` throughout.

## Practical consequences

**Deploying application code needs no rebuild of `app`.** `git pull` is
sufficient, because of the volume mount. Rebuilding `backend` changes nothing
that users see.

**The scheduler is different.** It has no such mount and does need
`up -d --build scheduler`, which is why the PR #55 fix required one.

**`backend` is an unused duplicate.** Not broken — but it is what made this
confusing, and it will confuse the next person. Either remove it from the compose
file or move the nginx upstream to it and retire `app`. That is an architectural
decision and is not urgent.

## How to check this rather than assume it

```bash
grep -A2 'upstream' nginx/nginx.conf                          # who receives traffic
docker compose -f docker-compose.prod.yml config | grep -A5 'volumes'  # mounted or baked
docker compose -f docker-compose.prod.yml exec -T <svc> \
  grep -c '<symbol added in a known PR>' /app/app/main.py     # what code is actually there
```

The third is the only one that answers "is this current". The first two explain
why, and uptime answers nothing at all.
