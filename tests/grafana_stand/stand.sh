#!/usr/bin/env bash
# Disposable local Grafana stand for the SORA alert rules. NOT run in CI, and it
# never touches the project's compose stacks, volumes or database.
#
# Every resource it creates is named with the prefix sora-gstand-, and `teardown`
# removes only those. Grafana is the production image pinned by digest; Prometheus
# is the production major/minor version. Nothing here connects to production.
#
# Usage:
#   stand.sh up-base                      network + metrics + prometheus
#   stand.sh grafana <name> <volume> <provisioning-dir>
#   stand.sh stop <name>                  stop a grafana container, keep its volume
#   stand.sh rm <name>                    remove a grafana container
#   stand.sh override <out-dir>           write a copy of the repo's alerting with
#                                         shortened holds and a 30s group interval
#   stand.sh provisioning-from-main <out> extract origin/main's grafana/provisioning
#   stand.sh teardown                     remove every sora-gstand-* resource
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="${GSTAND_WORK:-$HOME/.sora-gstand}"
mkdir -p "$WORK"

GRAFANA_IMAGE="grafana/grafana@sha256:b28bae15e219c998fb0e0424ed724930cc61b1f61fb404d47c862f9a23f9e572"
PROM_IMAGE="prom/prometheus:v3.15.0"
PY_IMAGE="python:3.11-slim"
NET="sora-gstand-net"
METRICS="sora-gstand-metrics"
PROM="sora-gstand-prometheus"

admin_env() {
  # Throwaway stand password, generated once, kept outside the repository.
  if [ ! -f "$WORK/admin.env" ]; then
    printf 'GSTAND_PW=%s\n' "$(openssl rand -hex 16)" > "$WORK/admin.env"
    chmod 600 "$WORK/admin.env"
  fi
  # shellcheck disable=SC1090
  . "$WORK/admin.env"
}

net() {
  docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
}

up_base() {
  net
  docker rm -f "$METRICS" >/dev/null 2>&1 || true
  docker run -d --name "$METRICS" --network "$NET" \
    -p 127.0.0.1:18080:8000 \
    -v "$REPO/tests/grafana_stand/metrics_server.py:/app/metrics_server.py:ro" \
    "$PY_IMAGE" python -I /app/metrics_server.py >/dev/null
  docker rm -f "$PROM" >/dev/null 2>&1 || true
  docker run -d --name "$PROM" --network "$NET" --network-alias prometheus \
    -p 127.0.0.1:19090:9090 \
    -v "$REPO/tests/grafana_stand/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
    "$PROM_IMAGE" >/dev/null
}

grafana() {
  local name="$1" volume="$2" prov="$3"
  admin_env
  net
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker run -d --name "$name" --network "$NET" \
    -p 127.0.0.1:13000:3000 \
    -e "GF_SECURITY_ADMIN_USER=admin" \
    -e "GF_SECURITY_ADMIN_PASSWORD=$GSTAND_PW" \
    -v "$volume:/var/lib/grafana" \
    -v "$prov/datasources:/etc/grafana/provisioning/datasources:ro" \
    -v "$prov/dashboards:/etc/grafana/provisioning/dashboards:ro" \
    -v "$prov/alerting:/etc/grafana/provisioning/alerting:ro" \
    "$GRAFANA_IMAGE" >/dev/null
  echo "started $name on volume $volume with provisioning $prov"
}

stop() { docker stop "$1" >/dev/null; }
rm_one() { docker rm -f "$1" >/dev/null 2>&1 || true; }

override() {
  local out="$1"
  mkdir -p "$out/alerting" "$out/datasources" "$out/dashboards"
  cp -R "$REPO/grafana/provisioning/datasources/." "$out/datasources/"
  cp -R "$REPO/grafana/provisioning/dashboards/." "$out/dashboards/"
  # Stand-only: the holds and the evaluation interval are shortened so a state
  # change can be observed inside a session. The expressions, labels, states and
  # every other field are the repository's own, untouched.
  sed -e 's/^    interval: 5m$/    interval: 30s/' \
      -e 's/for: 30m$/for: 1m/' \
      -e 's/for: 45m$/for: 2m/' \
      -e 's/for: 15m$/for: 1m/' \
      "$REPO/grafana/provisioning/alerting/alerts.yml" > "$out/alerting/alerts.yml"
  echo "override written to $out"
  diff "$REPO/grafana/provisioning/alerting/alerts.yml" "$out/alerting/alerts.yml" || true
}

provisioning_from_main() {
  local out="$1"
  rm -rf "$out"
  mkdir -p "$out"
  (cd "$REPO" && git archive origin/main grafana/provisioning) | tar -x -C "$out"
  echo "origin/main provisioning extracted to $out/grafana/provisioning"
}

teardown() {
  for c in $(docker ps -aq --filter "name=^sora-gstand-"); do docker rm -f "$c" >/dev/null; done
  for v in $(docker volume ls -q --filter "name=^sora-gstand-"); do docker volume rm "$v" >/dev/null; done
  docker network rm "$NET" >/dev/null 2>&1 || true
  echo "sora-gstand-* containers, volumes and network removed"
}

case "${1:-}" in
  up-base) up_base ;;
  grafana) grafana "$2" "$3" "$4" ;;
  stop) stop "$2" ;;
  rm) rm_one "$2" ;;
  override) override "$2" ;;
  provisioning-from-main) provisioning_from_main "$2" ;;
  teardown) teardown ;;
  *) sed -n '2,20p' "$0"; exit 2 ;;
esac
