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
# Outside the checkout, deliberately. The default was inside it, which made the
# guard refuse its own second run: it writes a manifest, the manifest dirties the
# tree, and the next deployment is declined for an unclean tree. The behavioural
# tests missed it because every one of them pointed MANIFEST_DIR at a sandbox --
# the setup supplied the condition under test and made it unobservable.
#
# .gitignore would have hidden it rather than fixed it. Deployment evidence is
# operational state, not source, and does not belong in a working copy at all.
MANIFEST_DIR="${MANIFEST_DIR:-/var/lib/sora/deployments}"
HEALTH_ATTEMPTS="${HEALTH_ATTEMPTS:-5}"
HEALTH_DELAY="${HEALTH_DELAY:-3}"
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

# ---------------------------------------------------------------- serialisation

# One deployment at a time.
#
# Two runs sharing a second produced the same manifest name and one silently
# replaced the other, which breaks the rollback chain: the record of what the
# survivor replaced is gone. But the collision was the symptom. Two concurrent
# runs also race on `docker compose up`, on the nginx recreation, and on the
# checkout itself during a rollback.
#
# Contention refuses rather than skipping. A backup that skips because another
# is running has lost nothing; a deployment that skips has silently not happened
# while its operator believes it did.
#
# -E 75 so contention is distinguishable from a broken lock. Without it a missing
# directory or a permissions fault reads as "someone else is deploying".
# In a directory no one but its owner can write to, rather than the shared one.
#
# /var/lock is /run/lock, mode 1777. The sticky bit stops another user deleting
# root's lock file, which is what I checked and reported as sufficient. It is
# not: nothing stops them creating the file first under its predictable name, so
# a deployment can be blocked at will -- and nothing stops them putting a symlink
# there, which `exec 9>` would follow and truncate as root.
#
# infra/tmpfiles.d/sora.conf recreates the directory on boot, because /run is a
# tmpfs and does not survive one.
LOCK_DIR="${DEPLOY_LOCK_DIR:-/run/sora}"
LOCK_FILE="${DEPLOY_LOCK:-$LOCK_DIR/deploy.lock}"
# Created only when absent, and never re-moded. An unconditional
# `install -d -m 0750` made the assertions below unreachable: whatever state the
# directory was in, it was 0750 by the time anything looked, and five
# deliberately unsafe modes passed. Repairing it would be too late regardless --
# a symlink planted while it was writable is already inside.
#
# No -o/-g here. Forcing root ownership fails outright for a non-root caller,
# which is how CI runs: every deployment test failed there while all 77 passed in
# a root container. The ownership property is asserted below instead, against
# whoever is actually running, which is the honest form of it.
if [ ! -d "$LOCK_DIR" ]; then
    install -d -m 0750 "$LOCK_DIR" 2>/dev/null || fail "cannot create $LOCK_DIR"
fi

# A real directory. -d follows symlinks, so a link pointing at somewhere
# writable satisfies it while leaving the lock somewhere else entirely.
[ ! -L "$LOCK_DIR" ] || fail "$LOCK_DIR is a symlink; the lock must sit in a real directory"
[ -d "$LOCK_DIR" ]   || fail "$LOCK_DIR is not a directory"

# Owned by whoever is deploying -- root in production, the runner in CI. Stated
# this way rather than "UID 0" because a check that only holds as root is one
# that never runs anywhere it could be tested, and the property that matters is
# that nobody *else* controls the directory.
LOCK_DIR_UID="$(stat -c '%u' "$LOCK_DIR")"
[ "$LOCK_DIR_UID" = "$(id -u)" ] \
    || fail "$LOCK_DIR is owned by uid $LOCK_DIR_UID, not by the deploying user ($(id -u))"

# And writable by nobody else. Not "only root can write" -- 0755 and 0711 are
# perfectly safe from planting, and describing them as root-only would be wrong.
# The property is the absence of group and other write bits.
LOCK_DIR_MODE="$(stat -c '%a' "$LOCK_DIR")"
LOCK_DIR_GRP="${LOCK_DIR_MODE: -2:1}"
LOCK_DIR_OTH="${LOCK_DIR_MODE: -1}"
if [ $(( LOCK_DIR_GRP & 2 )) -ne 0 ] || [ $(( LOCK_DIR_OTH & 2 )) -ne 0 ]; then
    fail "$LOCK_DIR is writable by group or others (mode $LOCK_DIR_MODE); the lock can be planted or symlinked"
fi

exec 9>"$LOCK_FILE" || fail "cannot open lock $LOCK_FILE"
lock_rc=0
flock -n -E 75 9 || lock_rc=$?
case $lock_rc in
    0)  ;;
    75) fail "another deployment holds $LOCK_FILE; wait for it to finish" ;;
    *)  fail "flock failed with status $lock_rc" ;;
esac

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

# From the last published manifest, not from git HEAD.
#
# `git rev-parse HEAD` was wrong in both modes: on a deploy HEAD is already the
# target, and on a rollback the checkout has happened by this point. It recorded
# the commit being deployed as the commit being replaced, so the one field a
# rollback needs always pointed at the wrong place -- and pointed there
# confidently.
#
# The last manifest is the only record of what is actually running, which is why
# a new one is published solely after the smoke checks pass: an aborted run must
# not overwrite the state it failed to replace.
# Created here rather than at publication time: under `set -o pipefail` a find
# over a directory that does not exist fails the whole pipeline, which under
# `set -e` ended the run before it began -- on the very first deployment, the one
# case where there is nothing to read.
mkdir -p "$MANIFEST_DIR"
LATEST="$MANIFEST_DIR/latest"

