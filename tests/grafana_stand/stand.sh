#!/usr/bin/env bash
# Disposable local Grafana stand. No production or compose-stack operations.
# Run `init` first; see SAFETY.md for the intentionally narrower CLI.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
GUARD="$REPO/tests/grafana_stand/path_guard.py"
LABEL="online.sora.gstand.run"
die() { echo "stand refused: $*" >&2; exit 2; }

# A locally named context may still target a remote daemon. Inspect context
# metadata only, and refuse before any daemon operation unless it is a Unix socket.
ENGINE_CHECKED=0
docker() {
  local endpoint
  if [ "$ENGINE_CHECKED" != 1 ]; then
    if [ -n "${DOCKER_HOST:-}" ]; then
      [[ "$DOCKER_HOST" == unix:///* ]] || die "remote Docker host refused"
    fi
    if [ -n "${DOCKER_CONTEXT:-}" ]; then
      endpoint="$(command docker context inspect --format '{{.Endpoints.docker.Host}}' "$DOCKER_CONTEXT")"
    elif [ -n "${DOCKER_HOST:-}" ]; then
      endpoint="$DOCKER_HOST"
    else
      endpoint="$(command docker context inspect --format '{{.Endpoints.docker.Host}}')"
    fi
    [[ "$endpoint" == unix:///* ]] || die "non-local Docker context refused"
    ENGINE_CHECKED=1
  fi
  command docker "$@"
}

case "${1:-}" in
  init) [ "$#" = 1 ] || die "init takes no arguments"; python3 "$GUARD" init; exit ;;
  up-base|grafana|targets|control|verify|stop|rm|override|provisioning-from-main|teardown|names) ;;
  *) die "unknown command; see SAFETY.md" ;;
esac
RUN_ID="$(python3 "$GUARD" session)"
WORK="$(python3 "$GUARD" root)"
PREFIX="sora-gstand-$RUN_ID"
NET="$PREFIX-net"
METRICS="$PREFIX-metrics"
PROM="$PREFIX-prometheus"
GRAFANA_IMAGE="grafana/grafana@sha256:b28bae15e219c998fb0e0424ed724930cc61b1f61fb404d47c862f9a23f9e572"
PROM_IMAGE="prom/prometheus:v3.15.0"
PY_IMAGE="python:3.11-slim"

suffix() { [[ "$1" =~ ^[a-z][a-z0-9-]{0,31}$ ]] || die "invalid stand resource suffix"; }
local_name() {
  [[ "$1" == "$PREFIX-"* ]] || die "foreign resource name"
  [[ "${1#"$PREFIX-"}" =~ ^[a-z][a-z0-9-]{0,47}$ ]] || die "invalid resource name"
}
owned() {
  local kind="$1" name="$2" field=".Labels" actual
  local_name "$name"
  if [ "$kind" = container ]; then field=".Config.Labels"; fi
  actual="$(docker "$kind" inspect --format "{{ index $field \"$LABEL\" }}" "$name")" \
    || die "resource unavailable; ownership not established"
  [ "$actual" = "$RUN_ID" ] || die "resource ownership mismatch"
}
ensure_network() {
  if docker network inspect "$NET" >/dev/null 2>&1; then
    owned network "$NET"
  else
    docker network create --label "$LABEL=$RUN_ID" "$NET" >/dev/null
  fi
}
ensure_volume() {
  local name="$1"
  local_name "$name"
  if docker volume inspect "$name" >/dev/null 2>&1; then
    owned volume "$name"
  else
    docker volume create --label "$LABEL=$RUN_ID" "$name" >/dev/null
  fi
}
remove_container() {
  local name="$1"
  owned container "$name"
  docker rm -f "$name" >/dev/null
}
replace_container() {
  local name="$1" found
  local_name "$name"
  found="$(docker container ls -a --filter "name=^/$name$" --format '{{.Names}}')"
  if [ -n "$found" ]; then
    [ "$found" = "$name" ] || die "unexpected container lookup result"
    remove_container "$name"
  fi
}
up_base() {
  python3 "$GUARD" targets restore
  ensure_network
  replace_container "$METRICS"
  docker run -d --name "$METRICS" --label "$LABEL=$RUN_ID" --network "$NET" \
    -p 127.0.0.1:18080:8000 \
    -v "$REPO/tests/grafana_stand/metrics_server.py:/app/metrics_server.py:ro" \
    "$PY_IMAGE" python -I /app/metrics_server.py >/dev/null
  replace_container "$PROM"
  docker run -d --name "$PROM" --label "$LABEL=$RUN_ID" --network "$NET" --network-alias prometheus \
    -p 127.0.0.1:19090:9090 \
    -v "$REPO/tests/grafana_stand/stand-prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
    -v "$WORK/targets:/etc/prometheus/targets:ro" "$PROM_IMAGE" >/dev/null
}
control() {
  local query
  query="$(python3 "$REPO/tests/grafana_stand/control.py" "$@")"
  owned container "$METRICS"
  # Talk inside the owned metrics container, not to whichever process happens
  # to listen on the host's shared port. No arbitrary URL or Python is accepted.
  docker exec "$METRICS" python -I -c \
    'import sys, urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/control?" + sys.argv[1], timeout=5).close()' \
    "$query"
  echo "stand controls applied"
}
grafana() {
  local variant="$1" name volume prov pw
  suffix "$variant"
  name="$PREFIX-$variant"; volume="$name-data"
  prov="$(python3 "$GUARD" provisioning "$2")"
  pw="$(python3 "$GUARD" password)"
  ensure_network
  ensure_volume "$volume"
  replace_container "$name"
  docker run -d --name "$name" --label "$LABEL=$RUN_ID" --network "$NET" \
    -p 127.0.0.1:13000:3000 -e GF_SECURITY_ADMIN_USER=admin \
    -e "GF_SECURITY_ADMIN_PASSWORD=$pw" \
    -v "$volume:/var/lib/grafana" \
    -v "$prov/datasources:/etc/grafana/provisioning/datasources:ro" \
    -v "$prov/dashboards:/etc/grafana/provisioning/dashboards:ro" \
    -v "$prov/alerting:/etc/grafana/provisioning/alerting:ro" "$GRAFANA_IMAGE" >/dev/null
  echo "started $name"
}
override() {
  local out
  out="$(python3 "$GUARD" new-output "$1")"
  mkdir "$out/alerting" "$out/datasources" "$out/dashboards"
  cp -R "$REPO/grafana/provisioning/datasources/." "$out/datasources/"
  cp -R "$REPO/grafana/provisioning/dashboards/." "$out/dashboards/"
  sed -e 's/^    interval: 5m$/    interval: 30s/' \
      -e 's/for: 30m$/for: 1m/' -e 's/for: 45m$/for: 2m/' -e 's/for: 15m$/for: 1m/' \
      "$REPO/grafana/provisioning/alerting/alerts.yml" > "$out/alerting/alerts.yml"
  echo "$out"
}
provisioning_from_main() {
  local out
  out="$(python3 "$GUARD" new-output "$1")"
  # Never delete/overwrite an existing directory, even one from this run.
  (cd "$REPO" && git archive origin/main grafana/provisioning) | tar -x -C "$out"
  echo "$out/grafana/provisioning"
}
teardown() {
  local containers volumes networks name
  containers="$(docker container ls -a --filter "label=$LABEL=$RUN_ID" --format '{{.Names}}')"
  volumes="$(docker volume ls --filter "label=$LABEL=$RUN_ID" --format '{{.Name}}')"
  networks="$(docker network ls --filter "label=$LABEL=$RUN_ID" --format '{{.Name}}')"
  # Preflight every candidate before any deletion. Prefix alone never proves ownership.
  for name in $containers; do owned container "$name"; done
  for name in $volumes; do owned volume "$name"; done
  for name in $networks; do owned network "$name"; done
  for name in $containers; do docker rm -f "$name" >/dev/null; done
  for name in $volumes; do docker volume rm "$name" >/dev/null; done
  for name in $networks; do docker network rm "$name" >/dev/null; done
  echo "this run's owned Docker resources removed; files retained"
}

case "$1" in
  names) [ "$#" = 1 ] || die "names takes no arguments"; printf 'prefix=%s\nnetwork=%s\nmetrics=%s\nprometheus=%s\n' "$PREFIX" "$NET" "$METRICS" "$PROM" ;;
  up-base) [ "$#" = 1 ] || die "up-base takes no arguments"; up_base ;;
  grafana) [ "$#" = 3 ] || die "grafana <variant> <provisioning-dir>"; grafana "$2" "$3" ;;
  targets) [ "$#" = 2 ] || die "targets vanish|restore"; python3 "$GUARD" targets "$2" ;;
  control) [ "$#" -gt 1 ] || die "control key=value ..."; shift; control "$@" ;;
  verify) [ "$#" = 2 ] || die "verify <variant>"; suffix "$2"; owned container "$METRICS"; owned container "$PROM"; owned container "$PREFIX-$2" ;;
  stop) [ "$#" = 2 ] || die "stop <variant>"; suffix "$2"; owned container "$PREFIX-$2"; docker stop "$PREFIX-$2" >/dev/null ;;
  rm) [ "$#" = 2 ] || die "rm <variant>"; suffix "$2"; remove_container "$PREFIX-$2" ;;
  override) [ "$#" = 2 ] || die "override <new-output-name>"; override "$2" ;;
  provisioning-from-main) [ "$#" = 2 ] || die "provisioning-from-main <new-output-name>"; provisioning_from_main "$2" ;;
  teardown) [ "$#" = 1 ] || die "teardown takes no arguments"; teardown ;;
esac
