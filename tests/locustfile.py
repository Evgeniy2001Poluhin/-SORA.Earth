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
