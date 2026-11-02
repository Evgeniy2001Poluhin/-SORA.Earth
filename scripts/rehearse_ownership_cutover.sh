#!/usr/bin/env bash
# Measure the ownership cutover on a restored copy of production, in a
# PostgreSQL this script creates, uses and removes -- and nothing else.
#
#   scripts/rehearse_ownership_cutover.sh \
#       --dump /path/to/sora_earth_YYYYMMDD.dump --dump-sha256 <hex> \
#       --image sora_earth_app:<release> --release-sha <full sha> \
#       [--runs 3] [--report /path/report.json] [--pg-user sora] \
#       [--pg-image postgres:16-alpine] [--cpus 2] [--memory 2g] \
#       [--app-memory 4g] [--app-workers 4] [--source-db-bytes N] \
#       [--disk-reserve-bytes N] [--max-load N] [--min-mem-kb N] \
#       [--health-url URL]
#
# Run it from a checkout of the release itself -- a separate clone at
# --release-sha with a clean tree, never the production checkout, which the
# deployment requires to stay on main. The script refuses any other checkout.
#
# What it does, three times and each time on a fresh restore:
#
#   restore     scripts/pg_restore.sh into the disposable PostgreSQL only
#   migrate     the release image's `alembic upgrade head` -- the full pending
#               upgrade of the dump, e.g. the accounts and ownership revisions
#   bootstrap   the release image's scripts/bootstrap_users.py
#   verify      scripts/verify_schema_head.py from the same image
#   start       the release image's backend, as production starts it, until
#               /api/v1/health reports the database healthy
#   measure     duration, lock wait and lock hold, WAL, and the peak disk use
#               of database, WAL and temporary files -- sampled through the
#               migration, not read once at the end
#   check data  ids, counts and content hashes of the original columns of
#               evaluations and predictions_log unchanged; every old row
#               public with no owner; FK, CHECK, default and indexes present
#
# and once more with a session holding a conflicting lock: the migration must
# refuse in about the migration's own lock_timeout and leave both tables'
# content and the whole fingerprint exactly as they were.
#
# The report is JSON and the thresholds are below, in this file. Any breach,
# any failed check and any leftover object is a non-zero exit. A breach means
# reworking the migration and measuring again, not raising a number here.
#
# Built to run on the production host, beside the production stack, so what it
# refuses matters as much as what it measures:
#
#   - before anything is started or decompressed: the release checkout, the
#     image's content against it, PostgreSQL 16, the report path, no deploy in
#     progress, no backup running, the host load, and the memory, disk and
#     inodes -- with a production disk reserve subtracted and an unknown or zero
#     inode count refused
#   - it holds the deployment lock for its whole run, so no deployment starts
#     beside it, and watches the host: load, memory, a backup or deployment
#     beginning, and optionally a health URL -- any of them aborts it (exit 5)
#   - it never reads the production .env, credentials or volumes: compose runs
#     under `env -i` with a generated --env-file and its own project directory
#   - it never connects to the production database: every connection goes to
#     `pg` on an internal network that only this run's containers join, and the
#     restore goes through `docker exec` into the container whose id and nonce
#     label this run recorded when it created it
#   - it never pulls an image: both must already be present
#   - its containers have cpus and memory limits, recorded with the host load
#   - it stops itself after REHEARSAL_WALL_SECONDS (at most 3600), cleanup
#     included: the cleanup has a reserve inside that limit and every command
#     in it is bounded
#   - on any exit or signal it removes exactly its own containers, network,
#     volumes and temporary copy, and checks they are gone -- never a prune --
#     and a docker listing that fails is "not confirmed", not "nothing left"
#   - it never touches the original dump; it works on a checksummed copy, and
#     checks the original's checksum again at the end
#
# Exit: 0 every threshold met and everything removed; 1 a threshold or check
# breached; 2 refused before measuring (arguments, checksum, space, safety);
# 3 cleanup could not be confirmed; 4 the report could not be written;
# 5 aborted because production degraded or a deployment or backup began;
# 124 the wall-clock limit; 128+n a signal.
#
# shellcheck disable=SC2329
#   Functions reached only from traps and from `must`/`bounded` arguments --
#   cleanup, on_signal, ours, cols_of -- read as unused to ShellCheck.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE_REHEARSAL="$REPO/infra/rehearsal/docker-compose.rehearsal.yml"
PRODUCTION_PROJECT="sora_earth_ai_platform"
# shellcheck source=scripts/pg_lib.sh disable=SC1091
source "$REPO/scripts/pg_lib.sh"

# ------------------------------------------------------------- thresholds
# Fixed here, reviewed with the code. Not arguments, not environment.
MIN_RUNS=3                  # fewer restores than this is never a pass
MAX_MIGRATION_S=60          # the slowest of the runs, whole migrator
MAX_LOCK_WAIT_S=1           # waiting for a lock, with nothing else connected
MAX_DDL_LOCK_HOLD_S=60      # AccessExclusiveLock on either owned table
MAX_STOP_WINDOW_S=300       # stop -> migrate -> bootstrap -> start -> health
DEPLOY_STOP_BUDGET_S=60     # scripts/deploy_production.sh: STOP_TIMEOUT, at its maximum
DEPLOY_DRAIN_BUDGET_S=30    # scripts/deploy_production.sh: DRAIN_SECONDS, at its maximum
NGINX_BUDGET_S=30           # nginx recreate, nginx -t, upstream and certificate checks,
                            # external health: production-only, so budgeted, not measured
DISK_FACTOR=3               # free space >= 3x the estimate, and >= 3x the measured peak
DUMP_EXPANSION=10           # a custom-format dump restores to at most this many times its size
NEG_MIN_S=5                 # the conflicting-lock run must refuse in about 10s
NEG_MAX_S=30
SAMPLE_INTERVAL=0.05        # lock and disk sampler, seconds between samples
MIN_SAMPLES=3
APP_START_TIMEOUT_S=240     # the backend must report a healthy database within this
WALL_MAX=3600
CLEANUP_RESERVE_MAX=180     # seconds of the wall kept for the cleanup
DISK_RESERVE_MIN=$((1024 * 1024 * 1024))           # never less than 1 GiB held back
DISK_RESERVE_DEFAULT=$((10 * 1024 * 1024 * 1024))  # 10 GiB for production by default

die()  { echo "REFUSED: $*" >&2; exit 2; }
step() { echo; echo "== $* =="; }

# ------------------------------------------------------------- arguments
DUMP=""; DUMP_SHA=""; APP_IMAGE=""; RELEASE_SHA=""; RUNS=3; REPORT=""
PG_IMAGE="postgres:16-alpine"; PG_USER="sora"; CPUS=2; MEMORY=2g
APP_MEMORY=4g; APP_WORKERS=4; SOURCE_DB_BYTES=0
DISK_RESERVE="$DISK_RESERVE_DEFAULT"; MAX_LOAD=""; MIN_MEM_KB=524288; HEALTH_URL=""
while [ $# -gt 0 ]; do
    [ $# -ge 2 ] || die "$1 needs a value"
    case "$1" in
        --dump)               DUMP="$2" ;;
        --dump-sha256)        DUMP_SHA="$2" ;;
        --image)              APP_IMAGE="$2" ;;
        --release-sha)        RELEASE_SHA="$2" ;;
        --runs)               RUNS="$2" ;;
        --report)             REPORT="$2" ;;
        --pg-image)           PG_IMAGE="$2" ;;
        --pg-user)            PG_USER="$2" ;;
        --cpus)               CPUS="$2" ;;
        --memory)             MEMORY="$2" ;;
        --app-memory)         APP_MEMORY="$2" ;;
        --app-workers)        APP_WORKERS="$2" ;;
        --source-db-bytes)    SOURCE_DB_BYTES="$2" ;;
        --disk-reserve-bytes) DISK_RESERVE="$2" ;;
        --max-load)           MAX_LOAD="$2" ;;
        --min-mem-kb)         MIN_MEM_KB="$2" ;;
        --health-url)         HEALTH_URL="$2" ;;
        *) die "unknown argument '$1'" ;;
    esac
    shift 2
done
is_uint() { case "$1" in ''|*[!0-9]*) return 1 ;; esac; [ "${#1}" -le 18 ]; }
[ -n "$DUMP" ] && [ -n "$DUMP_SHA" ] && [ -n "$APP_IMAGE" ] && [ -n "$RELEASE_SHA" ] \
    || die "--dump, --dump-sha256, --image and --release-sha are required"
