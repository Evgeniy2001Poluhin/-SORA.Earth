#!/usr/bin/env bash
# Behavioural checks for the deployment guard.
#
# The guard is almost entirely refusals, so that is what this tests: the cases
# it must decline, each one standing for something that actually happened.
#
# git is real, not stubbed. The rollback rule is an ancestry question, and
# `git merge-base --is-ancestor` is the thing under test -- a stub would test
# the stub. Each case builds a throwaway repository with a real origin.
#
# docker, curl and systemctl are stubbed, driven by files in $STUB_DIR, so a
# test can produce a state a real daemon will not produce on demand: an orphan
# container surviving --remove-orphans, a config hash that does not match, a
# certificate mounted from the wrong place.
set -uo pipefail

SCRIPT="${SCRIPT_UNDER_TEST:-$(cd "$(dirname "$0")/.." && pwd)/scripts/deploy_production.sh}"

PASS=0; FAIL=0

# The tally reports on the exit path, not from the last line of the file.
#
# It used to be two statements at the bottom: an echo and `[ "$FAIL" -eq 0 ]`.
# The exit status of a script is the status of its last command, so a section
# appended below them ran, counted its failures into $FAIL, and left the status
# of whatever it happened to end with -- an `rm -rf`, which succeeds. One real
# FAIL was reported in the output and the job went green (found while adding
# the post-deploy behaviour cases).
#
# On the trap it holds wherever the last case is written.
_report_tally() {
    local rc=$?
    echo
    echo "  passed: $PASS   failed: $FAIL"
    if [ "$FAIL" -ne 0 ]; then
        exit 1
    fi
    exit "$rc"
}
trap _report_tally EXIT
ok()    { PASS=$((PASS+1)); printf '  ok    %s\n' "$1"; }
bad()   { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 — expected [$3], got [$2]"; fi; }

# Refuses for the stated reason, not merely refuses. Every precondition here
# exits non-zero, so a status alone cannot tell "wrong branch" from "the stub
# fell over", and a test that cannot tell them apart passes for the wrong
# reason.
refused_because() {
    local label="$1" want="$2"
    if [ "$RC" = 0 ]; then bad "$label — expected a refusal, got exit 0"; return; fi
    if grep -qi -- "$want" "$SANDBOX/out"; then ok "$label"; else
        bad "$label — refused, but not for '$want': $(grep -i REFUSED "$SANDBOX/out" | head -1)"
    fi
}

new_sandbox() {
    SANDBOX="$(mktemp -d)"
    STUB_DIR="$SANDBOX/stub"; mkdir -p "$STUB_DIR/bin"
    ORIGIN="$SANDBOX/origin.git"
    REPO="$SANDBOX/repo"

    # git -C throughout rather than cd, so no test can leave the shell somewhere
    # unexpected and no cd needs an error path of its own.
    git init -q --bare "$ORIGIN"
    git clone -q "$ORIGIN" "$REPO" 2>/dev/null
    git -C "$REPO" config user.email t@t
    git -C "$REPO" config user.name t
    mkdir -p "$REPO/nginx"
    {
        echo "upstream sora_backend {"
        echo "    server backend:8000;"
        echo "}"
    } > "$REPO/nginx/nginx.conf"
    echo "services: {}" > "$REPO/compose.yml"
    git -C "$REPO" add -A >/dev/null
    git -C "$REPO" commit -qm "first"
    git -C "$REPO" branch -M main >/dev/null 2>&1
    git -C "$REPO" push -q origin main 2>/dev/null
    FIRST="$(git -C "$REPO" rev-parse HEAD)"
    echo "second" > "$REPO/second.txt"
    git -C "$REPO" add -A >/dev/null
    git -C "$REPO" commit -qm "second"
    git -C "$REPO" push -q origin main 2>/dev/null
    SECOND="$(git -C "$REPO" rev-parse HEAD)"

    CONF_SUM="$(sha256sum "$REPO/nginx/nginx.conf" | awk '{print $1}')"
    : > "$STUB_DIR/services"          # declared services, one per line
    echo "nginx" >> "$STUB_DIR/services"
    echo "backend" >> "$STUB_DIR/services"
    : > "$STUB_DIR/running"           # containers: name|service|ports
    echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" >> "$STUB_DIR/running"
    echo "p-backend-1|backend|8000/tcp" >> "$STUB_DIR/running"
    echo "$CONF_SUM" > "$STUB_DIR/container_conf_sum"
    # `resolve` is required since #129: without it nginx caches the address
    # and a container recreate strands it.
    echo "    server backend:8000 resolve;" > "$STUB_DIR/upstream_line"
    echo "/etc/letsencrypt" > "$STUB_DIR/cert_source"
    echo 200 > "$STUB_DIR/http_code"

    cat > "$STUB_DIR/bin/docker" <<'STUB'
#!/usr/bin/env bash
argv="$*"
# Every invocation is recorded. "Refused before anything started" is otherwise
# unfalsifiable: the guard exits non-zero either way, and only the absence of an
# `up` tells prevention from detection.
printf '%s\n' "$argv" >> "$STUB_DIR/calls"
case "$argv" in
    # Before "config --services", which would otherwise not match this at all --
    # kept adjacent so the two cannot drift apart.
    *"config --format json"*)  cat "$STUB_DIR/rendered"; exit 0 ;;
    *"config --services"*)  cat "$STUB_DIR/services"; exit 0 ;;
    *"ps --format {{.Service}}"*)
        cut -d'|' -f2 "$STUB_DIR/running"; exit 0 ;;
    *'{{.Label "com.docker.compose.service"}}|{{.Ports}}'*)
        # Before the label-only branch, which would otherwise swallow this.
        # Field 2 is the compose service, field 3 the ports.
        awk -F'|' '{print $2"|"$3}' "$STUB_DIR/running"; exit 0 ;;
    *'{{.Label "com.docker.compose.service"}} {{.Image}} {{.ID}}'*)
        awk -F'|' '{print $2" image-"$2" id-"$2}' "$STUB_DIR/running"; exit 0 ;;
    *'{{.Label "com.docker.compose.service"}}'*)
        cut -d'|' -f2 "$STUB_DIR/running"; exit 0 ;;
    *'{{.Names}}|{{.Ports}}'*)
        awk -F'|' '{print $1"|"$3}' "$STUB_DIR/running"; exit 0 ;;
    *'{{.Names}} {{.Ports}}'*)
        awk -F'|' '{print "  "$1" "$3}' "$STUB_DIR/running"; exit 0 ;;
    # Before the generic "ps -q": the rollback asks for ids, and a test needs to
    # be able to say a container existed before this run started.
    *"ps -aq"*)
        # A superset once the mutation has run: the ids that existed plus the
        # one this run created. Returning the same list both times left the
        # `created` set empty, so `docker stop` never ran and the ownership
        # assertion held for the wrong reason -- an unconditional
        # `docker stop $now` would have passed it too.
        [ -f "$STUB_DIR/pre_cids" ] && cat "$STUB_DIR/pre_cids"
        [ -f "$STUB_DIR/calls" ] && grep -qE 'up -d (--build|--no-build) --remove-orphans' "$STUB_DIR/calls" \
            && echo "cid-created-by-this-run"
        exit 0 ;;
    # Before every other `up`/`run` branch: the migration step must be
    # distinguishable from a container start, or "migrated once, first" cannot
    # be told from "started something".
    # The tag's id, and a way for a test to make the running container disagree
    # with it. Without a branch here the id comes back empty and the identity
    # check refuses every deployment for the wrong reason.
    *"inspect -f {{.Created}}"*)
        # Container age. `container_created` lets a test place a container
        # before the interrupted run began, which is the case an image
        # comparison alone cannot reject.
        cat "$STUB_DIR/container_created" 2>/dev/null || echo "2026-08-14T03:39:28.100020191Z"
        exit 0 ;;
    *"exec -T backend python3 -"*)
        # The post-deploy behaviour probe. Reads its own route table from the
        # running app, so the stub answers with a route table and the codes a
        # correct deployment produces; a case that wants a defect overwrites
        # `probe_json`.
        if [ -f "$STUB_DIR/probe_json" ]; then
            cat "$STUB_DIR/probe_json"
        else
            printf '%s\n' '{"absent_path":"/api/v1/__deploy_probe_absent__","absent":404,"get_only_path":"/api/v1/ab/stats","get_only":405,"post_path":"/api/v1/auth/login","post":422}'
        fi
        exit 0 ;;
    *"verify_schema_head.py"*)
        [ -f "$STUB_DIR/schema_behind" ] && {
            echo "REFUSING TO START: the database is behind" >&2; exit 1; }
        echo "schema check: schema is at head"
        exit 0 ;;
    *"image inspect"*)
        cat "$STUB_DIR/app_image_id" 2>/dev/null || echo "sha256:image"
        exit 0 ;;
    *"build backend"*)
        [ -f "$STUB_DIR/build_fails" ] && exit 1
        exit 0 ;;
    *"run --rm --no-deps migrate"*|*"run --rm --build migrate"*|*"run --rm migrate"*)
        n=$(( $(cat "$STUB_DIR/migrate_calls" 2>/dev/null || echo 0) + 1 ))
        echo "$n" > "$STUB_DIR/migrate_calls"
        [ -f "$STUB_DIR/migrate_fails" ] && exit 1
        exit 0 ;;
    *"up -d postgres"*)
        [ -f "$STUB_DIR/postgres_fails" ] && exit 1
        exit 0 ;;
    *"up -d --no-build --remove-orphans"*)
        # Both the deployment start and the rollback restore spell this the same
        # way. They differ in one thing only: the restore passes a second `-f`,
        # the generated image-pin override. Matching on the text alone swallowed
        # the restore, so `no_build_fails` was never read, `rolled_back` was
        # never set, and four rollback cases quietly stopped testing anything.
        # Counted as tokens: `grep -c` counts matching lines, and argv is one
        # line, so it answers 1 for both spellings and decides nothing.
        if [ "$(printf '%s' "$argv" | tr ' ' '\n' | grep -c -- '^-f$')" -ge 2 ]; then
            touch "$STUB_DIR/rolled_back"
            [ -f "$STUB_DIR/no_build_fails" ] && exit 1
            exit 0
        fi
        # The deployment start.
        [ -f "$STUB_DIR/slow_up" ] && sleep 6
        n=$(( $(cat "$STUB_DIR/up_calls" 2>/dev/null || echo 0) + 1 ))
        echo "$n" > "$STUB_DIR/up_calls"
        if [ -f "$STUB_DIR/up_fails_after_first" ] && [ "$n" -gt 1 ]; then exit 1; fi
        exit 0 ;;
    *"up -d --no-build"*)
        # The exact-image restore. Recorded so a test can tell it from a
        # rebuild, which is a different operation with a different verdict.
        touch "$STUB_DIR/rolled_back"
        [ -f "$STUB_DIR/no_build_fails" ] && exit 1
        exit 0 ;;
    *"up -d --build"*)
        # `slow_up` holds the mutation open so a test can interrupt it. Without
        # it the window between `up` and the postflight is too short to hit.
        [ -f "$STUB_DIR/slow_up" ] && sleep 6
        # A rollback rebuilds, so this is called twice. `up_fails_after_first`
        # lets a test fail the second one and nothing else -- which is what
        # separates "refused, previous state restored" from "refused, and the
        # rollback did not complete".
        n=$(( $(cat "$STUB_DIR/up_calls" 2>/dev/null || echo 0) + 1 ))
        echo "$n" > "$STUB_DIR/up_calls"
        if [ -f "$STUB_DIR/up_fails_after_first" ] && [ "$n" -gt 1 ]; then exit 1; fi
        exit 0 ;;
    *"ps -q nginx"*)        echo "cid-nginx"; exit 0 ;;
    *"ps -q"*)              echo "cid-x"; exit 0 ;;
    *"sha256sum /etc/nginx/nginx.conf"*)
        echo "$(cat "$STUB_DIR/container_conf_sum")  /etc/nginx/nginx.conf"; exit 0 ;;
    *"nginx -t"*)
        [ -f "$STUB_DIR/nginx_t_fails" ] && exit 1
        exit 0 ;;
    # Both spellings: the check used `grep -A2 \'upstream sora_backend\'` and
    # now uses an awk range written `upstream[[:space:]]+sora_backend`, which
    # the literal glob no longer matched -- the stub fell through, returned
    # nothing, and every deployment test refused with an empty upstream.
    *"upstream sora_backend"*|*"sora_backend[[:space:]]"*|*"+sora_backend"*)
        # `upstream_cmd_fails` reproduces what a real daemon did: the command
        # exits non-zero with no output -- `grep` finding nothing -- which under
        # `set -e` ends the script outside any `fail`.
        [ -f "$STUB_DIR/upstream_cmd_fails" ] && exit 1
        cat "$STUB_DIR/upstream_line"; exit 0 ;;
    *"inspect"*"Destination"*)  cat "$STUB_DIR/cert_source"; exit 0 ;;
    # Before the generic image branch: the snapshot reads the compose service
    # off each container, and without this it comes back empty and the rollback
    # silently degrades to a rebuild.
    *"inspect -f {{index .Config.Labels"*)
        echo "${STUB_SERVICE:-nginx}"; exit 0 ;;
    *"inspect -f {{.Image}}"*)
        # `restored_image` lets a test say the image after the rollback is not
        # the one recorded -- the case the verification exists for.
        if [ -f "$STUB_DIR/rolled_back" ] && [ -f "$STUB_DIR/restored_image" ]; then
            cat "$STUB_DIR/restored_image"
        else
            echo "sha256:image"
        fi
        exit 0 ;;
    *) exit 0 ;;
