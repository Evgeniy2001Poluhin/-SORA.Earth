from locust import HttpUser, task, between

class SoraUser(HttpUser):
    wait_time = between(0.1, 0.5)
    host = "http://localhost:8000"
    token = None

    def on_start(self):
        r = self.client.post("/auth/login",
            json={"username": "admin", "password": "sora2026"})
        if r.status_code == 200:
            self.token = r.json().get("access_token")
        self.headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}

    @task(5)
    def health(self):
        self.client.get("/health")

    @task(3)
    def predict(self):
        self.client.post("/predict", json={
            "project_name": "LoadTest Solar",
            "country": "Germany",
            "sector": "renewable_energy",
            "budget": 150000,
            "duration_months": 24,
            "co2_reduction": 50,
            "social_impact": 7,
            "technology_readiness": 8,
            "category": "solar",
            "region": "Europe"
        }, headers=self.headers)

    @task(2)
    def evaluate(self):
        self.client.post("/evaluate", json={
            "project_name": "LoadTest Eval",
            "country": "Germany",
            "sector": "renewable_energy",
            "budget": 100000,
            "duration_months": 12,
            "co2_reduction": 40,
            "social_impact": 6,
            "technology_readiness": 7,
            "category": "solar",
            "region": "Europe"
        }, headers=self.headers)

    @task(2)
    def model_metrics(self):
        self.client.get("/model/metrics", headers=self.headers)

    @task(1)
    def me(self):
        self.client.get("/auth/me", headers=self.headers)

    @task(1)
    def model_status(self):
        self.client.get("/model/status", headers=self.headers)

    @task(1)
    def drift(self):
        self.client.get("/model/drift", headers=self.headers)
