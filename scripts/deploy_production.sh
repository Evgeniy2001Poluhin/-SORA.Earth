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

# Which phase the run is in, so a refusal knows whether anything needs undoing.
# `none` until the snapshot is taken, `mutating` from the moment `up` is issued.
PHASE=none
# Guards against re-entering the rollback from a signal raised while the
# rollback itself is running -- it issues `up`, which takes time, and a second
# INT during it would otherwise start a second rollback on top of the first.
ABORTING=0
# Container ids of this project before mutation, so a rollback can tell what
# this run created from what was already there. Stopping "anything unexpected"
# would reach containers this deployment does not own.
PRE_CIDS=""

# Every refusal goes through here, and after mutation begins it does not simply
# exit. Eight checks sit after `up` -- the port property, the nginx checksum,
# `nginx -t`, the upstream, the certificate store, the smoke test -- and each one
# used to abort a deployment whose containers were already running, leaving the
# state it refused in place. The port case is the worst of them: it named an
# off-host port accurately and left it open.
fail() {
    if [ "$PHASE" = mutating ]; then
        abort_deployment "$*"
    fi
    echo "REFUSED: $*" >&2
    exit 1
}

# Containers this run created, and only those. Anything that was present in the
# snapshot is left alone: it may belong to another compose project, or have been
# started before this deployment, and a rollback that stops it damages something
# it was never asked to touch.
stop_containers_created_by_this_run() {
    local now created=""
    now="$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" 2>/dev/null || true)"
    while IFS= read -r cid; do
        [ -n "$cid" ] || continue
        case " $PRE_CIDS " in
            *" $cid "*) ;;
            *) created="$created $cid" ;;
        esac
    done <<< "$now"
    if [ -n "${created// /}" ]; then
        echo "  stopping containers created by this run:$created" >&2
        # shellcheck disable=SC2086
        #   Deliberately unquoted: a list of ids to be split.
        docker stop $created >/dev/null 2>&1 || true
    else
        echo "  this run created no containers" >&2
    fi
}

# A record of the operation itself, written before it starts.
#
# The manifest says what was deployed *successfully*; nothing said that a run
# had begun. After a `kill -9` between `up` and the postflight there was no way
# to tell whether a deployment had been interrupted, which phase it reached, or
# which snapshot the next run should trust -- the tree looked deployed and the
# containers were whatever the interrupted run had left.
#
# Written atomically: a journal torn in half by the same kill it exists to
# survive would be worse than none, because it would be read.
JOURNAL="$MANIFEST_DIR/in-progress"

journal_write() {
    local state="$1" detail="${2-}"
    local tmp="$JOURNAL.$$"
    {
        echo "run            $RUN_ID"
        echo "state          $state"
        echo "started        $STARTED_AT"
        echo "updated        $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "mode           $MODE"
        echo "target         ${TARGET:-unset}"
        echo "previous       ${PREV_COMMIT:-none}"
        echo "pre_containers ${PRE_CIDS:-none}"
        [ -n "$detail" ] && echo "detail         $detail"
        echo "images"
        printf '%s\n' "${PREV_IMAGE_IDS:-}" | sed 's/^/  /'
    } > "$tmp" && mv -f "$tmp" "$JOURNAL"
}

journal_clear() { rm -f "$JOURNAL"; }

# The images that were running, by id, started without a build.
#
# compose accepts an id where a tag goes, so a generated override pins each
# service to exactly what it was running. `--no-build` is the point: a build
# would defeat the whole exercise.
restore_recorded_images() {
    [ -n "$PREV_IMAGE_IDS" ] || return 1
    local ovr
    ovr="$(mktemp)" || return 1
    {
        echo "services:"
        while IFS="$(printf '\t')" read -r svc img; do
            [ -n "$svc" ] || continue
            printf '  %s:\n    image: "%s"\n' "$svc" "$img"
        done <<< "$PREV_IMAGE_IDS"
    } > "$ovr"
    local rc=0
    "${DC[@]}" -f "$ovr" up -d --no-build --remove-orphans >/dev/null 2>&1 || rc=1
    rm -f "$ovr"
    return "$rc"
}

