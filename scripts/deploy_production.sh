#!/usr/bin/env bash
# The only supported way to deploy production.
#
# It exists because every incident this month came from deploying by hand:
# the stack was brought up with docker-compose.yml, so the container serving the
# public site was a dev one carrying SORA_OFFLINE=1 and running sixteen-day-old
# code, publishing port 8000 to the internet; rebuilding `backend` changed
# nothing because nothing routed to it; and `curl /health` returned 200
# throughout, which was read as confirmation that a deployment had happened.
#
# So this refuses more than it does. Every check below corresponds to something
# that actually went wrong, and each one names it.
#
# Nothing here is a substitute for a host firewall. There is none.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="$REPO/docker-compose.prod.yml"
MANIFEST_DIR="$REPO/docs/maximum/evidence/deployments"
DC=(docker compose -f "$COMPOSE")

cd "$REPO"

fail() { echo "REFUSED: $*" >&2; exit 1; }
step() { echo; echo "== $* =="; }

# ---------------------------------------------------------------- preconditions

step "the tree must be main, clean, and current"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$BRANCH" = "main" ] || fail "on branch '$BRANCH'; production is deployed from main only"

# Deliberately not `git status --porcelain | grep -v` with an ignore list. A
# local edit is exactly how the nginx upstream came to differ from the
# repository for weeks without anyone being able to tell.
DIRTY="$(git status --porcelain)"
[ -z "$DIRTY" ] || { echo "$DIRTY" >&2; fail "working tree is not clean"; }

git fetch --quiet origin main
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse origin/main)"
[ "$LOCAL" = "$REMOTE" ] || fail "HEAD ($LOCAL) is not origin/main ($REMOTE); merge first, then deploy"

echo "  main @ ${LOCAL:0:12}, clean, matching origin"

step "no container may come from a file other than the production one"

DECLARED="$(python3 -c "
import yaml
print(' '.join(yaml.safe_load(open('$COMPOSE'))['services']))
")"
echo "  declared services: $DECLARED"

# Containers in this project whose service is not declared here came from
# another compose file -- which is how a dev 'app' container ended up serving
# the public site. --remove-orphans below removes them, but they are named first
# so the removal is visible rather than silent.
PROJECT="$(basename "$REPO")"
STRAY=""
while IFS= read -r line; do
    [ -n "$line" ] || continue
    svc="${line%%|*}"
    case " $DECLARED " in
        *" $svc "*) ;;
        *) STRAY="$STRAY $svc" ;;
    esac
done < <(docker ps -a --filter "label=com.docker.compose.project=$PROJECT" \
             --format '{{.Label "com.docker.compose.service"}}|{{.Names}}' 2>/dev/null || true)

[ -z "$STRAY" ] || echo "  containers from another compose file will be removed:$STRAY"

# ------------------------------------------------------------------- deployment

step "deploying"
"${DC[@]}" up -d --build --remove-orphans

# nginx is recreated rather than restarted. Its configuration is a bind-mounted
# single file, so the container is pinned to the inode that existed when it was
# created; a `git pull` replaces the file and the container keeps reading the
# old one. This was observed: an edit to the host file was invisible inside the
# container and `nginx -s reload` re-read the stale copy.
"${DC[@]}" up -d --force-recreate nginx

# ---------------------------------------------------------------- verification

step "verifying what is actually running"

RUNNING="$("${DC[@]}" ps --format '{{.Service}}' | sort | tr '\n' ' ')"
for svc in $DECLARED; do
    case " $RUNNING " in
        *" $svc "*) ;;
        *) fail "declared service '$svc' is not running (running: $RUNNING)" ;;
    esac
done
echo "  all declared services are up: $RUNNING"

# Published ports, as a property rather than a list of known-bad examples.
# Anything reachable off-host other than 80 and 443 is refused, whatever form it
# takes -- "9000:8000", "0.0.0.0:9000:8000" and a bare "8000" all publish
# everywhere and all have to fail the same way.
BAD=""
while IFS='|' read -r name ports; do
    [ -n "$ports" ] || continue
    # docker ps renders a published port as "0.0.0.0:80->80/tcp" or
    # "127.0.0.1:9090->9090/tcp"; an unpublished one has no "->" and is ignored.
    # Verified against every form, including ":::8000->8000/tcp", which is
    # published on all IPv6 interfaces and must be refused like its IPv4 twin.
    while IFS= read -r p; do
        case "$p" in *"->"*) ;; *) continue ;; esac
        hostpart="${p%%->*}"
        ip="${hostpart%:*}"
        hostport="${hostpart##*:}"
        case "$ip" in 127.0.0.1|::1|"[::1]"|"") continue ;; esac
        case "$hostport" in 80|443) continue ;; esac
        BAD="$BAD
  $name publishes $p off-host"
    done <<< "${ports//, /$'\n'}"
done < <(docker ps --filter "label=com.docker.compose.project=$PROJECT" \
             --format '{{.Names}}|{{.Ports}}')

if [ -n "$BAD" ]; then
    echo "$BAD" >&2
    fail "a port other than 80/443 is reachable off-host; only nginx should be"
fi
echo "  no port other than 80/443 is reachable off-host"

# The container's configuration must be the repository's. The mount cannot be
# trusted to have followed the file, which is the whole reason nginx is
# force-recreated above; this is what proves it worked.
REPO_SUM="$(sha256sum "$REPO/nginx/nginx.conf" | awk '{print $1}')"
CTR_SUM="$("${DC[@]}" exec -T nginx sha256sum /etc/nginx/nginx.conf | awk '{print $1}')"
[ "$REPO_SUM" = "$CTR_SUM" ] \
    || fail "nginx is serving a configuration that is not the repository's (repo ${REPO_SUM:0:12}, container ${CTR_SUM:0:12})"
echo "  nginx configuration matches the repository (${REPO_SUM:0:12})"

UPSTREAM="$("${DC[@]}" exec -T nginx sh -c "grep -A2 'upstream sora_backend' /etc/nginx/nginx.conf | grep server" | tr -d ' \r')"
case "$UPSTREAM" in
    serverbackend:8000\;) ;;
    *) fail "nginx proxies to '$UPSTREAM'; expected backend:8000" ;;
esac
echo "  upstream is backend:8000"

step "the site answers"
for path in /health /api/v1/health /; do
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 20 "https://sora-earth.online$path" || echo 000)"
    [ "$code" = "200" ] || fail "https://sora-earth.online$path returned $code"
    printf '  %-18s %s\n' "$path" "$code"
done

# ------------------------------------------------------------------- the record

step "recording what was deployed"
mkdir -p "$MANIFEST_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
MANIFEST="$MANIFEST_DIR/$STAMP.txt"
{
    echo "deployed_at   $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "commit        $LOCAL"
    echo "compose_file  docker-compose.prod.yml"
    echo "nginx_config  sha256:$REPO_SUM"
    echo "nginx_upstream $UPSTREAM"
    echo
    echo "images:"
    for svc in $DECLARED; do
        cid="$("${DC[@]}" ps -q "$svc" 2>/dev/null | head -1)"
        [ -n "$cid" ] || continue
        printf '  %-12s %s\n' "$svc" "$(docker inspect -f '{{.Image}}' "$cid")"
    done
    echo
    echo "published ports:"
    docker ps --filter "label=com.docker.compose.project=$PROJECT" \
        --format '  {{.Names}} {{.Ports}}' | grep -- '->' || echo "  (none)"
} > "$MANIFEST"

cat "$MANIFEST"
echo
echo "manifest: $MANIFEST"
