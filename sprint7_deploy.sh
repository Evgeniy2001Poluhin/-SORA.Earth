#!/bin/bash
set -e

echo "=== Sprint 7: Nginx + Grafana Alerts + Locust ==="

# ─────────────────────────────────────────────
# 1. Nginx reverse proxy
# ─────────────────────────────────────────────
echo "--- 1/4 Nginx ---"
mkdir -p nginx

cat > nginx/nginx.conf << 'EOF'
worker_processes auto;
events { worker_connections 1024; }

http {
    upstream sora_backend {
        server app:8000;
    }

    # Rate limiting zone
    limit_req_zone $binary_remote_addr zone=api:10m rate=30r/s;

    server {
        listen 80;
        server_name _;

        # Security headers
        add_header X-Frame-Options DENY always;
        add_header X-Content-Type-Options nosniff always;
        add_header X-XSS-Protection "1; mode=block" always;
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;

        # Gzip
        gzip on;
        gzip_types application/json text/plain text/css application/javascript;
        gzip_min_length 256;

        # API proxy
        location /api/ {
            limit_req zone=api burst=20 nodelay;
            proxy_pass http://sora_backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_read_timeout 120s;
            proxy_connect_timeout 10s;
        }

        # Health (no rate limit)
        location /health {
            proxy_pass http://sora_backend;
        }

        # Metrics (internal only in prod, open for Prometheus here)
        location /metrics {
            proxy_pass http://sora_backend;
        }

        # WebSocket
        location /ws/ {
            proxy_pass http://sora_backend;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_read_timeout 86400s;
        }

        # Admin dashboard static
        location /static/ {
            proxy_pass http://sora_backend;
        }

        # Everything else
        location / {
            proxy_pass http://sora_backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        }
    }
}
EOF
echo "nginx/nginx.conf created"

# ─────────────────────────────────────────────
# 2. Grafana alerting rules
# ─────────────────────────────────────────────
echo "--- 2/4 Grafana Alerts ---"
mkdir -p grafana/provisioning/alerting

cat > grafana/provisioning/alerting/alerts.yml << 'EOF'
apiVersion: 1

groups:
  - orgId: 1
    name: SORA MLOps Alerts
    folder: SORA
    interval: 5m
    rules:
      - uid: sora-drift-detected
        title: Drift Detected
        condition: A
        data:
          - refId: A
            relativeTimeRange:
              from: 300
              to: 0
            datasourceUid: prometheus
            model:
              expr: increase(sora_drift_detected_total[5m]) > 0
              intervalMs: 1000
              maxDataPoints: 43200
        for: 0s
        labels:
          severity: warning
        annotations:
          summary: "Data drift detected — consider retraining"

      - uid: sora-retrain-failed
        title: Retrain Failed
        condition: A
        data:
          - refId: A
            relativeTimeRange:
              from: 600
              to: 0
            datasourceUid: prometheus
            model:
              expr: increase(sora_model_rejected_total[10m]) > 0
              intervalMs: 1000
              maxDataPoints: 43200
        for: 0s
        labels:
          severity: critical
        annotations:
          summary: "Model retrain was rejected (AUC degraded)"

      - uid: sora-auc-degradation
        title: AUC Below Threshold
        condition: A
        data:
          - refId: A
            relativeTimeRange:
              from: 600
              to: 0
            datasourceUid: prometheus
            model:
              expr: sora_model_auc < 0.85
              intervalMs: 1000
              maxDataPoints: 43200
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Model AUC dropped below 0.85"

      - uid: sora-high-latency
        title: High Prediction Latency
        condition: A
        data:
          - refId: A
            relativeTimeRange:
              from: 300
              to: 0
            datasourceUid: prometheus
            model:
              expr: histogram_quantile(0.95, rate(sora_prediction_latency_ms_bucket[5m])) > 500
              intervalMs: 1000
              maxDataPoints: 43200
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "P95 prediction latency exceeds 500ms"

      - uid: sora-app-down
        title: App Unreachable
        condition: A
        data:
          - refId: A
            relativeTimeRange:
              from: 300
              to: 0
            datasourceUid: prometheus
            model:
              expr: up{job="sora-app"} == 0
              intervalMs: 1000
              maxDataPoints: 43200
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "SORA app target is DOWN"
EOF
echo "grafana/provisioning/alerting/alerts.yml created"

# ─────────────────────────────────────────────
# 3. Locust load test
# ─────────────────────────────────────────────
echo "--- 3/4 Locust ---"

cat > tests/locustfile.py << 'PYEOF'
"""
SORA.Earth Load Test — Locust
Target: 50 RPS sustained, p95 < 500ms

Run:
  pip install locust
  locust -f tests/locustfile.py --host http://localhost:8000
  # or headless:
  locust -f tests/locustfile.py --host http://localhost:8000 \
         --users 50 --spawn-rate 5 --run-time 2m --headless \
         --csv output/loadtest
"""
import json
import random
from locust import HttpUser, task, between, events

COUNTRIES = [
    "USA", "DEU", "GBR", "FRA", "JPN", "CHN", "IND", "BRA",
    "CAN", "AUS", "KOR", "NLD", "SWE", "NOR", "FIN", "CHE",
]

SAMPLE_PAYLOAD = {
    "country": "DEU",
    "year": 2022,
    "co2_emissions": 8.1,
    "renewable_energy_pct": 42.0,
    "gdp_per_capita": 48000,
    "population_density": 237,
    "forest_area_pct": 32.7,
    "industrial_share_gdp": 27.0,
    "energy_intensity": 3.5,
    "political_stability": 0.8,
    "rule_of_law": 1.6,
}

TOKEN = None


class SoraUser(HttpUser):
    wait_time = between(0.5, 2.0)

    def on_start(self):
        global TOKEN
        if TOKEN is None:
            r = self.client.post(
                "/api/v1/auth/login-json",
                json={"username": "admin", "password": "sora2026"},
                name="/auth/login",
            )
            if r.status_code == 200:
                TOKEN = r.json().get("access_token")
        self.token = TOKEN
        self.headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}

    # ── Public endpoints (high frequency) ──

    @task(10)
    def health(self):
        self.client.get("/health")

    @task(8)
    def evaluate(self):
        payload = {**SAMPLE_PAYLOAD, "country": random.choice(COUNTRIES)}
        self.client.post("/api/v1/evaluate", json=payload, name="/evaluate")

    @task(6)
    def predict(self):
        payload = {**SAMPLE_PAYLOAD, "country": random.choice(COUNTRIES)}
        self.client.post("/api/v1/predict", json=payload, name="/predict")

    @task(3)
    def predict_explain(self):
        payload = {**SAMPLE_PAYLOAD, "country": random.choice(COUNTRIES)}
        self.client.post("/api/v1/predict/explain", json=payload, name="/predict/explain")

    @task(2)
    def countries(self):
        self.client.get("/api/v1/countries")

    @task(2)
    def country_benchmark(self):
        c = random.choice(COUNTRIES)
        self.client.get(f"/api/v1/analytics/country-benchmark/{c}", name="/country-benchmark")

    @task(1)
    def model_compare(self):
        payload = {**SAMPLE_PAYLOAD}
        self.client.post("/api/v1/analytics/model-compare", json=payload, name="/model-compare")

    # ── Admin endpoints (low frequency) ──

    @task(1)
    def admin_snapshot(self):
        self.client.get("/api/v1/admin/snapshot", headers=self.headers, name="/admin/snapshot")

    @task(1)
    def admin_timeline(self):
        self.client.get("/api/v1/admin/timeline", headers=self.headers, name="/admin/timeline")

    @task(1)
    def ai_teammate_status(self):
        self.client.get(
            "/api/v1/admin/ai-teammate/status",
            headers=self.headers,
            name="/ai-teammate/status",
        )
