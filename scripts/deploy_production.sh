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
# The single application image. backend, scheduler and migrate all reference it
# in docker-compose.prod.yml; tests/test_migration_owner.py asserts the default
# here and the default there have not drifted apart.
APP_IMAGE="${SORA_APP_IMAGE:-sora_earth_app:current}"
DC=(docker compose -p "$PROJECT" -f "$COMPOSE")

# The cutover budgets. Old code is stopped before the schema changes, so these
# bound how long the site is down, and each one ends in a refusal rather than a
# wait without end.
#
#   STOP_TIMEOUT       seconds `compose stop` gives backend and scheduler to
#                      finish their requests before they are killed
#   DRAIN_SECONDS      how long, after they stop, to wait for every other
#                      client transaction on the database to end
#   MIGRATE_DEADLINE   the whole migrator, start to exit. `lock_timeout` in the
#                      migration bounds the wait for a lock, not a backfill or
#                      an index build; this bounds everything
STOP_TIMEOUT="${DEPLOY_STOP_TIMEOUT:-60}"
DRAIN_SECONDS="${DEPLOY_DRAIN_SECONDS:-30}"
MIGRATE_DEADLINE="${DEPLOY_MIGRATE_DEADLINE:-120}"
# How long to wait for a stopped migrator to disappear, container and session.
MIGRATOR_GONE_SECONDS="${DEPLOY_MIGRATOR_GONE_SECONDS:-30}"
# How long the client creating the migrator (`compose run -d`) may take to
# return. It is never killed; past this the migrator's existence is unknown and
# the run refuses with every rollback forbidden.
MIGRATOR_CREATE_SECONDS="${DEPLOY_MIGRATOR_CREATE_SECONDS:-60}"
# The image every application service of this run is pinned to, by id, once it
# is built; and the override file that pins it. Empty until the build.
APP_IMAGE_ID=""
PIN_FILE=""

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
# Where the cutover is, for the journal and for the refusal messages.
#
#   untouched  nothing has been stopped; what ran before still runs
#   stopping   nginx, backend or scheduler is being stopped
#   stopped    all three are stopped, and stay so until the new code starts
#   started    the new backend and scheduler have been started
#
# After a refusal the services may be left stopped -- deliberately, when the
# schema can no longer serve the old code -- and the message has to say so.
# "The new code is still running" was the only sentence this script had, and
# it is false for every refusal between the stop and the start.
APPS_STATE=untouched
CUTOVER_STAGE=none
# The one migrator of this run, by name, so that a deadline or a signal can stop
# exactly it and confirm it is gone before anything reads the schema. Until the
# migrator's transaction has ended its columns are invisible to every other
# session, and a probe made earlier would allow the old code back onto a schema
# that is about to change under it.
MIGRATOR_NAME=""
MIGRATOR_ID=""
# none, running (its creating client has not returned), returned, or unknown
# (the client outlived MIGRATOR_CREATE_SECONDS).
MIGRATOR_CREATE=none
MIGRATOR_CREATE_PID=""
MIGRATOR_CREATE_STARTED=0
MIGRATOR_GONE=0
MIGRATE_TIMED_OUT=0

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
        logged docker stop $created || true
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
        echo "cutover        $CUTOVER_STAGE"
        echo "apps           $APPS_STATE"
        [ -n "$MIGRATOR_NAME" ] && echo "migrator       $MIGRATOR_NAME"
        [ -n "$APP_IMAGE_ID" ] && echo "image_pin      $APP_IMAGE_ID"
        [ -n "$detail" ] && echo "detail         $detail"
        echo "images"
        printf '%s\n' "${PREV_IMAGE_IDS:-}" | sed 's/^/  /'
    } > "$tmp" && mv -f "$tmp" "$JOURNAL"
}

journal_clear() { rm -f "$JOURNAL"; }

# Where the rollback's own commands write. They used to go to /dev/null, so the
# rollback of 2026-10-26 said only "the recorded images could not be restored"
# and nothing about why. Beside the journal when that is writable; otherwise a
# temporary file; and failing both, nowhere -- an unwritable log must not stop
# the rollback it would have described.
ROLLBACK_LOG=""
rollback_log() {
    if [ -z "$ROLLBACK_LOG" ]; then
        local want="$MANIFEST_DIR/rollback-$RUN_ID.log"
        if { : >> "$want"; } 2>/dev/null; then
            ROLLBACK_LOG="$want"
        elif _tmp_log="$(mktemp 2>/dev/null)"; then
            ROLLBACK_LOG="$_tmp_log"
        else
            ROLLBACK_LOG=/dev/null
        fi
        echo "  rollback log: $ROLLBACK_LOG" >&2
    fi
    printf '%s\n' "$ROLLBACK_LOG"
}

# Runs one rollback command with its output in the log and its exit code both in
# the log and on stderr.
logged() {
    local log rc
    log="$(rollback_log)"
    printf '\n$ %s\n' "$*" >> "$log" 2>/dev/null || true
    "$@" >> "$log" 2>&1
    rc=$?
    printf '[exit %s]\n' "$rc" >> "$log" 2>/dev/null || true
    echo "  [exit $rc] $*" >&2
    return "$rc"
}