# Asked afterwards, not assumed from the exit status of the command above. An
# `up` that returns 0 having started something else is exactly the failure this
# is here to catch.
verify_restored_images() {
    [ -n "$PREV_IMAGE_IDS" ] || return 1
    local mismatch=""
    while IFS="$(printf '\t')" read -r svc want; do
        [ -n "$svc" ] || continue
        local cid got
        cid="$("${DC[@]}" ps -q "$svc" 2>/dev/null | head -1)"
        got="$(docker inspect -f '{{.Image}}' "$cid" 2>/dev/null)"
        [ "$got" = "$want" ] || mismatch="$mismatch $svc"
    done <<< "$PREV_IMAGE_IDS"
    if [ -n "${mismatch// /}" ]; then
        echo "  images do not match the snapshot for:$mismatch" >&2
        return 1
    fi
    return 0
}

# Refused after mutation: put back what was replaced, then report.
#
# Exit 1 means "refused, and the previous state is running again". Exit 76 means
# "refused, and the rollback did not complete" -- a different situation calling
# for a different response, which a single non-zero status cannot express.
abort_deployment() {
    local why="$1"
    ABORTING=1
    echo "REFUSED: $why" >&2
    echo
    echo "== rolling back =="

    if [ -z "$PREV_COMMIT" ]; then
        # Nothing recorded to go back to -- the first deployment, or a manifest
        # directory that was lost. There is no previous state to restore, so the
        # only safe action is to stop what this run started.
        echo "  no previous deployment recorded; nothing to restore" >&2
        journal_write rollback-impossible "no previous manifest"
        stop_containers_created_by_this_run
        exit 76
    fi

    git --no-pager checkout --quiet --detach "$PREV_COMMIT" 2>/dev/null \
        || { echo "  ROLLBACK FAILED: cannot check out ${PREV_COMMIT:0:12}" >&2
             # Every other terminal path records its outcome; this one left the
             # journal reading `mutating`, which names the wrong phase and hides
             # that the checkout is what broke.
             journal_write rollback-failed "cannot check out ${PREV_COMMIT:0:12}"
             stop_containers_created_by_this_run
             exit 76; }

    # The recorded images, by id, without building.
    #
    # Rebuilding the commit was what this did before, and it is a different
    # operation: it produces whatever the source builds today, which is not
    # necessarily what was running. "restored <commit>" then meant "an `up`
    # succeeded", a claim about the command rather than about the state.
    if restore_recorded_images && verify_restored_images; then
        echo "  restored the images that were running at ${PREV_COMMIT:0:12}"
        # Written, then cleared. The record exists for anyone watching the file
        # during the rollback, and the run leaves nothing behind: the previous
        # state is verified back, so there is nothing for an operator to
        # reconcile and the next deployment must not be refused.
        #
        # Leaving it made exit 1 and exit 76 the same thing in practice -- both
        # required the same manual step -- which defeats having two codes.
        journal_write rolled-back "restored recorded images at ${PREV_COMMIT:0:12}"
        journal_clear
        exit 1
    fi

    # Falling back to a rebuild, and saying so. It usually produces a working
    # deployment and is not a restoration, so the exit code is the one that
    # means "the rollback did not complete" -- an operator has to look, rather
    # than read "restored" and move on.
    echo "  the recorded images could not be restored; rebuilding instead" >&2
    if "${DC[@]}" up -d --build --remove-orphans >/dev/null 2>&1 \
        && "${DC[@]}" up -d --force-recreate nginx >/dev/null 2>&1; then
        journal_write rollback-incomplete "rebuilt ${PREV_COMMIT:0:12}; images not verified"
        echo "  ROLLBACK INCOMPLETE: re-deployed the source of ${PREV_COMMIT:0:12};" >&2
        echo "  this is not the runtime state that was replaced -- verify before trusting it" >&2
        exit 76
    fi

    echo "  ROLLBACK FAILED: could not restore ${PREV_COMMIT:0:12}" >&2
    journal_write rollback-failed "could not restore ${PREV_COMMIT:0:12}"
    stop_containers_created_by_this_run
    exit 76
}

step() { echo; echo "== $* =="; }