esac
STUB
    # One status per line, consumed in order; the last line repeats once the list
    # runs out. That is what tells a transient failure from a permanent one --
    # with a single fixed status the retry loop cannot be observed at all, and a
    # broken retry would pass.
    # The rendered compose configuration, as `config --format json` returns it.
    # Production's actual shape: nginx off-host on 80 and 443, everything else
    # on loopback. A test that needs a different one overwrites this file.
    cat > "$STUB_DIR/rendered" <<'JSON'
{"services": {
  "nginx":    {"ports": [{"target": 80,  "published": "80",  "protocol": "tcp", "mode": "ingress"},
                         {"target": 443, "published": "443", "protocol": "tcp", "mode": "ingress"}]},
  "app":      {"ports": [{"host_ip": "127.0.0.1", "target": 8000, "published": "8000", "protocol": "tcp", "mode": "ingress"}]},
  "postgres": {"ports": [{"host_ip": "127.0.0.1", "target": 5432, "published": "5432", "protocol": "tcp", "mode": "ingress"}]}
}}
JSON

    cat > "$STUB_DIR/bin/curl" <<'STUB'
#!/usr/bin/env bash
seq_file="$STUB_DIR/http_code"
n_file="$STUB_DIR/http_calls"
n=$(( $(cat "$n_file" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$n_file"
# Recorded so a test can assert the probe identifies itself. Without this the
# marker could be dropped and every acceptance query would silently go back to
# counting the deployment's own retries as user traffic.
printf '%s\n' "$*" >> "$STUB_DIR/curl_args"
# The behaviour probe asks about three specific paths and must not consume the
# health sequence: those codes are about a different question, and letting the
# probe eat them made the health retry cases fail for a reason that had nothing
# to do with health.
case "$*" in
    *__deploy_probe_absent__*)
        printf '%s' "$(cat "$STUB_DIR/probe_ext_absent" 2>/dev/null || echo 404)"; exit 0 ;;
    *"/api/v1/ab/stats"*)
        printf '%s' "$(cat "$STUB_DIR/probe_ext_get_only" 2>/dev/null || echo 405)"; exit 0 ;;
    *"/api/v1/auth/login"*)
        printf '%s' "$(cat "$STUB_DIR/probe_ext_post" 2>/dev/null || echo 422)"; exit 0 ;;
esac
total=$(wc -l < "$seq_file")
line=$(( n <= total ? n : total ))
code="$(sed -n "${line}p" "$seq_file")"
# curl prints 000 and exits non-zero when it cannot connect, which is exactly
# the case the caller has to handle without appending a second 000.
printf '%s' "$code"
[ "$code" = "000" ] && exit 7
exit 0
STUB
    # Frozen, so "two deployments in the same second" is a fact of the test
    # rather than a race against the wall clock. The earlier version waited for
    # the second to roll over, which made the case it cared about the one it was
    # least likely to exercise.
    cat > "$STUB_DIR/bin/date" <<'STUB'
#!/usr/bin/env bash
case "$*" in
    *%Y%m%dT%H%M%SZ*)      echo "20260803T120000Z" ;;
    *%Y-%m-%dT%H:%M:%SZ*)  echo "2026-08-03T12:00:00Z" ;;
    # `command date` is not an escape from this file. `command` bypasses
    # functions and aliases; it does not bypass PATH, and PATH begins with the
    # directory this stub lives in -- so it re-executed itself, forked
    # exponentially, and the runner killed the job with SIGTERM at 87 seconds.
    # Nothing reached this branch until --finalize asked for `date -d ... +%s`,
    # so the recursion sat here unfired for as long as the stub has existed.
    *)  real="$(PATH=/usr/bin:/bin command -v date)"
        [ -n "$real" ] || { echo "stub date: no system date found" >&2; exit 127; }
        exec "$real" "$@" ;;
esac
STUB
    cat > "$STUB_DIR/bin/systemctl" <<'STUB'
#!/usr/bin/env bash
exit 1
STUB
    cat > "$STUB_DIR/bin/crontab" <<'STUB'
#!/usr/bin/env bash
exit 1
STUB
    chmod +x "$STUB_DIR/bin/"*
}

run_guard() {
    export STUB_DIR
    # shellcheck disable=SC2030
    #   The subshell is the point: the stubbed PATH must reach the script and
    #   nothing else, so that a later real command in this file cannot pick up a
    #   stub by accident. Losing the change on the way out is the intent.
    ( export PATH="$STUB_DIR/bin:$PATH"
      DEPLOY_REPO="$REPO" \
      COMPOSE_FILE="$REPO/compose.yml" \
      COMPOSE_PROJECT_NAME="p" \
      SITE_URL="http://stand.invalid" \
      MANIFEST_DIR="${MANIFEST_OVERRIDE:-$SANDBOX/manifests}" \
      DEPLOY_LOCK_DIR="${LOCK_DIR_OVERRIDE:-$SANDBOX/lockdir}" \
      HEALTH_ATTEMPTS="${HEALTH_ATTEMPTS:-3}" \
      HEALTH_DELAY=0 \
        bash "$SCRIPT" "$@" ) > "$SANDBOX/out" 2>&1
    RC=$?
}

echo "== the stub intercepts =="
# First, because every case below is worthless if the real docker is being run.
# In a subshell, like run_guard, so the stubbed PATH never leaks into this one.
new_sandbox
# shellcheck disable=SC2031
#   Same reason as run_guard above: the stubbed PATH is scoped to this one
#   command on purpose, so it cannot leak into the rest of the file.
check "the stubbed docker answers" \
    "$( export STUB_DIR; PATH="$STUB_DIR/bin:$PATH" docker compose config --services | tr '\n' ' ' )" \
    "nginx backend "
# And that a stub which does not handle a call reaches the real tool rather than
# itself. `date` freezes two formats and passes everything else through; it did
# so with `command date`, which bypasses functions and aliases but not PATH --
# and PATH begins with the stub. It re-executed itself without bound.
#
# The failure had no failing assertion. The job died at 87 seconds with SIGTERM
# and printed no verdict at all, which is why it is worth a case of its own:
# `timeout` turns the hang into an answer. The two frozen formats are asserted
# beside it, because a pass-through that forgot to freeze would be the other way
# to make this line green.
# shellcheck disable=SC2031
check "an unhandled date falls through to the system date, not to itself" \
    "$( export STUB_DIR; PATH="$STUB_DIR/bin:$PATH" timeout 10 date -u -d @86400 +%Y-%m-%d 2>/dev/null || echo "recursed or hung" )" \
    "1970-01-02"
# shellcheck disable=SC2031
check "and the run-id format is still frozen" \
    "$( export STUB_DIR; PATH="$STUB_DIR/bin:$PATH" date -u +%Y%m%dT%H%M%SZ )" \
    "20260803T120000Z"
rm -rf "$SANDBOX"

echo "== it refuses to deploy anything but current, clean main =="
new_sandbox
git -C "$REPO" checkout -qb sidebranch
run_guard
refused_because "a branch other than main" "production is deployed from main only"
rm -rf "$SANDBOX"

new_sandbox
echo stray > "$REPO/stray.txt"
run_guard
refused_because "a dirty working tree" "working tree is not clean"
rm -rf "$SANDBOX"

new_sandbox
# HEAD one commit behind what origin/main points at.
git -C "$REPO" reset -q --hard "$FIRST"
run_guard
refused_because "a HEAD that is not origin/main" "is not origin/main"
rm -rf "$SANDBOX"

