#!/bin/sh
# Make a renewed certificate reach nginx, without restarting anything else.
#
# certbot renews on a systemd timer and writes the new certificate into
# /etc/letsencrypt/live/. nginx reads its certificate once, at start, so until
# something tells it to re-read, the site keeps serving the *old* certificate --
# and keeps serving it after expiry, while `certbot renew` reports success. The
# renewal is not the delivery.
#
# Two things were wrong before this file existed, both found by the deploy
# script's own check on 2026-08-16:
#
#   1. `renew-cert.sh` passed `--deploy-hook` as a flag to its own `certbot
#      renew`. A flag applies to that invocation only. The systemd timer runs a
#      plain `certbot renew`, which never saw it -- so the hook existed in the
#      repository and nowhere in the renewal path.
#   2. That command named `docker-compose.yml`. Production runs with
#      `docker-compose.prod.yml`, a different compose project, so the `exec`
#      addressed a container that is not the one serving the site.
#
# Installed at /etc/letsencrypt/renewal-hooks/deploy/, where certbot runs every
# executable after a successful renewal, whatever invoked it.
set -eu

COMPOSE_FILE="${SORA_COMPOSE_FILE:-/opt/sora_earth_ai_platform/docker-compose.prod.yml}"

# `nginx -s reload`, never `restart` and never `up`. A reload re-reads the
# configuration and the certificate in the running process: connections in
# flight are not dropped, and nothing else in the stack is touched. Restarting
# the container would work too and would take the site down for the duration,
# which is a strange price to pay for a file certbot has already written.
exec docker compose -f "$COMPOSE_FILE" exec -T nginx nginx -s reload