# A signal during mutation must undo what the mutation started.
#
# Only `fail()` routed through the rollback, so a SIGTERM -- an operator's
# Ctrl-C, a session that dropped, an orchestrator killing the run -- left the
# new containers running with nothing left to undo them. That is the same
# fail-open shape as the port check: the deployment stops, and the state it
# stops in is the one nobody accepted.
#
# `exit` inside the handler rather than a bare return: trapping a signal
# replaces its default action, so a handler that tidies up and returns leaves
# the script running while the caller believes it stopped.
on_signal() {
    local sig="$1"
    if [ "$ABORTING" = 1 ]; then
        # Already unwinding. A second signal must not start a second rollback.
        echo "  received SIG$sig during rollback; ignoring" >&2
        return
    fi
    if [ "$PHASE" = mutating ]; then
        abort_deployment "interrupted by SIG$sig"
    fi
    echo "REFUSED: interrupted by SIG$sig" >&2
    exit $((128 + $(kill -l "$sig" 2>/dev/null || echo 15)))
}
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM
trap 'on_signal HUP' HUP

# And an unexpected exit, which is neither a signal nor a `fail`.
#
# Found by running the rollback against a real Docker daemon rather than
# against stubs: `UPSTREAM="$(... | grep server)"` returns non-zero when the
# pattern is absent, and under `set -e` that ended the script on the spot --
# past `up`, before any verdict. No REFUSED was printed, the journal stayed at
# `mutating`, the new version kept serving, and the exit code was 1: the code
# that means "the previous state is running again".
#
# Eighteen post-mutation commands can fail that way. Routing the exit trap
# through the rollback covers all of them, including the ones nobody has
# thought of.
on_unexpected_exit() {
    local rc=$?
    # Success, or a phase where nothing has been changed yet: nothing to undo.
    [ "$rc" -eq 0 ] && exit 0
    [ "$PHASE" = mutating ] || exit "$rc"
    # Already unwinding: abort_deployment exits, and that exit lands here.
    [ "$ABORTING" = 0 ] || exit "$rc"
    abort_deployment "unexpected failure (exit $rc)"
}
trap on_unexpected_exit EXIT

MODE=deploy
TARGET=""
# This run's identity, used by the journal. Generated before the arguments are
# parsed so a refusal there is still attributable.
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
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
# Derived from the directory, never given separately. An overridable DEPLOY_LOCK
# let the lock file sit outside LOCK_DIR, so every check below validated a
# directory the lock was not in -- security theatre with a passing test suite,
# and the tests were the ones using the override.
LOCK_FILE="$LOCK_DIR/deploy.lock"
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

# Nobody else can write in this directory by now, so a symlink here would have to
# predate it. Cheap to refuse, and `exec 9>` would otherwise follow it.
[ ! -L "$LOCK_FILE" ] || fail "$LOCK_FILE is a symlink"

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

# An unfinished journal means a previous run was interrupted between `up` and
# its verdict. What is running then is whatever that run left, and this one
# cannot know whether it is safe to build on -- so it refuses and names the
# operator's next move rather than deploying on top of an unknown state.
if [ -f "$JOURNAL" ]; then
    echo "  an unfinished deployment is recorded:" >&2
    sed 's/^/    /' "$JOURNAL" >&2
    fail "a previous run did not finish; inspect the state, then remove $JOURNAL to proceed"
fi

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
    # The journal records deployments this script made. One made by hand leaves
    # no entry, and the next scripted run then names the last *scripted* commit
    # as the one it replaced, skipping everything in between (#133). The "to
    # undo:" line this script prints is derived from that field, so an operator
    # following the script's own suggestion can land several commits further
    # back than they expect -- silently, at the moment they are least able to
    # check.
    #
    # Read before the checkout below. Afterwards HEAD is the target and the
    # observation is worthless: that is precisely the mistake the manifest
    # itself made once, recording the commit being deployed as the commit being
    # replaced.
    RUNNING_SHA="$(git rev-parse HEAD)"
    JOURNAL_SHA=""
    if [ -L "$MANIFEST_DIR/latest" ] || [ -e "$MANIFEST_DIR/latest" ]; then
        JOURNAL_SHA="$(awk '/^commit /{print $2}' \
            "$MANIFEST_DIR/$(readlink "$MANIFEST_DIR/latest")" 2>/dev/null || true)"
    fi

    echo "  checked out now: ${RUNNING_SHA:0:12}"
    if [ -n "$JOURNAL_SHA" ]; then
        echo "  journal records: ${JOURNAL_SHA:0:12}"
    else
        echo "  journal records: nothing yet"
    fi
    echo "  rollback target: ${TARGET:0:12}"

    # Only when the journal claims something and is contradicted. An empty
    # journal cannot mislead anyone -- there is no suggestion to follow, so the
    # target came from the operator rather than from this script, and refusing
    # would block the first recovery on a fresh host for no reason.
    if [ -n "$JOURNAL_SHA" ] && [ "$JOURNAL_SHA" != "$RUNNING_SHA" ]; then
        echo "  the journal does not describe what is checked out: something was" >&2
        echo "  deployed outside this script, so its history has a gap and any" >&2
        echo "  target taken from it is a guess rather than a record." >&2
        # The acknowledgement carries the observed commit, so it cannot be set
        # in advance or pasted from a runbook: the operator has to read this
        # output to produce it.
        [ "${ROLLBACK_ACKNOWLEDGE:-}" = "$RUNNING_SHA" ] || fail \