echo "== rollback is allowed, but only to merged code =="
# The rule this replaces required HEAD == origin/main unconditionally, which
# forbade rolling back to the last good commit -- the guard would have blocked
# recovery during the incident it exists to prevent.
new_sandbox
run_guard --rollback "$FIRST"
check "an ancestor of origin/main is accepted" \
    "$(grep -c 'rolling back to' "$SANDBOX/out")" "1"
check "and it is not refused"  "$([ "$RC" = 0 ] && echo ran || echo "exit $RC")" "ran"
rm -rf "$SANDBOX"

new_sandbox
# A commit that exists but was never merged: reachable from no branch on origin.
git -C "$REPO" checkout -q -b unmerged
echo x > "$REPO/x.txt"
git -C "$REPO" add -A >/dev/null
git -C "$REPO" commit -qm unmerged
UNMERGED="$(git -C "$REPO" rev-parse HEAD)"
git -C "$REPO" checkout -q main
run_guard --rollback "$UNMERGED"
refused_because "an unmerged commit" "not an ancestor of origin/main"
rm -rf "$SANDBOX"

new_sandbox
run_guard --rollback deadbeefdeadbeefdeadbeefdeadbeefdeadbeef
refused_because "a commit that does not exist" "is not a commit in this repository"
rm -rf "$SANDBOX"

new_sandbox
run_guard --rollback
refused_because "--rollback without a commit" "needs a commit"
rm -rf "$SANDBOX"

new_sandbox
run_guard --rollback "$SECOND"
refused_because "rolling back to origin/main itself" "use a plain deploy"
rm -rf "$SANDBOX"

new_sandbox
run_guard --wat
refused_because "an unknown argument" "unknown argument"
rm -rf "$SANDBOX"

echo "== it refuses a deployment that did not end where it should =="
new_sandbox
# A declared service that never came up.
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp" > "$STUB_DIR/running"
run_guard
refused_because "a declared service missing" "is not running"
rm -rf "$SANDBOX"

new_sandbox
# The converse, and the one that mattered: a container nothing declares,
# surviving beside the declared ones. This is the shape of the dev `app`
# container that served the public site for sixteen days.
echo "p-app-1|app|0.0.0.0:8000->8000/tcp" >> "$STUB_DIR/running"
run_guard
refused_because "an undeclared container surviving" "does not declare"
rm -rf "$SANDBOX"

echo "== it refuses anything reachable off-host but 80 and 443 =="
# Enumerated rather than sampled: a guard written against the one example that
# prompted it is how "8000:8000" got banned while "9000:8000" stayed legal.
for form in "0.0.0.0:8000->8000/tcp" "0.0.0.0:9000->8000/tcp" ":::8000->8000/tcp"; do
    new_sandbox
    echo "nginx" > "$STUB_DIR/services"
    echo "p-nginx-1|nginx|$form" > "$STUB_DIR/running"
    run_guard
    refused_because "published off-host: $form" "reachable off-host"
    rm -rf "$SANDBOX"
done
# Each of these states a form that must be *accepted*. The published 80 and 443
# come alongside it, because the guard now also requires them: a state where
# neither is published is not "a deployment with an extra loopback port", it is
# a deployment serving nothing, and it must be refused for that reason instead.
for form in "127.0.0.1:9090->9090/tcp" "0.0.0.0:80->80/tcp" "0.0.0.0:443->443/tcp" "8000/tcp"; do
    new_sandbox
    echo "nginx" > "$STUB_DIR/services"
    echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp, $form" > "$STUB_DIR/running"
    run_guard
    check "accepted: $form" "$([ "$RC" = 0 ] && echo ok || echo "refused: $(grep -m1 REFUSED "$SANDBOX/out")")" "ok"
    rm -rf "$SANDBOX"
done

echo "== it refuses an nginx that is not serving the repository's config =="
new_sandbox
echo "0000000000000000000000000000000000000000000000000000000000000000" > "$STUB_DIR/container_conf_sum"
run_guard
refused_because "a config hash that does not match" "not the repository's"
rm -rf "$SANDBOX"

new_sandbox
touch "$STUB_DIR/nginx_t_fails"
run_guard
refused_because "a config nginx itself rejects" "rejects its own configuration"
rm -rf "$SANDBOX"

new_sandbox
echo "    server app:8000 resolve;" > "$STUB_DIR/upstream_line"
run_guard
refused_because "an upstream that is not backend" "expected backend:8000"
rm -rf "$SANDBOX"

new_sandbox
# The host is right and the address would still be frozen at worker start.
# Accepting this is how the fix could be reverted while every deployment kept
# reporting success (#129).
echo "    server backend:8000;" > "$STUB_DIR/upstream_line"
run_guard
refused_because "an upstream without resolve" "has no 'resolve'"
rm -rf "$SANDBOX"

echo "== it refuses certificates from anywhere but the renewed store =="
# ./certs held a copy three weeks behind /etc/letsencrypt and on no renewal
# path. It would have served a valid certificate right up until it did not.
new_sandbox
echo "/opt/sora_earth_ai_platform/certs" > "$STUB_DIR/cert_source"
run_guard
refused_because "a certificate store that is not renewed" "not /etc/letsencrypt"
rm -rf "$SANDBOX"

echo "== health checks are retried, but not forever =="
new_sandbox
# Transient: nginx has just been recreated and the backend may still be taking
# its first connections, so one failure means nothing.
printf '000\n200\n' > "$STUB_DIR/http_code"
run_guard
check "a first failure is retried, not fatal"  "$RC" "0"
check "and the retry is reported"              "$(grep -c 'retrying' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

new_sandbox
# Permanent: every attempt fails and the deployment must end in an error rather
# than hang.
printf '000\n' > "$STUB_DIR/http_code"
run_guard
refused_because "a persistent failure" "after 3 attempts"
check "the status is not doubled" \
    "$(grep -c 'returned 000000' "$SANDBOX/out")" "0"
check "it reports the real status" \
    "$(grep -c 'returned 000 after' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

new_sandbox
echo 502 > "$STUB_DIR/http_code"
run_guard
refused_because "a non-200 from the site" "returned 502"
# The manifest is the next run's idea of what is deployed. Publishing one for a
# deployment whose smoke checks failed would name a state that was never
# reached, and the following rollback would aim at it.
check "no manifest is published when smoke fails" \
    "$(find "$SANDBOX/manifests" -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
check "and no partial file is left behind" \
    "$(find "$SANDBOX/manifests" -name '*.tmp' 2>/dev/null | wc -l | tr -d ' ')" "0"
rm -rf "$SANDBOX"

echo "== a good deployment is recorded =="
new_sandbox
run_guard
check "exit status is 0"          "$RC" "0"
check "a manifest was written"    "$(find "$SANDBOX/manifests" -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "1"
MAN="$SANDBOX/manifests/$(readlink "$SANDBOX/manifests/latest")"
check "it records the commit"     "$(grep -c "^commit         $SECOND" "$MAN")" "1"
check "it records the mode"       "$(grep -c '^mode           deploy' "$MAN")" "1"
check "it records the config hash" "$(grep -c "^nginx_config   sha256:$CONF_SUM" "$MAN")" "1"
# Without this a rollback has nothing to aim at: the manifest of the run that
# breaks production is the only record of what it replaced.
check "it records what it replaced" "$(grep -c 'state replaced by this run' "$MAN")" "1"
check "including the previous commit" "$(grep -c '^previous_commit ' "$MAN")" "1"
check "and the previous images"     "$(grep -c '^previous_images:' "$MAN")" "1"
# The systemctl stub exits 1, so this scenario is "the lookup failed", not "no
# renewal is configured". The previous assertion here demanded the words "no
# certbot timer" for it -- enshrining the conflation of the two. Production
# showed the cost on 2026-08-05: the deployment reported renewal missing while
# certbot.timer was enabled, active and had run ten hours earlier.
check "an unavailable systemctl is reported as undetermined, not absent" \
    "$(grep -qi 'could not query systemd' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "and it does not claim the timer is missing" \
    "$(grep -qi 'no certbot timer and no cron' "$SANDBOX/out" && echo yes || echo no)" "no"
# The guard refuses a dirty tree, so a guard that dirties the tree refuses its
# own next run. The default manifest directory was inside the checkout and every
# test overrode it, which is precisely why nobody noticed.
check "the checkout is left clean" \
    "$(git -C "$REPO" status --porcelain | wc -l | tr -d ' ')" "0"
rm -rf "$SANDBOX"

echo "== and the checkout would not stay clean if manifests lived in it =="
# The control for the line above: with the manifest directory inside the
# checkout, the tree is dirty afterwards and the next deployment is refused.
# Without this the "left clean" assertion could pass for any reason at all.
new_sandbox
MANIFEST_OVERRIDE="$REPO/docs/evidence" run_guard
check "a manifest written into the checkout dirties it" \
    "$([ "$(git -C "$REPO" status --porcelain | wc -l)" -gt 0 ] && echo dirty || echo clean)" "dirty"
run_guard
refused_because "and the next run is then refused" "working tree is not clean"
rm -rf "$SANDBOX"

echo "== the manifest names what it replaced, not what it deployed =="
new_sandbox
run_guard
check "first deploy succeeds" "$RC" "0"
MAN1="$SANDBOX/manifests/$(readlink "$SANDBOX/manifests/latest")"
check "with no previous deployment recorded" \
    "$(grep -c '^previous_commit none-recorded' "$MAN1")" "1"

# A third commit, so the second deployment has a different target from the
# first. Deploying the same commit twice would make previous_commit and commit
# equal for an honest reason, and the assertion below could not tell a correct
# implementation from the bug it exists to catch -- both produce SECOND.
echo third > "$REPO/third.txt"
git -C "$REPO" add -A >/dev/null
git -C "$REPO" commit -qm third
git -C "$REPO" push -q origin main 2>/dev/null
THIRD="$(git -C "$REPO" rev-parse HEAD)"

sleep 1
run_guard
check "second deploy succeeds" "$RC" "0"
# Followed through the pointer, not chosen by sorting names -- the sorting is
# what broke, so a test that sorts would agree with the bug.
MAN2="$SANDBOX/manifests/$(readlink "$SANDBOX/manifests/latest")"
check "two manifests exist" \
    "$(find "$SANDBOX/manifests" -name '*.txt' | wc -l | tr -d ' ')" "2"
check "the second deployed the new commit" \
    "$(awk '/^commit /{print $2}' "$MAN2")" "$THIRD"
# The field a rollback depends on. It used to be `git rev-parse HEAD`, which on
# a deploy is already the target -- so it recorded the commit being deployed as
# the one being replaced, confidently and always wrongly. With two different
# commits the two answers finally differ.
check "and names the first deploy's commit as replaced" \
    "$(awk '/^previous_commit /{print $2}' "$MAN2")" "$SECOND"