# The images that were running, by id, started without a build.
#
# compose accepts an id where a tag goes, so a generated override pins each
# service to exactly what it was running. `--no-build` is the point: a build
# would defeat the whole exercise.
restore_recorded_images() {
    [ -n "$PREV_IMAGE_IDS" ] || return 1
    local ovr
    # Named, so the restore can be told from the deployment's own image pin in
    # a list of calls -- both are a second -f on the same `up`.
    ovr="$(mktemp "${TMPDIR:-/tmp}/sora-image-restore.XXXXXX")" || return 1
    {
        echo "services:"
        while IFS="$(printf '\t')" read -r svc img; do
            [ -n "$svc" ] || continue
            printf '  %s:\n    image: "%s"\n' "$svc" "$img"
        done <<< "$PREV_IMAGE_IDS"
    } > "$ovr"
    local rc=0
    logged "${DC[@]}" -f "$ovr" up -d --no-build --remove-orphans || rc=1
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
        echo "images do not match the snapshot for:$mismatch" >> "$(rollback_log)" 2>/dev/null || true
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
    # Chosen once, here, in this shell: `logged` asks for it from a command
    # substitution, where an assignment would not survive, and a fallback to
    # mktemp would then make a new file per command.
    rollback_log > /dev/null

    # The migrator first, and confirmed gone, before anything below reads the
    # schema. A signal or a deadline can arrive while its transaction is open:
    # the ownership columns are then invisible to every other session, the
    # probe would answer "absent", and the old code would be started onto a
    # schema that commits a moment later. If it cannot be confirmed, nothing
    # can be decided, so nothing is.
    local gone=0
    ensure_migrator_gone || gone=$?
    if [ "$gone" = 2 ]; then
        echo "  ROLLBACK REFUSED: whether the migrator $MIGRATOR_NAME exists cannot be known --" >&2
        echo "  it was never positively identified -- so the schema may still change and no" >&2
        echo "  code may be started onto it." >&2
        refuse_rollback "migrator unknown: never positively identified, so its create may still land; the schema was not read"
    elif [ "$gone" != 0 ]; then
        echo "  ROLLBACK REFUSED: the migrator $MIGRATOR_NAME could not be confirmed stopped," >&2
        echo "  so the schema cannot be read reliably and no code may be started onto it." >&2
        refuse_rollback "the migrator could not be confirmed stopped; the schema was not read"
    fi

    if [ -z "$PREV_COMMIT" ]; then
        # Nothing recorded to go back to -- the first deployment, or a manifest
        # directory that was lost. There is no previous state to restore, so the
        # only safe action is to stop what this run started.
        echo "  no previous deployment recorded; nothing to restore" >&2
        journal_write rollback-impossible "no previous manifest"
        stop_containers_created_by_this_run
        settle_partial_start
        # The cutover may have stopped containers that predate this run; they
        # stay stopped, and the operator has to know that. Written again after
        # the stop, so the journal's `apps` line is what was observed.
        if [ "$APPS_STATE" != untouched ]; then
            state_after_refusal >&2
            journal_write rollback-impossible "no previous manifest"
        fi
        exit 76
    fi

    # Before the checkout: restoring code that predates persistent accounts
    # would undo every block and reset made since. Leave the new code running
    # and stop; an operator decides, with external access closed if need be.
    if accounts_downgrade_blocked "$PREV_COMMIT"; then
        echo "  ROLLBACK REFUSED: ${PREV_COMMIT:0:12} predates persistent accounts and the" >&2
        echo "  users table now exists (or its existence could not be read). That code would" >&2
        echo "  refuse the migrated schema and ignore every block and reset. See" >&2
        echo "  CLAUDE.md, 'Accounts live in the users table'." >&2
        refuse_rollback "previous code predates persistent accounts and the users table exists"
    fi

    # Independent of the accounts barrier, and asked even when that one
    # allows: code with persistent accounts and without project ownership --
    # the release before ownership -- passes the first and fails this one.
    if ownership_downgrade_blocked "$PREV_COMMIT"; then
        echo "  ROLLBACK REFUSED: ${PREV_COMMIT:0:12} has no project ownership, and the database" >&2
        echo "  has ownership columns or audit tables (probe answered '${OWNERSHIP_PROBE_ANSWER:-nothing}')." >&2
        echo "  That code would write rows the schema refuses and show every specialist's" >&2
        echo "  saved project to everyone. See CLAUDE.md, 'Project ownership'." >&2
        refuse_rollback "previous code has no project ownership and the schema has it (or it could not be read)"
    fi

    git --no-pager checkout --quiet --detach "$PREV_COMMIT" 2>/dev/null \
        || { echo "  ROLLBACK FAILED: cannot check out ${PREV_COMMIT:0:12}" >&2
             # Every other terminal path records its outcome; this one left the
             # journal reading `mutating`, which names the wrong phase and hides
             # that the checkout is what broke.
             journal_write rollback-failed "cannot check out ${PREV_COMMIT:0:12}"
             stop_containers_created_by_this_run
             settle_partial_start
             [ "$APPS_STATE" = untouched ] || state_after_refusal >&2
             journal_write rollback-failed "cannot check out ${PREV_COMMIT:0:12}"
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
    if logged "${DC[@]}" up -d --build --remove-orphans \
        && logged "${DC[@]}" up -d --force-recreate nginx; then
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

# A refused rollback: nothing old is restored, and what is left running is
# established and said, not assumed.
refuse_rollback() {
    settle_partial_start
    state_after_refusal >&2
    journal_write rollback-refused "$1"
    exit 76
}

# The start of the new backend and scheduler failed partway: one may be
# running and the other not -- `up` reports failure for the pair, not for each.
# A refusal that leaves them so would print "STOPPED" over a running backend.
# Stopped and confirmed here; if that cannot be confirmed the state is
# `unknown`, and the message says so instead.
settle_partial_start() {
    [ "$APPS_STATE" = starting ] || return 0
    echo "  the new backend and scheduler may be partly running; stopping them" >&2
    if logged "${DC[@]}" stop -t "$STOP_TIMEOUT" backend scheduler \
        && confirm_apps_stopped backend scheduler; then
        APPS_STATE=stopped
    else
        APPS_STATE=unknown
    fi
}

step() { echo; echo "== $* =="; }

# One question to the production database, asked of PostgreSQL itself rather
# than through the pooler or the application, and accepted only whole.
#
#   pg_ask SQL REGEX
#
# Succeeds only when psql exited 0 AND the whole answer matches REGEX, an
# extended regular expression the caller anchors with ^ and $ -- which in bash
# bind to the ends of the string, not of a line, so a second line fails it. The
# answer is left in PG_ANSWER either way, prefixed with the exit status when
# that was not 0, so a refusal can quote what came back.
#
# Both halves, because each was the whole check once: `pg_scalar ... || true`
# kept the text and dropped the status, so `ownership:0` printed by a psql that
# then failed -- an error after the row, a broken pipe, a container stopping --
# read as "no ownership schema" and allowed the old code back.
PG_ANSWER=""
pg_ask() {
    local sql="$1" want="$2" out rc=0
    # shellcheck disable=SC2016
    #   Single quotes on purpose: $POSTGRES_USER, $POSTGRES_DB and $0 belong to
    #   the postgres container's shell, not this one.
    out="$("${DC[@]}" exec -T postgres sh -c \
        'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "$0"' \
        "$sql" < /dev/null 2>/dev/null)" || rc=$?
    out="${out//$'\r'/}"
    if [ "$rc" -ne 0 ]; then
        PG_ANSWER="(exit $rc) $out"
        return 1
    fi
    PG_ANSWER="$out"
    [[ "$out" =~ $want ]]
}

# Code from before persistent accounts cannot be restored once `users` exists.
#
# Two reasons, either sufficient. That code ignores the table and rebuilds
# admin, analyst and viewer from SORA_DEFAULT_*_PASSWORD, so every block and
# reset made since would be lifted. And its entrypoint refuses a database past
# its own Alembic head, so it would not start at all -- while the image-id
# check below would still report the restore as a success.
#
# Whether the table exists is asked on its own: a query that names `users`
# fails outright where it is absent, CASE or not, and an absent table is the
# one case where going back is still fine -- the migration never got there.
# Succeeds (refuse) when the table exists or the answer cannot be read; fails
# (allow) when the target has the accounts code or the table is absent -- and
# "absent" means psql exited 0 and answered exactly `users:false`.
accounts_downgrade_blocked() {
    local to="$1"
    git cat-file -e "$to:app/accounts.py" 2>/dev/null && return 1
    if pg_ask "SELECT 'users:' || (to_regclass('public.users') IS NOT NULL)::text" \
        '^users:(true|false)$' && [ "$PG_ANSWER" = users:false ]; then
        return 1
    fi
    return 0
}

# Code from before project ownership cannot be restored once the schema has it.
#
# Any one of the four columns -- owner_user_id or data_scope, on evaluations or
# predictions_log -- or either audit table is enough. That code inserts rows
# without an owner into a column whose default is `pilot` and whose CHECK then
# refuses them, and it reads both tables without the scope filter, so every
# specialist's saved project would be served to everybody. Pilot rows are not
# counted: the barrier is about the schema the old code would meet, and an
# empty pilot changes nothing about that. Nor is alembic_version consulted --
# a revision number says what was recorded, the catalogue says what exists.
#
# Asked of pg_catalog, so a partially applied schema -- one column of four --
# still blocks. The answer carries a prefix and must be exactly
# `ownership:<count>` from a psql that exited 0: an error, a non-zero status
# with any output, an empty reply or anything else is a refusal, because
# "could not tell" is not "absent".
#
# Succeeds (refuse) when the schema has any of it or cannot be read; fails
# (allow) when the target carries the ownership code or the schema has none.
# The answer is left in OWNERSHIP_PROBE_ANSWER for the message.
OWNERSHIP_PROBE_SQL="SELECT 'ownership:' || ((SELECT count(*) FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid = a.attrelid JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relname IN ('evaluations', 'predictions_log') AND a.attname IN ('owner_user_id', 'data_scope') AND a.attnum > 0 AND NOT a.attisdropped) + (SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relname IN ('project_access_events', 'project_access_targets')))"
OWNERSHIP_PROBE_ANSWER=""
ownership_downgrade_blocked() {
    local to="$1"
    # All three, read from the target commit and not from the working tree:
    # the access rules, the only writer of the owned rows, and the migration
    # that creates the columns. A commit with one of them is a commit from the
    # middle of the change.
    if git cat-file -e "$to:app/project_access.py" 2>/dev/null \
        && git cat-file -e "$to:app/services/evaluation_store.py" 2>/dev/null \
        && git cat-file -e "$to:alembic/versions/b7d41f2e9c35_project_ownership_and_access_audit.py" 2>/dev/null; then
        return 1
    fi
    local ok=0
    pg_ask "$OWNERSHIP_PROBE_SQL" '^ownership:[0-9]+$' || ok=1
    OWNERSHIP_PROBE_ANSWER="$PG_ANSWER"
    [ "$ok" = 0 ] && [ "$PG_ANSWER" = "ownership:0" ] && return 1
    return 0
}

# What is left running after a refusal, said plainly. The operator reads this
# at the moment the site is down, and the wrong sentence sends them the wrong
# way: before the cutover there was only one, "the new code is still running".
#
# Between the stop and a verified start the state is observed, not assumed:
# "STOPPED" is printed only when docker confirms every backend and scheduler
# container stopped. Anything else is `unknown`, and said to be.
state_after_refusal() {
    case "$APPS_STATE" in
        started)
            echo "  The new backend and scheduler are running: fix forward, closing external"
            echo "  access if need be." ;;
        untouched)
            echo "  Nothing had been stopped yet: what ran before this run is still running." ;;
        *)
            if [ "$APPS_STATE" != unknown ] && confirm_apps_stopped backend scheduler; then
                APPS_STATE=stopped
                echo "  backend, scheduler and nginx are STOPPED and the site is not serving. That"
                echo "  is deliberate: neither the old code (refused above) nor the new one (not"
                echo "  verified) is started without an operator. Fix forward with a corrected"
                echo "  release through this script; restoring a backup is a separate, emergency"
                echo "  operation. Do not start containers by hand. See docs/DEPLOY.md."
            else
                APPS_STATE=unknown
                echo "  backend and scheduler could NOT be confirmed stopped (see above), and nginx"
                echo "  may be stopped too. Code may be running on a schema this run cannot vouch"
                echo "  for. Inspect with \`docker compose -p $PROJECT ps -a\` before anything"
                echo "  else; do not start containers by hand. See docs/DEPLOY.md."
            fi ;;
    esac
}

cutover_stage() {
    CUTOVER_STAGE="$1"
    echo "  cutover: $1"
    journal_write mutating "cutover: $1"
}

# Every client transaction on the database has ended, other than this
# question's own. Polled for DRAIN_SECONDS. Returns 0 when quiet, 1 when the
# time ran out, 2 when the answer could not be read -- which must refuse, not
# pass, because an unreadable count of zero is how a check stops checking.
SESSIONS_SQL="SELECT 'active:' || count(*) FROM pg_catalog.pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() AND backend_type = 'client backend' AND state IS DISTINCT FROM 'idle'"
wait_for_quiet_database() {
    local i=0
    while :; do
        if ! pg_ask "$SESSIONS_SQL" '^active:[0-9]+$'; then
            echo "  could not count open transactions (answer '${PG_ANSWER:-nothing}')" >&2
            return 2
        fi
        [ "$PG_ANSWER" = active:0 ] && return 0
        i=$((i + 1))
        if [ "$i" -ge "$DRAIN_SECONDS" ]; then
            echo "  ${PG_ANSWER#active:} client transaction(s) still open after ${DRAIN_SECONDS}s" >&2
            return 1
        fi
        sleep 1
    done
}