# Followed through an explicit pointer, never by sorting filenames.
#
# Picking the newest by name was wrong twice over. It assumed the clock supplies
# ordering, and the collision suffix broke even that: '-' (0x2D) sorts before
# '.' (0x2E), so "<stamp>-1.txt" comes out older than "<stamp>.txt" and the run
# after a collision read the file it had just been careful not to overwrite. The
# fix for the collision broke the chain it existed to protect, and the test
# missed it because it asserted the new file existed rather than that the next
# run would find it.
#
# `latest` is switched atomically after the smoke checks pass, so ordering does
# not depend on names, on the clock, or on how a shell sorts punctuation.
if [ -L "$LATEST" ] || [ -e "$LATEST" ]; then
    PREV_MANIFEST="$MANIFEST_DIR/$(readlink "$LATEST")"
    PREV_COMMIT="$(awk '/^commit /{print $2}' "$PREV_MANIFEST" 2>/dev/null || true)"
    echo "  previously deployed: ${PREV_COMMIT:-unrecorded} (from $(basename "$PREV_MANIFEST"))"
else
    PREV_COMMIT=""
    echo "  no previous deployment recorded in $MANIFEST_DIR"
fi

PREV_IMAGES="$(docker ps -a --filter "label=com.docker.compose.project=$PROJECT" \
    --format '{{.Label "com.docker.compose.service"}} {{.Image}} {{.ID}}' | sort || true)"

# Declared services come from `docker compose config`, not from parsing the YAML
# here: it resolves interpolation, overrides and extends, so what is checked is
# what compose will act on rather than what the file appears to say.
#
# Held in an array. As a space-joined string every use needed unquoted expansion,
# and a service name with a space in it would have split into two names that are
# each "not running" -- a refusal with a nonsense reason.
mapfile -t DECLARED < <("${DC[@]}" config --services | sort)
[ "${#DECLARED[@]}" -gt 0 ] || fail "$COMPOSE declares no services"
echo "  declared services: ${DECLARED[*]}"

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
for svc in "${DECLARED[@]}"; do
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
    case " ${DECLARED[*]} " in
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

# `code="$(curl ... || echo 000)"` looked defensive and was not: on a connection
# failure curl prints its own "000" and exits non-zero, so the fallback appended
# a second one and the variable became "000000" -- never equal to 200, so the
# refusal still happened, but the message reported a status that does not exist.
# Captured explicitly instead.
http_code() {
    local code
    if ! code="$(curl -s -o /dev/null -w '%{http_code}' -m 20 "$1" 2>/dev/null)"; then
        code=000
    fi
    printf '%s' "$code"
}

# Bounded retries. nginx has just been recreated and the backend may still be
# accepting its first connections, so one immediate failure means little; five
# in a row means the deployment is broken. Without a limit this would hang
# instead of failing, which is worse than either.
for path in /health /api/v1/health /; do
    code=000
    attempt=1
    while [ "$attempt" -le "$HEALTH_ATTEMPTS" ]; do
        code="$(http_code "$SITE$path")"
        if [ "$code" = "200" ]; then break; fi
        if [ "$attempt" -lt "$HEALTH_ATTEMPTS" ]; then
            echo "  $path returned $code, retrying ($attempt/$HEALTH_ATTEMPTS)"
            sleep "$HEALTH_DELAY"
        fi
        attempt=$((attempt + 1))
    done
    [ "$code" = "200" ] || fail "$SITE$path returned $code after $HEALTH_ATTEMPTS attempts"
    printf '  %-18s %s\n' "$path" "$code"
done

# ------------------------------------------------------------------- the record

step "recording what was deployed"

# Published only here, and atomically.
#
# Everything above can refuse, and every refusal exits before this line, so a run
# that failed leaves the previous manifest as the newest -- which is correct,
# because the previous deployment is still what is serving. A manifest written
# earlier, or written in pieces, would name a state that was never reached and
# would then be read as the rollback target by the next run.
mkdir -p "$MANIFEST_DIR"
# Unique by construction rather than by hoping the clock has moved. The name
# carries a timestamp because a human reading the directory wants one, but
# nothing depends on it: mktemp guarantees the file is new, and `latest` decides
# which one is current.
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
MANIFEST="$(mktemp "$MANIFEST_DIR/$STAMP-XXXXXX.txt")"
TMP_MANIFEST="$MANIFEST.tmp"
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
    for svc in "${DECLARED[@]}"; do
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
    echo "previous_commit ${PREV_COMMIT:-none-recorded}"
    echo "previous_images:"
    printf '%s\n' "$PREV_IMAGES" | sed 's/^/  /'
} > "$TMP_MANIFEST"
mv "$TMP_MANIFEST" "$MANIFEST"

# The pointer moves last, and atomically. Everything above can refuse, and every
# refusal exits before this line, so a run that failed leaves `latest` naming the
# deployment that is still serving -- which is what the next run must roll back
# to. `ln -sfn` alone is not atomic; the rename is.
ln -sfn "$(basename "$MANIFEST")" "$MANIFEST_DIR/.latest.$$"
mv -Tf "$MANIFEST_DIR/.latest.$$" "$LATEST"

cat "$MANIFEST"
echo
echo "manifest: $MANIFEST"
if [ -n "$PREV_COMMIT" ]; then
    echo "to undo:  $0 --rollback $PREV_COMMIT"
else
    echo "to undo:  no previous deployment is recorded; nothing to roll back to"
fi