"the journal (${JOURNAL_SHA:0:12}) disagrees with the checkout (${RUNNING_SHA:0:12}).
     Confirm that ${TARGET:0:12} is the commit you want, then re-run with
     ROLLBACK_ACKNOWLEDGE=$RUNNING_SHA"
        echo "  divergence acknowledged for ${RUNNING_SHA:0:12}"
    fi

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

# The ids on their own, for the rollback. Recorded here because after `up` there
# is no way to tell which containers this run created -- and a rollback that
# stops the wrong one damages something it does not own.
PRE_CIDS="$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" 2>/dev/null | tr '\n' ' ' || true)"

# service<TAB>image-id for what is *running*, from `docker inspect`, not from
# `docker ps --format {{.Image}}`.
#
# That format prints the tag -- `sora-backend:latest` -- and a tag is a pointer.
# Rebuilding the same commit moves it: a base image can have been updated, a
# dependency resolved differently, a download returned something else. Restoring
# "the tag it had" therefore restores whatever that name means now, which is the
# question, not the answer.
#
# The id is the image. Verified that compose accepts one in place of a tag and
# starts the container without building.
PREV_IMAGE_IDS="$(
    for _cid in $(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" 2>/dev/null); do
        _svc="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$_cid" 2>/dev/null)"
        _img="$(docker inspect -f '{{.Image}}' "$_cid" 2>/dev/null)"
        if [ -n "$_svc" ] && [ -n "$_img" ]; then
            printf '%s\t%s\n' "$_svc" "$_img"
        fi
    done | sort -u
)"
# `if`, not `[ ... ] && [ ... ] && printf`. Under `set -e` that chain returns 1
# whenever the guard is false -- which is every container the inspect could not
# read -- and the whole command substitution then fails the script, silently,
# before it has printed a reason.
if [ -n "$PREV_IMAGE_IDS" ]; then
    echo "  recorded $(printf '%s\n' "$PREV_IMAGE_IDS" | wc -l | tr -d ' ') running image id(s) for rollback"
else
    echo "  no running image ids recorded; a rollback will have to rebuild"
fi

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

# --------------------------------------------------------------------- preflight

# What this configuration *would* publish, checked before anything starts.
#
# The runtime check further down stays: it reads what is actually reachable and
# catches a container started from a file this deployment never read. But it
# runs after `up`, and `fail` only exits -- so an offending port is named
# accurately and left open. Detection is not prevention, and the two are
# different jobs.
#
# Rendered, never grepped. `config` resolves interpolation, overrides and
# multiple -f; the YAML text does not say what compose will act on.
step "what this configuration would publish"

command -v python3 >/dev/null 2>&1 \
    || fail "python3 is required to read the rendered compose configuration"

# The rendered document carries interpolated environment values, so it is never
# printed, logged or written to a manifest. Only the port tuple leaves this
# pipeline.
RENDERED_PORTS="$("${DC[@]}" config --format json 2>/dev/null | python3 -c '
import json, sys
try:
    doc = json.load(sys.stdin)
except Exception:
    sys.exit(3)
for name, svc in sorted((doc.get("services") or {}).items()):
    for spec in svc.get("ports") or []:
        # \x1f (unit separator), not a tab. A tab is IFS whitespace, so bash
        # collapses a run of them: an absent host_ip vanished and every
        # later field shifted left, which turned "nginx, all interfaces,
        # port 80" into "host address 80" and refused it.
        print("%s\x1f%s\x1f%s\x1f%s\x1f%s\x1f%s" % (
            name,
            # Absent and empty both mean every interface. Written as both,
            # because nginx renders with no host_ip key at all.
            spec.get("host_ip", "") or "",
            # A string, and it can be a range: "8000-8010". Never parsed as a
            # number -- an int() either throws or truncates, and a truncated
            # "80-81" reads as the allowed 80.
            spec.get("published", ""),
            spec.get("target", ""),
            spec.get("protocol", "tcp"),
            spec.get("mode", ""),
        ))
