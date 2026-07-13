#!/bin/bash
# Check forecast routes on production server

echo "========================================="
echo "CHECKING FORECAST ROUTES ON PRODUCTION"
echo "========================================="
echo ""

# Test new metrics endpoints
echo "Testing: /api/v1/forecast/metrics/latest"
curl -s -o /dev/null -w "Status: %{http_code}\n" https://sora-earth.online/api/v1/forecast/metrics/latest

echo ""
echo "Testing: /api/v1/forecast/metrics/performance"
curl -s -o /dev/null -w "Status: %{http_code}\n" https://sora-earth.online/api/v1/forecast/metrics/performance

echo ""
echo "Testing (if duplicate): /api/v1/forecast/forecast/metrics/latest"
curl -s -o /dev/null -w "Status: %{http_code}\n" https://sora-earth.online/api/v1/forecast/forecast/metrics/latest

echo ""
echo "========================================="
echo "CHECKING CODE VERSION ON SERVER"
echo "========================================="
echo ""

ssh root@45.137.60.67 "cd /opt/sora_earth_ai_platform && python3 scripts/verify_forecast_routes.py 2>&1 | tail -20"
