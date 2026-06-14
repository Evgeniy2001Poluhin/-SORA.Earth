#!/bin/sh
cd /opt/sora_earth_ai_platform
docker run --rm -v /opt/sora_earth_ai_platform/certs:/etc/letsencrypt -v /opt/sora_earth_ai_platform/certbot/www:/var/www/certbot certbot/certbot renew --webroot -w /var/www/certbot --quiet
docker compose -f docker-compose.prod.yml exec -T nginx nginx -s reload