')" || fail "the compose configuration could not be rendered"

# `mode` is recorded but not treated as evidence about exposure. It describes
# Swarm publication, and in a standalone deployment it does not change what is
# reachable -- measured: mode: host published 0.0.0.0 and [::] exactly as
# ingress would. It is refused when unexpected, as an unsupported configuration.
BAD_DECL=""
NGINX_80=0
NGINX_443=0
while IFS=$'\x1f' read -r svc ip pub tgt proto mode; do
    [ -n "$svc" ] || continue
    case "$ip" in
        127.0.0.1|::1) continue ;;          # loopback, both families
        ""|0.0.0.0|::) ;;                   # every interface
        *) BAD_DECL="$BAD_DECL
  $svc would publish on host address $ip"; continue ;;
    esac

    # Off-host from here. The whole tuple must match, exactly, as strings.
    if [ "$proto" != "tcp" ] || { [ -n "$mode" ] && [ "$mode" != "ingress" ] && [ "$mode" != "host" ]; }; then
        BAD_DECL="$BAD_DECL
  $svc would publish $pub off-host with protocol '$proto' mode '$mode'"
        continue
    fi
    case "$pub:$tgt" in
        80:80)   [ "$svc" = nginx ] && NGINX_80=1 ;;
        443:443) [ "$svc" = nginx ] && NGINX_443=1 ;;
        *) BAD_DECL="$BAD_DECL
  $svc would publish $pub->$tgt off-host" ; continue ;;
    esac
done <<< "$RENDERED_PORTS"

if [ -n "$BAD_DECL" ]; then
    echo "$BAD_DECL" >&2
    fail "this configuration would publish a port other than 80/443 off-host"
fi

# The converse, and it is a separate property. "Nothing forbidden is published"
# is satisfied by publishing nothing at all -- an empty list passes a universal
# check. Losing 80 costs every caller who arrives over http://, and no refusal
# anywhere fires for it: the smoke test only exercises https://.
[ "$NGINX_80" = 1 ]  || fail "this configuration publishes no off-host 80/tcp for nginx"
[ "$NGINX_443" = 1 ] || fail "this configuration publishes no off-host 443/tcp for nginx"
echo "  only nginx 80/tcp and 443/tcp would be published off-host"

# ------------------------------------------------------------------- deployment

step "deploying"

# From here a refusal has something to undo, so `fail` routes through
# abort_deployment. Set immediately before `up` rather than earlier: a refusal
# during the preflight has nothing to roll back, and rolling back anyway would
# rebuild the previous state for no reason.
PHASE=mutating
# Written before the first change, not after: a journal that appears only once
# the mutation succeeded records nothing about the case it exists for.
journal_write mutating

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

# And that the ports which must be reachable are. The loop above is a safety
# property -- "nothing forbidden" -- and an empty binding list satisfies it
# completely. Every off-host row is evaluated, and the result is then checked
# for what has to be present.
#
# One declaration can surface as more than one binding: on the host measured
# here a single entry with no host_ip produced 0.0.0.0 and [::]. How many
# appear depends on the daemon and the network configuration, so this asks
# whether the endpoint is published at all, not how many rows carry it.
SEEN_80=0
SEEN_443=0
# Keyed on the compose service, not on the container name.
#
# `case "$name" in *nginx*)` also matched `nginx-helper`, `old-nginx`, anything
# with the substring in it. A deployment where nginx published nothing and some
# other container published 80 and 443 would have satisfied this completeness
# check -- the gate would report the site as served while the service that is
# supposed to serve it was not listening.
while IFS='|' read -r svc ports; do
    [ "$svc" = nginx ] || continue
    [ -n "$ports" ] || continue
    while IFS= read -r p; do
        case "$p" in *"->"*) ;; *) continue ;; esac
        hostpart="${p%%->*}"
        ip="${hostpart%:*}"
        hostport="${hostpart##*:}"
        case "$ip" in 127.0.0.1|::1|"[::1]") continue ;; esac
        case "$p" in *"/tcp") ;; *) continue ;; esac
        case "$hostport" in 80) SEEN_80=1 ;; 443) SEEN_443=1 ;; esac
    done <<< "${ports//, /$'\n'}"