PYEOF
echo "tests/locustfile.py created"

# ─────────────────────────────────────────────
# 4. Update docker-compose.yml — add nginx
# ─────────────────────────────────────────────
echo "--- 4/4 docker-compose update ---"

python3 << 'PYEOF'
content = open("docker-compose.yml").read()

if "nginx:" in content:
    print("nginx service already in docker-compose.yml, skipping")
else:
    # Add nginx service before volumes section
    nginx_block = """
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
    depends_on:
      - app
    restart: unless-stopped
"""
    # Also update prometheus to scrape through app directly (no change needed)
    # And add alerting volume to grafana
    grafana_old = "      - ./grafana/provisioning/dashboards:/etc/grafana/provisioning/dashboards"
    grafana_new = grafana_old + "\n      - ./grafana/provisioning/alerting:/etc/grafana/provisioning/alerting"

    if "provisioning/alerting" not in content:
        content = content.replace(grafana_old, grafana_new)

    content = content.replace("\nvolumes:", nginx_block + "\nvolumes:")
    open("docker-compose.yml", "w").write(content)
    print("nginx + grafana alerting volume added to docker-compose.yml")
PYEOF

echo ""
echo "=== Sprint 7 DONE ==="
echo ""
echo "Next steps:"
echo "  1. docker compose up -d --build"
echo "  2. Check: curl http://localhost/health  (via nginx:80)"
echo "  3. Check: curl http://localhost:8000/health  (direct)"
echo "  4. Grafana alerts: http://localhost:3000 → Alerting → Alert rules"
echo "  5. Load test:"
echo "     pip install locust"
echo "     locust -f tests/locustfile.py --host http://localhost:8000 --users 50 --spawn-rate 5 --run-time 2m --headless --csv output/loadtest"
