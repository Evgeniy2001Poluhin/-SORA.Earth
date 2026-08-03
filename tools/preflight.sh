#!/usr/bin/env bash
# shellcheck disable=SC2016
#   The container body is single-quoted deliberately: it runs inside the
#   container, where $CHANGED and the rest come from that environment.
#   Expanding here would bake the host's values into a script the container
#   never sees. File scope because the body is one long single-quoted argument
#   and a directive cannot sit inside a line continuation.
#
# What CI will do, before CI does it.
#
# Two failures this week came from the local environment differing from the
# runner, and each cost a full CI cycle to discover:
#
#   the deployment tests were run as root, where CI is not, so
#   `install -d -o root -g root` passed here and failed there -- 47 of 77
#
#   six tests used @pytest.mark.asyncio, and pytest-asyncio lives in
#   requirements-dev.txt, which CI does not install -- it passed here because
#   my environment had the plugin
#
# The second was not really about a plugin. Local Python is 3.9 against CI's
# 3.11, so `X | None` annotations fail at collection and forty test files cannot
# even be gathered here. There was no command that meant "what CI will see", so
# CI was the first thing that ever saw it.
#
# This is that command. It runs in the image CI runs, installs exactly what CI
# installs, and refuses to go on when a step fails.
#
# Usage:
#   tools/preflight.sh                       everything
#   tools/preflight.sh tests/test_foo.py     that file first, then everything
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
# A prebuilt image with the dependencies baked in, falling back to installing
# them at run time when it is absent.
#
# Installing requirements.txt into a fresh container costs seven to eight
# minutes even with a warm pip cache, because the cache saves the download and
# not the install. Paid once per check, that is enough to stop anyone running
# the check. Build it with:
#
#   tools/preflight.sh --build
#
PY_IMAGE="${PREFLIGHT_IMAGE:-sora-preflight:latest}"
BASE_IMAGE="python:3.11-slim"
# A named volume, because `pip install -r requirements.txt` pulls torch, SHAP,
# transformers and chromadb. Cold that is several minutes; cached it is seconds,
# and a preflight nobody waits for is a preflight nobody runs.
CACHE_VOL="sora-preflight-pip"

if [ "${1:-}" = "--build" ]; then
    echo "== building $PY_IMAGE (once; minutes) =="
    docker build -t "$PY_IMAGE" -f - "$REPO" <<DOCKERFILE
FROM $BASE_IMAGE
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
        git build-essential && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && pip install --no-cache-dir pytest pytest-cov pytest-timeout
WORKDIR /w
DOCKERFILE
    echo "== built =="
    exit 0
fi

if ! docker image inspect "$PY_IMAGE" >/dev/null 2>&1; then
    echo "note: $PY_IMAGE is missing; falling back to $BASE_IMAGE and installing"
    echo "      dependencies inside the run. Build it once with: $0 --build"
    PY_IMAGE="$BASE_IMAGE"
    NEED_INSTALL=1
fi
NEED_INSTALL="${NEED_INSTALL:-0}"

