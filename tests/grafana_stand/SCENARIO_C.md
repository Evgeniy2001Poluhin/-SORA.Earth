# Scenario C: portable driver; runtime acceptance remains separate

This is a new driver for the historical scenario in `RESULTS.md`, not evidence
that its transitions occurred again. Tests use fake clocks and recording Docker
stubs. Depends on local ownership guards (`SAFETY.md`, commit `8291cd6b`).

## Inert plan

```sh
python3 -B tests/grafana_stand/scenario_c.py --plan
```

Default invocation does the same. No stand directory, password, HTTP or Docker
is accessed. The fixed schedule is:

| Elapsed | Action |
|---|---|
| 0 | counters increment; latency 900 ms; success timestamp 1000 seconds old; retrain age 1300000; gap 0 |
| 15 min | counters stop; latency becomes 50 ms |
| 60 min | app target vanishes from file_sd |
| 85 min | app target and healthy metrics restored |
| 95 min | capture ends; restoration attempted again |

Polling is every 10 seconds. Missing an observation/action by more than 30 seconds
or a wall/monotonic clock discontinuity aborts the run. No accelerated holds,
timing scale or retry. Restoration failures remain in the timeline and fail the
run. The driver removes no resources. Killing it or losing the daemon can prevent
restoration; inspect the stand and timeline before reuse.

## Future local runtime run — not performed by this change

Requires Bash, Python 3.11+, a local Docker daemon and an explicitly authorized
disposable stand. Keep the host awake for about 95 minutes. Read `SAFETY.md` first.
Use a fresh empty run directory and volume, not a previous scenario's resources.

```sh
export GSTAND_WORK="$HOME/.sora-gstand/scenario-c-new-run"
bash tests/grafana_stand/stand.sh init
bash tests/grafana_stand/stand.sh up-base
bash tests/grafana_stand/stand.sh grafana c "$PWD/grafana/provisioning"
python3 -B tests/grafana_stand/check.py datasources
python3 -B tests/grafana_stand/check.py rules
python3 -B tests/grafana_stand/check.py states
# After readiness and provisioning are checked, opt in explicitly:
python3 -B tests/grafana_stand/scenario_c.py --execute c-original-holds
```

On macOS an operator may use `caffeinate` for that last command. No Mac paths are
embedded. The Prometheus file is `stand-prometheus.yml`, not `prometheus.yml`.

The driver verifies three container ownership labels and evaluator readiness
before changing metrics. This does not prove the running Grafana used the exact
repository rules. Before accepting a runtime run, separately verify provisioned
rules, the original 5-minute group interval/real holds, datasource uid, actual
image digests/platforms and a clean volume. Grafana's image is digest-pinned;
Prometheus and Python use version tags in the helper. Record resolved image ids
and digests: Git/file hashes and tags alone are not complete binary environment
pins. Do not modify source/provisioning during a run.

The fresh guarded output contains `manifest.json` (source SHA, script/provisioning
hashes, plan, run id) and `timeline.jsonl` (UTC observations, elapsed times,
actions, rule states, abort/restoration/finished events). No credentials, labels
or free-form API error text is copied. Existing output names are refused. The
manifest identifies source files at startup, not the actual mounted files or
running container versions; it is not a runtime attestation.

Independently assess recorded transitions against the actual provisioned rules.
Exit zero means the schedule/capture finished; it does not mean expected alerts
fired or a contact point delivered anything. Production acceptance is out of scope.
