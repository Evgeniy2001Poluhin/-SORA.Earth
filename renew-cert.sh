#!/bin/sh
# Renew, and let the installed deploy hook do the delivery.
#
# This used to pass --deploy-hook as a flag, which applies to *this* invocation
# only -- and the systemd timer runs a plain `certbot renew`, so the reload
# never happened on the path that actually renews. It also named
# docker-compose.yml while production runs docker-compose.prod.yml, addressing a
# container that is not the one serving the site.
#
# The hook now lives in /etc/letsencrypt/renewal-hooks/deploy/, where certbot
# runs it after any successful renewal, however the renewal was started. See
# scripts/certbot-deploy-hook.sh.
certbot renew --quiet
