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
case "$argv" in
    *"config --services"*)  cat "$STUB_DIR/services"; exit 0 ;;
    *"ps --format {{.Service}}"*)
        cut -d'|' -f2 "$STUB_DIR/running"; exit 0 ;;
    *'{{.Label "com.docker.compose.service"}} {{.Image}} {{.ID}}'*)
        awk -F'|' '{print $2" image-"$2" id-"$2}' "$STUB_DIR/running"; exit 0 ;;
    *'{{.Label "com.docker.compose.service"}}'*)
        cut -d'|' -f2 "$STUB_DIR/running"; exit 0 ;;
    *'{{.Names}}|{{.Ports}}'*)
        awk -F'|' '{print $1"|"$3}' "$STUB_DIR/running"; exit 0 ;;
    *'{{.Names}} {{.Ports}}'*)
        awk -F'|' '{print "  "$1" "$3}' "$STUB_DIR/running"; exit 0 ;;
    *"ps -q nginx"*)        echo "cid-nginx"; exit 0 ;;
    *"ps -q"*)              echo "cid-x"; exit 0 ;;
    *"sha256sum /etc/nginx/nginx.conf"*)
        echo "$(cat "$STUB_DIR/container_conf_sum")  /etc/nginx/nginx.conf"; exit 0 ;;
    *"nginx -t"*)
        [ -f "$STUB_DIR/nginx_t_fails" ] && exit 1
        exit 0 ;;
    *"upstream sora_backend"*)  cat "$STUB_DIR/upstream_line"; exit 0 ;;
    *"inspect"*"Destination"*)  cat "$STUB_DIR/cert_source"; exit 0 ;;
    *"inspect -f {{.Image}}"*)  echo "sha256:image"; exit 0 ;;
    *) exit 0 ;;
esac
STUB
    # One status per line, consumed in order; the last line repeats once the list
    # runs out. That is what tells a transient failure from a permanent one --
    # with a single fixed status the retry loop cannot be observed at all, and a
    # broken retry would pass.
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
      DEPLOY_LOCK="${LOCK_OVERRIDE:-$SANDBOX/deploy.lock}" \
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
for form in "127.0.0.1:9090->9090/tcp" "0.0.0.0:80->80/tcp" "0.0.0.0:443->443/tcp" "8000/tcp"; do
    new_sandbox
    echo "nginx" > "$STUB_DIR/services"
    echo "p-nginx-1|nginx|$form" > "$STUB_DIR/running"
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
    LOCK_OVERRIDE="$SANDBOX/lockdir/deploy.lock" run_guard
    check "mode $mode is accepted" \
        "$([ "$RC" = 0 ] && echo ok || echo "refused: $(grep -m1 REFUSED "$SANDBOX/out")")" "ok"
    rm -rf "$SANDBOX"
done
for mode in 770 757 777 720 702; do
    new_sandbox
    mkdir -p "$SANDBOX/lockdir"; chmod "$mode" "$SANDBOX/lockdir"
    LOCK_OVERRIDE="$SANDBOX/lockdir/deploy.lock" run_guard
    refused_because "mode $mode is refused" "writable by group or others"
    rm -rf "$SANDBOX"
done

new_sandbox
# -d follows symlinks, so a link satisfies "is a directory" while the lock ends
# up somewhere else entirely -- possibly somewhere the attacker chose.
mkdir -p "$SANDBOX/real"; chmod 0750 "$SANDBOX/real"
ln -s "$SANDBOX/real" "$SANDBOX/linkdir"
LOCK_DIR_OVERRIDE="$SANDBOX/linkdir" LOCK_OVERRIDE="$SANDBOX/linkdir/deploy.lock" run_guard
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
LOCK_DIR_OVERRIDE="$OTHER_DIR" LOCK_OVERRIDE="$SANDBOX/unused.lock" run_guard
refused_because "a lock directory owned by someone else" "not by the deploying user"
rm -rf "$SANDBOX"

echo "== deployments are serialised, and manifests are never overwritten =="
new_sandbox
# Contention must refuse, not skip. A backup that skips has lost nothing; a
# deployment that skips has silently not happened while its operator believes
# it did.
(
    exec 9>"$SANDBOX/deploy.lock"
    flock -n 9
    run_guard
    refused_because "a second deployment while one holds the lock" "another deployment holds"
)
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

echo
echo "  passed: $PASS   failed: $FAIL"
[ "$FAIL" -eq 0 ]
