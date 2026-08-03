#!/usr/bin/env bash
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
PY_IMAGE="python:3.11-slim"
# A named volume, because `pip install -r requirements.txt` pulls torch, SHAP,
# transformers and chromadb. Cold that is several minutes; cached it is seconds,
# and a preflight nobody waits for is a preflight nobody runs.
CACHE_VOL="sora-preflight-pip"

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

docker run --rm \
    -v "$REPO:/w" -w /w \
    -v "$CACHE_VOL:/root/.cache/pip" \
    -e SORA_OFFLINE=1 \
    -e RUN_SCHEDULER=false \
    -e DATABASE_URL="sqlite:////tmp/preflight.db" \
    -e REDIS_URL="" \
    -e SECRET_KEY=preflight \
    -e SORA_ADMIN_TOKEN=preflight \
    -e CHANGED="${CHANGED[*]}" \
    "$PY_IMAGE" bash -euo pipefail -c '
echo
echo "-- 1/4 dependencies, exactly as CI installs them"
# The same two lines as .github/workflows/ci.yml. Deliberately not
# requirements-dev.txt: that is what hid the pytest-asyncio difference, since it
# carries the plugin CI has never had.
apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq --no-install-recommends git build-essential >/dev/null 2>&1
pip install --quiet -r requirements.txt
pip install --quiet pytest pytest-cov pytest-timeout
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
'

echo
echo "== preflight passed =="