# The migrator is gone, and that is known rather than inferred: the client that
# created it has returned on its own, every container that client could have
# made is found and removed, docker confirms the container is no longer there,
# no database session carries its application_name -- and, while the
# applications are stopped, no open transaction from anyone. Idempotent.
#
# Returns 0 when that is confirmed, 1 when it is not, and 2 when it cannot be
# known -- the creating client has not returned, so a create or start it sent
# may still land after any check made now. No number of empty `docker ps`
# answers removes that doubt: an earlier version killed the client and then
# waited a fixed settle window for a late create to appear, and a create
# accepted later than the window would have been missed. Every caller treats 1
# and 2 as "the schema may not be read and no old code may start".
ensure_migrator_gone() {
    [ -n "$MIGRATOR_NAME" ] || return 0
    [ "$MIGRATOR_GONE" = 1 ] && return 0
    local i=0 ids="" id gone ans
    # The creating client is never killed. If it is still running, wait for it
    # -- it is in its own session, so the operator's Ctrl-C did not reach it --
    # up to what is left of its budget; if it has still not returned, nothing
    # can be confirmed.
    if [ "$MIGRATOR_CREATE" = running ]; then
        echo "  waiting for the migrator's creating client to return" >&2
        while kill -0 "$MIGRATOR_CREATE_PID" 2>/dev/null; do
            if [ $((SECONDS - MIGRATOR_CREATE_STARTED)) -ge "$MIGRATOR_CREATE_SECONDS" ]; then
                MIGRATOR_CREATE=unknown
                break
            fi
            sleep 1
        done
        if [ "$MIGRATOR_CREATE" = running ]; then
            local crc=0
            wait "$MIGRATOR_CREATE_PID" 2>/dev/null || crc=$?
            identify_migrator "$crc"
        fi
    fi
    if [ "$MIGRATOR_CREATE" = unknown ]; then
        echo "  the migrator $MIGRATOR_NAME was never positively identified: its creating" >&2
        echo "  client did not return, or returned without a container docker can name, so" >&2
        echo "  a create or start it sent may still land; whether a migrator exists cannot" >&2
        echo "  be known" >&2
        return 2
    fi
    # A known object from here: the id docker gave for this run's own name.
    # It is removed and its absence confirmed below; the name is asked again in
    # case the same name names something else as well.
    [ -z "$MIGRATOR_ID" ] || ids="$MIGRATOR_ID"
    if ! ans="$(docker ps -aq --no-trunc --filter "name=^/${MIGRATOR_NAME}\$" 2>/dev/null)"; then
        echo "  docker cannot say whether a container named $MIGRATOR_NAME exists" >&2
        return 1
    fi
    for id in $ans; do case " $ids " in *" $id "*) ;; *) ids="$ids $id" ;; esac; done
    for id in $ids; do
        i=0
        while :; do
            # Positive: the daemon says this id no longer exists. A removed
            # container cannot be started again, so nothing it was asked to do
            # can still happen.
            if gone="$(docker inspect -f '{{.Id}}' "$id" 2>&1)"; then
                echo "  stopping and removing the migrator container ${id:0:12}" >&2
                docker stop -t 10 "$id" >/dev/null 2>&1 || true
                docker rm -f "$id" >/dev/null 2>&1 || true
            else
                case "$gone" in
                    *"No such"*) break ;;
                    *) echo "  docker cannot say whether ${id:0:12} still exists: $gone" >&2 ;;
                esac
            fi
            i=$((i + 1))
            if [ "$i" -ge "$MIGRATOR_GONE_SECONDS" ]; then
                echo "  the migrator container ${id:0:12} could not be confirmed removed within ${MIGRATOR_GONE_SECONDS}s" >&2
                return 1
            fi
            sleep 1
        done
    done
    MIGRATOR_ID=""
    # Its server-side session. A backend whose client vanished mid-statement
    # can run on until it next touches the socket; terminating it rolls its
    # transaction back now rather than whenever it notices. Repeated until the
    # same question finds no such session at all.
    i=0
    while :; do
        if ! pg_ask "SELECT 'migrator:' || count(pg_terminate_backend(pid)) FROM pg_catalog.pg_stat_activity WHERE application_name = '$MIGRATOR_NAME'" '^migrator:[0-9]+$'; then
            echo "  could not ask the database about the migrator's session (answer '${PG_ANSWER:-nothing}')" >&2
            return 1
        fi
        [ "$PG_ANSWER" = migrator:0 ] && break
        echo "  terminated ${PG_ANSWER#migrator:} session(s) of the migrator; asking again" >&2
        i=$((i + 1))
        if [ "$i" -ge "$MIGRATOR_GONE_SECONDS" ]; then
            echo "  the migrator's database session survived ${MIGRATOR_GONE_SECONDS}s of termination" >&2
            return 1
        fi
        sleep 1
    done
    # And, while nothing else should be talking to the database, nobody else.
    if [ "$APPS_STATE" != started ]; then
        wait_for_quiet_database || return 1
    fi
    MIGRATOR_GONE=1
    echo "  the migrator is gone and its transaction has ended" >&2
    return 0
}

# Every container of these services is stopped -- replicas included, which is
# why this lists containers by label rather than asking `ps -q <service>` for
# one. Refuses when the list cannot be read: "could not see one running" is
# not "none running".
confirm_apps_stopped() {
    local svc out live=""
    for svc in "$@"; do
        out="$(docker ps -a \
            --filter "label=com.docker.compose.project=$PROJECT" \
            --filter "label=com.docker.compose.service=$svc" \
            --format '{{.ID}} {{.State}}' 2>/dev/null)" \
            || { echo "  cannot read the state of $svc's containers" >&2; return 1; }
        while read -r cid state; do
            [ -n "$cid" ] || continue
            case "$state" in
                exited|created|dead) ;;
                *) live="$live $svc:$cid:${state:-unknown}" ;;
            esac
        done <<< "$out"
    done
    if [ -n "$live" ]; then
        echo "  still alive after stop:$live" >&2
        return 1
    fi
    return 0
}

# The one migrator, under a name tied to this run, from the pinned image,
# against a deadline -- and with an identity this script holds.
#
# Created detached (`run -d`), so the client's work ends when the container
# exists and has been started, and the container is then a thing with an id
# that can be waited on, stopped and removed. The creating client runs in its
# own session, so a Ctrl-C at the terminal reaches this script and not it, and
# it is never killed: a client killed mid-request is exactly the case whose
# outcome nobody can know. A client that does not return within
# MIGRATOR_CREATE_SECONDS leaves the migrator's existence unknown, and that is
# reported as such -- never resolved by waiting a little and assuming.
#
# PGAPPNAME tags its session, so ensure_migrator_gone can find it on the
# server. Returns the migrator's exit code, 124 when the deadline passed (it is
# then still running and the caller must stop it), 125 when it could not be
# created or its creation could not be confirmed.
#
# What the creating client left behind, decided once it has returned with
# status $1. The only state that lets anything proceed is a container docker
# names for this run's own name: an object with an id, which can be removed and
# whose absence can then be confirmed. Anything else -- the client exited
# non-zero after a lost response, exited 0 yet docker names nothing, or docker
# cannot be asked -- is `unknown`, whatever the status said: a client that
# failed does not show that the daemon did not accept its create or start, and
# a create accepted then can still land later.
identify_migrator() {
    local rc="$1" got
    MIGRATOR_ID=""
    if got="$(docker inspect -f '{{.Id}}' "$MIGRATOR_NAME" 2>/dev/null)" \
        && [[ "$got" =~ ^[A-Za-z0-9]+$ ]]; then
        MIGRATOR_ID="$got"
        MIGRATOR_CREATE=returned
    else
        MIGRATOR_CREATE=unknown
        echo "  no container is named $MIGRATOR_NAME after its client exited $rc" >&2
    fi
}

run_migrator() {
    MIGRATOR_NAME="$(printf '%s-migrate-%s' "$PROJECT" "$RUN_ID" | tr -cd 'A-Za-z0-9_.-')"
    MIGRATOR_GONE=0
    MIGRATOR_ID=""
    journal_write mutating "cutover: migrating with $MIGRATOR_NAME"
    local out rc=0 state started
    out="$(mktemp)" || return 125
    MIGRATOR_CREATE=running
    MIGRATOR_CREATE_STARTED=$SECONDS
    setsid -w "${DCP[@]}" run -d --no-deps -T --name "$MIGRATOR_NAME" \
        -e "PGAPPNAME=$MIGRATOR_NAME" migrate < /dev/null > "$out" 2>&1 &
    MIGRATOR_CREATE_PID=$!
    while kill -0 "$MIGRATOR_CREATE_PID" 2>/dev/null; do
        if [ $((SECONDS - MIGRATOR_CREATE_STARTED)) -ge "$MIGRATOR_CREATE_SECONDS" ]; then
            MIGRATOR_CREATE=unknown
            echo "  the migrator's creating client did not return within ${MIGRATOR_CREATE_SECONDS}s" >&2
            rm -f "$out"
            return 125
        fi
        sleep 1
    done
    wait "$MIGRATOR_CREATE_PID" || rc=$?
    sed 's/^/  migrate: /' "$out" >&2
    rm -f "$out"
    identify_migrator "$rc"
    if [ "$MIGRATOR_CREATE" = unknown ]; then
        echo "  the migrator's creating client exited $rc and docker names no container" >&2
        echo "  $MIGRATOR_NAME; a client that failed does not show that its create was refused" >&2
        return 125
    fi
    if [ "$rc" -ne 0 ]; then
        echo "  the migrator was created (${MIGRATOR_ID:0:12}) but its client exited $rc" >&2
        return 125
    fi
    started=$SECONDS
    while :; do
        state="$(docker inspect -f '{{.State.Status}}' "$MIGRATOR_ID" 2>/dev/null || echo unreadable)"
        case "$state" in
            exited|dead) break ;;
            created|running|restarting|paused) ;;
            *) echo "  the migrator's state cannot be read ('$state')" >&2; return 125 ;;
        esac
        if [ $((SECONDS - started)) -ge "$MIGRATE_DEADLINE" ]; then
            MIGRATE_TIMED_OUT=1
            echo "  the migrator passed its ${MIGRATE_DEADLINE}s deadline" >&2
            return 124
        fi
        sleep 1
    done
    docker logs "$MIGRATOR_ID" 2>&1 | sed 's/^/  migrate: /' >&2 || true
    rc="$(docker inspect -f '{{.State.ExitCode}}' "$MIGRATOR_ID" 2>/dev/null || echo unreadable)"
    case "$rc" in
        ''|*[!0-9]*) echo "  the migrator's exit code cannot be read ('$rc')" >&2; return 125 ;;
    esac
    return "$rc"
}