CHANGED=("$@")
if [ ${#CHANGED[@]} -eq 0 ]; then
    # Test files touched against origin/main. Not a guess at what matters --
    # just the ones most likely to fail, run first so the answer arrives early.
    while IFS= read -r f; do
        [ -n "$f" ] && CHANGED+=("$f")
    done < <(git -C "$REPO" diff --name-only origin/main...HEAD -- 'tests/*.py' 2>/dev/null || true)
fi

echo "== preflight in $PY_IMAGE =="
[ ${#CHANGED[@]} -gt 0 ] && printf '   changed tests: %s\n' "${CHANGED[*]}"

docker volume create "$CACHE_VOL" >/dev/null 2>&1 || true

# A named container and a trap, because the mechanism failing has to look like
# failure. A previous run left a container in Docker's `Dead` state with five
# shell processes waiting on it: `docker ps` reported it up, `docker exec` said
# it was gone, and the wait simply never ended. A check that hangs is
# indistinguishable from a check that is still working, which is the same defect
# this whole branch is about, in the tool meant to catch it.
CONTAINER="sora-preflight-$$"
cleanup() {
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

# An upper bound. The full suite takes about twelve minutes; thirty is generous
# and still finite. Without it a stuck pip or a wedged daemon waits for ever.
PREFLIGHT_TIMEOUT="${PREFLIGHT_TIMEOUT:-1800}"

# A watchdog rather than `timeout`, which is GNU coreutils and absent from
# macOS -- where this actually runs. Depending on it would have meant the
# hang-protection itself failing to start, which is the exact shape of the
# problem it was added for.
( sleep "$PREFLIGHT_TIMEOUT"
  if docker inspect "$CONTAINER" >/dev/null 2>&1; then
      echo "preflight: killing $CONTAINER after ${PREFLIGHT_TIMEOUT}s" >&2
      docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  fi ) &
WATCHDOG=$!
kill_watchdog() { kill "$WATCHDOG" 2>/dev/null || true; }
trap 'cleanup; kill_watchdog' EXIT INT TERM

# Output goes to a file as well as the terminal.
#
# A run whose parent shell died left the container to finish alone, and `--rm`
# took its logs with it -- the result of a twelve-minute suite simply gone. The
# check has to survive losing the thing watching it, or the answer it produces
# is only worth as much as the terminal it was typed in.
LOG="${PREFLIGHT_LOG:-${TMPDIR:-/tmp}/preflight-$(date -u +%Y%m%dT%H%M%SZ).log}"
echo "   log: $LOG"

run_rc=0
docker run --name "$CONTAINER" --rm \
    -v "$REPO:/w" -w /w \
    -v "$CACHE_VOL:/root/.cache/pip" \
    -e SORA_OFFLINE=1 \
    -e RUN_SCHEDULER=false \
    -e DATABASE_URL="sqlite:////tmp/preflight.db" \
    -e REDIS_URL="" \
    -e SECRET_KEY=preflight \
    -e SORA_ADMIN_TOKEN=preflight \
    -e CHANGED="${CHANGED[*]}" \
    -e NEED_INSTALL="$NEED_INSTALL" \
    "$PY_IMAGE" bash -euo pipefail -c '
echo
echo "-- 1/4 dependencies, exactly as CI installs them"
# The same two lines as .github/workflows/ci.yml. Deliberately not
# requirements-dev.txt: that is what hid the pytest-asyncio difference, since it
# carries the plugin CI has never had.
if [ "$NEED_INSTALL" = "1" ]; then
    apt-get update -qq >/dev/null 2>&1
    apt-get install -y -qq --no-install-recommends git build-essential >/dev/null 2>&1
    pip install --quiet -r requirements.txt
    pip install --quiet pytest pytest-cov pytest-timeout
else
    echo "   baked into the image"
fi
python -c "import sys; print(f\"   python {sys.version.split()[0]}\")"

echo
echo "-- 2/4 the changed tests"
if [ -n "${CHANGED:-}" ]; then
    # shellcheck disable=SC2086
    #   Deliberate word splitting: CHANGED is a space-separated list of paths
    #   built from git output, and pytest takes them as separate arguments.
    python -m pytest $CHANGED -q --no-header -p no:cacheprovider --timeout=60
else
    echo "   (none)"
fi

echo
echo "-- 3/4 the whole backend suite must at least be collectable"
# The step that would have caught the Python version gap on its own: forty files
# fail to import under 3.9 and the failure has nothing to do with any test.
python -m pytest tests/ --ignore=tests/test_api.py --ignore=tests/test_scoring_baseline.py \
    --collect-only -q --no-header -p no:cacheprovider 2>&1 | tail -3

echo
echo "-- 4/4 the whole backend suite"
python -m pytest tests/ --ignore=tests/test_api.py --ignore=tests/test_scoring_baseline.py \
    -q --no-header -p no:cacheprovider --timeout=60 2>&1 | tail -6
' 2>&1 | tee -a "$LOG" || run_rc=${PIPESTATUS[0]}

if ! kill -0 "$WATCHDOG" 2>/dev/null && [ "$run_rc" != 0 ]; then
    fail_msg="killed by the watchdog after ${PREFLIGHT_TIMEOUT}s"
elif [ "$run_rc" != 0 ]; then
    fail_msg="exited $run_rc"
fi
if [ "$run_rc" != 0 ]; then
    echo
    echo "== preflight FAILED: $fail_msg ==" >&2
    exit 1
fi

# Last, because the deployment guard refuses an unclean tree and this is where
# that gets discovered cheaply. Eleven stray SQLite files from local test runs
# sat here before anyone looked -- each harmless, none committed, and together
# enough to make the guard decline a deployment.
echo
echo "-- git tree must be clean of build and test debris"
STRAY="$(git -C "$REPO" status --porcelain --untracked-files=all \
         | grep -vE "^(A|M|\?\?) +(app|tests|tools|docs|infra|scripts|alembic|\.github)/" || true)"
if [ -n "$STRAY" ]; then
    printf '   %s\n' "$STRAY" >&2
    echo "== preflight FAILED: the working tree carries files nothing should have left ==" >&2
    exit 1
fi
echo "   clean"

echo
echo "== preflight passed =="
