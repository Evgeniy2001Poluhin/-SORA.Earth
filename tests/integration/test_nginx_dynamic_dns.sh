#!/usr/bin/env bash
# Does nginx follow an upstream whose container changed address?
#
# `nginx -t` proves the configuration parses. It does not prove re-resolution,
# which is the property #129 turns on, so this drives it: move the backend to a
# new address, hold the old one with a container that listens on nothing, leave
# nginx alone, and require the proxy to recover by itself.
#
# Opt-in. pytest does not collect it -- it is a shell script, and it needs a
# Docker daemon. Run it deliberately:
#
#     tests/integration/test_nginx_dynamic_dns.sh nginx/nginx.conf
#     tests/integration/test_nginx_dynamic_dns.sh nginx/nginx.conf legacy
#
# `legacy` rebuilds the pre-fix shape and **must** fail at step 6. A run that
# has not been shown to fail proves nothing, so the two modes are the test.
#
# Everything is isolated: unique project, own network, no published ports, no
# volumes from the real stack, synthetic upstreams. It never touches production
# containers, and the trap removes what it made even on failure or interrupt.
#
# Four things learned the hard way, recorded so the next person does not:
#
#   * `localhost` inside the container resolves to ::1 first and nginx listens
#     on IPv4, so requests must go to 127.0.0.1.
#   * `--network-alias` must be given to `docker run`. Adding it afterwards
#     with `docker network connect` to the *same* network fails, and the name
#     silently does not resolve.
#   * `/var/log/nginx/access.log` in the official image is a symlink to
#     /dev/stdout. Reading it with `cat`/`tail` blocks forever -- it wedged an
#     ssh session and killed three runs at the exact step that mattered. Use
#     `docker logs`.
#   * a stand that cannot tell "the address did not change" from "the proxy did
#     not recover" must say the first out loud rather than report the second.
set -uo pipefail

CONF=${1:?usage: test_nginx_dynamic_dns.sh <nginx.conf> [fixed|legacy]}
MODE=${2:-fixed}

command -v docker >/dev/null || { echo "SKIP: no docker"; exit 0; }
docker info >/dev/null 2>&1 || { echo "SKIP: docker daemon unreachable"; exit 0; }
[ -f "$CONF" ] || { echo "FAIL: $CONF not found"; exit 1; }

# Unique per run: a leftover container from an interrupted run once held the
# address the next run needed, and the failure looked like a broken proxy.
P="ndns$$_$(date +%s)"
NET="${P}_net"
DIR="$(mktemp -d)"
LOG="$DIR/run.log"

for name in "${P}-nginx" "${P}-backend" "${P}-grafana" "${P}-filler"; do
    docker ps -a --format '{{.Names}}' | grep -qx "$name" && {
        echo "FAIL: $name already exists; refusing to run"; exit 1; }
done
docker network ls --format '{{.Name}}' | grep -qx "$NET" && {
    echo "FAIL: network $NET already exists; refusing to run"; exit 1; }

cleanup() {
    docker rm -f "${P}-nginx" "${P}-backend" "${P}-grafana" "${P}-filler" >/dev/null 2>&1
    docker network rm "$NET" >/dev/null 2>&1
    [ "${KEEP_LOG:-0}" = "1" ] || rm -rf "$DIR"
}
trap cleanup EXIT INT TERM

note() { echo "$*" | tee -a "$LOG"; }
die()  { note "FAIL: $*"; note "full log: $LOG"; KEEP_LOG=1; exit 1; }
broken() { note "STAND BROKEN: $*"; note "full log: $LOG"; KEEP_LOG=1; exit 2; }

# The block under test is lifted from the real file rather than retyped, so the
# stand cannot drift from what production runs.
python3 - "$CONF" "$DIR/nginx.conf" "$MODE" <<'PY' || exit 2
import re, sys
src, out, mode = sys.argv[1:4]
c = open(src).read()
res = "\n".join(l.strip() for l in c.splitlines()
                if re.match(r"\s*resolver(_timeout)?\s", l))
be = re.search(r"upstream\s+sora_backend\s*\{.*?\}", c, re.S)
gr = re.search(r"upstream\s+sora_grafana\s*\{.*?\}", c, re.S)
if not be:
    sys.exit("upstream sora_backend not found in " + src)
be, gr = be.group(0), (gr.group(0) if gr else "upstream sora_grafana { server grafana:3000; }")
if mode == "legacy":
    res = ""
    be = "upstream sora_backend { server backend:8000; }"
    gr = "upstream sora_grafana { server grafana:3000; }"
open(out, "w").write(f"""events {{ worker_connections 64; }}
http {{
    {res}
    {be}
    {gr}
    log_format up '$status $upstream_addr $uri';
    access_log /var/log/nginx/access.log up;
    server {{
        listen 80;
        location /grafana/ {{ proxy_pass http://sora_grafana/grafana/; proxy_connect_timeout 2s; }}
        location /         {{ proxy_pass http://sora_backend;         proxy_connect_timeout 2s; }}
    }}
}}
""")
PY

