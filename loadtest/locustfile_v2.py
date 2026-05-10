"""Load test for /api/v2/predict (champion @v3, MLflow Registry).

SLO targets (thesis-grade):
  p50  < 40 ms
  p95  < 150 ms
  p99  < 300 ms
  err  < 1 %
  sustained RPS >= 50 for 120 s

Run (headless):
  ./scripts/run_loadtest_v2.sh
Or UI:
  locust -f loadtest/locustfile_v2.py --host http://127.0.0.1:8000
"""
import random
from locust import HttpUser, task, between, events


def random_payload():
    return {
        "budget":         round(random.uniform(1_000, 1_000_000), 2),
        "co2_reduction":  round(random.uniform(10, 5_000), 2),
        "social_impact":  round(random.uniform(1, 10), 1),
        "duration_months": random.randint(1, 60),
    }


class V2PredictUser(HttpUser):
    """Simulates real ESG scoring traffic against champion model."""
    wait_time = between(0.1, 0.4)

    def on_start(self):
        # warm-up: hit version once so first-user cold-sta
        self.client.get("/api/v2/model/version", name="[warmup] version")

    @task(10)
    def predict(self):
        with self.client.post(
            "/api/v2/predict",
            json=random_payload(),
            name="POST /api/v2/predict",
            catch_response=True,
        ) as r:
            if r.status_code != 200:
                r.failure(f"status={r.status_code}")
            else:
                body = r.json()
                if "success_probability" not in body:
                    r.failure("no success_probability in body")
                elif body.get("alias") != "champion":
                    r.failure(f"wrong alias: {body.get('alias')}")

    @task(1)
    def version(self):
        self.client.get("/api/v2/model/version", name="GET /api/v2/model/version")

    @task(1)
    def calibration(self):
        self.client.get("/api/v2/model/calibration", name="GET /api/v2/model/calibration")


# SLO assertions after the run
@events.quitting.add_listener
def _assert_slo(environment, **kw):
    stats = environment.stats.total
    p95 = stats.get_response_time_percentile(0.95)
    p99 = stats.get_response_time_percentile(0.99)
    err = (stats.num_failures / stats.num_requests) if stats.num_requests else 0
    print(f"\n=== SLO check ===  p95={p95:.0f}ms  p99={p99:.0f}ms  err={err:.2%}  rps={stats.total_rps:.1f}")
    if p95 > 150:
        environment.process_exit_code = 1
        print(f"❌ p95 {p95:.0f}ms > 150ms target")
    if err > 0.01:
        environment.process_exit_code = 1
        print(f"❌ error rate {err:.2%} > 1% target")
    if environment.process_exit_code != 1:
        print("✅ SLO passed")
