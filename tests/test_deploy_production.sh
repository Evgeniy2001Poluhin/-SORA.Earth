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
    echo "    server backend:8000;" > "$STUB_DIR/upstream_line"
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
        [ -f "$STUB_DIR/pre_cids" ] && cat "$STUB_DIR/pre_cids"
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
    *"upstream sora_backend"*)  cat "$STUB_DIR/upstream_line"; exit 0 ;;
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
    *)                     command date "$@" ;;
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
echo "    server app:8000;" > "$STUB_DIR/upstream_line"
run_guard
refused_because "an upstream that is not backend" "expected backend:8000"
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
check "it warns about certificate renewal" \
    "$(grep -qi 'no certbot timer' "$SANDBOX/out" && echo yes || echo no)" "yes"
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

echo
echo "  passed: $PASS   failed: $FAIL"
[ "$FAIL" -eq 0 ]