# The image of every application service of this run, pinned by id.
#
# A tag is a pointer. Building once and naming the result `sora_earth_app:current`
# made the migrator, the bootstrap and the applications share a *name*; each
# `run` and `up` still resolved that name when it ran, so a build, a pull or a
# retag between them -- another deployment's, an operator's -- would migrate
# with one image and serve another, and the id comparison afterwards would only
# report it. The override below names the id itself for every service that
# uses the image, and every compose command of the cutover from the migrator
# to the last `up` carries it: DCP, never DC.
#
# Only for services the configuration declares, profiles included: an override
# for a service the base file lacks creates that service -- with an image and no
# command -- and the next `up --remove-orphans` would start it.
pin_application_image() {
    [[ "$APP_IMAGE_ID" =~ ^sha256:[A-Za-z0-9._-]+$ ]] \
        || fail "the built image id '$APP_IMAGE_ID' is not an image id; nothing can be pinned to it"
    local all svc pinned=""
    all="$("${DC[@]}" --profile '*' config --services 2>/dev/null)" \
        || fail "the services of $COMPOSE could not be listed, so the image cannot be pinned"
    PIN_FILE="$(mktemp "${TMPDIR:-/tmp}/sora-image-pin.XXXXXX")" \
        || fail "cannot create the image pin override"
    {
        echo "services:"
        for svc in migrate bootstrap-users backend scheduler; do
            if grep -qxF -- "$svc" <<<"$all"; then
                printf '  %s:\n    image: "%s"\n' "$svc" "$APP_IMAGE_ID"
                pinned="$pinned $svc"
            fi
        done
    } > "$PIN_FILE" || fail "cannot write the image pin override"
    DCP=("${DC[@]}" -f "$PIN_FILE")
    echo "  pinned${pinned:- nothing} to ${APP_IMAGE_ID:0:19}"
}
DCP=("${DC[@]}")

# A commit can be merged before it may be deployed.
#
# Work that lands in stages -- the ownership change is the first: its schema
# and routes are merged while the cutover that makes them safe to run is not --
# carries a `.deploy-blocked` file whose first line says why. The question is
# asked of the *target* commit, not of the working tree, so it holds for a
# deploy and for `--rollback` alike, and it is asked before checkout, build,
# migration or any container change. There is no override: the stage that
# completes the work removes the file, and that commit is what gets deployed.
#
# The file stops this script only. A copy of the script from before this
# function existed does not know to look.
refuse_blocked_target() {
    local to="$1" reason
    git cat-file -e "$to:.deploy-blocked" 2>/dev/null || return 0
    reason="$(git cat-file -p "$to:.deploy-blocked" 2>/dev/null | head -n 1 || true)"
    fail "${to:0:12} is marked not deployable by .deploy-blocked: ${reason:-no reason given}. Nothing was changed; deploy a commit without the marker."
}

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
    # The pin override is this run's alone, and every path out passes here --
    # abort_deployment's `exit` included.
    [ -z "$PIN_FILE" ] || rm -f "$PIN_FILE"
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
        --finalize)
            # Reconcile the ledger after a run that did everything and died
            # before recording it. Changes nothing: no build, no migration, no
            # `up`, no checkout. It proves the end state and writes the record
            # that is missing, or it refuses (#160).
            MODE=finalize; shift ;;
        *) fail "unknown argument '$1'" ;;
    esac
done

# The budgets are numbers, checked before anything is read or locked. Each is
# used in an arithmetic comparison, and a value that is not a plain decimal
# integer does not fail there safely: `[ "$i" -ge abc ]` is an error that
# `while` reads as false, so a mistyped deadline silently became no deadline;
# and a leading zero is octal to bash arithmetic, so 08 is an error and 010 is
# eight.
require_seconds() {   # name value minimum
    case "$2" in
        ''|*[!0-9]*|0?*) fail "$1 must be a whole number written without leading zeros, not '$2'" ;;
    esac
    [ "${#2}" -le 5 ] || fail "$1 is implausibly large: '$2'"
    [ "$2" -ge "$3" ] || fail "$1 must be at least $3, not '$2'"
}
require_seconds DEPLOY_STOP_TIMEOUT "$STOP_TIMEOUT" 1
require_seconds DEPLOY_DRAIN_SECONDS "$DRAIN_SECONDS" 1
require_seconds DEPLOY_MIGRATE_DEADLINE "$MIGRATE_DEADLINE" 1
require_seconds DEPLOY_MIGRATOR_GONE_SECONDS "$MIGRATOR_GONE_SECONDS" 1
require_seconds DEPLOY_MIGRATOR_CREATE_SECONDS "$MIGRATOR_CREATE_SECONDS" 1
require_seconds HEALTH_ATTEMPTS "$HEALTH_ATTEMPTS" 1
require_seconds HEALTH_DELAY "$HEALTH_DELAY" 0

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
if [ "$MODE" = finalize ]; then
    # The journal is the input here, not an obstacle. Without one there is
    # nothing to reconcile, and finalising anyway would invent a record.
    if [ ! -f "$JOURNAL" ]; then
        # A second --finalize is the expected shape of a retry, so it says what
        # is true rather than sounding like a fault.
        if [ -L "$MANIFEST_DIR/latest" ] || [ -e "$MANIFEST_DIR/latest" ]; then
            _last="$MANIFEST_DIR/$(readlink "$MANIFEST_DIR/latest" 2>/dev/null || true)"
            if grep -q '^finalized      yes' "$_last" 2>/dev/null; then
                echo "  already finalized: $(basename "$_last")"
                echo "  nothing to do; no journal remains and the record exists"
                exit 0
            fi
        fi
        fail "no unfinished deployment is recorded in $JOURNAL; nothing to finalize"
    fi
    echo "  reconciling the record of an interrupted run:"
    sed 's/^/    /' "$JOURNAL"
    # Everything below is read from what that run wrote, so the record names
    # the commit it actually deployed rather than whatever origin/main is now.
    TARGET="$(awk '/^target /{print $2}' "$JOURNAL")"
    FINALIZED_RUN="$(awk '/^run /{print $2}' "$JOURNAL")"
    JOURNAL_MODE="$(awk '/^mode /{print $2}' "$JOURNAL")"
    # A journal with no run id was not written by this script. Reconciling it
    # would publish a manifest whose provenance field reads `unknown`, which is
    # a record that cannot be traced back to anything.
    if [ -z "$FINALIZED_RUN" ]; then
        fail "the journal records no run id; it was not written by this script and cannot be reconciled automatically"
    fi
    # `if`, not `A && B || C`. The chain reads as if-then-else and is not one:
    # this file already carries that lesson a few hundred lines down, where the
    # same shape swallowed a failure under `set -e`.
    if [ -z "$TARGET" ] || [ "$TARGET" = unset ]; then
        fail "the journal records no target commit; it cannot be reconciled automatically"
    fi
    [ "$JOURNAL_MODE" = deploy ] \
        || fail "the interrupted run was '$JOURNAL_MODE', and only a deploy can be finalized"
    # `mutating` is the only state a finished-but-unrecorded run can be left in.
    # `rollback-impossible` or `rollback-failed` describe a run that refused and
    # tried to undo itself: what is running then is whatever the rollback
    # managed, which is exactly the state nobody may write a manifest for.
    JOURNAL_STATE="$(awk '/^state /{print $2}' "$JOURNAL")"
    [ "$JOURNAL_STATE" = mutating ] \
        || fail "the journal is in state '$JOURNAL_STATE', not 'mutating'; only a run interrupted while deploying can be finalized"
    JOURNAL_STARTED="$(awk '/^started /{print $2}' "$JOURNAL")"
    JOURNAL_PREVIOUS="$(awk '/^previous /{print $2}' "$JOURNAL")"
    [ -n "$JOURNAL_STARTED" ] \
        || fail "the journal records no start time; container ages cannot be checked against it"
    # The script's own checkout, read before `cd "$REPO"` takes effect for git.
    RECOVERY_TOOL_SHA="$(git -C "$(cd "$(dirname "$0")/.." && pwd)" rev-parse HEAD 2>/dev/null || true)"
    echo "  run       ${FINALIZED_RUN:-unknown}"
    echo "  started   $JOURNAL_STARTED"
    echo "  target    $TARGET"
    echo "  previous  ${JOURNAL_PREVIOUS:-none}"
elif [ -f "$JOURNAL" ]; then
    echo "  an unfinished deployment is recorded:" >&2
    sed 's/^/    /' "$JOURNAL" >&2
    fail "a previous run did not finish. Inspect the state, then either run --finalize (which proves the deployment completed and writes the missing record) or remove $JOURNAL to deploy over it"
fi

# A local edit is exactly how the nginx upstream came to differ from the
# repository for weeks with nobody able to tell. Required in both modes.
DIRTY="$(git status --porcelain)"
[ -z "$DIRTY" ] || { echo "$DIRTY" >&2; fail "working tree is not clean"; }

git fetch --quiet origin main
ORIGIN_MAIN="$(git rev-parse origin/main)"

if [ "$MODE" = finalize ]; then
    # Nothing is chosen here -- the target came from the journal. What is
    # checked is that the checkout still is what that run deployed. If somebody
    # moved it since, this run cannot describe what is running.
    HEAD_SHA="$(git rev-parse HEAD)"
    [ "$HEAD_SHA" = "$TARGET" ] \
        || fail "the checkout is at $HEAD_SHA and the interrupted run targeted $TARGET; the record cannot be reconciled from a tree that has moved"
    echo "  finalizing the record for ${TARGET:0:12}"
elif [ "$MODE" = deploy ]; then
    BRANCH="$(git rev-parse --abbrev-ref HEAD)"
    [ "$BRANCH" = "main" ] || fail "on branch '$BRANCH'; production is deployed from main only"
    HEAD_SHA="$(git rev-parse HEAD)"
    [ "$HEAD_SHA" = "$ORIGIN_MAIN" ] \
        || fail "HEAD ($HEAD_SHA) is not origin/main ($ORIGIN_MAIN); merge first, then deploy"
    TARGET="$HEAD_SHA"
    refuse_blocked_target "$TARGET"
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
    # Before the accounts question and before the checkout below.
    refuse_blocked_target "$TARGET"
    # Before anything changes: code from before persistent accounts would lift
    # every block and reset made since (accounts_downgrade_blocked).
    if accounts_downgrade_blocked "$TARGET"; then
        fail "${TARGET:0:12} predates persistent accounts and the users table exists (or could not be checked): that code refuses the migrated schema and would ignore every block and reset. Roll back to a commit that has app/accounts.py, or fix forward."
    fi
    # And, independently, code from before project ownership once the schema
    # has any part of it (ownership_downgrade_blocked). Also before the
    # checkout, so a refusal leaves the tree and every container as they were.
    if ownership_downgrade_blocked "$TARGET"; then
        fail "${TARGET:0:12} has no project ownership and the database has ownership columns or audit tables (probe answered '${OWNERSHIP_PROBE_ANSWER:-nothing}', and anything but ownership:0 refuses): that code would write rows the schema refuses and serve every saved project to everyone. Roll back to a commit with app/project_access.py, or fix forward."
    fi
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