[[ "$DUMP_SHA" =~ ^[0-9a-f]{64}$ ]] || die "--dump-sha256 must be 64 lower-case hex characters"
[[ "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]] || die "--release-sha must be a full 40-character commit id"
is_uint "$RUNS" && [ "$RUNS" -ge 1 ] && [ "$RUNS" -le 10 ] || die "--runs must be a number from 1 to 10"
[[ "$PG_USER" =~ ^[a-z_][a-z0-9_]*$ ]] || die "--pg-user must be a plain role name"
[[ "$CPUS" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "--cpus must be a number"
[[ "$MEMORY" =~ ^[0-9]+[kmg]$ ]] || die "--memory must look like 2g"
[[ "$APP_MEMORY" =~ ^[0-9]+[kmg]$ ]] || die "--app-memory must look like 4g"
is_uint "$APP_WORKERS" && [ "$APP_WORKERS" -ge 1 ] && [ "$APP_WORKERS" -le 16 ] || die "--app-workers must be 1..16"
is_uint "$SOURCE_DB_BYTES" || die "--source-db-bytes must be a number of bytes"
is_uint "$DISK_RESERVE" || die "--disk-reserve-bytes must be a number of bytes"
[ "$DISK_RESERVE" -ge "$DISK_RESERVE_MIN" ] || die "--disk-reserve-bytes may not be below $DISK_RESERVE_MIN"
is_uint "$MIN_MEM_KB" || die "--min-mem-kb must be a number"
[ -z "$MAX_LOAD" ] || [[ "$MAX_LOAD" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "--max-load must be a number"
[ -z "$HEALTH_URL" ] || [[ "$HEALTH_URL" =~ ^https?://[^[:space:]]+$ ]] || die "--health-url must be an http(s) URL"
WALL="${REHEARSAL_WALL_SECONDS:-$WALL_MAX}"
is_uint "$WALL" && [ "$WALL" -ge 30 ] || die "REHEARSAL_WALL_SECONDS must be a number of at least 30"
[ "$WALL" -le "$WALL_MAX" ] || WALL="$WALL_MAX"   # lowered, never raised
CLEANUP_RESERVE=$(( WALL / 3 < CLEANUP_RESERVE_MAX ? WALL / 3 : CLEANUP_RESERVE_MAX ))
MONITOR_INTERVAL="${REHEARSAL_MONITOR_INTERVAL:-5}"
is_uint "$MONITOR_INTERVAL" && [ "$MONITOR_INTERVAL" -ge 1 ] || die "REHEARSAL_MONITOR_INTERVAL must be a number"
# Where production keeps the signs of a deployment and a backup. Overridable
# only so the refusals can be tested away from /run; the paths used are
# recorded in the report.
DEPLOY_LOCK="${REHEARSAL_DEPLOY_LOCK:-/run/sora/deploy.lock}"
DEPLOY_JOURNAL="${REHEARSAL_DEPLOY_JOURNAL:-/var/lib/sora/deployments/in-progress}"
BACKUP_LOCK_DIR="${REHEARSAL_BACKUP_LOCK_DIR:-/run/sora-earth}"

command -v docker >/dev/null || die "docker is required"
command -v python3 >/dev/null || die "python3 is required"
command -v git >/dev/null || die "git is required"
docker compose version >/dev/null 2>&1 || die "docker compose is required"

# ------------------------------------------------------------- the release
# The checkout this runs from is the release: the migration, the bootstrap and
# the schema check it measures are compared below against the image, file by
# file, so "the release SHA" in the report is a fact about what ran.
HEAD_SHA="$(git -C "$REPO" rev-parse HEAD 2>/dev/null)" || die "$REPO is not a git checkout"
[ "$HEAD_SHA" = "$RELEASE_SHA" ] \
    || die "this checkout is at $HEAD_SHA, not the release $RELEASE_SHA; run from a clean clone of the release"
[ -z "$(git -C "$REPO" status --porcelain --untracked-files=no 2>/dev/null)" ] \
    || die "the checkout has local changes; the release is what is committed"

# Present already. A pull on the production host is a change nobody reviewed.
docker image inspect "$APP_IMAGE" >/dev/null 2>&1 || die "the release image $APP_IMAGE is not present; this script does not pull"
docker image inspect "$PG_IMAGE" >/dev/null 2>&1 || die "$PG_IMAGE is not present; this script does not pull"
APP_IMAGE_ID="$(docker image inspect -f '{{.Id}}' "$APP_IMAGE")"
PG_IMAGE_ID="$(docker image inspect -f '{{.Id}}' "$PG_IMAGE")"
# Production runs PostgreSQL 16; a measurement on another major is about
# another planner, another catalogue and another WAL. Checked on the image now
# and on the running server later.
PG_MAJOR="$(docker image inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$PG_IMAGE" | sed -n 's/^PG_MAJOR=//p')"
[ "$PG_MAJOR" = 16 ] || die "$PG_IMAGE is PostgreSQL ${PG_MAJOR:-of unknown version}, not 16"

# ------------------------------------------------------------- the files
[ -f "$DUMP" ] && [ ! -L "$DUMP" ] || die "no such regular file: $DUMP"
DUMP_ABS="$(cd "$(dirname "$DUMP")" && pwd)/$(basename "$DUMP")"
NONCE="$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
[ "${#NONCE}" -eq 16 ] || die "could not generate a nonce"
PROJ="sora-rehearsal-$NONCE"
[ "$PROJ" != "$PRODUCTION_PROJECT" ] || die "refusing the production project name"
DB=sora_rehearsal
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
START_EPOCH="$(date +%s)"
DEADLINE=$((START_EPOCH + WALL))
[ -n "$REPORT" ] || REPORT="${HOME:-/tmp}/$PROJ.json"
REPORT_DIR="$(cd "$(dirname "$REPORT")" 2>/dev/null && pwd)" || die "the report's directory does not exist: $(dirname "$REPORT")"
REPORT="$REPORT_DIR/$(basename "$REPORT")"
case "$REPORT" in
    "$REPO"/*) die "the report must not be written inside the checkout ($REPO)" ;;
esac
# Never an existing file, and so never the dump: a --report naming the dump, a
# symlink to it or a hard link of it used to be truncated here, before the
# checksum was read -- destroying the backup the rehearsal exists to test.
if [ -e "$REPORT" ] || [ -L "$REPORT" ]; then
    if [ "$REPORT" -ef "$DUMP_ABS" ]; then
        die "the report $REPORT is the dump (or a link to it); refusing to touch it"
    fi
    die "the report $REPORT already exists; give a new path"
fi
[ "$REPORT" != "$DUMP_ABS" ] && [ "$REPORT" != "$DUMP_ABS.sha256" ] || die "the report path names the dump"
_probe_file="$(mktemp "$REPORT_DIR/.rehearsal-report-probe.XXXXXX" 2>/dev/null)" \
    || die "cannot write in the report's directory $REPORT_DIR"
rm -f "$_probe_file"

# ------------------------------------------------------------- coordination
# Taken and held with flock(2) through python's fcntl: the same lock the
# deployment takes with flock(1), and available where flock(1) is not. The lock
# belongs to the open file description bash holds on fd 8, so it lasts until
# this script exits, though python returns at once.
lock_fd8() {   # path -> 0 held, 75 someone else holds it, 1 cannot
    exec 8>>"$1" || return 1
    python3 -I -c 'import fcntl, sys
try:
    fcntl.flock(8, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(75)' || return $?
}
# Whether somebody holds a lock, without keeping it.
lock_is_held() {   # path -> 0 held, 1 free or absent
    [ -e "$1" ] || return 1
    python3 -I - "$1" <<'PY'
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDONLY)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(0)
fcntl.flock(fd, fcntl.LOCK_UN)
sys.exit(1)
PY
}
backup_running() {   # prints what, 0 when one is
    local f
    if [ -d "$BACKUP_LOCK_DIR" ]; then
        for f in "$BACKUP_LOCK_DIR"/*.lock; do
            [ -e "$f" ] || continue
            if lock_is_held "$f"; then echo "$f is held"; return 0; fi
        done
    fi
    if command -v pgrep >/dev/null 2>&1 && pgrep -x pg_dump >/dev/null 2>&1; then
        echo "a pg_dump process is running"; return 0
    fi
    return 1
}
DEPLOY_LOCK_STATE="absent"
if [ -e "$DEPLOY_LOCK" ]; then
    _lrc=0; lock_fd8 "$DEPLOY_LOCK" || _lrc=$?
    case "$_lrc" in
        0)  DEPLOY_LOCK_STATE="held by this rehearsal" ;;
        75) die "a deployment holds $DEPLOY_LOCK; the rehearsal does not run beside one" ;;
        *)  die "cannot take $DEPLOY_LOCK (status $_lrc)" ;;
    esac
fi
[ ! -e "$DEPLOY_JOURNAL" ] || die "an unfinished deployment is recorded in $DEPLOY_JOURNAL"
if _why="$(backup_running)"; then die "a backup is running ($_why)"; fi
host_load() { python3 -I -c 'import os; print("%.2f" % os.getloadavg()[0])'; }
HOST_CPUS="$(python3 -I -c 'import os; print(os.cpu_count() or 1)')"
[ -n "$MAX_LOAD" ] || MAX_LOAD=$((HOST_CPUS * 2))
LOAD_BEFORE="$(host_load)"
python3 -I -c 'import sys; sys.exit(0 if float(sys.argv[1]) < float(sys.argv[2]) else 1)' "$LOAD_BEFORE" "$MAX_LOAD" \
    || die "the host load is $LOAD_BEFORE, at or above --max-load $MAX_LOAD; choose a quieter window"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/sora-rehearsal.XXXXXX")" || die "cannot create a work directory"
chmod 700 "$WORK"
mkdir -p "$WORK/runs" "$WORK/project"
ENV_FILE="$WORK/rehearsal.env"
PG_CID=""
WATCHDOG=""
MONITOR=""
BG_PIDS=""
SAMPLER=""
SAMPLER_STOP=""

# Compose, scoped to this run and to nothing inherited: no shell variable and no
# .env file from anywhere reaches the interpolation.
DCR() {
    env -i PATH="$PATH" HOME="${HOME:-/tmp}" ${DOCKER_HOST:+DOCKER_HOST="$DOCKER_HOST"} \
        ${DOCKER_CONTEXT:+DOCKER_CONTEXT="$DOCKER_CONTEXT"} \
        docker compose -p "$PROJ" --project-directory "$WORK/project" \
        --env-file "$ENV_FILE" -f "$COMPOSE_FILE_REHEARSAL" "$@"
}

psql_q() {   # one query against the restore target, unaligned
    docker exec -i "$PG_CID" psql -X -v ON_ERROR_STOP=1 -U "$PG_USER" -d "${2:-$DB}" -AtF'|' -q -c "$1" < /dev/null
}

have_sha() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | awk '{print $1}'; }
mem_k() { python3 -I -c 'import sys; v=sys.argv[1].lower(); m={"k":1,"m":1024,"g":1024**2}; print(int(float(v[:-1])*m[v[-1]]))' "$1"; }

# A command with a time limit, without timeout(1), which macOS lacks. 124 when
# it had to be killed.
bounded() {   # seconds command...
    local limit="$1" p waited=0
    shift
    "$@" &
    p=$!
    while kill -0 "$p" 2>/dev/null; do
        if [ "$waited" -ge $((limit * 10)) ]; then
            kill -TERM "$p" 2>/dev/null; sleep 1; kill -KILL "$p" 2>/dev/null
            wait "$p" 2>/dev/null
            return 124
        fi
        sleep 0.1; waited=$((waited + 1))
    done
    wait "$p"
}

# ------------------------------------------------------------- inventory
inventory() {   # file; fails if any listing failed
    local root rc=0
    root="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null)" || rc=1
    {
        echo "## containers"; docker ps -a --format '{{.ID}} {{.Names}}' | sort || rc=1
        echo "## volumes";    docker volume ls --format '{{.Name}}' | sort || rc=1
        echo "## networks";   docker network ls --format '{{.Name}}' | sort || rc=1
        echo "## host df ($root, or / if docker cannot be asked)"; df -Pk "${root:-/}" 2>&1 || df -Pk / 2>&1
    } > "$1" 2>&1
    return "$rc"
}

# Every object of this run that still exists, one per line. Non-zero when any
# listing failed: the old version sent docker's errors to /dev/null, and a
# daemon that could not answer read as "nothing left behind".
ours() {
    local rc=0 kind filter out
    for kind in container volume network; do
        for filter in "label=sora.rehearsal.nonce=$NONCE" "label=com.docker.compose.project=$PROJ"; do
            case "$kind" in
                container) out="$(bounded 30 docker ps -aq --filter "$filter")" || { rc=1; continue; } ;;
                volume)    out="$(bounded 30 docker volume ls -q --filter "$filter")" || { rc=1; continue; } ;;
                network)   out="$(bounded 30 docker network ls -q --filter "$filter")" || { rc=1; continue; } ;;
            esac
            [ -z "$out" ] || printf '%s\n' "$out" | sed "s/^/$kind /"
        done
    done
    return "$rc"
}

remaining() { echo $((DEADLINE - $(date +%s))); }
# A cleanup command, bounded by what is left of the wall clock and never by
# more than 60 seconds. Skipped -- and so unconfirmed -- when nothing is left.
cbounded() {
    local left
    left=$(( $(remaining) - 5 ))
    [ "$left" -gt 0 ] || return 124
    [ "$left" -le 60 ] || left=60
    bounded "$left" "$@"
}

# ------------------------------------------------------------- cleanup
CLEANED=0
cleanup() {
    local rc=$?
    [ "$CLEANED" = 1 ] && return
    CLEANED=1
    trap - EXIT INT TERM HUP
    set +e
    [ -f "$WORK/timed_out" ] && rc=124
    [ -f "$WORK/degraded" ] && rc=5
    echo; echo "== removing what this run created =="
    : > "$WORK/cleanup_started"
    # The watchdog and the monitor have done their part; from here the wall is
    # held by bounding every command.
    for p in $WATCHDOG $MONITOR; do kill "$p" 2>/dev/null; wait "$p" 2>/dev/null; done
    for p in $BG_PIDS; do kill "$p" 2>/dev/null; wait "$p" 2>/dev/null; done
    local confirmed=true why=""
    # Compose first, scoped to the project; then anything still labelled ours.
    # Never a prune: a prune is global and this host is production.
    if [ -f "$ENV_FILE" ]; then
        cbounded DCR --profile rehearsal-run down -v --remove-orphans --timeout 10 >/dev/null 2>&1
    fi
    local kind filter id
    for kind in container volume network; do
        for filter in "label=sora.rehearsal.nonce=$NONCE" "label=com.docker.compose.project=$PROJ"; do
            case "$kind" in
                container) ids="$(cbounded docker ps -aq --filter "$filter" 2>/dev/null)" ;;
                volume)    ids="$(cbounded docker volume ls -q --filter "$filter" 2>/dev/null)" ;;
                network)   ids="$(cbounded docker network ls -q --filter "$filter" 2>/dev/null)" ;;
            esac
            for id in $ids; do
                case "$kind" in
                    container) cbounded docker rm -f "$id" >/dev/null 2>&1 ;;
                    volume)    cbounded docker volume rm "$id" >/dev/null 2>&1 ;;
                    network)   cbounded docker network rm "$id" >/dev/null 2>&1 ;;
                esac
            done
        done
    done
    local left
    if ! left="$(ours)"; then
        confirmed=false; why="a docker listing failed or timed out, so what remains is unknown"
    fi
    [ -z "$left" ] || { confirmed=false; why="${why:+$why; }objects remain"; }
    if ! cbounded inventory "$WORK/inventory-after.txt"; then
        confirmed=false; why="${why:+$why; }the inventory after could not be taken"
    fi
    # The original dump, untouched.
    local dump_after
    dump_after="$(cbounded have_sha "$DUMP_ABS" 2>/dev/null)"
    [ "$dump_after" = "$DUMP_SHA" ] || { confirmed=false; why="${why:+$why; }the original dump's checksum is now '${dump_after:-unreadable}'"; }
    [ -n "$left" ] && { echo "  NOT REMOVED:" >&2; printf '%s\n' "$left" | sed 's/^/    /' >&2; }
    # The report: written to a new file beside its destination and linked into
    # place, which fails if anything is there -- so it can only ever be created,
    # never overwrite a file, the dump included.
    local report_ok=true
    python3 -I - "$WORK" "$REPORT" "$confirmed" "$rc" "$left" "$why" "$dump_after" <<'PY' || report_ok=false
import json, os, sys, tempfile
work, report, ok, rc, left, why, dump_after = sys.argv[1:8]
def read(p):
    try:
        with open(os.path.join(work, p)) as fh:
            return fh.read()
    except OSError:
        return None
try:
    doc = json.loads(read("report-partial.json") or read("report-start.json") or "{}")
except ValueError:
    doc = {"partial_report_unreadable": True}
before, after = read("inventory-before.txt") or "", read("inventory-after.txt") or ""
def section(text, name):
    out, on = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            on = line[3:] == name
            continue
        if on and line:
            out.append(line)
    return out
inv = {"before": before, "after": after, "difference": {}}
for name in ("containers", "volumes", "networks"):
    b, a = set(section(before, name)), set(section(after, name))
    inv["difference"][name] = {"appeared": sorted(a - b), "disappeared": sorted(b - a)}
doc["inventory"] = inv
doc["host_samples"] = [l.split() for l in (read("host_samples") or "").splitlines() if l]
doc["aborted"] = {"timed_out": read("timed_out") is not None, "degraded": (read("degraded") or "").strip() or None}
doc["cleanup"] = {"all_removed_and_confirmed": ok == "true", "why_not": why or None,
                  "left_behind": [l for l in left.splitlines() if l]}
doc["dump_sha256_after"] = dump_after or None
doc["exit_status_before_cleanup"] = int(rc)
if doc.get("passed") and (ok != "true" or int(rc) != 0):
    doc["passed"] = False
    doc.setdefault("breaches", []).append("cleanup or exit status")
d = os.path.dirname(report)
fd, tmp = tempfile.mkstemp(prefix=".rehearsal-report.", dir=d)
try:
    with os.fdopen(fd, "w") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.link(tmp, report)
finally:
    os.unlink(tmp)
PY
    rm -rf "$WORK"
    if [ -e "$WORK" ]; then
        echo "  the work directory $WORK could not be removed" >&2
        confirmed=false
    fi
    if [ "$report_ok" != true ]; then
        echo "  REPORT NOT WRITTEN: $REPORT could not be created; this run is not evidence" >&2
        [ "$confirmed" = true ] || echo "  CLEANUP NOT CONFIRMED: $why" >&2
        exit 4
    fi
    echo "  report: $REPORT"
    if [ "$confirmed" != true ]; then
        echo "  CLEANUP NOT CONFIRMED: $why" >&2
        exit 3
    fi
    echo "  removed: containers, network, volumes and the dump copy of $PROJ"
    exit "$rc"
}
on_signal() { echo "  interrupted by SIG$1" >&2; exit $((128 + $2)); }
trap cleanup EXIT
trap 'on_signal INT 2' INT
trap 'on_signal TERM 15' TERM
trap 'on_signal HUP 1' HUP

# The wall clock, in two steps. At WALL - CLEANUP_RESERVE: kill this run's
# containers, which is what makes a foreground docker command return, and
# TERM the script. Twenty seconds later, if the cleanup has still not begun,
# TERM the script's children too -- a docker client stuck on a daemon would
# otherwise hold the trap off for ever. The cleanup then has the reserve.
MAIN_PID=$$
(
    trap 'kill "$sp" 2>/dev/null; exit 0' TERM
    sleep $((WALL - CLEANUP_RESERVE)) & sp=$!
    wait "$sp" || exit 0
    : > "$WORK/timed_out"
    echo "  the rehearsal reached its limit: ${WALL}s, less ${CLEANUP_RESERVE}s kept for the cleanup" >&2
    for c in $(docker ps -q --filter "label=sora.rehearsal.nonce=$NONCE"); do
        docker kill "$c" >/dev/null 2>&1
    done
    kill -TERM "$MAIN_PID" 2>/dev/null
    sleep 20 & sp=$!
    wait "$sp" || exit 0
    [ -e "$WORK/cleanup_started" ] || pkill -TERM -P "$MAIN_PID" 2>/dev/null
) &
WATCHDOG=$!

# ------------------------------------------------------------- start
step "rehearsal $PROJ"
echo "  dump      $DUMP_ABS"
echo "  release   $RELEASE_SHA"
echo "  image     $APP_IMAGE (${APP_IMAGE_ID:0:19})"
echo "  postgres  $PG_IMAGE (PostgreSQL $PG_MAJOR), cpus=$CPUS memory=$MEMORY; backend memory=$APP_MEMORY workers=$APP_WORKERS"
echo "  wall      ${WALL}s, ${CLEANUP_RESERVE}s of it for the cleanup"
echo "  deploy lock $DEPLOY_LOCK: $DEPLOY_LOCK_STATE; host load $LOAD_BEFORE of max $MAX_LOAD ($HOST_CPUS cpus)"
inventory "$WORK/inventory-before.txt" || die "the docker inventory could not be taken; nothing has been started"

step "the image is the release"
# Every Python file under alembic/, app/ and scripts/ that git tracks at the
# release, plus alembic.ini and entrypoint.sh, must be in the image with the
# same content -- and the image may hold no migration git does not. A tag can
# name anything; this is what makes the release SHA in the report true.
git -C "$REPO" ls-files -z -- alembic.ini entrypoint.sh 'alembic/*.py' 'app/*.py' 'scripts/*.py' \
    | while IFS= read -r -d '' _f; do
        printf '%s  %s\n' "$(have_sha "$REPO/$_f")" "$_f"
    done | sort -k2 > "$WORK/release-files.txt"
[ -s "$WORK/release-files.txt" ] || die "could not hash the release's files"
! grep -q '^  ' "$WORK/release-files.txt" || die "a release file could not be hashed"
bounded 120 docker run --rm --network none --label "sora.rehearsal.nonce=$NONCE" --entrypoint sh "$APP_IMAGE_ID" -c \
    'cd /app && { find alembic app scripts -type f -name "*.py"; echo alembic.ini; echo entrypoint.sh; } | sort | xargs sha256sum' \
    2>/dev/null | awk '{print $1"  "$2}' | sort -k2 > "$WORK/image-files.txt" \
    || die "could not read the image's files"
python3 -I - "$WORK/release-files.txt" "$WORK/image-files.txt" <<'PY' || die "the image $APP_IMAGE is not the release $RELEASE_SHA (see above)"
import sys
def load(p):
    out = {}
    for line in open(p):
        h, _, name = line.rstrip("\n").partition("  ")
        out[name] = h
    return out
rel, img = load(sys.argv[1]), load(sys.argv[2])
bad = [n for n in rel if img.get(n) != rel[n]]
extra = [n for n in img if n.startswith("alembic/") and n not in rel]
for n in bad[:20]:
    print("  differs or missing in the image: %s" % n, file=sys.stderr)
for n in extra[:20]:
    print("  a migration in the image that the release does not have: %s" % n, file=sys.stderr)
sys.exit(1 if bad or extra else 0)
PY
echo "  $(wc -l < "$WORK/release-files.txt" | tr -d ' ') files identical to the release"

step "the dump"
# Read, never written. The copy is what every later step uses.
GOT_SHA="$(have_sha "$DUMP_ABS")"
[ "$GOT_SHA" = "$DUMP_SHA" ] || die "the dump's sha256 is $GOT_SHA, not the $DUMP_SHA given"
echo "  checksum matches"
DUMP_BYTES="$(wc -c < "$DUMP_ABS" | tr -d ' ')"

step "memory, disk and inodes, before anything is started or decompressed"
# Read in a container that starts no database: the postgres image with its
# entrypoint replaced, on an anonymous volume at the data directory -- which
# lives where the rehearsal's volume will, on the docker data root.
# kB rather than bytes from awk, which prints a large product as 1.2e+11.
# shellcheck disable=SC2016
#   Single quotes on purpose: the script runs in the probe container's shell.
PROBE="$(bounded 120 docker run --rm --network none --label "sora.rehearsal.nonce=$NONCE" \
    --label sora.rehearsal.probe=space --entrypoint sh "$PG_IMAGE_ID" -c '
        d=/var/lib/postgresql/data
        echo "kb_free=$(df -Pk "$d" | awk "NR==2 {print \$4}")"
        echo "inodes_free=$(df -Pi "$d" | awk "NR==2 {print \$4}")"
        echo "mem_available_kb=$(awk "/^MemAvailable:/ {print \$2}" /proc/meminfo)"
        echo "cpus=$(nproc)"' 2>/dev/null)" || die "the space probe could not run"
probe() { printf '%s\n' "$PROBE" | sed -n "s/^$1=//p" | head -1; }
FREE_KB="$(probe kb_free)"; FREE_INODES="$(probe inodes_free)"
MEM_AVAIL_K="$(probe mem_available_kb)"; DOCKER_CPUS="$(probe cpus)"
is_uint "$FREE_KB" || die "free space on the docker data root could not be read ('$FREE_KB')"
FREE_BYTES=$((FREE_KB * 1024))
is_uint "$MEM_AVAIL_K" || die "available memory could not be read ('$MEM_AVAIL_K')"
# An inode count that is unknown, or zero -- what some filesystems report when
# they do not count inodes -- is not "plenty".
is_uint "$FREE_INODES" && [ "$FREE_INODES" -gt 0 ] \
    || die "free inodes on the docker data root are unknown ('${FREE_INODES}'); refusing rather than assuming"
WORK_FREE_BYTES="$(( $(df -Pk "$WORK" | awk 'NR==2 {print $4}') * 1024 ))"
# The database the dump becomes: the operator's measurement of production when
# given, else a conservative expansion of the compressed dump, whichever is
# larger. The peak is that database restored, the WAL of restoring and
# migrating it, and the migration's rewrite with its temporary files.
DB_EST=$(( DUMP_BYTES * DUMP_EXPANSION ))
[ "$SOURCE_DB_BYTES" -le "$DB_EST" ] || DB_EST="$SOURCE_DB_BYTES"
PEAK_EST=$(( DB_EST * 3 ))
PEAK_INODES=$(( 5000 + PEAK_EST / 1048576 ))
LIMIT_K=$(( $(mem_k "$MEMORY") + $(mem_k "$APP_MEMORY") ))
echo "  dump ${DUMP_BYTES}B; database estimate ${DB_EST}B; peak estimate ${PEAK_EST}B, ${PEAK_INODES} inodes"
echo "  docker data root: ${FREE_BYTES}B and ${FREE_INODES} inodes free; production reserve ${DISK_RESERVE}B"
echo "  work directory: ${WORK_FREE_BYTES}B free; memory available ${MEM_AVAIL_K}kB; limits ${LIMIT_K}kB"
[ $(( FREE_BYTES - DISK_RESERVE )) -ge $(( PEAK_EST * DISK_FACTOR )) ] \
    || die "free space on the docker data root less the reserve is below ${DISK_FACTOR}x the estimated peak"
[ $(( WORK_FREE_BYTES - DISK_RESERVE )) -ge $(( DUMP_BYTES * DISK_FACTOR )) ] \
    || die "free space for the dump copy in $WORK less the reserve is below ${DISK_FACTOR}x the dump"
[ "$FREE_INODES" -ge $(( PEAK_INODES * DISK_FACTOR )) ] \
    || die "free inodes on the docker data root are below ${DISK_FACTOR}x the estimate"
# The rehearsal's PostgreSQL and backend at their limits at once, with as much
# again left for the production stack.
[ "$MEM_AVAIL_K" -ge $(( LIMIT_K * 2 )) ] \
    || die "available memory ${MEM_AVAIL_K}kB is below twice the rehearsal's limits (${LIMIT_K}kB)"
[ "$MEM_AVAIL_K" -ge $(( MIN_MEM_KB + LIMIT_K )) ] \
    || die "available memory ${MEM_AVAIL_K}kB would fall below --min-mem-kb $MIN_MEM_KB"
# The seed the backend loads, read-only, as production mounts it.
SEED_CHECKOUT="$REPO" bash "$REPO/scripts/check_seed_materialized.sh" >/dev/null 2>&1 \
    || die "the model seed in $REPO/models is not materialized; the backend could not start"

cp "$DUMP_ABS" "$WORK/dump"
chmod 400 "$WORK/dump"
[ "$(have_sha "$WORK/dump")" = "$DUMP_SHA" ] || die "the copy of the dump does not match its checksum"
# pg_restore.sh checks a sidecar if there is one; give it the absolute path.
printf '%s  %s\n' "$DUMP_SHA" "$WORK/dump" > "$WORK/dump.sha256"

rand() { od -An -N24 -tx1 /dev/urandom | tr -d ' \n'; }
umask 077
{
    echo "REHEARSAL_NONCE=$NONCE"
    echo "REHEARSAL_PG_IMAGE=$PG_IMAGE_ID"
    echo "REHEARSAL_APP_IMAGE=$APP_IMAGE_ID"
    echo "REHEARSAL_PG_USER=$PG_USER"
    echo "REHEARSAL_PG_PASSWORD=$(rand)"
    echo "REHEARSAL_DB=$DB"
    echo "REHEARSAL_CPUS=$CPUS"
    echo "REHEARSAL_MEMORY=$MEMORY"
    echo "REHEARSAL_APP_MEMORY=$APP_MEMORY"
    echo "REHEARSAL_APP_WORKERS=$APP_WORKERS"
    echo "REHEARSAL_MODELS_DIR=$REPO/models"
    echo "REHEARSAL_JWT_SECRET=$(rand)"
    echo "REHEARSAL_ADMIN_TOKEN=$(rand)"
    echo "REHEARSAL_ADMIN_API_KEY=$(rand)"
    echo "REHEARSAL_ADMIN_PASSWORD=A1-$(rand)"
    echo "REHEARSAL_ANALYST_PASSWORD=B2-$(rand)"
    echo "REHEARSAL_VIEWER_PASSWORD=C3-$(rand)"
} > "$ENV_FILE"
umask 022

# The release's head, read from the image without a database.
EXPECTED_HEAD="$(bounded 120 DCR run --rm --no-deps -T --entrypoint alembic migrate heads 2>/dev/null | awk '{print $1}' | head -1)"
[[ "$EXPECTED_HEAD" =~ ^[0-9a-f]{12}$ ]] || die "could not read the release image's alembic head ('$EXPECTED_HEAD')"
echo "  release head $EXPECTED_HEAD"

python3 -I - "$WORK/report-start.json" <<PY
import json, sys
json.dump({"rehearsal": "$PROJ", "started_at": "$STARTED_AT", "passed": False,
           "breaches": ["did not reach the verdict"],
           "release": {"sha": "$RELEASE_SHA", "image": "$APP_IMAGE", "image_id": "$APP_IMAGE_ID"},
           "dump": {"path": "$DUMP_ABS", "sha256": "$DUMP_SHA", "bytes": $DUMP_BYTES}},
          open(sys.argv[1], "w"), indent=2)
PY

# ------------------------------------------------------------- the monitor
# Production comes first. Every MONITOR_INTERVAL seconds: the host load, the
# memory left, a deployment or a backup beginning, and the health URL when one
# is given. A breach writes why, kills this run's containers and TERMs the
# script, which then exits 5 through the cleanup.
(
    trap 'exit 0' TERM
    high=0; unhealthy=0
    while :; do
        sleep "$MONITOR_INTERVAL" & wait $! || exit 0
        load="$(host_load)"
        mem="-"
        pcid="$(cat "$WORK/pg_cid" 2>/dev/null)"
        [ -z "$pcid" ] || mem="$(docker exec "$pcid" awk '/^MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null || echo -)"
        echo "$(date +%s) load=$load mem_available_kb=$mem" >> "$WORK/host_samples"
        reason=""
        if python3 -I -c 'import sys; sys.exit(0 if float(sys.argv[1]) >= float(sys.argv[2]) else 1)' "$load" "$MAX_LOAD"; then
            high=$((high + 1)); [ "$high" -lt 3 ] || reason="host load $load at or above $MAX_LOAD for three samples"
        else
            high=0
        fi
        if is_uint "$mem" && [ "$mem" -lt "$MIN_MEM_KB" ]; then reason="available memory ${mem}kB below $MIN_MEM_KB"; fi
        [ ! -e "$DEPLOY_JOURNAL" ] || reason="a deployment began ($DEPLOY_JOURNAL appeared)"
        if why="$(backup_running)"; then reason="a backup began ($why)"; fi
        if [ -n "$HEALTH_URL" ]; then
            if curl -fsS -o /dev/null -m 10 "$HEALTH_URL" 2>/dev/null; then unhealthy=0
            else unhealthy=$((unhealthy + 1)); [ "$unhealthy" -lt 2 ] || reason="$HEALTH_URL failed twice"; fi
        fi
        if [ -n "$reason" ]; then
            echo "$reason" > "$WORK/degraded"
            echo "  ABORTING: $reason" >&2
            for c in $(docker ps -q --filter "label=sora.rehearsal.nonce=$NONCE"); do docker kill "$c" >/dev/null 2>&1; done
            kill -TERM "$MAIN_PID" 2>/dev/null
            exit 0
        fi
    done
) &
MONITOR=$!

# ------------------------------------------------------------- the safety case
# Called before every restore and every migration. A destructive step runs
# only against the container this run created, proven by id, nonce, project,
# mounts, ports and network -- never against a name that merely resolves.
assert_disposable() {
    local cid nonce proj ports mounts nets internal members
    cid="$(DCR ps -q pg 2>/dev/null | head -1)"
    [ -n "$cid" ] && [ "${cid:0:12}" = "${PG_CID:0:12}" ] \
        || die "the rehearsal postgres is not the container this run created"
    nonce="$(docker inspect -f '{{index .Config.Labels "sora.rehearsal.nonce"}}' "$PG_CID")"
    proj="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$PG_CID")"
    [ "$nonce" = "$NONCE" ] && [ "$proj" = "$PROJ" ] || die "container $PG_CID does not carry this run's nonce and project"
    ports="$(docker inspect -f '{{range $p, $b := .HostConfig.PortBindings}}{{$p}} {{end}}' "$PG_CID")"
    [ -z "${ports// /}" ] || die "the rehearsal postgres publishes ports: $ports"
    mounts="$(docker inspect -f '{{range .Mounts}}{{.Type}}:{{.Name}} {{end}}' "$PG_CID")"
    [ "$mounts" = "volume:${PROJ}_pgdata " ] || die "the rehearsal postgres mounts something other than its own volume: $mounts"
    nets="$(docker inspect -f '{{range $n, $v := .NetworkSettings.Networks}}{{$n}} {{end}}' "$PG_CID")"
    [ "$nets" = "${PROJ}_rehearsal " ] || die "the rehearsal postgres is attached to: $nets"
    internal="$(docker network inspect -f '{{.Internal}} {{index .Labels "sora.rehearsal.nonce"}}' "${PROJ}_rehearsal")"
    [ "$internal" = "true $NONCE" ] || die "the rehearsal network is not internal or not ours: $internal"
    # Every container on the network is ours, so `pg` can only be ours.
    members="$(docker network inspect -f '{{range $id, $c := .Containers}}{{$id}} {{end}}' "${PROJ}_rehearsal")"
    for cid in $members; do
        [ "$(docker inspect -f '{{index .Config.Labels "sora.rehearsal.nonce"}}' "$cid" 2>/dev/null)" = "$NONCE" ] \
            || die "a container that is not this run's is on the rehearsal network: $cid"
    done
}

step "a disposable PostgreSQL 16"
bounded 180 DCR up -d --wait pg >/dev/null 2>&1 || die "the rehearsal postgres would not start"
PG_CID="$(DCR ps -q pg | head -1)"
[ -n "$PG_CID" ] || die "the rehearsal postgres has no container"
PG_CID="$(docker inspect -f '{{.Id}}' "$PG_CID")"
echo "$PG_CID" > "$WORK/pg_cid"
assert_disposable
PG_VERSION_NUM="$(psql_q "SHOW server_version_num" rehearsal_boot)"
[[ "$PG_VERSION_NUM" =~ ^16[0-9]{4}$ ]] || die "the server reports version $PG_VERSION_NUM, not 16"
echo "  ${PG_CID:0:12}, server $PG_VERSION_NUM, internal network ${PROJ}_rehearsal, volume ${PROJ}_pgdata, no ports"
PG_SETTINGS="$(psql_q "SELECT string_agg(name || '=' || setting || coalesce(unit, ''), ', ' ORDER BY name) FROM pg_settings WHERE name IN ('server_version', 'shared_buffers', 'work_mem', 'maintenance_work_mem', 'max_wal_size', 'synchronous_commit', 'fsync', 'wal_level', 'checkpoint_timeout')" rehearsal_boot)"

# ------------------------------------------------------------- one run
restore() {
    assert_disposable
    PG_CONTAINER="$PG_CID" PGUSER="$PG_USER" bash "$REPO/scripts/pg_restore.sh" "$WORK/dump" "$DB" > "$WORK/restore.log" 2>&1 \
        || { tail -20 "$WORK/restore.log" >&2; die "the restore failed"; }
}

OWNED_TABLES="evaluations predictions_log"
cols_of() { psql_q "SELECT string_agg(quote_ident(column_name), ',' ORDER BY ordinal_position) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = '$1'"; }
data_of() { # table, column list: count|md5 of ids|md5 of the rows over those columns
    psql_q "SELECT count(*), coalesce(md5(string_agg(id::text, ',' ORDER BY id)), '-'), coalesce(md5(string_agg(row($2)::text, E'\n' ORDER BY id)), '-') FROM public.$1"
}
scalar() { psql_q "$1" | tr -d '\r'; }
fingerprint() { PG_CONTAINER="$PG_CID" PGUSER="$PG_USER" pg_fingerprint "$DB"; }
# A value that must exist: written, and refused if empty. A check that reads
# two empty files and finds them equal has checked nothing.
must() {   # file, then the command
    local f="$1"; shift
    "$@" > "$f" 2> "$f.err" || die "could not read $(basename "$f"): $(head -3 "$f.err")"
    [ -s "$f" ] || die "$(basename "$f") came back empty"
    # psql reading a script from stdin goes on after a failed statement and
    # exits 0, so an error is visible only here.
    [ ! -s "$f.err" ] || die "$(basename "$f") was read with errors: $(head -3 "$f.err")"
}

# The sampler: one psql session, fed a query every SAMPLE_INTERVAL, so the
# cost is a query and not a docker exec per sample. Locks of the migrator's
# session, and the bytes of the database, the WAL and the temporary files --
# whose peak, not the final size, is what the disk has to hold.
start_sampler() {
    local app="$1" out="$2"
    local q="SELECT extract(epoch FROM clock_timestamp()), coalesce((SELECT string_agg(coalesce(wait_event_type, '-'), ',') FROM pg_stat_activity WHERE application_name = '$app'), 'none'), (SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid WHERE a.application_name = '$app' AND l.granted AND l.mode = 'AccessExclusiveLock' AND l.relation IN (SELECT oid FROM pg_class WHERE relname IN ('evaluations', 'predictions_log') AND relnamespace = 'public'::regnamespace)), pg_database_size(current_database()), (SELECT coalesce(sum(size), 0) FROM pg_ls_waldir()), (SELECT coalesce(sum(size), 0) FROM pg_ls_tmpdir());"
    SAMPLER_STOP="$out.stop"
    rm -f "$SAMPLER_STOP"
    # Ended by a file rather than a kill: psql exits at the end of its input,
    # and so does the docker exec carrying it. Killing the client could leave
    # the session running inside the container.
    ( while [ ! -e "$SAMPLER_STOP" ]; do echo "$q"; sleep "$SAMPLE_INTERVAL"; done ) \
        | docker exec -i -e "PGAPPNAME=$PROJ-sampler" "$PG_CID" psql -X -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$DB" -AtF'|' -q > "$out" 2> "$out.err" &
    SAMPLER=$!
    BG_PIDS="$BG_PIDS $SAMPLER"
    # Its first sample before anything is measured: the baseline of the disk.
    local i=0
    while [ ! -s "$out" ] && [ "$i" -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
}
stop_sampler() {   # writes alive-at-stop and the sampler's exit status beside the samples
    local out="$1" alive=no rc=0 i=0
    kill -0 "$SAMPLER" 2>/dev/null && alive=yes
    : > "$SAMPLER_STOP"
    while kill -0 "$SAMPLER" 2>/dev/null && [ "$i" -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
    kill "$SAMPLER" 2>/dev/null || true
    wait "$SAMPLER" 2>/dev/null || rc=$?
    echo "$alive" > "$out.alive"
    echo "$rc" > "$out.rc"
}

run_once() {   # n, kind (measure|negative)
    local n="$1" kind="$2" r="$WORK/runs/$1"
    mkdir -p "$r"
    step "run $n ($kind): restore"
    local t0 t1
    t0="$(now_ms)"; restore; t1="$(now_ms)"
    echo "$((t1 - t0))" > "$r/restore_ms"
    must "$r/alembic_before" scalar "SELECT coalesce((SELECT version_num FROM alembic_version), 'none')"
    must "$r/db_bytes_before" scalar "SELECT pg_database_size(current_database())"
    must "$r/lsn_before" scalar "SELECT pg_current_wal_lsn()"
    for t in $OWNED_TABLES; do
        must "$r/cols_$t" cols_of "$t"
        must "$r/data_before_$t" data_of "$t" "$(cat "$r/cols_$t")"
        must "$r/bytes_before_$t" scalar "SELECT pg_total_relation_size('public.$t')"
    done
    must "$r/fingerprint_before" fingerprint
    echo "  at $(cat "$r/alembic_before"); evaluations $(cut -d'|' -f1 "$r/data_before_evaluations") rows, predictions_log $(cut -d'|' -f1 "$r/data_before_predictions_log") rows"

    local blocker=""
    if [ "$kind" = negative ]; then
        # A reader holding the lock any ALTER TABLE has to wait for -- until it
        # is terminated below, however long the migrator takes to start. A
        # fixed sleep let a slow container start find the lock already gone:
        # measured on a loaded laptop, the migrator reached its ALTER after a
        # 120 s blocker had ended, and the "negative" run simply migrated.
        docker exec -e PGAPPNAME="$PROJ-blocker" "$PG_CID" psql -X -U "$PG_USER" -d "$DB" -q \
            -c "BEGIN; LOCK TABLE public.evaluations IN ACCESS SHARE MODE; SELECT pg_sleep(3600); COMMIT;" \
            > /dev/null 2>&1 &
        blocker=$!
        BG_PIDS="$BG_PIDS $blocker"
        local i=0
        until [ "$(scalar "SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid WHERE a.application_name = '$PROJ-blocker' AND l.granted AND l.relation = 'public.evaluations'::regclass")" = 1 ]; do
            i=$((i + 1)); [ "$i" -lt 100 ] || die "the blocking session never took its lock"; sleep 0.1
        done
        echo "  a session holds ACCESS SHARE on evaluations"
    fi

    step "run $n ($kind): migrate"
    local app="$PROJ-migrate-$n" rc=0
    assert_disposable
    start_sampler "$app" "$r/samples"
    t0="$(now_ms)"
    DCR run --rm --no-deps -T --name "$app" -e "PGAPPNAME=$app" migrate > "$r/migrate.log" 2>&1 || rc=$?
    t1="$(now_ms)"
    stop_sampler "$r/samples"
    echo "$((t1 - t0))" > "$r/migrate_ms"; echo "$rc" > "$r/migrate_rc"
    must "$r/wal_bytes" scalar "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), '$(cat "$r/lsn_before")')"
    echo "  exit $rc in $((t1 - t0))ms; $(wc -l < "$r/samples" | tr -d ' ') samples"

    if [ "$kind" = negative ]; then
        scalar "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE application_name = '$PROJ-blocker'" > /dev/null
        wait "$blocker" 2>/dev/null || true
        # The same reads as before, and they must succeed: what is compared
        # is content, both tables, and the whole fingerprint.
        must "$r/alembic_after" scalar "SELECT coalesce((SELECT version_num FROM alembic_version), 'none')"
        for t in $OWNED_TABLES; do
            must "$r/cols_after_$t" cols_of "$t"
            must "$r/data_after_$t" data_of "$t" "$(cat "$r/cols_$t")"
        done
        must "$r/fingerprint_after" fingerprint
        return 0
    fi

    step "run $n: bootstrap, schema check, and the backend's start"
    rc=0; t0="$(now_ms)"
    DCR run --rm --no-deps -T bootstrap-users > "$r/bootstrap.log" 2>&1 || rc=$?
    t1="$(now_ms)"; echo "$((t1 - t0))" > "$r/bootstrap_ms"; echo "$rc" > "$r/bootstrap_rc"
    rc=0; t0="$(now_ms)"
    DCR run --rm --no-deps -T verify-head > "$r/verify.log" 2>&1 || rc=$?
    t1="$(now_ms)"; echo "$((t1 - t0))" > "$r/verify_ms"; echo "$rc" > "$r/verify_rc"
    # The new backend, from its own entrypoint, until it says the database is
    # healthy -- the part of the window the deployment spends between the
    # forced recreate and its health checks.
    local bcid="" healthy=no
    t0="$(now_ms)"
    if DCR up -d --no-deps --force-recreate backend > "$r/backend-up.log" 2>&1; then
        bcid="$(DCR ps -q backend | head -1)"
        while [ $(( ($(now_ms) - t0) / 1000 )) -lt "$APP_START_TIMEOUT_S" ]; do
            if [ -n "$bcid" ] && docker exec "$bcid" curl -fsS -m 5 http://127.0.0.1:8000/api/v1/health 2>/dev/null \
                | python3 -I -c 'import json, sys; d = json.load(sys.stdin); sys.exit(0 if d["checks"]["database"]["status"] == "healthy" else 1)' 2>/dev/null; then
                healthy=yes; break
            fi
            sleep 1
        done
    fi
    t1="$(now_ms)"
    echo "$((t1 - t0))" > "$r/app_start_ms"; echo "$healthy" > "$r/app_healthy"
    [ -z "$bcid" ] || docker logs "$bcid" > "$r/backend.log" 2>&1 || true
    DCR rm -sf backend > /dev/null 2>&1 || true
    echo "  bootstrap exit $(cat "$r/bootstrap_rc"), schema check exit $(cat "$r/verify_rc"), backend healthy: $healthy in $((t1 - t0))ms"

    step "run $n: the data"
    must "$r/alembic_after" scalar "SELECT coalesce((SELECT version_num FROM alembic_version), 'none')"
    must "$r/db_bytes_after" scalar "SELECT pg_database_size(current_database())"
    scalar "SELECT count(*) FROM users" > "$r/users_after" 2>/dev/null || echo "unreadable" > "$r/users_after"
    for t in $OWNED_TABLES; do
        data_of "$t" "$(cat "$r/cols_$t")" > "$r/data_after_$t" 2>&1 || true
        scalar "SELECT count(*) FROM public.$t WHERE data_scope IS DISTINCT FROM 'public' OR owner_user_id IS NOT NULL" > "$r/not_public_$t" 2>&1 || true
        scalar "SELECT pg_total_relation_size('public.$t')" > "$r/bytes_after_$t"
    done
    scalar "SELECT
        (SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND conname IN ('fk_evaluations_owner_user', 'fk_predictions_log_owner_user')) || '|' ||
        (SELECT count(*) FROM pg_constraint WHERE contype = 'c' AND convalidated AND conname IN ('ck_evaluations_owner_scope', 'ck_predictions_log_owner_scope')) || '|' ||
        (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name IN ('evaluations', 'predictions_log') AND column_name = 'data_scope' AND is_nullable = 'NO' AND column_default LIKE '''pilot''%') || '|' ||
        (SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE i.indisvalid AND c.relname IN ('ix_evaluations_owner_recent', 'ix_evaluations_scope_recent', 'ix_predictions_log_owner_recent', 'ix_predictions_log_scope_recent', 'ix_predictions_log_scope_id')) || '|' ||
        (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relname IN ('project_access_events', 'project_access_targets'))" \
        > "$r/structure" 2>&1 || true
    echo "  structure fk|check|default|indexes|audit = $(cat "$r/structure") (want 2|2|2|5|2)"
}

for i in $(seq 1 "$RUNS"); do run_once "$i" measure; done
run_once negative negative

# ------------------------------------------------------------- the verdict
step "verdict"
DATA_ROOT_FREE_K="$(docker exec "$PG_CID" df -Pk /var/lib/postgresql/data | awk 'NR==2 {print $4}')"
is_uint "$DATA_ROOT_FREE_K" || die "free space on the data root could not be read at the end"
LOAD_AFTER="$(host_load)"
set +e
python3 -I - "$WORK" "$RUNS" <<PY
import json, os, sys
work, runs = sys.argv[1], int(sys.argv[2])
R = os.path.join(work, "runs")
def rd(run, name, default=None):
    try:
        with open(os.path.join(R, str(run), name)) as fh:
            return fh.read().strip()
    except OSError:
        return default
def num(v, default=0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def sampler(run, migrate_s):
    """Lock wait, lock hold and peak disk from the samples -- and whether the
    samples can be believed. An empty or broken sampler used to give wait 0,
    hold 0 and a pass."""
    rows, bad = [], 0
    for line in (rd(run, "samples", "") or "").splitlines():
        parts = line.split("|")
        try:
            rows.append((float(parts[0]), parts[1], int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5])))
        except (IndexError, ValueError):
            bad += 1
    gaps = [rows[i + 1][0] - rows[i][0] for i in range(len(rows) - 1)]
    res = max(gaps) if gaps else None
    seen = [r for r in rows if r[1] != "none"]
    wait = sum(rows[i + 1][0] - rows[i][0] for i in range(len(rows) - 1) if "Lock" in rows[i][1].split(","))
    held = [r[0] for r in rows if r[2] > 0]
    hold = (held[-1] - held[0] + (res or 0)) if held else 0.0
    totals = [r[3] + r[4] + r[5] for r in rows]
    peak_extra = (max(totals) - totals[0]) if totals else None
    span = (rows[-1][0] - rows[0][0]) if rows else 0.0
    problems = []
    if rd(run, "samples.alive") != "yes":
        problems.append("the sampler had died before the migration ended")
    if (rd(run, "samples.err", "") or "").strip():
        problems.append("the sampler reported errors: %s" % rd(run, "samples.err")[:200])
    if bad:
        problems.append("%d unparseable samples" % bad)
    if len(rows) < $MIN_SAMPLES:
        problems.append("only %d samples" % len(rows))
    if span < migrate_s - 1.0:
        problems.append("samples span %.2fs of a %.2fs migration" % (span, migrate_s))
    if res is not None and res > 1.0:
        problems.append("a gap of %.2fs between samples" % res)
    if not seen:
        problems.append("the migrator's session was never observed")
    return {"samples": len(rows), "unparseable": bad, "span_s": round(span, 3),
            "resolution_s": res, "migrator_observed": len(seen),
            "lock_wait_s": round(wait, 3), "ddl_lock_hold_s": round(hold, 3),
            "peak_extra_bytes": peak_extra,
            "peak_bytes_db_wal_tmp": max(totals) if totals else None,
            "valid": not problems, "problems": problems}

breaches, checks, report_runs = [], [], []
head = """$EXPECTED_HEAD"""
checks.append(("at least $MIN_RUNS measured runs, each on a fresh restore", runs >= $MIN_RUNS))
for n in range(1, runs + 1):
    run = {"run": n}
    for k in ("restore_ms", "migrate_ms", "bootstrap_ms", "verify_ms", "app_start_ms"):
        run[k] = int(num(rd(n, k)))
    for k in ("migrate_rc", "bootstrap_rc", "verify_rc"):
        run[k] = int(num(rd(n, k), -1))
    run["app_healthy"] = rd(n, "app_healthy") == "yes"
    run["alembic_before"], run["alembic_after"] = rd(n, "alembic_before"), rd(n, "alembic_after")
    run["wal_bytes"] = int(num(rd(n, "wal_bytes")))
    run["db_bytes_before"], run["db_bytes_after"] = int(num(rd(n, "db_bytes_before"))), int(num(rd(n, "db_bytes_after")))
    run["users_after"] = rd(n, "users_after")
    # What the migrator and the backend said, the end of it: a failure here
    # is otherwise a boolean with no reason once the work directory is gone.
    run["log_tail"] = {k: "\n".join((rd(n, k + ".log", "") or "").splitlines()[-30:])
                       for k in ("migrate", "bootstrap", "verify", "backend")}
    run["sampler"] = sampler(n, run["migrate_ms"] / 1000)
    run["stop_window_s"] = {
        "stop_budget": $DEPLOY_STOP_BUDGET_S, "drain_budget": $DEPLOY_DRAIN_BUDGET_S,
        "migrate": run["migrate_ms"] / 1000, "bootstrap": run["bootstrap_ms"] / 1000,
        "schema_check": run["verify_ms"] / 1000, "backend_start_to_healthy": run["app_start_ms"] / 1000,
        "nginx_and_checks_budget": $NGINX_BUDGET_S,
    }
    run["stop_window_total_s"] = round(sum(run["stop_window_s"].values()), 3)
    run["tables"] = {}
    for t in ("evaluations", "predictions_log"):
        before, after = rd(n, "data_before_" + t), rd(n, "data_after_" + t)
        run["tables"][t] = {
            "columns_before": rd(n, "cols_" + t),
            "control_before": before, "control_after": after,
            "bytes_before": int(num(rd(n, "bytes_before_" + t))),
            "bytes_after": int(num(rd(n, "bytes_after_" + t))),
            "rows_not_public": rd(n, "not_public_" + t),
        }
        checks.append(("run %d: %s ids, count and original columns unchanged" % (n, t), bool(before) and before == after))
        checks.append(("run %d: every old %s row is public with no owner" % (n, t), rd(n, "not_public_" + t) == "0"))
    run["fingerprint_before_lines"] = len((rd(n, "fingerprint_before") or "").splitlines())
    run["structure_fk_check_default_indexes_audit"] = rd(n, "structure")
    checks.append(("run %d: the lock and disk sampler is believable (%s)" % (n, "; ".join(run["sampler"]["problems"]) or "ok"), run["sampler"]["valid"]))
    checks.append(("run %d: migrator exit 0" % n, run["migrate_rc"] == 0))
    checks.append(("run %d: bootstrap exit 0" % n, run["bootstrap_rc"] == 0))
    checks.append(("run %d: the new code accepts the schema" % n, run["verify_rc"] == 0))
    checks.append(("run %d: the backend started and reported the database healthy" % n, run["app_healthy"]))
    checks.append(("run %d: at the release head %s" % (n, head), run["alembic_after"] == head))
    checks.append(("run %d: FK, CHECK, default, indexes, audit tables" % n, run["structure_fk_check_default_indexes_audit"] == "2|2|2|5|2"))
    report_runs.append(run)

def worst(get):
    vals = [get(r) for r in report_runs]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None
w_migrate = worst(lambda r: r["migrate_ms"] / 1000)
w_wait = worst(lambda r: r["sampler"]["lock_wait_s"])
w_hold = worst(lambda r: r["sampler"]["ddl_lock_hold_s"])
w_window = worst(lambda r: r["stop_window_total_s"])
w_peak = worst(lambda r: r["sampler"]["peak_extra_bytes"])
free_bytes = int("$DATA_ROOT_FREE_K") * 1024
thresholds = [
    ("migration, slowest run (s)", w_migrate, "<=", $MAX_MIGRATION_S),
    ("lock wait, worst run (s)", w_wait, "<=", $MAX_LOCK_WAIT_S),
    ("DDL lock hold, worst run (s)", w_hold, "<=", $MAX_DDL_LOCK_HOLD_S),
    ("stop window: stop, drain, migrate, bootstrap, check, backend to healthy, nginx (s)", w_window, "<=", $MAX_STOP_WINDOW_S),
    ("free space on the data root less the reserve / peak extra use (x)",
     ((free_bytes - $DISK_RESERVE) / w_peak) if w_peak and w_peak > 0 else (None if w_peak is None else float("inf")), ">=", $DISK_FACTOR),
]

neg = {"migrate_ms": int(num(rd("negative", "migrate_ms"))), "migrate_rc": int(num(rd("negative", "migrate_rc"), -1)),
       "alembic_before": rd("negative", "alembic_before"), "alembic_after": rd("negative", "alembic_after"),
       "tables": {}}
for t in ("evaluations", "predictions_log"):
    before, after = rd("negative", "data_before_" + t), rd("negative", "data_after_" + t)
    neg["tables"][t] = {"control_before": before, "control_after": after,
                        "columns_before": rd("negative", "cols_" + t), "columns_after": rd("negative", "cols_after_" + t)}
    checks.append(("negative: %s content unchanged (count, ids, row hash)" % t, bool(before) and before == after))
    checks.append(("negative: %s columns unchanged" % t, bool(rd("negative", "cols_" + t)) and rd("negative", "cols_" + t) == rd("negative", "cols_after_" + t)))
fb, fa = rd("negative", "fingerprint_before"), rd("negative", "fingerprint_after")
neg["fingerprint_identical"] = bool(fb) and fb == fa
checks.append(("negative: the migration refused", neg["migrate_rc"] not in (0, -1)))
# The refusal is judged on the lock wait the sampler saw and on the error
# itself, not on the migrator's wall time: that includes the container's
# start, which on a loaded host was longer than the whole lock_timeout.
neg["sampler"] = sampler("negative", neg["migrate_ms"] / 1000)
neg["log_tail"] = "\n".join((rd("negative", "migrate.log", "") or "").splitlines()[-30:])
checks.append(("negative: refused by its lock_timeout (the error says so)", "lock timeout" in (rd("negative", "migrate.log", "") or "")))
checks.append(("negative: it waited $NEG_MIN_S..$NEG_MAX_S s on the lock before refusing",
               $NEG_MIN_S <= neg["sampler"]["lock_wait_s"] <= $NEG_MAX_S))
checks.append(("negative: the whole fingerprint is identical", neg["fingerprint_identical"]))
checks.append(("negative: alembic_version unchanged", bool(neg["alembic_before"]) and neg["alembic_before"] == neg["alembic_after"]))

verdicts = []
for name, value, op, limit in thresholds:
    if value is None:
        ok = False
    elif op == "<=":
        ok = value <= limit
    else:
        ok = value >= limit
    verdicts.append({"threshold": name, "value": (round(value, 3) if value not in (None, float("inf")) else str(value)),
                     "limit": "%s %s" % (op, limit), "ok": ok})
    if not ok:
        breaches.append(name)
for name, ok in checks:
    if not ok:
        breaches.append(name)

doc = {
    "rehearsal": "$PROJ",
    "started_at": "$STARTED_AT",
    "dump": {"path": "$DUMP_ABS", "sha256": "$DUMP_SHA", "bytes": $DUMP_BYTES},
    "release": {"sha": "$RELEASE_SHA", "image": "$APP_IMAGE", "image_id": "$APP_IMAGE_ID", "alembic_head": head,
                "image_matches_release_files": True},
    "postgres": {"image": "$PG_IMAGE", "image_id": "$PG_IMAGE_ID", "server_version_num": "$PG_VERSION_NUM",
                 "settings": """$PG_SETTINGS"""},
    "resources": {"cpus_limit": "$CPUS", "memory_limit": "$MEMORY", "app_memory_limit": "$APP_MEMORY",
                  "app_workers": $APP_WORKERS, "host_cpus": $HOST_CPUS, "docker_cpus": "$DOCKER_CPUS",
                  "host_load_before": "$LOAD_BEFORE", "host_load_after": "$LOAD_AFTER", "max_load": "$MAX_LOAD",
                  "memory_available_kb_before": $MEM_AVAIL_K, "data_root_free_bytes_before": $FREE_BYTES,
                  "data_root_free_inodes_before": $FREE_INODES, "data_root_free_bytes_at_end": free_bytes,
                  "disk_reserve_bytes": $DISK_RESERVE, "estimated_db_bytes": $DB_EST,
                  "estimated_peak_bytes": $PEAK_EST, "estimated_peak_inodes": $PEAK_INODES,
                  "source_db_bytes_given": $SOURCE_DB_BYTES},
    "coordination": {"deploy_lock": "$DEPLOY_LOCK", "deploy_lock_state": "$DEPLOY_LOCK_STATE",
                     "deploy_journal": "$DEPLOY_JOURNAL", "backup_lock_dir": "$BACKUP_LOCK_DIR",
                     "health_url": "$HEALTH_URL" or None, "monitor_interval_s": $MONITOR_INTERVAL},
    "runs": report_runs,
    "negative": neg,
    "thresholds": verdicts,
    "checks": [{"check": c, "ok": ok} for c, ok in checks],
    "breaches": breaches,
    "passed": not breaches,
    "notes": [
        "lock wait, DDL lock hold and disk are sampled every $SAMPLE_INTERVAL s from pg_stat_activity, pg_locks, pg_database_size, pg_ls_waldir and pg_ls_tmpdir; each run records its sampler's resolution and validity",
        "the stop window adds the deploy script's stop ($DEPLOY_STOP_BUDGET_S s) and drain ($DEPLOY_DRAIN_BUDGET_S s) budgets at their maxima and a $NGINX_BUDGET_S s budget for nginx and its checks; migrate, bootstrap, schema check and the backend's start to a healthy database are measured",
        "the scheduler is not started here; production starts it beside the backend, outside the measured path",
    ],
}
with open(os.path.join(work, "report-partial.json"), "w") as fh:
    json.dump(doc, fh, indent=2, sort_keys=True)
for v in verdicts:
    print("  %-4s %-90s %s (%s)" % ("ok" if v["ok"] else "FAIL", v["threshold"], v["value"], v["limit"]))
for c, ok in checks:
    if not ok:
        print("  FAIL %s" % c)
print("  %d checks, %d breaches" % (len(checks), len(breaches)))
sys.exit(1 if breaches else 0)
PY
VERDICT=$?
set -e
exit "$VERDICT"
