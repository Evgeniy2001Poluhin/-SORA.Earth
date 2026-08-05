#!/usr/bin/env bash
# The rollback, against a real Docker daemon.
#
# tests/test_deploy_production.sh drives the guard with stubs: it proves the
# logic and cannot prove that `docker compose -f override up --no-build`
# actually starts a recorded image on a real daemon. This does, and it found a
# defect none of those 121 cases could see -- `UPSTREAM="$(... | grep server)"`
# returns non-zero when the pattern is absent, and `set -e` ended the run there,
# past `up`, with no REFUSED, the journal stuck at `mutating`, the new version
# still serving, and exit code 1: the code meaning "the previous state is back".
#
# Run inside an isolated docker-in-docker, never against a host that has
# anything else on it -- it publishes 80 and 443 and removes containers by
# compose project:
#
#   docker run -d --privileged --name sora-dind -e DOCKER_TLS_CERTDIR= docker:27-dind
#   docker exec sora-dind apk add --no-cache git bash curl util-linux python3
#   docker exec sora-dind mkdir -p /guard
#   docker cp scripts sora-dind:/guard/
#   docker cp tools/rollback_exercise.sh sora-dind:/exercise.sh
#   docker exec sora-dind bash /exercise.sh
#
# Not in CI: it needs a privileged daemon, and a privileged container in CI is a
# larger decision than this test is worth. It is a maintenance exercise, run
# before a deployment that changes the rollback path.
#
# One scenario is deliberately absent. "The recorded image is gone" cannot be
# staged here: Docker keeps an image alive while a container references it, so
# it cannot be removed out from under a running deployment. That path is covered
# by the stubbed suite (`no_build_fails` -> exit 76, ROLLBACK INCOMPLETE), and
# the limit is recorded rather than the condition faked.
set -uo pipefail
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok    %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; }
chk() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 — ожидалось [$3], получено [$2]"; fi; }

W=/exercise; rm -rf "$W"; mkdir -p "$W"; cd "$W" || exit 1
# A previous run's leftovers would otherwise decide the outcome: the journal
# refuses the next deployment, and the containers are whatever it left. Each
# run of this exercise must start from nothing.
docker compose -p ex down --remove-orphans >/dev/null 2>&1 || true
# shellcheck disable=SC2046
#   Deliberately unsplit: a list of container ids.
docker rm -f $(docker ps -aq --filter "label=com.docker.compose.project=ex") >/dev/null 2>&1 || true
rm -rf /var/lib/sora/deployments /run/sora
git init -q --bare origin.git
git clone -q origin.git repo 2>/dev/null
cd repo || exit 1; git config user.email e@e; git config user.name e

mkdir -p nginx
cat > nginx/nginx.conf <<'CONF'
events {}
http {
  server { listen 80; location / { return 200 "A\n"; } }
}
CONF
cat > Dockerfile <<'DOCKER'
FROM nginx:alpine
COPY nginx/nginx.conf /etc/nginx/nginx.conf
RUN echo VERSION_A > /version
DOCKER
cat > compose.yml <<'COMPOSE'
services:
  nginx:
    build: .
    ports:
      - "80:80"
      - "443:443"
COMPOSE
git add -A && git commit -qm "version A" && git branch -M main && git push -q origin main
COMMIT_A="$(git rev-parse HEAD)"

echo "== версия A собрана и запущена =="
docker compose -p ex -f compose.yml up -d --build >/dev/null 2>&1
CID_A="$(docker compose -p ex ps -q nginx)"
IMG_A="$(docker inspect -f '{{.Image}}' "$CID_A")"
chk "A работает" "$([ -n "$CID_A" ] && echo yes || echo no)" "yes"
echo "     image A = ${IMG_A:0:24}"
chk "в образе A метка A" "$(docker exec "$CID_A" cat /version 2>/dev/null | tr -d '\r\n')" "VERSION_A"

# The manifest the guard rolls back to.
mkdir -p /var/lib/sora/deployments
printf 'commit %s\n' "$COMMIT_A" > /var/lib/sora/deployments/a.txt
ln -sf a.txt /var/lib/sora/deployments/latest

echo
echo "== версия B, и отказ после её запуска =="
sed -i 's/VERSION_A/VERSION_B/' Dockerfile
sed -i 's|return 200 "A\\n"|return 200 "B\\n"|' nginx/nginx.conf
git add -A && git commit -qm "version B" >/dev/null && git push -q origin main