# In finalize the containers running now are the *new* ones, so reading them
# here would record the deployment as having replaced itself. The interrupted
# run wrote what it replaced into the journal before it touched anything; that
# is the only honest source, and if it is absent the field says so rather than
# guessing.
if [ "$MODE" = finalize ]; then
    PREV_IMAGES="$(sed -n '/^images$/,$p' "$JOURNAL" | tail -n +2 | sed 's/^  //')"
    [ -n "$PREV_IMAGES" ] || PREV_IMAGES="(not recorded by the interrupted run)"
    PRE_CIDS="$(awk '/^pre_containers /{$1=""; print}' "$JOURNAL" | sed 's/^ //')"
    PREV_IMAGE_IDS=""
    # And the commit from the same place, for the same reason.
    #
    # It was read above from the last published manifest, which is right for a
    # deploy and only accidentally right here: the manifest chain and the
    # journal agree about what was running exactly while nothing has gone wrong
    # between them. Taking the images from one source and the commit from
    # another leaves a record whose two halves can describe different states.
    #
    # A disagreement is not reconciled to either side. It means the manifest
    # moved after the interrupted run recorded what it was replacing, and
    # nothing here can tell which of the two describes what is serving.
    if [ -n "$JOURNAL_PREVIOUS" ]; then
        if [ -n "$PREV_COMMIT" ] && [ "$PREV_COMMIT" != "$JOURNAL_PREVIOUS" ]; then
            fail "the interrupted run recorded $JOURNAL_PREVIOUS as the commit it replaced, but the last manifest names $PREV_COMMIT as deployed; they disagree about what was running and the record cannot be reconciled automatically"
        fi
        PREV_COMMIT="$JOURNAL_PREVIOUS"
    fi
else
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
fi   # end of the finalize / normal split for the state being replaced

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
# The migrator's creating client runs in a session of its own (run_migrator).
command -v setsid >/dev/null 2>&1 \
    || fail "setsid (util-linux) is required to keep the migrator's creating client out of reach of signals"

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

# What backend and scheduler need running before they start, transitively, from
# the rendered configuration. The cutover starts these on their own and then
# the two applications with --no-deps, so no `up` between the stop and the
# forced recreate has backend or scheduler -- or anything that depends on them,
# such as nginx or prometheus -- in its plan. A generic `up` there would start
# the stopped old containers again on the new schema.
APP_DEPS="$("${DC[@]}" config --format json 2>/dev/null | python3 -c '
import json, sys
svc = json.load(sys.stdin).get("services") or {}
seen, todo = set(), [s for s in ("backend", "scheduler") if s in svc]
while todo:
    for dep in (svc.get(todo.pop()) or {}).get("depends_on") or {}:
        if dep not in seen:
            seen.add(dep)
            todo.append(dep)
print(" ".join(sorted(seen - {"backend", "scheduler", "nginx"})))
')" || fail "the dependencies of backend and scheduler could not be read from the configuration"
echo "  backend and scheduler depend on: ${APP_DEPS:-nothing declared}"

# The guards must be armed in the configuration about to be deployed.
#
# `app/auth.py` refuses a development JWT key and refuses the built-in account
# passwords, and `app/secret_validation.py` checks every production secret --
# all three only when `SORA_ENV == "production"`. For months the compose file
# said `ENV: production`, which is the right intent under a name nothing in
# `app/` reads, so all three were off on a host whose configuration looked
# correct. Reported privately as GHSA-x724-6jh7-g3fr.
#
# Read out of `config`, not out of the running container: this is a claim about
# the deployment being made. What the container ended up with is checked after
# it starts, further down, the same split the prometheus configuration uses.
#
# `SORA_ENV_ACKNOWLEDGE=<value>` exists for the one case this must not block:
# deliberately deploying a non-production configuration to this host. It
# carries the observed value, so it cannot be set from a runbook in advance.
# Judged over the services the configuration actually declares. A compose file
# without `backend` has no guard to arm, and demanding one there would refuse
# for a reason that is not about secrets -- which is what the first version did
# to eight unrelated cases in tests/test_deploy_production.sh, masking their
# real refusals with this one.
_declared_env="$("${DC[@]}" config --format json 2>/dev/null \
    | python3 -c 'import json,sys
cfg = json.load(sys.stdin)["services"]
present = [s for s in ("backend", "scheduler") if s in cfg]
if not present:
    print("no-app-service")
else:
    print(",".join(
        f"{s}=" + str((cfg[s].get("environment") or {}).get("SORA_ENV"))
        for s in present))' 2>/dev/null || echo "unreadable")"

case "$_declared_env" in
    "no-app-service")
        echo "  no backend or scheduler declared; no secret guard to arm here"
        ;;
    "backend=production,scheduler=production"|"backend=production"|"scheduler=production")
        echo "  SORA_ENV=production for every declared app service: the secret guards are armed"
        ;;
    *)
        [ "${SORA_ENV_ACKNOWLEDGE:-}" = "$_declared_env" ] || fail \
"the deployment would not arm the production guards: $_declared_env

     app/auth.py refuses a development JWT key and refuses the built-in
     account passwords, and app/secret_validation.py checks every secret --
     each only when SORA_ENV == \"production\". Without it the application
     starts with the literals published in this repository.

     Set SORA_ENV: production for backend and scheduler in
     $(basename "$COMPOSE"), or, to deploy a non-production configuration
     here deliberately, re-run with
     SORA_ENV_ACKNOWLEDGE=$_declared_env"
        echo "  WARNING: deploying without the production guards, acknowledged" >&2
        ;;
esac

# ----------------------------------------------------------------- the seed

step "the model seed is materialized"

# A checkout where git-lfs did not smudge leaves models/*.pkl and *.pth as
# pointer stubs; a container built from one loads a pointer as a model and fails
# at the first prediction, with health green until then (the shape of #196).
# Refuse before building. Still in the preflight, so a refusal here has nothing
# to roll back. The guard reads only tracked files under models/, so a checkout
# with none -- this deploy guard's own test sandbox -- passes.
SEED_CHECKOUT="$REPO" bash "$(dirname "$0")/check_seed_materialized.sh" \
    || fail "the model seed is not materialized in $REPO; run 'git lfs pull' and retry"

# ------------------------------------------------------------------- deployment

step "deploying"

# From here a refusal has something to undo, so `fail` routes through
# abort_deployment. Set immediately before `up` rather than earlier: a refusal
# during the preflight has nothing to roll back, and rolling back anyway would
# rebuild the previous state for no reason.
if [ "$MODE" = finalize ]; then
    # Nothing below this point in the deployment section runs. finalize does not
    # build, does not migrate, does not `up`, does not recreate: the deployment
    # already happened, and repeating any of it would be a second deployment
    # wearing the name of a bookkeeping fix.
    echo "  skipped: finalize changes nothing, it records what is already running"
    APP_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$APP_IMAGE" 2>/dev/null || true)"
    [ -n "$APP_IMAGE_ID" ] \
        || fail "$APP_IMAGE does not exist, so what is running cannot be checked against it"

    # A tag is a pointer and `docker inspect` describes whatever is running now.
    # Neither says *when* it started, so containers predating the interrupted
    # run -- on the same tag, because it had not moved yet -- would satisfy an
    # image comparison and let this write a manifest for a deployment that never
    # happened. The age is the discriminator.
    _started_epoch="$(date -u -d "$JOURNAL_STARTED" +%s 2>/dev/null \
        || date -u -j -f '%Y-%m-%dT%H:%M:%SZ' "$JOURNAL_STARTED" +%s 2>/dev/null || true)"
    [ -n "$_started_epoch" ] \
        || fail "could not parse the journal start time '$JOURNAL_STARTED'"
    for _svc in backend scheduler; do
        _cid="$("${DC[@]}" ps -q "$_svc" 2>/dev/null || true)"
        [ -n "$_cid" ] || fail "$_svc has no container; the deployment did not complete"
        _created="$(docker inspect -f '{{.Created}}' "$_cid" 2>/dev/null || true)"
        _created_epoch="$(date -u -d "$_created" +%s 2>/dev/null \
            || date -u -j -f '%Y-%m-%dT%H:%M:%S' "${_created%%.*}" +%s 2>/dev/null || true)"
        [ -n "$_created_epoch" ] \
            || fail "could not read when $_svc was created; its age cannot be checked"
        [ "$_created_epoch" -ge "$_started_epoch" ] || fail \
            "$_svc was created at $_created, before the interrupted run started at $JOURNAL_STARTED; it is not part of that deployment"
        echo "  $_svc created $_created, after the run began"
    done

    # The schema, asked of the code that is running rather than of the tree.
    # Read-only -- one SELECT of alembic_version, no lock, no DDL -- and it is
    # the same check the entrypoint makes on every start (#125).
    "${DC[@]}" exec -T backend python3 ./scripts/verify_schema_head.py \
        || fail "the running backend does not consider the schema to be at head; the migration this run applied cannot be confirmed"
else
# The cutover, in this order and no other (#125 for the one migrator; project
# ownership for the stop before it):
#
#   build once, pin the id     the migration and the applications are one image,
#                              named by id in an override every later compose
#                              command carries
#   postgres healthy           `run --no-deps` bypasses compose's health gate
#   -- PHASE=mutating and the journal: nothing above changed a container --
#   stop nginx                 no new request reaches anything
#   stop backend, scheduler    -t $STOP_TIMEOUT to finish what they hold, then
#                              confirmed stopped -- every container, replicas too
#   database quiet             no client transaction left open
#   one migrator               named for this run, under a deadline
#   bootstrap-users            from the same image
#   dependencies               started on their own, --no-deps
#   backend, scheduler         --force-recreate from the pinned image: the first
#                              start after the migration is never a revival of
#                              the stopped old containers
#   everything else, nginx     only once the new code runs on that image
#
# Why the old code stops first. Since project ownership the old backend's
# INSERT into evaluations and predictions_log is refused by the new schema --
# the default scope is `pilot`, which needs an owner -- and its reads ignore the
# scope; the old scheduler reads the same tables into shared forecasts and
# metrics. Running either beside the migration, or after it, is a mixed rollout
# the schema was designed to make impossible. So no code serves while the
# schema changes, and a refusal from here on may leave the site stopped: the
# messages say which (state_after_refusal).
#
# What this does not do: undo DDL. A migration that failed partway leaves the
# schema where it left it -- the pending revisions run in one transaction, so
# they commit whole or not at all -- and the rollback barriers decide from what
# the catalogue shows, not from what was intended.
echo "  building the application image"
# Built once, here, and not again. backend, scheduler and migrate all reference
# ${SORA_APP_IMAGE}, so the image the migration runs from and the image the
# application starts from are the same by construction. Two `--build`
# invocations of the same Dockerfile are two build results, and "the migration
# came from the deployed image" would have been a resemblance rather than a
# fact.
"${DC[@]}" build backend \
    || fail "the application image would not build"

