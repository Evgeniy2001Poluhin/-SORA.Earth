"""Zero observations is not a verdict of stability, and one log directory.

`app/drift/detector.py` backs `GET /drift/features` and `GET /drift/predictions`.
Two defects, measured 2026-09-25 by running the real `report_features` from a
scratch directory:

**No live data reported "stable".**

    psi_v = psi(ref_counts, live_counts) if live_counts.sum() else 0.0
    ks_stat, ks_p = ks(stats["sample_ref"], vals) if len(vals) else (0.0, 1.0)
    ...
    "status": status_from_psi(psi_v),   # < 0.10 -> "stable"

An empty live sample gives PSI exactly 0.0, which maps to "stable":

    n_live_total: 0
       budget   numeric      psi=0.0 status='stable' n_live=0
       region   categorical  psi=0.0 status='stable' n_live=0

`n_live: 0` sits in the same object, so the denominator was not lost — but the
*verdict* is what a dashboard or an alert reads, and it said the model was fine.
This is the rule the repository already records for the other check in this
platform: a check that could not run is not "no drift".

**The writer's log directory was configurable and the reader's was not.**

    app/obs/request_log.py:  LOG_DIR = Path(os.getenv("SORA_REQUEST_LOG_DIR", "output/request_log"))
    app/drift/detector.py:   LOG_DIR = Path("output/request_log")

Measured: with `SORA_REQUEST_LOG_DIR=elsewhere` and rows written there, the
detector read `output/request_log` and reported `n_live_total: 0` with both
features "stable". Moving the log with one variable turned the drift report into
permanent calm, silently. One path, two definitions, one of which knew about the
setting.

Neither is reachable in production today: `output/ref_stats.json` is in neither
the tree nor the image (`output/` is in `.dockerignore`), so the routes answer a
truthful 404, and the request log is off by default. The short, natural path to
it is building the reference on the server so the drift page works, without
turning the log on.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

REF = {
    "n_samples": 1000,
    "model_version": "rf-v1",
    "numeric": {"budget": {
        "bin_edges": [0, 50000, 100000, 200000, 1000000],
        "counts_ref": [250, 250, 250, 250],
        "sample_ref": [10000, 60000, 120000, 300000, 90000, 40000],
    }},
    "categorical": {"region": {"Germany": 0.5, "India": 0.5}},
    "prediction": {
        "bin_edges": [0.0, 0.25, 0.5, 0.75, 1.0],
        "counts_ref": [250, 250, 250, 250],
        "sample_ref": [0.1, 0.3, 0.6, 0.9, 0.45, 0.2],
    },
}


@pytest.fixture
def reference(tmp_path, monkeypatch):
    """A built reference and an empty log directory, both under tmp_path.

    Patched on the module rather than by changing the working directory: the
    paths are module-level and relative, so a chdir would make the test depend
    on import order.
    """
    from app.drift import detector

    ref_path = tmp_path / "ref_stats.json"
    ref_path.write_text(json.dumps(REF), encoding="utf-8")
    log_dir = tmp_path / "request_log"
    log_dir.mkdir()

    monkeypatch.setattr(detector, "REF_PATH", ref_path)
    monkeypatch.setattr(detector, "LOG_DIR", log_dir)
    return log_dir


def _row(when=None, **features):
    """One line in the shape `app/obs/request_log.log_prediction` writes.

    Written to match the writer, not to match what the reader happens to touch:
    the first version of this fixture carried bare feature keys and no `pred`,
    and the reader raised KeyError -- a test failing on its own fixture rather
    than on the defect.
    """
    when = when or datetime.now(timezone.utc)
    return {
        "v": 1,
        "ts": when.isoformat(timespec="milliseconds"),
        "model": {"alias": "champion", "version": "rf-v1"},
        "features": dict(features),
        "pred": {"prob": 0.42, "label": 0},
        "latency_ms": 12.5,
    }


def _write_row(log_dir, when=None, **features):
    when = when or datetime.now(timezone.utc)
    path = log_dir / f"{when:%Y-%m-%d}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_row(when, **features)) + "\n")


def test_an_empty_window_is_not_reported_as_stable(reference):
    from app.drift.detector import report_features

    report = report_features(window_hours=24)

    assert report["n_live_total"] == 0, "this test needs an empty window"
    assert report["features"], "no features were reported, so this asserts nothing"
    for feature in report["features"]:
        assert feature["n_live"] == 0, feature
        assert feature["status"] != "stable", (
            f"{feature['feature']} reported 'stable' on zero observations; "
            f"a check that could not run is not a passing check"
        )
        assert feature["status"] == "not_measured", feature


def test_a_populated_window_is_still_judged(reference):
    """The control. Without it the refusal above would also hold on a detector
    that had stopped reporting "stable" at all."""
    from app.drift.detector import report_features

    for _ in range(40):
        _write_row(reference, budget=60000, region="Germany")

    report = report_features(window_hours=24)

    assert report["n_live_total"] == 40
    statuses = {f["status"] for f in report["features"]}
    assert "not_measured" not in statuses, report["features"]
    assert statuses <= {"stable", "warning", "drift"}, statuses


def test_a_row_outside_the_window_does_not_count_as_data(reference):
    """Rows exist, none of them recent: still not a measurement."""
    from app.drift.detector import report_features

    stale = datetime.now(timezone.utc) - timedelta(hours=48)
    _write_row(reference, when=stale, budget=60000, region="Germany")

    report = report_features(window_hours=24)

    assert report["n_live_total"] == 0
    for feature in report["features"]:
        assert feature["status"] == "not_measured", feature


def test_the_detector_reads_the_directory_the_writer_writes():
    """One definition of the path, not two.

    The writer honours `SORA_REQUEST_LOG_DIR`; the reader hardcoded the default,
    so moving the log with that variable made every feature read "stable".
    """
    from app.drift import detector
    from app.obs import request_log

    # Identity, not equality. Both default to "output/request_log", so `==`
    # held while the reader ignored SORA_REQUEST_LOG_DIR entirely -- a check
    # that could not fail. One object means one path by construction.
    assert detector.LOG_DIR is request_log.LOG_DIR, (
        f"the detector reads {detector.LOG_DIR} while the writer writes to "
        f"{request_log.LOG_DIR}; a drift report over an empty directory is not "
        f"a drift report"
    )


def test_predictions_report_agrees_with_the_features_report(reference):
    """The sibling route has the same shape and the same hazard."""
    from app.drift.detector import report_predictions

    empty = report_predictions(window_hours=24)
    assert empty["n_live"] == 0, empty
    assert empty["status"] == "not_measured", (
        f"the prediction-drift report called zero observations "
        f"{empty['status']!r}"
    )

    for _ in range(40):
        _write_row(reference, budget=60000, region="Germany")

    populated = report_predictions(window_hours=24)
    assert populated["n_live"] == 40, populated
    assert populated["status"] in {"stable", "warning", "drift"}, populated
