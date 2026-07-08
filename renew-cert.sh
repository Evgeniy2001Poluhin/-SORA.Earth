#!/bin/sh
certbot renew --quiet --deploy-hook "docker compose -f /opt/sora_earth_ai_platform/docker-compose.yml exec -T nginx nginx -s reload"