# Break the config-hash check: the container will hold what the image baked in,
# the repo says something else. That is a post-start refusal, which is the
# path under exercise.
echo "# drift" >> nginx/nginx.conf
git add -A && git commit -qm "drift" >/dev/null && git push -q origin main

export DEPLOY_REPO="$W/repo"
export COMPOSE_FILE="$W/repo/compose.yml"
export COMPOSE_PROJECT_NAME=ex
export MANIFEST_DIR=/var/lib/sora/deployments
export DEPLOY_LOCK_DIR=/run/sora
export SITE_URL="http://127.0.0.1"
export HEALTH_ATTEMPTS=1
export HEALTH_DELAY=0
mkdir -p /run/sora

bash /guard/scripts/deploy_production.sh > "$W/out1" 2>&1
RC1=$?
echo "     гард завершился с кодом $RC1"
grep -E "REFUSED|restored|ROLLBACK" "$W/out1" | head -4 | sed 's/^/     /'

CID_AFTER="$(docker compose -p ex ps -q nginx)"
IMG_AFTER="$(docker inspect -f '{{.Image}}' "$CID_AFTER" 2>/dev/null)"
echo "     image после отката = ${IMG_AFTER:0:24}"

echo
echo "== СЦЕНАРИЙ 1: обычный отказ после запуска =="
chk "код 1 — прежнее состояние вернулось" "$RC1" "1"
chk "образ совпадает со снимком A" "$IMG_AFTER" "$IMG_A"
chk "в контейнере снова метка A" "$(docker exec "$CID_AFTER" cat /version 2>/dev/null | tr -d '\r\n')" "VERSION_A"
chk "восстановлено без сборки" "$(grep -q 'restored the images that were running' "$W/out1" && echo yes || echo no)" "yes"
chk "журнал закрыт как rolled-back" "$(grep -q '^state          rolled-back' /var/lib/sora/deployments/in-progress 2>/dev/null && echo yes || echo no)" "yes"
chk "порт 80 обслуживает A" "$(curl -s --max-time 5 http://127.0.0.1/ | tr -d '\r\n')" "A"

# ---------------------------------------------------------------------------
# Helpers for the remaining scenarios: rebuild the stand from scratch each time,
# so no scenario inherits another's leftovers.
reset_stand() {
    docker compose -p ex -f "$W/repo/compose.yml" down --remove-orphans >/dev/null 2>&1 || true
    rm -rf /var/lib/sora/deployments /run/sora
    mkdir -p /var/lib/sora/deployments /run/sora
    cd "$W/repo" || return 1
    git checkout -q "$COMMIT_A" -- . 2>/dev/null
    git checkout -q main 2>/dev/null
    git reset -q --hard "$COMMIT_A"
    git push -qf origin main
    docker compose -p ex -f compose.yml up -d --build >/dev/null 2>&1
    CID_A="$(docker compose -p ex ps -q nginx)"
    IMG_A="$(docker inspect -f '{{.Image}}' "$CID_A")"
    printf 'commit %s\n' "$COMMIT_A" > /var/lib/sora/deployments/a.txt
    ln -sf a.txt /var/lib/sora/deployments/latest
}

# Move to a version that takes time to build, so a signal can land mid-mutation.
stage_slow_b() {
    cd "$W/repo" || return 1
    sed -i 's/VERSION_A/VERSION_B/' Dockerfile
    # Unique each time, so the layer cannot come from cache -- a cached
    # `RUN sleep 12` costs nothing, and the first attempt at this scenario
    # therefore killed a guard that had already finished and rolled back.
    sed -i '/^RUN sleep/d' Dockerfile
    echo "RUN sleep 20 && echo $(date +%s%N) > /stamp" >> Dockerfile
    git add -A && git commit -qm "slow B" >/dev/null && git push -qf origin main
}

# Wait for the mutation to actually begin, rather than sleeping a fixed time.
# A fixed wait is a guess about build speed: too short and the signal arrives
# before `up`, too long and the guard has already failed and rolled back on its
# own -- both of which happened here, and both looked like the signal handling
# not working.
await_mutating() {
    for _ in $(seq 1 20); do
        if grep -q '^state          mutating' /var/lib/sora/deployments/in-progress 2>/dev/null; then
            return 0
        fi
        sleep 1
    done
    return 1
}

run_guard_bg() {
    # No subshell. `( ... ) &` makes $! the subshell's pid, and a signal sent
    # there does not reach the guard inside it -- the first attempt at the
    # signal scenario killed the wrapper while the guard carried on, and the
    # rollback that did happen came from the exit trap, not from the signal.
    bash /guard/scripts/deploy_production.sh > "$W/out" 2>&1 &
    GUARD_PID=$!
}

