"""A controllable stand-in for the two SORA scrape targets, for the Grafana stand.

Serves /app/metrics (job sora-app) and /scheduler/metrics (job sora-scheduler).
The state is changed over HTTP by /control?key=value&..., so one container can
produce every scenario the alert rules must answer:

  app=ok|down                 down makes the scrape fail (HTTP 500), so Prometheus
                              writes up=0 for sora-app
  gap=<int>                   value of sora_observation_coverage_gap_days
  gap_present=0|1             0 omits the gap gauge entirely
  ts_present=0|1              0 omits the success-timestamp gauge entirely
  ts_lag=<seconds>            the timestamp is now - ts_lag (a value of 1000 is a
                              scheduler that stopped reporting 1000 seconds ago)
  retrain_since=<seconds>     sora_retrain_seconds_since_success on sora-app

Standard library only, so it runs in a stock python image with no install.
"""
import http.server
import time
import urllib.parse

STATE = {
    "app": "ok",
    "gap": 0,
    "gap_present": 1,
    "ts_present": 1,
    "ts_lag": 0.0,
    "retrain_since": 3600,
}


def _app_metrics():
    return (
        f"sora_retrain_seconds_since_success {STATE['retrain_since']}\n"
        "sora_drift_detected_total 0\n"
        "sora_model_rejected_total 0\n"
    )


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
                STATE[key] = type(STATE[key])(value) if key != "app" else value
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
