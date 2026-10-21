"""Read-only probes of the stand's Grafana API, printed as JSON or compact tables.

Used to record RESULTS.md. Reads the disposable admin password from the private run directory;
never prints it. See SAFETY.md for the initialization contract.

  check.py datasources
  check.py rules             provisioned rules: uid, condition, nodata/exec states
  check.py states            evaluator state per rule: health, lastEvaluation, alerts
"""
import base64
import json
import sys
import urllib.request

GRAFANA = "http://127.0.0.1:13000"


def _password():
    # Validate the private stand root; this read-only probe never creates a key.
    from path_guard import password, session
    work, _run_id = session()
    return password(work)


def get(path):
    req = urllib.request.Request(GRAFANA + path)
    token = base64.b64encode(f"admin:{_password()}".encode()).decode()
    req.add_header("Authorization", "Basic " + token)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def datasources():
    rows = get("/api/datasources")
    return [{k: d.get(k) for k in ("id", "uid", "name", "type", "url", "isDefault", "orgId")}
            for d in rows]


def rules():
    out = []
    for r in get("/api/v1/provisioning/alert-rules"):
        out.append({
            "uid": r["uid"],
            "title": r["title"],
            "condition": r["condition"],
            "noDataState": r["noDataState"],
            "execErrState": r["execErrState"],
            "for": r["for"],
            "datasourceUids": [d["datasourceUid"] for d in r["data"]],
        })
    return sorted(out, key=lambda x: x["uid"])


def states():
    out = []
    doc = get("/api/prometheus/grafana/api/v1/rules")
    for group in doc["data"]["groups"]:
        for rule in group["rules"]:
            out.append({
                "uid": rule.get("uid"),
                "name": rule.get("name"),
                "state": rule.get("state"),
                "health": rule.get("health"),
                "lastEvaluation": rule.get("lastEvaluation"),
                "lastError": rule.get("lastError"),
                "alerts": [{"state": a.get("state"), "labels": a.get("labels")}
                           for a in rule.get("alerts", [])],
            })
    return sorted(out, key=lambda x: str(x["uid"]))


def watch(seconds, every=10):
    """Poll the evaluator and print each change in a rule's state or alert states,
    with the UTC time it was seen. Stops after `seconds`. Returns the final states."""
    import time
    seen = {}
    deadline = time.time() + seconds
    final = []
    while True:
        final = states()
        now = time.strftime("%H:%M:%S", time.gmtime())
        for r in final:
            key = (r["state"], tuple(sorted(a["state"] for a in r["alerts"])))
            if seen.get(r["uid"]) != key:
                seen[r["uid"]] = key
                print(f"{now}Z {r['uid']}: state={r['state']} alerts={[a['state'] for a in r['alerts']]} "
                      f"lastEval={r['lastEvaluation']} err={r['lastError']}", flush=True)
        if time.time() >= deadline:
            return final
        time.sleep(every)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "watch":
        watch(int(sys.argv[2]))
        raise SystemExit(0)
    fn = {"datasources": datasources, "rules": rules, "states": states}.get(cmd)
    if fn is None:
        raise SystemExit(__doc__)
    print(json.dumps(fn(), indent=2, ensure_ascii=False))