rm -rf "$SANDBOX"

echo "== the lock must not sit anywhere others can write =="
# /var/lock is /run/lock, mode 1777. I checked the sticky bit, found it set, and
# reported that as sufficient. It is not: the sticky bit stops another user
# deleting root's lock file, not creating it first under its predictable name,
# and not planting a symlink there for `exec 9>` to follow and truncate as root.
#
# Enumerated over the mode space rather than sampled, since the check is
# arithmetic on two digits and it is easy to get the boundary wrong -- the first
# version of it did.
for mode in 700 750 755 711; do
    new_sandbox
    mkdir -p "$SANDBOX/lockdir"; chmod "$mode" "$SANDBOX/lockdir"
    LOCK_DIR_OVERRIDE="$SANDBOX/lockdir" run_guard
    check "mode $mode is accepted" \
        "$([ "$RC" = 0 ] && echo ok || echo "refused: $(grep -m1 REFUSED "$SANDBOX/out")")" "ok"
    rm -rf "$SANDBOX"
done
for mode in 770 757 777 720 702; do
    new_sandbox
    mkdir -p "$SANDBOX/lockdir"; chmod "$mode" "$SANDBOX/lockdir"
    LOCK_DIR_OVERRIDE="$SANDBOX/lockdir" run_guard
    refused_because "mode $mode is refused" "writable by group or others"
    rm -rf "$SANDBOX"
done

new_sandbox
# -d follows symlinks, so a link satisfies "is a directory" while the lock ends
# up somewhere else entirely -- possibly somewhere the attacker chose.
mkdir -p "$SANDBOX/real"; chmod 0750 "$SANDBOX/real"
ln -s "$SANDBOX/real" "$SANDBOX/linkdir"
LOCK_DIR_OVERRIDE="$SANDBOX/linkdir" run_guard
refused_because "a symlinked lock directory" "is a symlink"
rm -rf "$SANDBOX"

new_sandbox
# A directory someone else owns. They can replace the lock whatever its mode.
# Root can stage this directly; a non-root run borrows a system directory that
# root owns, which is the same property seen from the other side.
if [ "$(id -u)" = 0 ]; then
    mkdir -p "$SANDBOX/lockdir"; chmod 0750 "$SANDBOX/lockdir"
    chown 65534:65534 "$SANDBOX/lockdir" 2>/dev/null
    OTHER_DIR="$SANDBOX/lockdir"
else
    OTHER_DIR="/usr"     # root-owned, 0755
fi
LOCK_DIR_OVERRIDE="$OTHER_DIR" run_guard
refused_because "a lock directory owned by someone else" "not by the deploying user"
rm -rf "$SANDBOX"

echo "== deployments are serialised, and manifests are never overwritten =="
new_sandbox
# Contention must refuse, not skip. A backup that skips has lost nothing; a
# deployment that skips has silently not happened while its operator believes
# it did.
# The holder runs in the background; the assertion stays in this shell.
#
# It used to be the other way round -- guard and assertion both inside a
# subshell -- and the PASS/FAIL counters incremented there never reached the
# summary. A genuine failure printed FAIL and the run still ended "failed: 0".
# A test that can fail without changing the result is worse than no test.
#
# 0750 explicitly: mkdir uses the caller's umask, which is 002 for the CI
# runner, and the guard rightly refuses the 775 that produces. The lock path is
# the one the guard derives; it is no longer overridable, because an override
# let the file sit outside the directory all the checks validate.
mkdir -p "$SANDBOX/lockdir"; chmod 0750 "$SANDBOX/lockdir"
# The holder announces success; the test waits for that announcement.
#
# Polling `flock -n` and treating any non-zero result as "held" was wrong twice:
# flock returns non-zero for reasons other than contention, and after fifty
# failed probes the loop fell through and ran the guard anyway. If the holder
# never acquired the lock, the refusal under test would have been asserted
# against no contention at all -- passing or failing for reasons unrelated to
# the thing being checked.
( exec 9>"$SANDBOX/lockdir/deploy.lock"
  flock -n 9 || exit 1
  touch "$SANDBOX/lock-held"
  sleep 30 ) &
HOLDER=$!
HELD=0
for _ in $(seq 1 100); do
    if [ -f "$SANDBOX/lock-held" ]; then HELD=1; break; fi
    sleep 0.1
done
if [ "$HELD" != 1 ]; then
    bad "the lock holder never acquired the lock; contention was never tested"
else
    run_guard
    refused_because "a second deployment while one holds the lock" "another deployment holds"
fi
kill "$HOLDER" 2>/dev/null
wait "$HOLDER" 2>/dev/null
rm -rf "$SANDBOX"

new_sandbox
# The clock is frozen, so both of these land on the same timestamp. An earlier
# fix gave the second a "-1" suffix, and '-' sorts before '.', so picking the
# newest by name returned the *first* one -- the collision fix broke the chain it
# was written to protect. Nothing sorts names now; `latest` is followed.
#
# The assertion is deliberately about the chain rather than about files existing.
# The test this replaces checked that a new file appeared beside the old one,
# which stayed true while the chain was broken.
echo third > "$REPO/third.txt"
git -C "$REPO" add -A >/dev/null; git -C "$REPO" commit -qm third
git -C "$REPO" push -q origin main 2>/dev/null
THIRD="$(git -C "$REPO" rev-parse HEAD)"

git -C "$REPO" reset -q --hard "$SECOND"
git -C "$REPO" push -qf origin "HEAD:main" 2>/dev/null
run_guard
check "first deploy in this second succeeds"  "$RC" "0"
FIRST_MAN="$(readlink "$SANDBOX/manifests/latest")"

git -C "$REPO" reset -q --hard "$THIRD"
git -C "$REPO" push -qf origin "HEAD:main" 2>/dev/null
run_guard
check "second deploy in the same second succeeds" "$RC" "0"
SECOND_MAN="$(readlink "$SANDBOX/manifests/latest")"
check "they are different files"    "$([ "$FIRST_MAN" != "$SECOND_MAN" ] && echo different || echo same)" "different"
check "both manifests survive"      "$(find "$SANDBOX/manifests" -name '*.txt' | wc -l | tr -d ' ')" "2"
check "latest names the second"     "$(awk '/^commit /{print $2}' "$SANDBOX/manifests/$SECOND_MAN")" "$THIRD"
# The one that matters: the next run must read the second, not the first.
check "and the second names the first as replaced" \
    "$(awk '/^previous_commit /{print $2}' "$SANDBOX/manifests/$SECOND_MAN")" "$SECOND"
rm -rf "$SANDBOX"

new_sandbox
# A third deployment, still in the same frozen second, must see the second.
run_guard
echo third > "$REPO/third.txt"; git -C "$REPO" add -A >/dev/null
git -C "$REPO" commit -qm third; git -C "$REPO" push -q origin main 2>/dev/null
THIRD="$(git -C "$REPO" rev-parse HEAD)"
run_guard
echo fourth > "$REPO/fourth.txt"; git -C "$REPO" add -A >/dev/null
git -C "$REPO" commit -qm fourth; git -C "$REPO" push -q origin main 2>/dev/null
FOURTH="$(git -C "$REPO" rev-parse HEAD)"
run_guard
check "the third deploy succeeds"   "$RC" "0"
MAN3="$SANDBOX/manifests/$(readlink "$SANDBOX/manifests/latest")"
check "it deployed the newest commit"        "$(awk '/^commit /{print $2}' "$MAN3")" "$FOURTH"
check "and names the second deploy as previous" \
    "$(awk '/^previous_commit /{print $2}' "$MAN3")" "$THIRD"
rm -rf "$SANDBOX"

new_sandbox
# The pointer must win over any name in the directory.
#
# Without this the suite could not tell the pointer from sorting at all: with
# mktemp suffixes every manifest has the same shape, so picking the newest by
# name lands on the right file about half the time and a mutant that sorts
# survives. Confirmed -- one did, 66 tests green.
#
# The decoy sorts after every real manifest and names a commit that was never
# deployed. Anything that chooses by name reads it; anything that follows the
# pointer does not.
run_guard
REAL_PREV="$SECOND"
echo "commit deadbeefdeadbeefdeadbeefdeadbeefdeadbeef" > "$SANDBOX/manifests/zzzz-decoy.txt"
echo third > "$REPO/third.txt"; git -C "$REPO" add -A >/dev/null
git -C "$REPO" commit -qm third; git -C "$REPO" push -q origin main 2>/dev/null
run_guard
check "the deploy after a decoy succeeds" "$RC" "0"
MAND="$SANDBOX/manifests/$(readlink "$SANDBOX/manifests/latest")"
check "the decoy is ignored" \
    "$(awk '/^previous_commit /{print $2}' "$MAND")" "$REAL_PREV"
rm -rf "$SANDBOX"

new_sandbox
# A failed deployment must leave the pointer where it was: the previous
# deployment is still what is serving, and is still what a rollback aims at.
run_guard
GOOD_MAN="$(readlink "$SANDBOX/manifests/latest")"
echo 502 > "$STUB_DIR/http_code"
run_guard
refused_because "the second run fails smoke" "returned 502"
check "latest still names the good deployment" \
    "$(readlink "$SANDBOX/manifests/latest")" "$GOOD_MAN"
check "and no manifest was added"   "$(find "$SANDBOX/manifests" -name '*.txt' | wc -l | tr -d ' ')" "1"
rm -rf "$SANDBOX"

echo "== the readiness probe identifies itself =="
# nginx has just been recreated and the backend may still be accepting its first
# connections, so the probe is meant to start early, get a 502 and retry. That
# 502 lands in the same access log as user traffic. On release 4cd7232 the
# acceptance query counted it and reported a user-facing failure; there was
# none, and the release before reported zero only because the probe arrived
# after the backend was listening (#142).

new_sandbox
run_guard
check "the deploy succeeded" "$RC" "0"
# The denominator first: an empty file would make both sides of the next two
# comparisons 0 and they would pass having measured nothing.
check "the probe ran at all" \
    "$([ -s "$STUB_DIR/curl_args" ] && echo yes || echo no)" "yes"
check "every probe carries the marker" \
    "$(grep -c 'sora-deploy-healthcheck/1' "$STUB_DIR/curl_args")" \
    "$(wc -l < "$STUB_DIR/curl_args" | tr -d ' ')"
check "and the marker is not merely mentioned somewhere" \
    "$(grep -c -- '-A sora-deploy-healthcheck/1' "$STUB_DIR/curl_args")" \
    "$(wc -l < "$STUB_DIR/curl_args" | tr -d ' ')"