APP_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$APP_IMAGE" 2>/dev/null || true)"
[ -n "$APP_IMAGE_ID" ] \
    || fail "built $APP_IMAGE but cannot read its id, so nothing below can be verified against it"
pin_application_image

# `run --no-deps migrate` deliberately bypasses compose's dependency health
# gate. On a fresh volume initdb takes long enough for a TCP connection to be
# refused, so wait for PostgreSQL's own healthcheck before running Alembic.
"${DC[@]}" up -d --wait postgres \
    || fail "postgres would not start, so migrations cannot run"

# From here a refusal has something to undo -- the first stop is the first
# change to what serves -- so `fail` routes through abort_deployment. Not
# earlier: a build that fails, or a database that will not come up, has
# changed nothing that runs, and "rolling back" from there would restore and
# restart the very containers that were never touched.
PHASE=mutating
# Written before the first change, not after: a journal that appears only once
# the mutation succeeded records nothing about the case it exists for.
journal_write mutating

# Incoming requests first, so nothing new reaches a backend that is about to
# stop. nginx is recreated, never restarted, further down.
cutover_stage "stopping nginx"
APPS_STATE=stopping
"${DC[@]}" stop nginx \
    || fail "nginx would not stop; nothing was migrated"

cutover_stage "stopping backend and scheduler"
"${DC[@]}" stop -t "$STOP_TIMEOUT" backend scheduler \
    || fail "backend and scheduler would not stop; nothing was migrated"
# Asked, not assumed from the exit status: `restart: unless-stopped`, a replica,
# or a container started by hand would each leave old code writing to the
# tables the migration is about to change.
confirm_apps_stopped backend scheduler \
    || fail "backend or scheduler is still running, or their state cannot be read; nothing was migrated"
APPS_STATE=stopped

# And what they held. A stopped container's connections close, but a
# transaction the pooler was still finishing, or an operator's session, would
# otherwise make the migration wait on its lock and then fail.
cutover_stage "waiting for open transactions"
_quiet=0
wait_for_quiet_database || _quiet=$?
case "$_quiet" in
    0) ;;
    1) fail "client transactions were still open ${DRAIN_SECONDS}s after the applications stopped; nothing was migrated" ;;
    *) fail "the database could not be asked whether transactions are open; nothing was migrated" ;;
esac

# One migrator, from that exact image, with nothing else connected to change.
cutover_stage "migrating"
_mig=0
run_migrator || _mig=$?
if [ "$_mig" -ne 0 ]; then
    if [ "$MIGRATE_TIMED_OUT" = 1 ]; then
        # Stopped here, and again, idempotently, at the top of the rollback.
        ensure_migrator_gone || true
        fail "the migration did not finish within ${MIGRATE_DEADLINE}s and was stopped. The schema is whatever the catalogue shows once its transaction ended -- inspect it before retrying."
    fi
    if [ "$_mig" = 125 ]; then
        fail "the migrator could not be created, or its creation could not be confirmed; nothing has been recreated."
    fi
    fail "alembic upgrade head failed (exit $_mig); nothing has been recreated. The schema is wherever the migration left it -- inspect it before retrying."
fi
ensure_migrator_gone \
    || fail "the migrator exited but could not be confirmed gone; nothing has been started"

# The built-in accounts, once, into an empty `users` table -- and on every
# deployment after that, a check that an active administrator still exists.
# From the same image as the migration, before anything is recreated: the new
# code reads accounts only from that table, so serving it over an empty one
# would be a site nobody can sign in to. An existing table is never modified
# (scripts/bootstrap_users.py), so SORA_DEFAULT_*_PASSWORD changes nothing once
# accounts exist.
# Only where the checked-out code has the step: a rollback to a commit from
# before persistent accounts has neither the script nor the service.
cutover_stage "bootstrapping accounts"
[ ! -f scripts/bootstrap_users.py ] \
    || "${DCP[@]}" run --rm --no-deps bootstrap-users \
    || fail "the account bootstrap refused; nothing has been recreated. Either the SORA_DEFAULT_*_PASSWORD values are missing on a first deployment, or no active administrator exists."

# What the applications need, and nothing that needs them. `--no-deps` with the
# list computed in the preflight, so this cannot reach backend, scheduler or
# nginx however compose resolves dependencies.
cutover_stage "starting dependencies"
if [ -n "$APP_DEPS" ]; then
    read -r -a _deps <<< "$APP_DEPS"
    "${DCP[@]}" up -d --no-build --no-deps "${_deps[@]}" \
        || fail "the services backend and scheduler depend on would not start: $APP_DEPS"
fi

# The first start of backend and scheduler after the migration, and it is a
# forced recreate from the pinned image -- never `up` on its own, which decides
# from a configuration hash and the image it recorded on the container, and can
# simply start the stopped old container again. Measured on production
# 2026-10-26 (Compose v5.6.0): a release that changed only Grafana provisioning
# built a new image under the same tag, and `up --no-build` left backend and
# scheduler on the previous one. Before the cutover that was a refused release;
# after it, it would be the old code on the new schema.
cutover_stage "starting the new backend and scheduler"
# `starting` until compose says both are up. If it fails partway one of them
# may be running; the refusal then stops and confirms both rather than
# describing a stopped site over a running backend (settle_partial_start).
APPS_STATE=starting
"${DCP[@]}" up -d --no-build --no-deps --force-recreate backend scheduler \
    || fail "backend and scheduler could not be recreated on the built image"
APPS_STATE=started
journal_write mutating "cutover: new backend and scheduler started"

app_image_of() {
    local cid
    cid="$("${DC[@]}" ps -q "$1" 2>/dev/null || true)"
    [ -n "$cid" ] || return 0
    docker inspect -f '{{.Image}}' "$cid" 2>/dev/null || true
}
for svc in backend scheduler; do
    _img="$(app_image_of "$svc")"
    [ -n "$_img" ] || fail "$svc has no container, or its image cannot be read, after start"
    [ "$_img" = "$APP_IMAGE_ID" ] || fail \
        "$svc is running image $_img, not the $APP_IMAGE_ID the migration ran from"
done

# The schema, asked of the code that now runs. The entrypoint refuses to start
# on any other head; this makes the answer part of the run's own evidence.
"${DC[@]}" exec -T backend python3 ./scripts/verify_schema_head.py \
    || fail "the new backend does not consider the schema to be at head"

# Everything else -- grafana, mlflow, an orphan to remove -- only now, when the
# one generic `up` of this run can no longer revive anything old: backend and
# scheduler are already the new containers, on the pinned image. --no-build,
# because everything that builds was built above.
cutover_stage "starting the remaining services"
"${DCP[@]}" up -d --no-build --remove-orphans \
    || fail "the remaining services would not start"
# And it did not move them. Compose may still recreate a service it considers
# diverged; if it did, it must have been onto the same image.
for svc in backend scheduler; do
    _img="$(app_image_of "$svc")"
    [ "$_img" = "$APP_IMAGE_ID" ] || fail \
        "$svc is running image $_img, not the $APP_IMAGE_ID the migration ran from"
done

# nginx is recreated, not restarted. Its configuration is a bind-mounted single
# file, so the container stays pinned to the inode that existed when it was
# created; a git pull replaces the file and the container keeps reading the old
# one. Observed, with `nginx -s reload` re-reading the stale copy.
#
# With the pin, like every `up` after the build. nginx depends on backend, so
# this `up` also decides whether backend matches its configuration -- and
# without the override the configuration names the tag, not the id the
# running container was created from, which is a difference compose resolves
# by recreating backend from whatever the tag means now.
"${DCP[@]}" up -d --force-recreate nginx

# prometheus, for the same reason and with one difference.
#
# `infra/prometheus.yml` is a bind-mounted single file too, and Prometheus reads
# it once at startup. Measured on 2026-09-07: after deploying a new scrape
# target, the file on disk had it and the running container did not -- four days
# uptime -- so #267 was deployed and inert, which from the outside is
# indistinguishable from the scheduler serving nothing. Reloading over HTTP is
# not an option here: `POST /-/reload` answers 403, because the container's
# command is `--config.file=... --storage.tsdb.path=...` and nothing else, so
# `--web.enable-lifecycle` is absent (both read off the running container on
# 2026-09-07, #275).
#
# The difference from nginx: a failure here must not roll the deployment back.
# nginx serves the site; prometheus watches it. Losing the watcher is worth
# saying loudly and is not worth undoing a good deployment for, so this is
# guarded rather than bare -- under `set -e` a bare command would abort.
#
# `--no-deps` because `up <service>` is not a scalpel. prometheus declares
# `depends_on: backend`, and a compose that decided to recreate the dependency
# would recreate the backend *after* nginx has already been recreated -- an
# nginx holding an address that no longer exists, which is the 2026-08-09
# incident exactly (#129). Measured on the host with `--dry-run` on 2026-09-07:
# with and without the flag, only prometheus is touched. The flag is there so
# that stays true rather than because it is true today.
if "${DCP[@]}" up -d --force-recreate --no-deps prometheus >/dev/null 2>&1; then
    echo "  prometheus recreated, so it reads the current infra/prometheus.yml"
else
    echo "  WARNING: prometheus was not recreated; it is still running with the" >&2
    echo "           configuration it started with. Scrape targets added in this" >&2
    echo "           deployment are not being collected." >&2
fi
fi   # end of the mutating section, which finalize skips entirely