done < <(docker ps --filter "label=com.docker.compose.project=$PROJECT" \
             --format '{{.Label "com.docker.compose.service"}}|{{.Ports}}')
[ "$SEEN_80" = 1 ]  || fail "nginx publishes no off-host 80/tcp; http:// callers have nowhere to land"
[ "$SEEN_443" = 1 ] || fail "nginx publishes no off-host 443/tcp"
echo "  nginx publishes 80/tcp and 443/tcp, and nothing else is reachable off-host"

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

# The whole block, not a fixed window after the name. `grep -A2` broke the
# moment the block gained a comment: the `server` line moved past the third
# line, the parser returned nothing, and the deploy failed here rather than at
# anything real (#129). A parser that depends on the line count of a comment is
# a check on formatting.
UPSTREAM="$("${DC[@]}" exec -T nginx sh -c \
    "awk '/upstream[[:space:]]+sora_backend[[:space:]]*\\{/,/\\}/' /etc/nginx/nginx.conf \
     | grep -E '^[[:space:]]*server[[:space:]]'" | tr -d '\r' | tr -s ' ' | sed 's/^ //;s/ $//')"

case "$UPSTREAM" in
    *"backend:8000"*) ;;
    *) fail "nginx proxies to '$UPSTREAM'; expected backend:8000" ;;
esac
# `resolve` is what makes the address re-read on the resolver TTL instead of
# frozen when the workers start. Without it a container recreate outside this
# script strands nginx on an address that no longer exists -- 4.5 minutes of
# 502 on 2026-08-09, with every container reporting healthy.
case "$UPSTREAM" in
    *resolve*) ;;
    *) fail "upstream '$UPSTREAM' has no 'resolve'; nginx would cache the address" ;;