check "the run reports probe attempts separately" \
    "$(grep -c 'probe attempts' "$SANDBOX/out")" "1"
check "and says what acceptance must exclude" \
    "$(grep -c 'exclude only User-Agent' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

new_sandbox
# One 502 then 200 on each path: the shape a healthy deployment produces.
printf '502\n200\n200\n200\n' > "$STUB_DIR/http_code"
run_guard
check "an intermediate 502 does not fail the deployment" "$RC" "0"
check "and it is reported as a retry, not an outage" \
    "$(grep -c 'retrying' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

new_sandbox
# Never recovers. The final result is what decides, and a timeout is still a
# deployment failure -- marking the probe must not soften that.
printf '502\n' > "$STUB_DIR/http_code"
run_guard
refused_because "a probe that never reaches 200" "after"
rm -rf "$SANDBOX"

echo "== the rollback compares the journal against what is checked out =="
# The journal records deployments this script made. A deployment made by hand
# leaves no entry, so the next scripted run names the last *scripted* commit as
# the one it replaced -- skipping everything in between (#133). `--rollback`
# takes its target from that field via the "to undo:" line this script prints,
# so an operator following the script's own suggestion can land several commits
# further back than they expect, silently, at the moment they are least able to
# check.

new_sandbox
run_guard                                   # journal and checkout both SECOND
run_guard --rollback "$FIRST"
check "an agreeing journal does not block the rollback" "$RC" "0"
check "and it says what it compared" \
    "$(grep -c 'journal records' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

new_sandbox
run_guard                                   # journal records SECOND
# A deployment by hand: the checkout moves and the journal never hears about it.
git -C "$REPO" checkout -q --detach "$FIRST"
# The stub appends every docker invocation for the life of the sandbox, and the
# deployment above is a real one -- so counting from the start measures that,
# not the refusal. Truncated here so the window is the rollback attempt alone.
: > "$STUB_DIR/calls"
run_guard --rollback "$FIRST"
refused_because "a journal that disagrees with the checkout" \
    "disagrees with the checkout"
# Both sides of the comparison must appear, so an operator can see what the
# refusal is about without going to look. Asserted as "present", not as a
# count: the number of times each is printed is formatting, not contract.
check "the refusal names what is checked out" \
    "$(grep -qc "${FIRST:0:12}" "$SANDBOX/out" && echo yes || echo no)" "yes"
check "and what the journal claims" \
    "$(grep -qc "${SECOND:0:12}" "$SANDBOX/out" && echo yes || echo no)" "yes"
# `grep -c` prints 0 and exits 1 on an empty file, so `|| echo 0` appended a
# second 0 and the comparison saw "0\n0". The earlier fix -- truncating the
# file -- is what exposed this: while it held stale calls the count was
# non-zero and the fallback never fired.
check "and nothing was deployed" \
    "$(grep -c 'up -d' "$STUB_DIR/calls" || true)" "0"
check "and the checkout did not move" \
    "$(git -C "$REPO" rev-parse HEAD)" "$FIRST"
rm -rf "$SANDBOX"

new_sandbox
run_guard
git -C "$REPO" checkout -q --detach "$FIRST"
ROLLBACK_ACKNOWLEDGE="$FIRST" run_guard --rollback "$FIRST"
check "an acknowledgement carrying the observed commit is accepted" "$RC" "0"
check "and it says so out loud" \
    "$(grep -c 'divergence acknowledged' "$SANDBOX/out")" "1"
rm -rf "$SANDBOX"

new_sandbox
run_guard
git -C "$REPO" checkout -q --detach "$FIRST"
# The wrong SHA, and a plausible one: what the journal claims. An
# acknowledgement that could be pasted from the runbook without looking would
# be no acknowledgement at all.
ROLLBACK_ACKNOWLEDGE="$SECOND" run_guard --rollback "$FIRST"
refused_because "an acknowledgement of the wrong commit" "disagrees with the checkout"
rm -rf "$SANDBOX"

echo "== rollback reads the previous manifest, not the checkout =="
new_sandbox
run_guard                       # records SECOND as deployed
sleep 1
run_guard --rollback "$FIRST"   # rolls back to FIRST
check "the rollback succeeds"   "$RC" "0"
MANR="$SANDBOX/manifests/$(readlink "$SANDBOX/manifests/latest")"
check "it records the rollback target"  "$(awk '/^commit /{print $2}' "$MANR")" "$FIRST"
check "it records the mode"             "$(grep -c '^mode           rollback' "$MANR")" "1"
# Taken from the previous manifest. Read from the checkout it would have been
# FIRST, because the rollback checkout has already happened by that point.
check "and names the commit it replaced" "$(awk '/^previous_commit /{print $2}' "$MANR")" "$SECOND"
rm -rf "$SANDBOX"


echo "== a configuration that would publish off-host is refused before it starts =="
# Prevention, not detection. The runtime check below it sees the port only after
# `up`, and refusing then leaves it open -- see #71.
new_sandbox
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
cat > "$STUB_DIR/rendered" <<'JSON'
{"services": {
  "nginx": {"ports": [{"target": 80, "published": "80", "protocol": "tcp"},
                      {"target": 443, "published": "443", "protocol": "tcp"}]},
  "app":   {"ports": [{"target": 8000, "published": "8000", "protocol": "tcp"}]}
}}
JSON
run_guard
refused_because "a declared off-host 8000" "would publish"
check "and nothing was started" \
    "$(grep -qc 'up -d' "$STUB_DIR/calls" && echo started || echo no)" "no"
rm -rf "$SANDBOX"

echo "== the whole tuple decides, not the port number =="
# Each of these begins with an allowed number or looks close to one.
declared_refused() {
    local label="$1" spec="$2"
    new_sandbox
    echo "nginx" > "$STUB_DIR/services"
    echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
    printf '{"services": {"nginx": {"ports": [{"target": 80, "published": "80", "protocol": "tcp"}, {"target": 443, "published": "443", "protocol": "tcp"}, %s]}}}\n' \
        "$spec" > "$STUB_DIR/rendered"
    run_guard
    refused_because "$label" "would publish"
    rm -rf "$SANDBOX"
}
declared_refused "a range beginning at an allowed port" \
    '{"target": 80, "published": "80-81", "protocol": "tcp"}'
declared_refused "an allowed number over udp" \
    '{"target": 80, "published": "80", "protocol": "udp"}'
declared_refused "an allowed published with a different target" \
    '{"target": 8080, "published": "80", "protocol": "tcp"}'
declared_refused "a host address that is neither loopback nor all" \
    '{"host_ip": "10.0.0.5", "target": 80, "published": "80", "protocol": "tcp"}'
declared_refused "an unexpected publication mode" \
    '{"target": 80, "published": "80", "protocol": "tcp", "mode": "gateway"}'

echo "== loopback is allowed on both families =="
for ip in 127.0.0.1 ::1; do
    new_sandbox
    echo "nginx" > "$STUB_DIR/services"
    echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
    printf '{"services": {"nginx": {"ports": [{"target": 80, "published": "80", "protocol": "tcp"}, {"target": 443, "published": "443", "protocol": "tcp"}]}, "app": {"ports": [{"host_ip": "%s", "target": 8000, "published": "8000", "protocol": "tcp"}]}}}\n' \
        "$ip" > "$STUB_DIR/rendered"
    run_guard
    check "loopback $ip is accepted" \
        "$([ "$RC" = 0 ] && echo ok || echo "refused: $(grep -m1 REFUSED "$SANDBOX/out")")" "ok"
    rm -rf "$SANDBOX"
done

echo "== publishing nothing is not the same as publishing nothing forbidden =="
# The safety property is universally quantified, so an empty list satisfies it
# completely. Losing 80 costs every http:// caller, and the smoke test only
# exercises https://, so nothing else would notice.
# The runtime is made *correct* here on purpose: 80 and 443 are both published.
# Only the declaration is missing 80. Written the other way round the runtime
# check fires instead, the test passes, and the declared-side check it names is
# never exercised — removing that check failed nothing at all until this was
# split.
new_sandbox
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
printf '{"services": {"nginx": {"ports": [{"target": 443, "published": "443", "protocol": "tcp"}]}}}\n' \
    > "$STUB_DIR/rendered"
run_guard
refused_because "a configuration publishing no 80" "configuration publishes no off-host 80/tcp"
check "and it was refused before anything started" \
    "$(grep -qc 'up -d' "$STUB_DIR/calls" && echo started || echo no)" "no"
rm -rf "$SANDBOX"

new_sandbox
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|" > "$STUB_DIR/running"
run_guard
refused_because "a runtime publishing nothing at all" "no off-host 80/tcp"
rm -rf "$SANDBOX"


echo "== a decoy service cannot satisfy the completeness check =="
# `case "$name" in *nginx*)` matched any container whose name contained the
# substring. With nginx publishing nothing and a helper publishing 80 and 443,
# the gate reported the site as served while the service meant to serve it was
# not listening.
new_sandbox
printf 'nginx\nnginx-helper\n' > "$STUB_DIR/services"
{
    echo "p-nginx-1|nginx|"
    echo "p-nginx-helper-1|nginx-helper|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp"
} > "$STUB_DIR/running"
# Complete tuples, `mode` included, so the declaration passes the preflight
# cleanly and the refusal can only come from the runtime check.
cat > "$STUB_DIR/rendered" <<'JSON'
{"services": {
  "nginx":        {"ports": [{"target": 80, "published": "80", "protocol": "tcp", "mode": "ingress"},
                             {"target": 443, "published": "443", "protocol": "tcp", "mode": "ingress"}]},
  "nginx-helper": {"ports": []}
}}
JSON
run_guard
# The runtime wording, not the preflight's. Both say "no off-host 80/tcp", so
# matching that alone would pass on a preflight refusal and prove nothing about
# which containers the runtime check looked at.
refused_because "a helper publishing 80/443 does not stand in for nginx" \
    "nginx publishes no off-host 80/tcp"
check "the containers were started first" \
    "$(grep -qc 'up -d' "$STUB_DIR/calls" && echo yes || echo no)" "yes"