# ---------------------------------------------------------------- verification

step "verifying what is actually running"

# The application is running the image the migration ran from.
#
# Everything above arranges for that -- one tag, one build, --no-build on the
# start -- but arrangement is not evidence. If backend came up on a different
# image than $APP_IMAGE_ID, then either something rebuilt behind this script or
# the tag moved, and the migration that just ran belonged to a build nobody is
# serving.
for svc in backend scheduler; do
    _cid="$("${DC[@]}" ps -q "$svc" 2>/dev/null || true)"
    [ -n "$_cid" ] || fail "$svc has no container, so its image cannot be checked"
    _img="$(docker inspect -f '{{.Image}}' "$_cid" 2>/dev/null || true)"
    [ "$_img" = "$APP_IMAGE_ID" ] || fail \
        "$svc is running image $_img, not the $APP_IMAGE_ID the migration ran from"
done

# The scrape configuration prometheus is actually holding, not the one on disk.
#
# Recreating it above is the arrangement; this is the evidence. A container that
# came up with the wrong mount, or an older image, reports the same exit status
# as one that read the file. Warned about rather than fatal, for the same reason
# the recreate is guarded: monitoring must not undo a good deployment (#275).
if _live_prom="$("${DC[@]}" exec -T prometheus cat /etc/prometheus/prometheus.yml 2>/dev/null)"; then
    if [ "$_live_prom" = "$(cat "$REPO/infra/prometheus.yml")" ]; then
        echo "  prometheus is running the checked-in scrape configuration"
    else
        echo "  WARNING: prometheus holds a different scrape configuration than" >&2
        echo "           infra/prometheus.yml. Targets added here are not collected." >&2
    fi
else
    echo "  WARNING: could not read prometheus's configuration to compare it" >&2
fi

# What the process actually received, not what the file declared.
#
# The declaration is checked in the preflight; this is the evidence. A value
# can be declared and still not arrive -- an override file, a stale container,
# a typo in a name -- and the whole point of GHSA-x724-6jh7-g3fr is that a
# correct-looking declaration is not the same as a guard that fires.
#
# Only the name of the variable is printed. No secret is read here.
_live_env="$("${DC[@]}" exec -T backend python3 -c \
    'import app.auth as a; print(a.SORA_ENV)' 2>/dev/null | tr -d '\r')"
if [ "$_live_env" = "production" ]; then
    echo "  backend runs with SORA_ENV=production; the secret guards are active"
else
    echo "  WARNING: backend reports SORA_ENV=${_live_env:-<unreadable>}, so the" >&2
    echo "           dev-secret refusals in app/auth.py and the whole of" >&2
    echo "           app/secret_validation.py are inert (GHSA-x724-6jh7-g3fr)." >&2
fi

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

# Database health, read from the body of /api/v1/health.
#
# 200 on /api/v1/health proves the process is up. It does not prove the database
# is reachable: _check_db returns "degraded" on error, not "unhealthy", so a
# failed database connection still produces 200 "healthy" (#21).
#
# Read after the retry loop above, once the status is known to be 200. Not
# best-effort, unlike the model provenance read further down: a body that cannot
# be fetched or parsed gives "unknown", and a deployment that cannot show its
# database is reachable is not accepted -- the same rollback as any other failed
# check. Bounded like http_code, so a stalled response fails instead of hanging.
DB_HEALTH="unknown"
if _health_body="$(curl -fsS -m 20 -A "$DEPLOY_PROBE_UA" "$SITE/api/v1/health" 2>/dev/null)"; then
    DB_HEALTH="$(printf '%s' "$_health_body" | python3 -c '
import json, sys
try:
    checks = (json.load(sys.stdin) or {}).get("checks") or {}
    db = checks.get("database") or {}
    print(db.get("status") or "unknown")
except Exception:
    print("unknown")
' 2>/dev/null || echo unknown)"
fi

case "$DB_HEALTH" in
    healthy)
        echo "  database health: $DB_HEALTH"
        ;;
    *)
        fail "database health check reports '$DB_HEALTH', expected 'healthy'. The site answers 200 but cannot reach the database."
        ;;
esac

# ------------------------------------------------------------------- the record

# Accepted. Nothing after this point should roll back: the deployment is the
# state that is meant to be running, and a failure while writing the record is
# not a reason to undo it.
PHASE=committed

# ------------------------------------------------------- behaviour, not status

step "the deployed code behaves like the tested code"

# 200 on /health proves the process is up. It does not prove the image contains
# the application that was tested.
#
# On 2026-08-14 it did not. `app/main.py` registers a second catch-all under
# `if _SPA_INDEX.exists()` -- true in the image, false in CI, because CI has no
# built frontend. The handler that tells "no such path" from "wrong verb"
# skipped catch-alls by name and missed that one, so every absent path answered
# 404 in the test suite and 405 on production, on the same commit. Every health
# check was green throughout. It was found by asking the running server a
# question the suite had never asked it (#177).
#
# So the class this covers is "CI and the image build different applications",
# and the only instrument that can see it is the deployed server.
#
# Deliberately after PHASE=committed: a mismatch here stops and does **not**
# roll back. The site has already been proven healthy, so an automatic rollback
# would replace one state whose behaviour is unverified with another, and
# destroy the evidence in the process. The journal stays at `mutating` and no
# manifest is written, which leaves both recoveries open and neither taken:
# `--rollback` if the deployment is wrong, `--finalize` if it turns out to be
# right (#160, #161).

halt_without_rollback() {
    echo >&2
    echo "REFUSED: $*" >&2
    echo >&2
    echo "  Nothing was rolled back and nothing was recorded. What is running is" >&2
    echo "  what this run deployed, and the journal still says so." >&2
    echo >&2
    echo "  Two ways out, and this run takes neither:" >&2
    echo "    $0 --rollback ${PREV_COMMIT:-<the commit that was running before>}" >&2
    echo "        if the behaviour above is wrong" >&2
    echo "    $0 --finalize" >&2
    echo "        if it is right and only this check is mistaken" >&2
    exit 1
}

# The routes are read from the running application, never listed here.
#
# A list in this file would go stale exactly the way the test in #170 did: it
# described an arrangement that had moved, and stayed green. The probe asks the
# server which of its own routes are GET-only and which accept POST, so it
# keeps testing whatever is actually deployed.
#
# Read from the OpenAPI document the process is serving rather than by importing
# the app: a second import loads the models again -- fifteen seconds, measured
# in #50 -- and would describe a fresh process rather than the one serving.
BEHAVIOUR_PROBE=$("${DC[@]}" exec -T backend python3 - <<PROBE 2>/dev/null
import json, urllib.error, urllib.request

BASE = "http://127.0.0.1:8000"
UA = "$DEPLOY_PROBE_UA"


def code(path, method="POST"):
    if not path:
        return None
    req = urllib.request.Request(
        BASE + path, data=b"{}", method=method,
        headers={"Content-Type": "application/json", "User-Agent": UA})
    try:
        return urllib.request.urlopen(req, timeout=15).status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return 0


try:
    spec = json.load(urllib.request.urlopen(BASE + "/openapi.json", timeout=20))
except Exception as exc:
    print(json.dumps({"error": type(exc).__name__}))
    raise SystemExit(0)

paths = spec.get("paths", {})
# Sorted, so two runs against the same image choose the same routes and a
# difference in the result is a difference in behaviour.
# Only the API surface. Unconstrained, the first sorted GET-only path is "/",
# the SPA root -- a 405 from it says nothing about the API, which is where the
# #177 defect lived.
verbs = {p: {v.lower() for v in paths[p]} for p in sorted(paths)
         if p.startswith("/api/")}
get_only = next((p for p in verbs if verbs[p] == {"get"} and "{" not in p), None)

# No POST route is probed, and that is a deliberate departure from the plan
# this step was written to.
#
# The check would have been "a published POST route does not deny POST", with
# the route discovered rather than listed. Measured against production's own
# document, the first sorted candidate is /api/v1/ab/predict -- which writes
# a row to prediction_log. The verification step would have manufactured a
# prediction on every deployment, into the table the platform's analytics read,
# and the ordering is alphabetical: tomorrow's first candidate is whatever is
# added under an earlier name.
#
# A verification step must not change what it verifies. The two questions left
# are the ones the #177 class turns on -- an absent path and a wrong verb --
# and routing answers both before any handler runs.
print(json.dumps({
    "absent_path": "/api/v1/__deploy_probe_absent__",
    "absent": code("/api/v1/__deploy_probe_absent__"),
    "get_only_path": get_only,
    "get_only": code(get_only),
}))
PROBE
)

[ -n "$BEHAVIOUR_PROBE" ] \
    || halt_without_rollback "the behaviour probe produced nothing; the container did not answer"

_probe_field() {
    # `or ''` matters: a JSON null becomes Python None and prints as the four
    # characters `None`, which `[ -n ... ]` then reads as a value. The case
    # where the probe found no route to ask about would have looked like a
    # route named None and passed.
    printf '%s' "$BEHAVIOUR_PROBE" | python3 -c \
        "import json,sys; print(json.load(sys.stdin).get('$1') or '')" 2>/dev/null
}

if [ -n "$(_probe_field error)" ]; then
    halt_without_rollback "the running application would not serve its own route table: $(_probe_field error)"
fi

ABSENT_PATH="$(_probe_field absent_path)"
GET_ONLY_PATH="$(_probe_field get_only_path)"

# `if`, not `A && B || C`. That chain reads as if-then-else and is not one --
# a lesson this file already carries twice.
if [ -z "$GET_ONLY_PATH" ]; then
    halt_without_rollback "the route table offers no GET-only /api/ route to probe; this check cannot report on an application it could not read"
fi

echo "  probing, chosen from the running route table:"
echo "    absent    $ABSENT_PATH"
echo "    GET-only  $GET_ONLY_PATH"

# Through nginx as well as inside the container. The two disagreeing is itself
# the finding: it means a layer between the caller and the application is
# answering, and the tests never see that layer at all.
_external_code() {
    # No `|| echo 000`. curl already writes `000` when it cannot connect *and*
    # exits non-zero, so the fallback appended a second one: the function
    # returned `000000`, matching neither an expected code nor an unexpected
    # one, and an unreachable site would have passed.
    local out
    out="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
        -H 'Content-Type: application/json' -A "$DEPLOY_PROBE_UA" \
        -d '{}' "$SITE$1" 2>/dev/null)"
    printf '%s' "${out:-000}"
}