cat > "$DIR/app.py" <<'PY'
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = self.path.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
http.server.HTTPServer(("", int(sys.argv[1])), H).serve_forever()
PY

docker network create "$NET" >/dev/null || broken "could not create $NET"

start_backend() {
    docker run -d --name "${P}-backend" --network "$NET" --network-alias backend \
        -v "$DIR/app.py:/a.py:ro" python:3.11-alpine python3 /a.py 8000 >/dev/null
}
docker run -d --name "${P}-grafana" --network "$NET" --network-alias grafana \
    -v "$DIR/app.py:/a.py:ro" python:3.11-alpine python3 /a.py 3000 >/dev/null
start_backend
docker run -d --name "${P}-nginx" --network "$NET" \
    -v "$DIR/nginx.conf:/etc/nginx/nginx.conf:ro" nginx:alpine >/dev/null
sleep 5

ip_of()   { docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$1"; }
status()  { docker exec "${P}-nginx" wget -q -S -O /dev/null "http://127.0.0.1$1" 2>&1 \
              | head -1 | grep -oE '[0-9]{3}' | head -1; }
body_of() { docker exec "${P}-nginx" wget -q -O - "http://127.0.0.1$1" 2>/dev/null; }
upstreams() { docker logs "${P}-nginx" 2>&1 | grep -E '^[0-9]{3} '; }

note "mode: $MODE"

# 1-3: a working baseline, and the state that must not change under us.
code1=$(status /); ip1=$(ip_of "${P}-backend")
ngx_id1=$(docker inspect -f '{{.Id}}'             "${P}-nginx")
ngx_up1=$(docker inspect -f '{{.State.StartedAt}}' "${P}-nginx")
note "1. first request        = $code1   backend=$ip1"
[ "$code1" = "200" ] || broken "baseline request returned $code1, not 200"

gcode=$(status /grafana/x); gbody=$(body_of /grafana/x)
note "2. grafana /grafana/x   = $gcode   body=$gbody"
[ "$gcode" = "200" ] && [ "$gbody" = "/grafana/x" ] \
    || die "the named grafana upstream changed the URI: $gcode $gbody"

# 4: move the backend, and park the old address on something that listens on
#    nothing -- so a stale cached address cannot answer 200 by accident.
docker rm -f "${P}-backend" >/dev/null 2>&1
docker run -d --name "${P}-filler" --network "$NET" --ip "$ip1" alpine sleep 300 >/dev/null 2>&1
start_backend
sleep 4
ip2=$(ip_of "${P}-backend")
note "3. backend recreated    = $ip2   (was $ip1)"
[ "$ip1" != "$ip2" ] || broken "the address did not change; there is nothing to test"

# 5: nginx must be the same process throughout, or the test proves a restart.
ngx_id2=$(docker inspect -f '{{.Id}}'             "${P}-nginx")
ngx_up2=$(docker inspect -f '{{.State.StartedAt}}' "${P}-nginx")
note "4. nginx unchanged      = id $([ "$ngx_id1" = "$ngx_id2" ] && echo same || echo DIFFERENT), started $ngx_up2"
[ "$ngx_id1" = "$ngx_id2" ] || broken "the nginx container was replaced"
[ "$ngx_up1" = "$ngx_up2" ] || broken "nginx restarted; the test would prove nothing"

# 6: within valid=10s + resolver_timeout=2s + slack.
sleep 14
code2=$(status /)
note "5. after the move       = $code2"

# 7-8: the address nginx actually used, before and after.
before=$(upstreams | grep " /$" | head -1)
after=$(upstreams  | grep " /$" | tail -1)
note "6. upstream before      = $before"
note "7. upstream after       = $after"

gcode2=$(status /grafana/x); gbody2=$(body_of /grafana/x)
note "8. grafana after        = $gcode2   body=$gbody2"

echo | tee -a "$LOG"
if [ "$MODE" = "legacy" ]; then
    if [ "$code2" = "200" ]; then
        die "legacy config recovered on its own -- the stand is not exercising
     re-resolution, and the fixed result proves nothing"
    fi
    note "PASS(legacy): stale address gave $code2, as it must"
    exit 0
fi

[ "$code2" = "200" ] || die "did not recover: $code2 (upstream after: $after)"
echo "$after" | grep -q "$ip2" || die "recovered, but upstream is not $ip2: $after"
echo "$before" | grep -q "$ip1" || die "baseline upstream was not $ip1: $before"
[ "$gcode2" = "200" ] && [ "$gbody2" = "/grafana/x" ] || die "grafana broke: $gcode2 $gbody2"

note "PASS(fixed): upstream moved $ip1 -> $ip2 with no reload, URI preserved"
