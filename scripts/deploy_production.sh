#!/usr/bin/env bash
# The only supported way to deploy production, and the only supported way to
# roll it back.
#
# It exists because every incident this month came from deploying by hand: the
# stack was brought up with docker-compose.yml, so the container serving the
# public site was a dev one carrying SORA_OFFLINE=1 and running sixteen-day-old
# code, publishing port 8000 to the internet; rebuilding `backend` changed
# nothing because nothing routed to it; and `curl /health` returned 200
# throughout, which was read as confirmation that a deployment had happened.
#
# Every check below corresponds to something that actually went wrong.
#
# Usage:
#   deploy_production.sh                 deploy origin/main
#   deploy_production.sh --rollback SHA  redeploy an earlier merged commit
#
# Nothing here is a substitute for a host firewall. There is none.
set -euo pipefail

REPO="${DEPLOY_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
COMPOSE="${COMPOSE_FILE:-$REPO/docker-compose.prod.yml}"
# Fixed, never derived from the directory name. Deriving it means a renamed or
# copied checkout silently starts a second project and leaves the running one
# unmanaged -- the same class of mistake as deploying with the wrong compose
# file. It must keep matching the existing containers.
PROJECT="${COMPOSE_PROJECT_NAME:-sora_earth_ai_platform}"
SITE="${SITE_URL:-https://sora-earth.online}"
MANIFEST_DIR="${MANIFEST_DIR:-$REPO/docs/maximum/evidence/deployments}"
DC=(docker compose -p "$PROJECT" -f "$COMPOSE")

cd "$REPO"

fail() { echo "REFUSED: $*" >&2; exit 1; }
step() { echo; echo "== $* =="; }

MODE=deploy
TARGET=""
while [ $# -gt 0 ]; do
    case "$1" in
        --rollback)
            MODE=rollback
            [ $# -ge 2 ] || fail "--rollback needs a commit"
            TARGET="$2"; shift 2 ;;
        *) fail "unknown argument '$1'" ;;
    esac
done

# ---------------------------------------------------------------- preconditions

step "what may be deployed"

# A local edit is exactly how the nginx upstream came to differ from the
# repository for weeks with nobody able to tell. Required in both modes.
DIRTY="$(git status --porcelain)"
[ -z "$DIRTY" ] || { echo "$DIRTY" >&2; fail "working tree is not clean"; }

git fetch --quiet origin main
ORIGIN_MAIN="$(git rev-parse origin/main)"

if [ "$MODE" = deploy ]; then
    BRANCH="$(git rev-parse --abbrev-ref HEAD)"
    [ "$BRANCH" = "main" ] || fail "on branch '$BRANCH'; production is deployed from main only"
    HEAD_SHA="$(git rev-parse HEAD)"
    [ "$HEAD_SHA" = "$ORIGIN_MAIN" ] \
        || fail "HEAD ($HEAD_SHA) is not origin/main ($ORIGIN_MAIN); merge first, then deploy"
    TARGET="$HEAD_SHA"
    echo "  deploying origin/main @ ${TARGET:0:12}"
else
    # An earlier version required HEAD == origin/main unconditionally, which
    # forbade rolling back to the last good commit: the guard would have blocked
    # recovery during exactly the incident it exists to prevent.
    #
    # A rollback target may be any ancestor of origin/main -- code that was
    # reviewed and merged, only not the newest. Anything else is refused, so
    # that "roll back" cannot become a way to ship unreviewed code under
    # pressure.
    [ -n "$TARGET" ] || fail "--rollback needs a commit"
    git rev-parse --verify --quiet "$TARGET^{commit}" >/dev/null \
        || fail "'$TARGET' is not a commit in this repository"
    TARGET="$(git rev-parse "$TARGET^{commit}")"
    git merge-base --is-ancestor "$TARGET" "$ORIGIN_MAIN" \
        || fail "$TARGET is not an ancestor of origin/main; only merged code may be deployed"
    [ "$TARGET" != "$ORIGIN_MAIN" ] \
        || fail "$TARGET is origin/main; use a plain deploy rather than --rollback"
    echo "  rolling back to ${TARGET:0:12}, an ancestor of origin/main"
    git checkout --quiet --detach "$TARGET"
fi

# ------------------------------------------------------------ the state before

# Captured before anything changes, so a rollback has something to aim at. The
# manifest of the run that breaks production is the only record of what it
# replaced.
step "recording the state being replaced"
PREV_FILE="$(mktemp)"
{
    echo "previous_commit $(git rev-parse HEAD)"
    echo "previous_images:"
    docker ps -a --filter "label=com.docker.compose.project=$PROJECT" \
        --format '{{.Label "com.docker.compose.service"}} {{.Image}} {{.ID}}' \
        | sort | sed 's/^/  /' || true
} > "$PREV_FILE"
sed 's/^/  /' "$PREV_FILE"

# Declared services come from `docker compose config`, not from parsing the YAML
# here: it resolves interpolation, overrides and extends, so what is checked is
# what compose will act on rather than what the file appears to say.
DECLARED="$("${DC[@]}" config --services | sort | tr '\n' ' ')"
echo "  declared services: $DECLARED"

# ------------------------------------------------------------------- deployment

step "deploying"
"${DC[@]}" up -d --build --remove-orphans

# nginx is recreated, not restarted. Its configuration is a bind-mounted single
# file, so the container stays pinned to the inode that existed when it was
# created; a git pull replaces the file and the container keeps reading the old
# one. Observed, with `nginx -s reload` re-reading the stale copy.
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