_behaviour_row() {
    #  name  path  internal  external  expectation-test
    local name="$1" path="$2" inside="$3" outside="$4" want="$5"
    printf '  %-10s %-34s inside=%-4s nginx=%-4s  %s\n' \
        "$name" "$path" "$inside" "$outside" "$want"
}

INSIDE_ABSENT="$(_probe_field absent)"
INSIDE_GET_ONLY="$(_probe_field get_only)"
OUTSIDE_ABSENT="$(_external_code "$ABSENT_PATH")"
OUTSIDE_GET_ONLY="$(_external_code "$GET_ONLY_PATH")"

_behaviour_row absent "$ABSENT_PATH" "$INSIDE_ABSENT" "$OUTSIDE_ABSENT" "expected 404"
_behaviour_row get-only "$GET_ONLY_PATH" "$INSIDE_GET_ONLY" "$OUTSIDE_GET_ONLY" "expected 405"

# An absent path must say it is absent. 405 asserts the resource exists and the
# verb is wrong, which is a different claim and was false everywhere (#177).
[ "$INSIDE_ABSENT" = "404" ] \
    || halt_without_rollback "POST $ABSENT_PATH answered $INSIDE_ABSENT inside the container, expected 404"

# And a real path with the wrong verb must still say 405. Answering 404
# everywhere would be the same defect mirrored: the deployment would then deny
# that its own endpoints exist.
[ "$INSIDE_GET_ONLY" = "405" ] \
    || halt_without_rollback "POST $GET_ONLY_PATH answered $INSIDE_GET_ONLY inside the container, expected 405 for a GET-only route"

# Before the external assertions, because it is the more precise finding. If
# nginx answers 405 where the container answered 404, "expected 404 through
# nginx" is true and unhelpful -- it points at the application, which just gave
# the right answer. "They disagree" points at the layer between.
if [ "$INSIDE_ABSENT" != "$OUTSIDE_ABSENT" ] \
    || [ "$INSIDE_GET_ONLY" != "$OUTSIDE_GET_ONLY" ]; then
    halt_without_rollback "the container and nginx disagree about the same request; something between the caller and the application is answering (absent: $INSIDE_ABSENT vs $OUTSIDE_ABSENT, get-only: $INSIDE_GET_ONLY vs $OUTSIDE_GET_ONLY)"
fi

# And only then whether the agreed answer is the right one.
[ "$OUTSIDE_ABSENT" = "404" ] \
    || halt_without_rollback "POST $ABSENT_PATH answered $OUTSIDE_ABSENT through nginx, expected 404"
[ "$OUTSIDE_GET_ONLY" = "405" ] \
    || halt_without_rollback "POST $GET_ONLY_PATH answered $OUTSIDE_GET_ONLY through nginx, expected 405"

echo "  behaviour matches inside the container and through nginx"

step "monitoring is collecting"

# The comparison in the verification step proves prometheus re-read the file. It
# does not prove the file works: "the target is in the configuration" and "the
# target is being scraped" are different claims, and #275 exists because the
# second was false for four days while the first was true. The check that would
# have missed it is the one that reads the configuration; this one asks
# prometheus what it is actually scraping.
#
# Warned about, never fatal, for the reason the recreate is guarded: a target
# that is down is a fact about that target, and undoing a good deployment over
# it would be the wrong trade. Here rather than beside the other prometheus
# check because a container recreated moments ago has not scraped yet -- by this
# point the health retries and the behaviour probe have gone by.
# Commented-out lines excluded: the file carries prose next to the job names,
# and a job that is only described would otherwise be demanded here.
PROM_JOBS="$(awk -F: '/job_name:/ && !/^[[:space:]]*#/ {print $2}' \
    "$REPO/infra/prometheus.yml" 2>/dev/null \
    | tr -d " \"'" | grep -v '^$' || true)"
if [ -z "$PROM_JOBS" ]; then
    echo "  WARNING: no job_name found in $REPO/infra/prometheus.yml; nothing to check" >&2
else
    _prom_missing="$PROM_JOBS"
    _prom_attempt=1
    while [ "$_prom_attempt" -le "$HEALTH_ATTEMPTS" ]; do
        # `up` is prometheus's own record of the last scrape per target, so this
        # is the collector answering about itself rather than the deployment
        # answering about the collector. promtool prints one line per series,
        # which is why it is used instead of the JSON API.
        _prom_up="$("${DC[@]}" exec -T prometheus \
            promtool query instant http://127.0.0.1:9090 up 2>/dev/null || true)"
        _prom_missing=""
        # The `=> 1` matters. A target that is declared, discovered and failing
        # still appears in this output, with `=> 0` -- so matching the job name
        # alone would report the exact state #275 was about as healthy.
        while IFS= read -r _job; do
            [ -n "$_job" ] || continue
            case "$_prom_up" in
                *"job=\"$_job\"} => 1"*) ;;
                *) _prom_missing="$_prom_missing $_job" ;;
            esac
        done <<< "$PROM_JOBS"
        [ -z "$_prom_missing" ] && break
        [ "$_prom_attempt" -lt "$HEALTH_ATTEMPTS" ] && sleep "$HEALTH_DELAY"
        _prom_attempt=$((_prom_attempt + 1))
    done
    if [ -z "$_prom_missing" ]; then
        echo "  prometheus is scraping every declared job: $(printf '%s' "$PROM_JOBS" | tr '\n' ' ')"
    else
        echo "  WARNING: prometheus is not scraping:$_prom_missing" >&2
        echo "           declared in infra/prometheus.yml and not up after" >&2
        echo "           $HEALTH_ATTEMPTS attempts. Those metrics are not collected (#275)." >&2
    fi
fi

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
# The run id of the model actually serving, read from the health endpoint's
# model_provenance (#191, #329). The commit above says what code was deployed;
# this says which champion it is serving, which a commit cannot -- the champion
# changes by activation on a named volume, not by a commit. Best-effort: an
# accepted deployment still gets a manifest, recording `unknown` rather than
# refusing over a field the record does not depend on.
ACTIVE_MODEL_RUN=unknown
if _health_body="$(curl -fsS -A "$DEPLOY_PROBE_UA" "$SITE/api/v1/health" 2>/dev/null)"; then
    ACTIVE_MODEL_RUN="$(printf '%s' "$_health_body" | python3 -c '
import json, sys
try:
    provenance = (json.load(sys.stdin) or {}).get("model_provenance") or {}
    print(provenance.get("run_id") or "unknown")
except Exception:
    print("unknown")
' 2>/dev/null || echo unknown)"
fi

MANIFEST="$(mktemp "$MANIFEST_DIR/$STAMP-XXXXXX.txt")"
TMP_MANIFEST="$MANIFEST.tmp"
{
    echo "deployed_at    $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "mode           $MODE"
    echo "commit         $TARGET"
    echo "origin_main    $ORIGIN_MAIN"
    echo "active_model   $ACTIVE_MODEL_RUN"
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
    if [ "$MODE" = finalize ]; then
        echo "finalized      yes"
        echo "finalized_run  ${FINALIZED_RUN:-unknown}"
        # Which code reconciled the record, as distinct from what is deployed.
        #
        # These legitimately differ, and the difference is the point: the fix
        # that makes --finalize possible lands after the run it has to
        # reconcile, so the tool is newer than the target by construction. The
        # deployed checkout stays at the journal's target -- the equality is
        # never relaxed -- and the script is run from elsewhere with
        # DEPLOY_REPO pointing here. Recording only one of the two would make
        # the manifest ambiguous about which.
        echo "recovery_tool  ${RECOVERY_TOOL_SHA:-same checkout as target}"
        echo "note           the deploying run completed and was interrupted"
        echo "               before it wrote this record; nothing was rebuilt,"
        echo "               migrated or recreated to produce it (#160)"
        echo
    fi
    echo "--- state replaced by this run ---"
    echo "previous_commit ${PREV_COMMIT:-none-recorded}"
    echo "previous_images:"
    printf '%s\n' "$PREV_IMAGES" | sed 's/^/  /'
} > "$TMP_MANIFEST"
# The file's own contents first: a rename makes the name durable, not the bytes
# behind it. Without this a crash can leave a manifest that exists and is empty,
# which reads as "this deployment recorded nothing" rather than as a loss.
sync "$TMP_MANIFEST" 2>/dev/null || sync
mv "$TMP_MANIFEST" "$MANIFEST"

# The pointer moves last, and atomically. Everything above can refuse, and every
# refusal exits before this line, so a run that failed leaves `latest` naming the
# deployment that is still serving -- which is what the next run must roll back
# to. `ln -sfn` alone is not atomic; the rename is.
ln -sfn "$(basename "$MANIFEST")" "$MANIFEST_DIR/.latest.$$"
mv -Tf "$MANIFEST_DIR/.latest.$$" "$LATEST"

# Durable before the journal goes, and in that order.
#
# `journal_clear` used to run *before* the manifest was written. A run
# interrupted between the two left neither -- no record of what is deployed and
# no sign that anything was unfinished, which is strictly worse than the case
# the journal exists for. Measured on 2026-08-14: the interruption fell on the
# other side of that line, so the journal survived and the manifest was never
# written (#160).
#
# fsync of the directory, not just the file: a rename is durable only once the
# directory entry is. Without it a crash here can lose a manifest that
# `cat` would happily show.
sync "$MANIFEST_DIR" 2>/dev/null || sync
journal_clear
# And again, because the removal is itself a directory entry. Without it a crash
# here leaves the journal on disk beside a manifest that already describes the
# same run -- the next deployment then refuses, pointing at a run that is fully
# recorded.
sync "$MANIFEST_DIR" 2>/dev/null || sync

cat "$MANIFEST"
echo
echo "manifest: $MANIFEST"
if [ -n "$PREV_COMMIT" ]; then
    echo "to undo:  $0 --rollback $PREV_COMMIT"
else
    echo "to undo:  no previous deployment is recorded; nothing to roll back to"
fi