echo
echo "== СЦЕНАРИЙ 2: SIGTERM во время мутации =="
reset_stand
stage_slow_b
run_guard_bg
chk "мутация началась" "$(await_mutating && echo yes || echo no)" "yes"
kill -TERM "$GUARD_PID" 2>/dev/null
wait "$GUARD_PID" 2>/dev/null
CID2="$(docker compose -p ex ps -q nginx)"
IMG2="$(docker inspect -f '{{.Image}}' "$CID2" 2>/dev/null)"
chk "сигнал назван в выводе" \
    "$(grep -q 'interrupted by SIGTERM' "$W/out" && echo yes || echo no)" "yes"
chk "образ A восстановлен" "$IMG2" "$IMG_A"
chk "журнал закрыт как rolled-back" \
    "$(grep -q '^state          rolled-back' /var/lib/sora/deployments/in-progress 2>/dev/null && echo yes || echo no)" "yes"

echo
echo "== СЦЕНАРИЙ 3: kill -9 оставляет журнал, следующий запуск отказывается =="
reset_stand
stage_slow_b
run_guard_bg
chk "мутация началась" "$(await_mutating && echo yes || echo no)" "yes"
kill -9 "$GUARD_PID" 2>/dev/null
wait "$GUARD_PID" 2>/dev/null
sleep 1
chk "журнал пережил kill -9" \
    "$( [ -f /var/lib/sora/deployments/in-progress ] && echo present || echo missing )" "present"
chk "и хранит фазу mutating" \
    "$(grep -q '^state          mutating' /var/lib/sora/deployments/in-progress && echo yes || echo no)" "yes"
# A kill -9 leaves the build it started running, and that child still holds the
# lock -- so the very next run is refused for contention, not for the journal.
# Both refusals are correct and they arrive in that order; an operator sees the
# lock first and the journal once the orphan is gone.
bash /guard/scripts/deploy_production.sh > "$W/out3a" 2>&1
chk "сразу после kill -9 держит блокировка" \
    "$(grep -q 'another deployment holds' "$W/out3a" && echo yes || echo no)" "yes"

# Clear the orphaned build, as an operator would, then try again.
pkill -f "docker-compose|compose up" 2>/dev/null || true
docker ps -q --filter "label=com.docker.compose.project=ex" >/dev/null 2>&1
sleep 2
bash /guard/scripts/deploy_production.sh > "$W/out3" 2>&1
RC3=$?
chk "следующий запуск отказан" "$RC3" "1"
chk "и назвал действие оператора" \
    "$(grep -q 'remove /var/lib/sora/deployments/in-progress' "$W/out3" && echo yes || echo no)" "yes"

echo
echo "== СЦЕНАРИЙ 4: чужой контейнер не затрагивается откатом =="
# The "recorded image is gone" half of this cannot be staged here: Docker keeps
# an image alive while a container references it, so it cannot be removed out
# from under a running deployment. That path is covered by the stubbed suite
# (`no_build_fails` → exit 76, ROLLBACK INCOMPLETE), and this exercise records
# the limit rather than faking the condition.
#
# What *is* real here is the other half, and it is the one that can damage
# something: a rollback must not touch a container this deployment does not own.
reset_stand
docker run -d --name foreign-bystander alpine:3 sleep 300 >/dev/null 2>&1
stage_slow_b
bash /guard/scripts/deploy_production.sh > "$W/out4" 2>&1
RC4=$?
chk "откат произошёл" "$RC4" "1"
chk "чужой контейнер работает" \
    "$(docker inspect -f '{{.State.Running}}' foreign-bystander 2>/dev/null)" "true"
chk "и не упомянут в остановленных" \
    "$(grep -q 'foreign-bystander' "$W/out4" && echo mentioned || echo no)" "no"
docker rm -f foreign-bystander >/dev/null 2>&1

echo "== rendered override действительно несёт image id =="
reset_stand
OVR=/tmp/ovr.yml
printf 'services:\n  nginx:\n    image: "%s"\n' "$IMG_A" > "$OVR"
RENDERED="$(docker compose -p ex -f "$W/repo/compose.yml" -f "$OVR" config 2>/dev/null | grep -c "$IMG_A")"
chk "id виден в итоговой конфигурации" "$( [ "$RENDERED" -ge 1 ] && echo yes || echo no )" "yes"
rm -f "$OVR"

echo
echo "  итог: пройдено $PASS, провалено $FAIL"
[ "$FAIL" -eq 0 ]
