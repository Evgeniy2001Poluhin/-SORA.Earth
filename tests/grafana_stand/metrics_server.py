"""A controllable stand-in for the two SORA scrape targets, for the Grafana stand.

Serves /app/metrics (job sora-app) and /scheduler/metrics (job sora-scheduler).
The state is changed over HTTP by /control?key=value&..., so one container can
produce every scenario the alert rules must answer. Counters and histogram
buckets are real: they only grow, and they grow on each scrape while a switch is
on, so rate() and increase() see genuine increments.

  app=ok|down                 down makes the scrape fail (HTTP 500), so Prometheus
                              writes up=0 for sora-app
  drift_on=0|1                each sora-app scrape adds 1 to sora_drift_detected_total
  rejected_on=0|1             each sora-app scrape adds 1 to sora_model_rejected_total
  lat_ms=<float>              latency of the observations recorded per scrape
  lat_n=<int>                 observations recorded per scrape (0 = no traffic)
  gap=<int>                   value of sora_observation_coverage_gap_days
  gap_present=0|1             0 omits the gap gauge entirely
  ts_present=0|1              0 omits the success-timestamp gauge entirely
  ts_lag=<seconds>            the timestamp is now - ts_lag (1000 = a scheduler that
                              last reported 1000 seconds ago)
  retrain_since=<seconds>     sora_retrain_seconds_since_success on sora-app; -1 is
                              the never-succeeded sentinel

Standard library only, so it runs in a stock python image with no install.
"""
import http.server
import threading
import time
import urllib.parse

STATE = {
    "app": "ok",
    "drift_on": 0,
    "rejected_on": 0,
    "lat_ms": 50.0,
    "lat_n": 10,
    "gap": 0,
    "gap_present": 1,
    "ts_present": 1,
    "ts_lag": 0.0,
    "retrain_since": 3600,
}

#: Upper bounds of the latency histogram, in milliseconds.
BUCKETS = [10.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 2500.0, 5000.0]

COUNTERS = {
    "drift_total": 0,
    "rejected_total": 0,
    "lat_count": 0,
    "lat_sum": 0.0,
    "lat_buckets": {le: 0 for le in BUCKETS},
}
LOCK = threading.Lock()


def _record_scrape():
    """Advance the counters for one sora-app scrape. Called under LOCK."""
    if STATE["drift_on"]:
        COUNTERS["drift_total"] += 1
    if STATE["rejected_on"]:
        COUNTERS["rejected_total"] += 1
    n = int(STATE["lat_n"])
    if n > 0:
        value = float(STATE["lat_ms"])
        COUNTERS["lat_count"] += n
        COUNTERS["lat_sum"] += n * value
        for le in BUCKETS:
            if value <= le:
                COUNTERS["lat_buckets"][le] += n


def _app_metrics():
    with LOCK:
        _record_scrape()
        lines = [
            "# TYPE sora_drift_detected_total counter",
            f"sora_drift_detected_total {COUNTERS['drift_total']}",
            "# TYPE sora_model_rejected_total counter",
            f"sora_model_rejected_total {COUNTERS['rejected_total']}",
            f"sora_retrain_seconds_since_success {STATE['retrain_since']}",
            "# TYPE sora_prediction_latency_ms histogram",
        ]
        for le in BUCKETS:
            lines.append(
                f'sora_prediction_latency_ms_bucket{{le="{le}"}} {COUNTERS["lat_buckets"][le]}')
        lines.append(
            f'sora_prediction_latency_ms_bucket{{le="+Inf"}} {COUNTERS["lat_count"]}')
        lines.append(f"sora_prediction_latency_ms_sum {COUNTERS['lat_sum']}")
        lines.append(f"sora_prediction_latency_ms_count {COUNTERS['lat_count']}")
    return "\n".join(lines) + "\n"


def _scheduler_metrics():
    lines = []
    if STATE["gap_present"]:
        lines.append(f"sora_observation_coverage_gap_days {STATE['gap']}")
    if STATE["ts_present"]:
        ts = time.time() - STATE["ts_lag"]
        lines.append(f"sora_observation_coverage_last_success_timestamp_seconds {ts:.3f}")
    return "\n".join(lines) + "\n"


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/plain; version=0.0.4"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/control":
            for key, value in urllib.parse.parse_qsl(url.query):
                if key not in STATE:
                    return self._send(400, f"unknown key {key}\n")
                STATE[key] = value if key == "app" else type(STATE[key])(float(value))
                if key in ("ts_lag", "lat_ms"):
                    STATE[key] = float(value)
            return self._send(200, repr(STATE) + "\n")
        if url.path == "/state":
            return self._send(200, repr(STATE) + "\n")
        if url.path == "/app/metrics":
            if STATE["app"] != "ok":
                return self._send(500, "target down (stand)\n")
            return self._send(200, _app_metrics())
        if url.path == "/scheduler/metrics":
            return self._send(200, _scheduler_metrics())
        return self._send(404, "not found\n")

    def log_message(self, fmt, *args):  # keep the container log quiet
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