esac
echo "  upstream is $UPSTREAM"

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
# Report the certificate's own remaining life first. It is the fact that
# decides whether anyone needs to act, and unlike the renewal mechanism it can
# be read straight off disk without interrogating systemd.
# Every step below is guarded, because this block only *reports*. Under
# `set -euo pipefail` an unguarded `x="$(openssl ... | cut ...)"` ends the whole
# script when openssl is missing or the certificate is malformed: pipefail makes
# the pipeline fail even though `cut` succeeds, and set -e exits on the
# assignment before the next line can skip it. Measured, not assumed -- a status
# line has no business failing a deployment that is otherwise sound.
#
# The pipeline is gone entirely: `${_end#*=}` does what `cut -d= -f2` did, with
# no second command whose status could propagate.
CERT_MIN_DAYS=""
for _live in /etc/letsencrypt/live/*/; do
    [ -f "${_live}cert.pem" ] || continue
    _name="$(basename "$_live")"
    if ! _end="$(openssl x509 -enddate -noout -in "${_live}cert.pem" 2>/dev/null)"; then
        echo "  WARNING: could not read the certificate for ${_name}"
        continue
    fi
    _end="${_end#*=}"
    if [ -z "$_end" ] || ! _epoch="$(date -d "$_end" +%s 2>/dev/null)"; then
        echo "  WARNING: could not parse the expiry of ${_name} (${_end:-empty})"
        continue
    fi
    _days=$(( (_epoch - $(date +%s)) / 86400 ))
    echo "  ${_name} expires in ${_days} day(s)"
    if [ -z "$CERT_MIN_DAYS" ] || [ "$_days" -lt "$CERT_MIN_DAYS" ]; then
        CERT_MIN_DAYS="$_days"
    fi
done

# Three outcomes, not two. This printed "no certbot timer or cron entry found"
# whenever the lookup did not succeed, which reads as "renewal is not
# configured" -- a claim it cannot support when the real reason is that it
# could not ask. Observed on 2026-08-05: the deployment warned while
# certbot.timer was enabled, active, and had last run ten hours earlier. The
# warning was not reproducible afterwards, so the cause stays unproven; what is
# certain is that the check reported absence on evidence that only showed
# failure to determine.
#
# The result is captured into a variable rather than piped: under `set -o
# pipefail` a non-zero systemctl makes the whole pipeline non-zero regardless of
# what grep found, and the branch taken then says nothing about certbot.
RENEWAL="absent"
if _timers="$(systemctl list-timers 'certbot*' --no-pager 2>/dev/null)"; then
    case "$_timers" in
        *certbot*) RENEWAL="present (systemd timer)" ;;
    esac
else
    RENEWAL="undetermined"
fi
if [ "$RENEWAL" != "present (systemd timer)" ] \
   && crontab -l 2>/dev/null | grep -q certbot; then
    RENEWAL="present (cron)"
fi

case "$RENEWAL" in
    present*)
        echo "  a certbot renewal mechanism is present: ${RENEWAL#present }"
        if find /etc/letsencrypt/renewal-hooks/deploy -type f 2>/dev/null | grep -q .; then
            echo "  renewal has deploy hooks"
        else
            echo "  WARNING: no deploy hook in /etc/letsencrypt/renewal-hooks/deploy/;"
            echo "           a renewed certificate will not reach nginx until it restarts"
        fi
        ;;
    undetermined)
        echo "  NOTE: could not query systemd for a renewal timer, and root has"
        echo "        no certbot cron entry. This says nothing about whether"
        echo "        renewal is configured -- check by hand if the expiry above"
        echo "        is close."
        ;;
    *)
        echo "  WARNING: no certbot timer and no cron entry; certificates may not renew"
        ;;
esac

# A mechanism that exists is not a mechanism that worked. Expiry inside the
# 30-day window is when Let's Encrypt renewal should already have happened.
if [ -n "$CERT_MIN_DAYS" ] && [ "$CERT_MIN_DAYS" -lt 30 ]; then
    echo "  WARNING: a certificate expires in ${CERT_MIN_DAYS} day(s); renewal"
    echo "           should have run by now whatever the mechanism reports"
fi

step "the site answers"

PROBE_ATTEMPTS=0
PROBE_RETRIES=0

# `code="$(curl ... || echo 000)"` looked defensive and was not: on a connection
# failure curl prints its own "000" and exits non-zero, so the fallback appended
# a second one and the variable became "000000" -- never equal to 200, so the
# refusal still happened, but the message reported a status that does not exist.
# Captured explicitly instead.
# Every probe identifies itself, so acceptance can tell this deployment's own
# readiness retries from a user request that failed.
#
# The retries below are deliberate: nginx has just been recreated and the
# backend may still be accepting its first connections, so the probe is
# *expected* to start early and get a 502 before it gets a 200. That 502 lands
# in the same nginx access log as real traffic. On the 4cd7232 release the
# acceptance query counted it and reported one user-facing failure; there was
# none. The release before happened to report zero only because the probe
# arrived after the backend was already listening -- the measurement could
# never tell "no user was affected" from "our probe missed the window" (#142).
#
# Matched on this exact string, not on `curl`, not on `/health`, not on the
# host's own address: each of those would also hide an external monitor or an
# operator's own check, and a filter that removes evidence is worse than the
# noise it removes.
DEPLOY_PROBE_UA="sora-deploy-healthcheck/1"

http_code() {
    local code
    if ! code="$(curl -s -o /dev/null -w '%{http_code}' -m 20 \
                      -A "$DEPLOY_PROBE_UA" "$1" 2>/dev/null)"; then
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
    PROBE_ATTEMPTS=$((PROBE_ATTEMPTS + attempt))
    PROBE_RETRIES=$((PROBE_RETRIES + attempt - 1))
    printf '  %-18s %s\n' "$path" "$code"
done

# Reported as its own figure. A retry here is the probe working, not an outage,
# and the two must not share a number: "502 count in the window" cannot be a
# useful acceptance criterion while a healthy deployment contributes to it.
# The final result is what matters, and it is already enforced above -- a
# timeout is still a deployment failure.
echo "  probe attempts $PROBE_ATTEMPTS, of which retried $PROBE_RETRIES; final 200 on every path"
echo "  acceptance: exclude only User-Agent $DEPLOY_PROBE_UA when counting user-facing 5xx"

# ------------------------------------------------------------------- the record

# Accepted. Nothing after this point should roll back: the deployment is the
# state that is meant to be running, and a failure while writing the record is
# not a reason to undo it.
PHASE=committed
# Closed only here. Everything above this line is a state somebody has to look
# at; past it, the deployment is the one that is meant to be running.
journal_clear

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
