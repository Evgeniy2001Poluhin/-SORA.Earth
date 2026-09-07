"""The `Key metrics` table in CLAUDE.md must be true of the code (#268).

It was not. Three of its four entries could not have answered a question:

    sora_drift_detected             no such metric -- the name ends `_total`,
                                    and nothing incremented it, while a Grafana
                                    alert watched it (#266)
    sora_retrain_success/failure    no such metric -- it is
                                    `sora_retrain_total{status}`, written in a
                                    process Prometheus did not scrape (#267)
    sora_prediction_latency_seconds no such metric -- the name ends `_ms`

Three independent properties are checked, because a metric can fail any one of
them alone and read as healthy:

    declared    the name exists in app/prom_metrics.py
    written     something in app/ actually calls .inc()/.set()/.observe()
    reachable   the process that writes it is scraped by a target that exists

The third is not "the YAML mentions a job". A job named in the config that no
process serves is exactly the shape this file exists to catch, so the port in
the scrape target is compared with the port the process serves.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

#: Modules that run in the scheduler container rather than the backend.
SCHEDULER_MODULES = ("app/scheduler.py", "app/services/environmental/", "run_scheduler.py")

JOB_OF_PROCESS = {"backend": "sora-app", "scheduler": "sora-scheduler"}


# --------------------------------------------------------------- derivation


def _declared() -> dict[str, list[str]]:
    """Metric name -> label names, from `app/prom_metrics.py`."""
    out = {}
    for node in ast.walk(ast.parse((ROOT / "app" / "prom_metrics.py").read_text())):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if getattr(node.value.func, "id", None) not in {"Counter", "Gauge", "Histogram"}:
                continue
            if not node.value.args:
                continue
            labels: list[str] = []
            for arg in node.value.args[2:]:
                if isinstance(arg, ast.List):
                    labels = [e.value for e in arg.elts]
            for kw in node.value.keywords:
                if kw.arg == "labelnames" and isinstance(kw.value, ast.List):
                    labels = [e.value for e in kw.value.elts]
            out[node.value.args[0].value] = labels
    return out


def _writers() -> dict[str, set[str]]:
    """Metric name -> files that write it, resolving `import X as Y`."""
    by_var = {}
    for node in ast.walk(ast.parse((ROOT / "app" / "prom_metrics.py").read_text())):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if getattr(node.value.func, "id", None) in {"Counter", "Gauge", "Histogram"}:
                var = getattr(node.targets[0], "id", None)
                if var and node.value.args:
                    by_var[var] = node.value.args[0].value

    writers: dict[str, set[str]] = {m: set() for m in by_var.values()}
    for path in list((ROOT / "app").rglob("*.py")) + [ROOT / "run_scheduler.py"]:
        if not path.exists() or path.name == "prom_metrics.py":
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        alias = {v: v for v in by_var}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and "prom_metrics" in (node.module or ""):
                for a in node.names:
                    if a.name in by_var:
                        alias[a.asname or a.name] = a.name
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) in {
                "set", "inc", "observe", "dec"
            }:
                base = node.func.value
                while isinstance(base, ast.Call):
                    base = base.func
                while isinstance(base, ast.Attribute):
                    base = base.value
                if getattr(base, "id", None) in alias:
                    writers[by_var[alias[base.id]]].add(str(path.relative_to(ROOT)))
    return writers


#: The two markers that bound the table. Missing either one is a real failure --
#: the section was rewritten or removed -- and it must arrive as a red test, not
#: as a collection error: `pytest` then reports zero tests, which a harness
#: reading only the summary line cannot tell from green. Found exactly that way,
#: mutating the second marker away and seeing no output at all.
TABLE_START = "**Key metrics.**"
TABLE_END = "**Always name the job.**"


def _documented_rows() -> list[dict]:
    """The table in CLAUDE.md, parsed. Returns [] if the section is gone."""
    text = (ROOT / "CLAUDE.md").read_text()
    if TABLE_START not in text or TABLE_END not in text:
        return []
    start = text.index(TABLE_START)
    table = text[start : text.index(TABLE_END)]

    rows = []
    for line in table.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 4 or cells[0].startswith(("metric", "---")):
            continue
        name = cells[0].strip("`")
        labels = [] if cells[1] == "— (histogram)" or cells[1] == "—" else [
            c.strip().strip("`") for c in cells[1].split(",")
        ]
        process = cells[2].split(",")[0].strip()
        rows.append({"metric": name, "labels": labels,
                     "process": process, "job": cells[3].strip("`")})
    return rows


# ------------------------------------------------------- the three properties


def test_the_table_is_not_empty_and_names_the_metrics_it_should():
    """Negative control. Every check below passes vacuously on an empty table."""
    text = (ROOT / "CLAUDE.md").read_text()
    assert TABLE_START in text, f"CLAUDE.md no longer contains {TABLE_START!r}"
    assert TABLE_END in text, (
        f"CLAUDE.md no longer contains {TABLE_END!r} -- the warning that a bare "
        "metric name is ambiguous across the two scrape jobs"
    )

    rows = _documented_rows()
    assert len(rows) >= 5, f"only {len(rows)} rows parsed from CLAUDE.md; the parser broke"
    names = {r["metric"] for r in rows}
    assert "sora_predictions_total" in names
    assert "sora_drift_detected_total" in names
    assert "sora_retrain_total" in names


def test_the_derivation_itself_finds_something():
    """The other negative control: a broken scanner reports no violations."""
    declared, writers = _declared(), _writers()

    assert len(declared) >= 20, f"only {len(declared)} metrics declared; the scan broke"
    assert writers["sora_predictions_total"], "no writer found for a metric that has one"


@pytest.mark.parametrize("row", _documented_rows(), ids=lambda r: r["metric"])
def test_each_documented_metric_is_declared_written_and_reachable(row):
    metric, labels, process, job = row["metric"], row["labels"], row["process"], row["job"]
    declared, writers = _declared(), _writers()

    # 1. declared
    assert metric in declared, f"{metric} is documented and not declared"
    assert declared[metric] == labels, (
        f"{metric} labels: documented {labels}, declared {declared[metric]}"
    )

    # 2. written
    files = sorted(writers[metric])
    assert files, f"{metric} is documented and nothing writes it"

    # 3. the process it is attributed to is the one that writes it
    in_scheduler = all(f.startswith(SCHEDULER_MODULES) for f in files)
    actual_process = "scheduler" if in_scheduler else "backend"
    assert actual_process == process, (
        f"{metric} is documented as written in the {process}, but its writers are {files}"
    )
    assert JOB_OF_PROCESS[actual_process] == job, (
        f"{metric} is written in the {actual_process} but documented under job {job!r}"
    )


@pytest.mark.parametrize("job", sorted(set(JOB_OF_PROCESS.values())))
def test_each_documented_job_is_a_target_something_actually_serves(job):
    """Not "the YAML mentions it". A job named in the config that no process
    serves looks identical to a working one until you query it."""
    config = yaml.safe_load((ROOT / "infra" / "prometheus.yml").read_text())
    by_job = {c["job_name"]: c["static_configs"][0]["targets"] for c in config["scrape_configs"]}

    assert job in by_job, f"{job} is documented and not scraped"
    target = by_job[job][0]
    host, port = target.split(":")

    if host == "scheduler":
        served = ast.parse((ROOT / "app" / "scheduler_metrics.py").read_text())
        default = next(
            node.value.value for node in ast.walk(served)
            if isinstance(node, ast.Assign)
            and getattr(node.targets[0], "id", "") == "DEFAULT_PORT"
        )
        assert int(port) == default, f"scraping :{port}, the process serves :{default}"
    else:
        entrypoint = (ROOT / "entrypoint.sh").read_text()
        assert f"-b 0.0.0.0:{port}" in entrypoint, (
            f"scraping {target}, but entrypoint.sh binds a different port"
        )


def test_the_document_says_to_name_the_job():
    """Both processes export the same names. A query without `job` is ambiguous,
    and one of the two series is permanently zero -- which reads as a real
    measurement of nothing happening."""
    text = (ROOT / "CLAUDE.md").read_text()

    assert "Always name the job" in text
    assert 'job="sora-scheduler"' in text

    declared = set(_declared())
    writers = _writers()
    shared = [
        m for m in declared
        if writers[m] and all(f.startswith(SCHEDULER_MODULES) for f in writers[m])
    ]
    assert shared, "no scheduler-only metric found, so the warning would be pointless"


def test_no_metric_name_from_the_old_list_is_still_claimed():
    """The three names that never existed must not come back."""
    text = (ROOT / "CLAUDE.md").read_text()
    declared = set(_declared())

    for phantom in ("sora_prediction_latency_seconds", "sora_retrain_success"):
        assert phantom not in declared, f"{phantom} exists now; this test is stale"

    # The names may appear in the block that quotes the old list -- that block
    # is the record of what was wrong. What must not appear is one of them
    # presented as a metric, i.e. in the table itself.
    table_names = {row["metric"] for row in _documented_rows()}
    for phantom in ("sora_prediction_latency_seconds", "sora_retrain_success",
                    "sora_retrain_success/failure", "sora_drift_detected"):
        assert phantom not in table_names, f"{phantom} is back in the table"

    # And `sora_drift_detected` -- a real Python variable, not a metric name --
    # must not be offered as one in the old bullet form.
    assert re.search(r"^- `sora_drift_detected`", text, re.M) is None, (
        "the old bare name is still presented as a metric"
    )