# And the converse. Checking only that the declared services are up is what let
# a dev `app` container serve the site for sixteen days beside them: it was
# never missing, it was extra. --remove-orphans above should have taken it, and
# this is what proves it did.
EXTRA=""
while IFS= read -r svc; do
    [ -n "$svc" ] || continue
    case " $DECLARED " in
        *" $svc "*) ;;
        *) EXTRA="$EXTRA $svc" ;;
    esac
done < <(docker ps --filter "label=com.docker.compose.project=$PROJECT" \
             --format '{{.Label "com.docker.compose.service"}}' | sort -u)
[ -z "$EXTRA" ] || fail "containers survive that this compose file does not declare:$EXTRA"
echo "  running services are exactly the declared ones: $RUNNING"

# Published ports are read from the running containers, not from the compose
# file. The file says what was intended; docker ps says what is reachable, and
# only the second is the security property -- a container started earlier from
# another file publishes ports this file never mentioned.
#
# Checked as a property, not a list of known-bad strings: anything reachable
# off-host other than 80 and 443 is refused, whatever form it takes.
BAD=""
while IFS='|' read -r name ports; do
    [ -n "$ports" ] || continue
    while IFS= read -r p; do
        # "0.0.0.0:80->80/tcp" is published; "8000/tcp" is not.
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

# The configuration nginx holds must be the repository's. The mount cannot be
# trusted to have followed the file -- that is why nginx is force-recreated
# above, and this is what proves it worked.
REPO_SUM="$(sha256sum "$REPO/nginx/nginx.conf" | awk '{print $1}')"
CTR_SUM="$("${DC[@]}" exec -T nginx sha256sum /etc/nginx/nginx.conf | awk '{print $1}')"
[ "$REPO_SUM" = "$CTR_SUM" ] \
    || fail "nginx serves a configuration that is not the repository's (repo ${REPO_SUM:0:12}, container ${CTR_SUM:0:12})"
echo "  nginx configuration matches the repository (${REPO_SUM:0:12})"

"${DC[@]}" exec -T nginx nginx -t >/dev/null 2>&1 || fail "nginx rejects its own configuration"
echo "  nginx accepts the configuration"

UPSTREAM="$("${DC[@]}" exec -T nginx sh -c "grep -A2 'upstream sora_backend' /etc/nginx/nginx.conf | grep server" | tr -d ' \r')"
[ "$UPSTREAM" = "serverbackend:8000;" ] || fail "nginx proxies to '$UPSTREAM'; expected backend:8000"
echo "  upstream is backend:8000"

step "certificates"

# The store nginx reads must be the store certbot renews. The production compose
# file in this repository mounted ./certs -- a copy three weeks behind
# /etc/letsencrypt and on no renewal path. It would have served a valid
# certificate right up until it quietly did not.
NGINX_CID="$("${DC[@]}" ps -q nginx)"
CERT_SRC="$(docker inspect "$NGINX_CID" \
    -f '{{range .Mounts}}{{if eq .Destination "/etc/letsencrypt"}}{{.Source}}{{end}}{{end}}')"
[ "$CERT_SRC" = "/etc/letsencrypt" ] \
    || fail "nginx reads certificates from '${CERT_SRC:-nowhere}', not /etc/letsencrypt where they are renewed"
echo "  certificates come from /etc/letsencrypt"

# A renewal that never reloads nginx serves the old certificate until something
# restarts it. Having the mechanism is not the same as having the reload, so
# both are named. Absence is a warning rather than a refusal: it does not make
# this deployment wrong, it makes a future morning wrong.
if systemctl list-timers 'certbot*' --no-pager 2>/dev/null | grep -q certbot \
   || crontab -l 2>/dev/null | grep -q certbot; then
    echo "  a certbot renewal mechanism is present"
    if find /etc/letsencrypt/renewal-hooks/deploy -type f 2>/dev/null | grep -q .; then
        echo "  renewal has deploy hooks"
    else
        echo "  WARNING: no deploy hook in /etc/letsencrypt/renewal-hooks/deploy/;"
        echo "           a renewed certificate will not reach nginx until it restarts"
    fi
else
    echo "  WARNING: no certbot timer or cron entry found; certificates may not renew"
fi

step "the site answers"
for path in /health /api/v1/health /; do
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 20 "$SITE$path" || echo 000)"
    [ "$code" = "200" ] || fail "$SITE$path returned $code"
    printf '  %-18s %s\n' "$path" "$code"
done

# ------------------------------------------------------------------- the record

step "recording what was deployed"
mkdir -p "$MANIFEST_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
MANIFEST="$MANIFEST_DIR/$STAMP.txt"
{
    echo "deployed_at    $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "mode           $MODE"
    echo "commit         $TARGET"
    echo "origin_main    $ORIGIN_MAIN"
    echo "compose_file   $(basename "$COMPOSE")"
    echo "project        $PROJECT"
    echo "nginx_config   sha256:$REPO_SUM"
    echo "nginx_upstream $UPSTREAM"
    echo
    echo "images now:"
    for svc in $DECLARED; do
        cid="$("${DC[@]}" ps -q "$svc" 2>/dev/null | head -1)"
        [ -n "$cid" ] || continue
        printf '  %-12s %s\n' "$svc" "$(docker inspect -f '{{.Image}}' "$cid")"
    done
    echo
    echo "published ports:"
    docker ps --filter "label=com.docker.compose.project=$PROJECT" \
        --format '  {{.Names}} {{.Ports}}' | grep -- '->' || echo "  (none)"
    echo
    echo "--- state replaced by this run ---"
    cat "$PREV_FILE"
} > "$MANIFEST"
rm -f "$PREV_FILE"

cat "$MANIFEST"
echo
echo "manifest: $MANIFEST"
echo "to undo:  $0 --rollback <previous_commit from the section above>"