check "and the ports were read by compose service" \
    "$(grep -qc 'com.docker.compose.service"}}|{{.Ports' "$STUB_DIR/calls" && echo yes || echo no)" "yes"
echo "== a refusal after the containers are up rolls back =="
# Eight checks sit after `up`. Each used to abort a deployment whose containers
# were already running and leave the refused state in place -- the port case
# named an off-host port accurately and left it open.
with_previous_deployment() {
    mkdir -p "$SANDBOX/manifests"
    printf 'commit %s\n' "$FIRST" > "$SANDBOX/manifests/prev.txt"
    ln -sf prev.txt "$SANDBOX/manifests/latest"
}

new_sandbox
with_previous_deployment
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
touch "$STUB_DIR/nginx_t_fails"
run_guard
check "exit says the previous state is back"  "$RC" "1"
check "it rolled back"      "$(grep -qc 'rolling back' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "the recorded images were restored" \
    "$(grep -qc 'restored the images that were running' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "without building" \
    "$(grep -qc 'up -d --no-build' "$STUB_DIR/calls" && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== a refusal with nothing recorded stops only what this run created =="
# No previous manifest: there is no state to restore, so the only safe action is
# to stop what this run started. Anything already present may belong to another
# project, and stopping it damages something this deployment does not own.
new_sandbox
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
echo "cid-preexisting" > "$STUB_DIR/pre_cids"
touch "$STUB_DIR/nginx_t_fails"
run_guard
check "a distinct exit for an incomplete rollback" "$RC" "76"
check "it says nothing was recorded" \
    "$(grep -qc 'no previous deployment recorded' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "the pre-existing container is not stopped" \
    "$(grep -c 'stop cid-preexisting' "$STUB_DIR/calls")" "0"
# And the other half: the container this run created *is* stopped. Without it
# the case cannot tell "correctly excluded" from "the stop path never ran".
check "the container this run created is stopped" \
    "$(grep -q 'stop cid-created-by-this-run' "$STUB_DIR/calls" && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== a failed rollback is its own outcome =="
# Distinguished from a successful one: "refused, previous state running" and
# "refused, and the rollback did not complete" call for opposite responses, and
# one non-zero status cannot say which happened.
new_sandbox
with_previous_deployment
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
touch "$STUB_DIR/nginx_t_fails"
# Both routes broken: the exact restore and the rebuild it falls back to.
# Breaking only the rebuild no longer says anything, because the rollback no
# longer goes that way first.
touch "$STUB_DIR/no_build_fails"
touch "$STUB_DIR/up_fails_after_first"
run_guard
check "exit says the rollback did not complete" "$RC" "76"
check "and it says so"  "$(grep -qc 'ROLLBACK FAILED' "$SANDBOX/out" && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== a refusal before the containers start does not roll back =="
# Nothing has changed yet, so rebuilding the previous state would be work done
# for no reason -- and would make a preflight refusal indistinguishable from a
# post-mutation one in the record.
new_sandbox
with_previous_deployment
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
cat > "$STUB_DIR/rendered" <<'JSON'
{"services": {
  "nginx": {"ports": [{"target": 80, "published": "80", "protocol": "tcp"},
                      {"target": 443, "published": "443", "protocol": "tcp"}]},
  "app":   {"ports": [{"target": 8000, "published": "8000", "protocol": "tcp"}]}
}}
JSON
run_guard
check "refused with the plain status" "$RC" "1"
check "and no rollback was attempted" \
    "$(grep -qc 'rolling back' "$SANDBOX/out" && echo yes || echo no)" "no"
rm -rf "$SANDBOX"


echo "== a rebuild is reported as an incomplete rollback, not a restore =="
# Rebuilding the previous commit usually produces a working deployment, and it
# is not the runtime state that was replaced: a base image can have moved, a
# dependency resolved differently. Reporting it as "restored" would hand the
# operator a claim nobody checked.
new_sandbox
with_previous_deployment
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
touch "$STUB_DIR/nginx_t_fails"
touch "$STUB_DIR/no_build_fails"
run_guard
check "a rebuild does not count as a restore" "$RC" "76"
check "and it says which one happened" \
    "$(grep -qc 'ROLLBACK INCOMPLETE' "$SANDBOX/out" && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== an image that does not match the snapshot is not a restore =="
# The `up` can succeed having started something else. Verified afterwards
# against the recorded ids rather than inferred from the exit status.
new_sandbox
with_previous_deployment
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
echo "sha256:something-else" > "$STUB_DIR/restored_image"
touch "$STUB_DIR/nginx_t_fails"
run_guard
check "a different image is not a restore" "$RC" "76"
check "and the mismatch is named" \
    "$(grep -qc 'do not match the snapshot' "$SANDBOX/out" && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== an unfinished journal stops the next run =="
# After a kill between `up` and the verdict, what is running is whatever that
# run left. Deploying on top of it would build on a state nobody accepted.
new_sandbox
mkdir -p "$SANDBOX/manifests"
printf 'run x\nstate mutating\n' > "$SANDBOX/manifests/in-progress"
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
run_guard
refused_because "an interrupted previous run" "did not finish"
check "and nothing was started" \
    "$(grep -qc 'up -d' "$STUB_DIR/calls" && echo started || echo no)" "no"
rm -rf "$SANDBOX"

echo "== a successful deployment leaves no journal behind =="
new_sandbox
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
run_guard
check "the deployment succeeded" "$RC" "0"
check "and the journal is closed" \
    "$( [ -f "$SANDBOX/manifests/in-progress" ] && echo left || echo closed )" "closed"
rm -rf "$SANDBOX"


echo "== a signal during the mutation rolls back =="
# Only `fail()` routed through the rollback, so a SIGTERM -- Ctrl-C, a dropped
# session, an orchestrator killing the run -- left the new containers running
# with nothing to undo them.
new_sandbox
with_previous_deployment
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
touch "$STUB_DIR/slow_up"
# shellcheck disable=SC2030,SC2031
#   The subshell is the point, as in run_guard: the stubbed PATH must reach the
#   guard and nothing else, and losing the change on the way out is the intent.
( export PATH="$STUB_DIR/bin:$PATH"
  # Exported here, not inherited. These blocks passed only because run_guard
  # had exported STUB_DIR earlier in the file; run either one alone, or under a
  # name filter, and every stub resolved it to the empty string and wrote to
  # paths like /calls.
  export STUB_DIR
  DEPLOY_REPO="$REPO" COMPOSE_FILE="$REPO/compose.yml" COMPOSE_PROJECT_NAME=p \
  SITE_URL="http://stand.invalid" MANIFEST_DIR="$SANDBOX/manifests" \
  DEPLOY_LOCK_DIR="$SANDBOX/lockdir" HEALTH_ATTEMPTS=3 HEALTH_DELAY=0 \
    bash "$SCRIPT" ) > "$SANDBOX/out" 2>&1 &
GUARD=$!
sleep 3
kill -TERM "$GUARD" 2>/dev/null
wait "$GUARD" 2>/dev/null
RC=$?
check "it reports the interruption" \
    "$(grep -qc 'interrupted by SIGTERM' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "and rolled back rather than leaving it running" \
    "$(grep -qc 'up -d --no-build' "$STUB_DIR/calls" && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== a run killed outright leaves its journal behind =="
# SIGKILL cannot be trapped, which is the case the journal exists for: what is
# running afterwards is whatever the interrupted run left, and the next run has
# to be able to see that.
new_sandbox
with_previous_deployment
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
touch "$STUB_DIR/slow_up"
# shellcheck disable=SC2030,SC2031
#   The subshell is the point, as in run_guard: the stubbed PATH must reach the
#   guard and nothing else, and losing the change on the way out is the intent.
( export PATH="$STUB_DIR/bin:$PATH"
  # Exported here, not inherited. These blocks passed only because run_guard
  # had exported STUB_DIR earlier in the file; run either one alone, or under a
  # name filter, and every stub resolved it to the empty string and wrote to
  # paths like /calls.
  export STUB_DIR
  DEPLOY_REPO="$REPO" COMPOSE_FILE="$REPO/compose.yml" COMPOSE_PROJECT_NAME=p \
  SITE_URL="http://stand.invalid" MANIFEST_DIR="$SANDBOX/manifests" \
  DEPLOY_LOCK_DIR="$SANDBOX/lockdir" HEALTH_ATTEMPTS=3 HEALTH_DELAY=0 \
    bash "$SCRIPT" ) > "$SANDBOX/out" 2>&1 &
GUARD=$!
sleep 3
kill -9 "$GUARD" 2>/dev/null
wait "$GUARD" 2>/dev/null
sleep 1
check "the journal survives the kill" \
    "$( [ -f "$SANDBOX/manifests/in-progress" ] && echo present || echo missing )" "present"
check "and records the phase it reached" \
    "$(grep -qc 'state          mutating' "$SANDBOX/manifests/in-progress" 2>/dev/null && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"


echo "== a post-mutation command failing outside fail() still rolls back =="
# Found by running the rollback against a real Docker daemon, not by these
# stubs: `UPSTREAM="$(... | grep server)"` returns non-zero when the pattern is
# absent, and `set -e` ended the script on the spot -- past `up`, before any
# verdict. No REFUSED was printed, the journal stayed at `mutating`, the new
# version kept serving, and the exit code was 1: the code that means "the
# previous state is running again".
#
# Eighteen commands after the mutation can fail that way. The exit trap covers
# all of them, including the ones nobody has thought of.
new_sandbox
with_previous_deployment
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
touch "$STUB_DIR/upstream_cmd_fails"
run_guard
check "it says what happened"  \
    "$(grep -q 'unexpected failure' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "and rolled back rather than leaving the new version serving" \
    "$(grep -q 'up -d --no-build' "$STUB_DIR/calls" && echo yes || echo no)" "yes"
# Cleared, not left. A verified rollback puts the previous state back, so
# there is nothing for an operator to reconcile -- and a journal left behind
# would refuse the next deployment, making exit 1 and exit 76 the same thing in
# practice.
check "and leaves no journal to block the next run" \
    "$( [ -f "$SANDBOX/manifests/in-progress" ] && echo left || echo cleared )" "cleared"
rm -rf "$SANDBOX"


echo "== a rollback that cannot check out records why =="
# The manifest can name a commit this checkout no longer has -- a force-push, a
# pruned branch, a repository restored from elsewhere. Every other terminal path
# writes its outcome; this one left the journal reading `mutating`, which names
# the wrong phase and hides that the checkout is what broke.
new_sandbox
mkdir -p "$SANDBOX/manifests"
printf 'commit %s\n' "0000000000000000000000000000000000000000" > "$SANDBOX/manifests/prev.txt"
ln -sf prev.txt "$SANDBOX/manifests/latest"
echo "nginx" > "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
touch "$STUB_DIR/nginx_t_fails"
run_guard
check "the rollback did not complete" "$RC" "76"
check "and it names the checkout" \
    "$(grep -q 'cannot check out' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "the journal says rollback-failed, not mutating" \
    "$(grep -q '^state          rollback-failed' "$SANDBOX/manifests/in-progress" 2>/dev/null && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

echo "== renewal: present, absent and undetermined are three states =="
# Undetermined is covered above, by the default stub that exits 1. These two
# are the states that stub can never produce, and without them "undetermined"
# could be the only branch this suite ever reaches -- which is how the previous
# assertion looked correct while the other two were untested.

new_sandbox
cat > "$STUB_DIR/bin/systemctl" <<'STUB'
#!/usr/bin/env bash
cat <<'OUT'
NEXT                        LEFT  LAST                        PASSED UNIT          ACTIVATES
Wed 2026-08-05 21:12:48 UTC 5h    Wed 2026-08-05 05:05:04 UTC 10h    certbot.timer certbot.service
OUT
STUB
chmod +x "$STUB_DIR/bin/systemctl"
run_guard
check "a systemd timer is reported present" \
    "$(grep -q 'renewal mechanism is present: (systemd timer)' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "and no absence is claimed" \
    "$(grep -qi 'no certbot timer and no cron' "$SANDBOX/out" && echo yes || echo no)" "no"
rm -rf "$SANDBOX"

new_sandbox
cat > "$STUB_DIR/bin/systemctl" <<'STUB'
#!/usr/bin/env bash
echo "0 timers listed."
STUB
chmod +x "$STUB_DIR/bin/systemctl"
run_guard
# systemctl answered and certbot is genuinely not there. This is the only case
# in which claiming absence is supported by the evidence.
check "a successful lookup finding nothing warns of real absence" \
    "$(grep -qi 'no certbot timer and no cron' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "and it is not reported as undetermined" \
    "$(grep -qi 'could not query systemd' "$SANDBOX/out" && echo yes || echo no)" "no"
rm -rf "$SANDBOX"

echo "== migrations are one step, run once, before anything is recreated =="
# #125. The application entrypoint used to run `alembic upgrade head`, and the
# backend and the scheduler share that file, so `up -d` started two migrators
# against one database at the same moment. These cases are about the three
# properties the replacement has: once, first, and fatal.

new_sandbox
run_guard
check "the migration step ran" \
    "$(cat "$STUB_DIR/migrate_calls" 2>/dev/null || echo 0)" "1"
# Exactly once, not at least once. Two migrators is the defect.
check "and no more than once" \
    "$(grep -c 'run --rm --no-deps migrate' "$STUB_DIR/calls")" "1"
rm -rf "$SANDBOX"

new_sandbox
run_guard
# Line numbers in the recorded call log, so this is about order in time and not
# about the order the two greps happen to appear in.
MIG_LINE="$(grep -n 'run --rm --no-deps migrate' "$STUB_DIR/calls" | head -1 | cut -d: -f1)"
UP_LINE="$(grep -n 'up -d --no-build --remove-orphans' "$STUB_DIR/calls" | head -1 | cut -d: -f1)"
check "the migration is recorded before the containers are recreated" \
    "$([ -n "$MIG_LINE" ] && [ -n "$UP_LINE" ] && [ "$MIG_LINE" -lt "$UP_LINE" ] && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

new_sandbox
touch "$STUB_DIR/migrate_fails"
run_guard
refused_because "a failed migration refuses the deployment" "alembic upgrade head failed"
# The point of moving it out of the entrypoint: a failure stops the deploy
# instead of restarting a container until it works.
check "and nothing was recreated" \
    "$(grep -c 'up -d --no-build --remove-orphans' "$STUB_DIR/calls")" "0"
# A manifest, not "any file". `in-progress` lives in the same directory and is
# supposed to be there: it is the journal saying the run was interrupted. The
# first version of this counted it and reported a manifest that was never
# written.
check "and no manifest was written" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
check "but the journal records the interruption" \
    "$([ -f "$SANDBOX/manifests/in-progress" ] && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

new_sandbox
touch "$STUB_DIR/postgres_fails"
run_guard
refused_because "a database that will not start refuses the deployment" "postgres would not start"
check "and the migration was not attempted against nothing" \
    "$(cat "$STUB_DIR/migrate_calls" 2>/dev/null || echo 0)" "0"
rm -rf "$SANDBOX"

echo "== the migration and the application are one image, not two builds =="
# `migrate`, `backend` and `scheduler` share one tag and only `backend` builds
# it, so "migrated from the image being deployed" is a fact rather than a
# resemblance. These cases are about the script honouring that.

new_sandbox
run_guard
check "the image is built once, explicitly" \
    "$(grep -c 'build backend' "$STUB_DIR/calls")" "1"
# --no-build on the start: a rebuild there could produce a different image than
# the one just migrated from, which is the gap the shared tag closes.
check "and the containers start without rebuilding" \
    "$(grep -c 'up -d --no-build --remove-orphans' "$STUB_DIR/calls")" "1"
BUILD_LINE="$(grep -n 'build backend' "$STUB_DIR/calls" | head -1 | cut -d: -f1)"
MIG_LINE="$(grep -n 'run --rm --no-deps migrate' "$STUB_DIR/calls" | head -1 | cut -d: -f1)"
check "and the build comes before the migration" \
    "$([ -n "$BUILD_LINE" ] && [ -n "$MIG_LINE" ] && [ "$BUILD_LINE" -lt "$MIG_LINE" ] && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

new_sandbox
touch "$STUB_DIR/build_fails"
run_guard
refused_because "an image that will not build refuses the deployment" "would not build"
check "and no migration was attempted" \
    "$(cat "$STUB_DIR/migrate_calls" 2>/dev/null || echo 0)" "0"
rm -rf "$SANDBOX"

new_sandbox
# The tag resolves to one id; the running container reports another. Whatever
# the cause -- something rebuilt behind the script, the tag moved -- the
# migration belongs to a build nobody is serving.
echo "sha256:a-different-image" > "$STUB_DIR/app_image_id"
run_guard
refused_because "a container running a different image than the migration" "not the sha256:a-different-image the migration ran from"
rm -rf "$SANDBOX"

echo "== the application containers do not migrate =="
# A static read of the shipped entrypoint, because the property is "this file
# contains no DDL command" and running it would need a database.
ENTRYPOINT="$(cd "$(dirname "$0")/.." && pwd)/entrypoint.sh"
check "entrypoint.sh exists" \
    "$([ -f "$ENTRYPOINT" ] && echo yes || echo no)" "yes"
check "and does not run alembic upgrade" \
    "$(grep -cE '^[^#]*alembic[[:space:]]+upgrade' "$ENTRYPOINT")" "0"
check "and does verify the schema version instead" \
    "$(grep -cE '^[^#]*verify_schema_head' "$ENTRYPOINT")" "1"

echo "== a run that finished everything and died before recording it =="
# #160. The deploying run built, migrated, recreated and verified, then its SSH
# session was killed at a ten-minute timeout. Production was correct and
# unrecorded: journal present, no manifest. --finalize proves the end state and
# writes what is missing; it does not deploy anything again.

# The journal a deploy leaves behind at the moment of interruption.
write_journal() {
    local target="$1" mode="${2:-deploy}"
    mkdir -p "$SANDBOX/manifests"
    {
        echo "run            20260814T032941Z-1038993"
        echo "state          mutating"
        echo "started        2026-08-14T03:29:41Z"
        echo "updated        2026-08-14T03:29:41Z"
        echo "mode           $mode"
        echo "target         $target"
        echo "previous       $FIRST"
        echo "pre_containers cid-old-1 cid-old-2"
        echo "images"
        echo "  backend	sha256:old-backend"
        echo "  nginx	sha256:old-nginx"
    } > "$SANDBOX/manifests/in-progress"
}

new_sandbox
write_journal "$SECOND"
run_guard --finalize
check "it finalizes the interrupted run" "$RC" "0"
check "and writes exactly one manifest" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "1"
check "and marks it as a reconciliation, not a deployment" \
    "$(grep -qc '^finalized      yes' "$SANDBOX/manifests"/*.txt && echo yes || echo no)" "yes"
check "and removes the journal" \
    "$([ -f "$SANDBOX/manifests/in-progress" ] && echo present || echo gone)" "gone"
# The whole point: nothing was deployed a second time.
check "it did not build" "$(grep -c 'build backend' "$STUB_DIR/calls")" "0"
check "it did not migrate" "$(grep -c 'run --rm --no-deps migrate' "$STUB_DIR/calls")" "0"
check "it did not recreate the services" \
    "$(grep -c 'up -d --no-build --remove-orphans' "$STUB_DIR/calls")" "0"
# The value, not the presence of the field. There is no earlier manifest in this
# sandbox, so a run reading the commit from the manifest chain -- as a deploy
# correctly does -- writes `none-recorded` here and still has the line.
check "and it recorded what the interrupted run replaced, not itself" \
    "$(awk '/^previous_commit /{print $2}' "$SANDBOX/manifests"/*.txt)" "$FIRST"
rm -rf "$SANDBOX"

new_sandbox
# The two sources of "what was running" disagree. The journal was written before
# the interrupted run touched anything; the manifest was published by whatever
# ran last. Nothing here can tell which describes what is serving, so the record
# is not written from either.
write_journal "$SECOND"
mkdir -p "$SANDBOX/manifests"
# A literal rather than a commit of this sandbox: the only property needed is
# "not the one the journal names", and borrowing a variable set inside an
# earlier case would tie this to the order those cases happen to run in.
printf 'commit %s\n' "0000000000000000000000000000000000000042" \
    > "$SANDBOX/manifests/prev.txt"
ln -sf prev.txt "$SANDBOX/manifests/latest"
run_guard --finalize
refused_because "a journal and a manifest that disagree about what was replaced" "they disagree about what was running"
check "and no manifest was written" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' ! -name 'prev.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
check "and the journal is left for a human" \
    "$([ -f "$SANDBOX/manifests/in-progress" ] && echo present || echo gone)" "present"
rm -rf "$SANDBOX"

new_sandbox
run_guard --finalize
refused_because "finalize with no journal refuses" "nothing to finalize"
check "and writes no manifest" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
rm -rf "$SANDBOX"

echo "== finalize refuses whatever it cannot prove =="

new_sandbox
# Interrupted before the migration: the checkout is still the old commit, so
# what is running is not what the journal targeted.
write_journal "$SECOND"
git -C "$REPO" checkout --quiet --detach "$FIRST"
run_guard --finalize
refused_because "a checkout that has moved" "cannot be reconciled from a tree that has moved"
check "and no manifest was written" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
check "and the journal is left for a human" \
    "$([ -f "$SANDBOX/manifests/in-progress" ] && echo present || echo gone)" "present"
rm -rf "$SANDBOX"

new_sandbox
# Interrupted after the migration and before the recreate: the containers are
# still on the previous image, so they do not match the tag.
write_journal "$SECOND"
echo "sha256:a-different-image" > "$STUB_DIR/app_image_id"
run_guard --finalize
refused_because "containers not running the deployed image" "not the sha256:a-different-image the migration ran from"
check "and no manifest was written" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
rm -rf "$SANDBOX"

new_sandbox
# Interrupted after the recreate and before verification: finalize runs the
# verification now, and a service that is missing is still a refusal.
write_journal "$SECOND"
echo "nginx" > "$STUB_DIR/services"
echo "backend" >> "$STUB_DIR/services"
echo "p-nginx-1|nginx|0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp" > "$STUB_DIR/running"
run_guard --finalize
refused_because "a declared service that is not running" "is not running"
rm -rf "$SANDBOX"

new_sandbox
write_journal "$SECOND"
echo 500 > "$STUB_DIR/http_code"
run_guard --finalize
check "an unhealthy site refuses" "$([ "$RC" = 0 ] && echo no || echo yes)" "yes"
check "and writes no manifest" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
rm -rf "$SANDBOX"

new_sandbox
# A rollback that was interrupted is not something this path may finish: the
# checkout is detached and the record it would write is a different shape.
write_journal "$SECOND" rollback
run_guard --finalize
refused_because "an interrupted rollback is not finalized" "only a deploy can be finalized"
rm -rf "$SANDBOX"

echo "== the journal outlives the manifest, not the other way round =="
# It used to be cleared *before* the manifest was written, so an interruption
# between the two left neither: no record of what is deployed, and no sign that
# anything was unfinished.
new_sandbox
run_guard
check "a normal deploy still ends with a manifest and no journal" \
    "$([ -f "$SANDBOX/manifests/in-progress" ] && echo present || echo gone)" "gone"
MANIFEST_COUNT="$(find "$SANDBOX/manifests" -type f -name '*.txt' | wc -l | tr -d ' ')"
check "and exactly one manifest" "$MANIFEST_COUNT" "1"
# Ordering, read from the script. The behavioural half cannot observe it --
# both orders leave the same end state on a run that is not interrupted -- and
# the order is the whole fix.
CLEAR_LINE="$(grep -n '^journal_clear$' "$SCRIPT" | tail -1 | cut -d: -f1)"
LATEST_LINE="$(grep -n 'mv -Tf .*LATEST' "$SCRIPT" | head -1 | cut -d: -f1)"
if [ -n "$CLEAR_LINE" ] && [ -n "$LATEST_LINE" ] && [ "$CLEAR_LINE" -gt "$LATEST_LINE" ]; then
    ORDER=after
else
    ORDER=before
fi
check "and the journal is cleared after the manifest is published, not before" \
    "$ORDER" "after"
rm -rf "$SANDBOX"

echo "== finalize proves the deployment happened, not merely that images match =="
# A tag is a pointer and `docker inspect` describes what is running now. Neither
# says *when* it started, so containers predating the interrupted run -- on the
# same tag, because it had not moved yet -- satisfy an image comparison. The age
# is the discriminator.

new_sandbox
write_journal "$SECOND"
echo "2026-08-14T03:00:00.000000000Z" > "$STUB_DIR/container_created"
run_guard --finalize
refused_because "containers older than the interrupted run" "before the interrupted run started"
check "and no manifest was written" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
check "and the journal is left in place" \
    "$([ -f "$SANDBOX/manifests/in-progress" ] && echo present || echo gone)" "present"
rm -rf "$SANDBOX"

new_sandbox
write_journal "$SECOND"
# A journal with no run id was not written by this script; reconciling it would
# publish a manifest whose provenance reads `unknown`.
grep -v '^run ' "$SANDBOX/manifests/in-progress" > "$SANDBOX/j.tmp"
mv "$SANDBOX/j.tmp" "$SANDBOX/manifests/in-progress"
run_guard --finalize
refused_because "a journal with no run id" "no run id"
check "and no manifest was written" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
rm -rf "$SANDBOX"

new_sandbox
write_journal "$SECOND"
touch "$STUB_DIR/schema_behind"
run_guard --finalize
refused_because "a schema the running code does not accept" "cannot be confirmed"
rm -rf "$SANDBOX"

new_sandbox
# `rollback-failed` describes a run that refused and tried to undo itself. What
# is running then is whatever the rollback managed, which is the one state
# nobody may write a manifest for.
write_journal "$SECOND"
sed -i.bak 's/^state          mutating/state          rollback-failed/' "$SANDBOX/manifests/in-progress"
run_guard --finalize
refused_because "a journal that is not in state mutating" "not 'mutating'"
rm -rf "$SANDBOX"

new_sandbox
# The retry shape: finalize twice. The second must not write a second manifest.
write_journal "$SECOND"
run_guard --finalize
check "the first finalize succeeds" "$RC" "0"
FIRST_COUNT="$(find "$SANDBOX/manifests" -type f -name '*.txt' | wc -l | tr -d ' ')"
run_guard --finalize
check "the second says it is already done" "$RC" "0"
check "and says so rather than sounding like a fault" \
    "$(grep -qc 'already finalized' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "and writes no second manifest" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' | wc -l | tr -d ' ')" "$FIRST_COUNT"
rm -rf "$SANDBOX"
echo "== the deployed code is asked how it behaves, not only whether it answers =="
# The class this covers is "CI and the image build different applications", and
# no test suite can see it: on 2026-08-14 a catch-all registered only where the
# SPA has been built made every absent path answer 404 in CI and 405 on
# production, on the same commit, with every health check green (#177).
#
# Each case below reproduces that shape and asserts the deployment stops --
# without rolling back, and without recording itself.

new_sandbox
run_guard
check "a correct deployment passes the behaviour probe" "$RC" "0"
check "and says so" \
    "$(grep -qc 'behaviour matches inside the container and through nginx' "$SANDBOX/out" && echo yes || echo no)" "yes"
check "and records itself" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' | wc -l | tr -d ' ')" "1"
rm -rf "$SANDBOX"

new_sandbox
# The #177 defect exactly: an absent path claims the verb is the problem.
printf '%s\n' '{"absent_path":"/api/v1/__deploy_probe_absent__","absent":405,"get_only_path":"/api/v1/ab/stats","get_only":405,"post_path":"/api/v1/auth/login","post":422}' \
    > "$STUB_DIR/probe_json"
run_guard
refused_because "an absent path answering 405 stops the deployment" "expected 404"
check "and nothing was rolled back" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo rolled || echo untouched)" "untouched"
check "and no manifest was written" \
    "$(find "$SANDBOX/manifests" -type f -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')" "0"
check "and the journal is left for a human" \
    "$([ -f "$SANDBOX/manifests/in-progress" ] && echo present || echo gone)" "present"
check "and both recoveries are named" \
    "$(grep -qc -- '--rollback' "$SANDBOX/out" && grep -qc -- '--finalize' "$SANDBOX/out" && echo yes || echo no)" "yes"
rm -rf "$SANDBOX"

new_sandbox
# The mirror defect: answering 404 everywhere denies that real endpoints exist.
printf '%s\n' '{"absent_path":"/api/v1/__deploy_probe_absent__","absent":404,"get_only_path":"/api/v1/ab/stats","get_only":404,"post_path":"/api/v1/auth/login","post":422}' \
    > "$STUB_DIR/probe_json"
run_guard
refused_because "a real GET-only route answering 404 stops the deployment" "expected 405 for a GET-only route"
check "and nothing was rolled back" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo rolled || echo untouched)" "untouched"
rm -rf "$SANDBOX"

new_sandbox
# A published POST route denying its own verb.
printf '%s\n' '{"absent_path":"/api/v1/__deploy_probe_absent__","absent":404,"get_only_path":"/api/v1/ab/stats","get_only":405,"post_path":"/api/v1/auth/login","post":405}' \
    > "$STUB_DIR/probe_json"
run_guard
refused_because "a published POST route denying POST stops the deployment" "must not deny it"
rm -rf "$SANDBOX"

new_sandbox
# nginx and the container disagreeing is itself the finding: a layer between the
# caller and the application is answering, and no test sees that layer.
echo "405" > "$STUB_DIR/probe_ext_absent"
run_guard
refused_because "nginx and the container disagreeing stops the deployment" "disagree about the same request"
check "and nothing was rolled back" \
    "$([ -f "$STUB_DIR/rolled_back" ] && echo rolled || echo untouched)" "untouched"
rm -rf "$SANDBOX"

new_sandbox
# A route table that cannot be read is not a pass. The check must refuse rather
# than skip: "could not look" and "looked and found nothing wrong" are different.
printf '%s\n' '{"error":"URLError"}' > "$STUB_DIR/probe_json"
run_guard
refused_because "an unreadable route table stops the deployment" "would not serve its own route table"
rm -rf "$SANDBOX"

new_sandbox
# And the probe choosing nothing is the same: an application with no GET-only
# or POST route to ask about leaves this check reporting on nothing.
printf '%s\n' '{"absent_path":"/api/v1/__deploy_probe_absent__","absent":404,"get_only_path":null,"get_only":null,"post_path":null,"post":null}' \
    > "$STUB_DIR/probe_json"
run_guard
refused_because "no probeable routes stops the deployment" "cannot report on an application it could not read"
rm -rf "$SANDBOX"

echo "== a failing case fails the run, wherever it is written =="
# Not a case about deployment. The tally used to be two statements at the bottom
# of this file, and the exit status of a script is that of its last command --
# so a section appended below them counted its failures into $FAIL and left the
# status of whatever it ended with. One real FAIL was printed and the job went
# green.
#
# Asserted by running this file's own reporting in a subshell rather than by
# reading it, because "the trap is registered" and "the status is non-zero" are
# different claims and only the second one matters.
_TALLY_PROBE="$(mktemp)"
cat > "$_TALLY_PROBE" <<'PROBE'
PASS=0; FAIL=0
_report_tally() {
    local rc=$?
    echo "  passed: $PASS   failed: $FAIL"
    if [ "$FAIL" -ne 0 ]; then exit 1; fi
    exit "$rc"
}
trap _report_tally EXIT
FAIL=1
true            # a last command that succeeds, as `rm -rf` did
PROBE
bash "$_TALLY_PROBE" >/dev/null 2>&1
check "a run ending on a successful command still fails when a case failed" "$?" "1"
cat > "$_TALLY_PROBE" <<'PROBE'
PASS=0; FAIL=0
_report_tally() {
    local rc=$?
    if [ "$FAIL" -ne 0 ]; then exit 1; fi
    exit "$rc"
}
trap _report_tally EXIT
true
PROBE
bash "$_TALLY_PROBE" >/dev/null 2>&1
check "and a run with no failures still succeeds" "$?" "0"
rm -f "$_TALLY_PROBE"
