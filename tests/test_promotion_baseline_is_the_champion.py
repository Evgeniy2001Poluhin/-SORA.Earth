"""The promotion gate compares against the serving champion, not the last training row (#329).

The degradation check needs the AUC of the model that is actually serving. It
took it from `_get_current_metrics()`, the newest `retrain_log` row with
`status='success'` -- which is the most recently *trained* candidate, including
one the gate went on to reject and one no gate ruled on. So after a rejection the
baseline was the rejected candidate's own score, and a worse model could clear a
degradation check against it.

`serving_baseline()` reads the champion the loader would serve: the activated
model's own `metrics.json`, or the seed's when nothing is active. When the active
model is present but its metrics are missing or disagree with its manifest, it
refuses rather than guessing -- and `gated_decision` turns that refusal into a
rejected promotion, never a comparison against a stale training row.
"""
import json

import pytest


@pytest.fixture
def layout(tmp_path, monkeypatch):
    seed = tmp_path / "models"
    seed.mkdir()
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("SORA_MODELS_DIR", str(seed))
    monkeypatch.setenv("SORA_SEED_DIR", str(seed))
    monkeypatch.setenv("SORA_RUNTIME_DIR", str(runtime))
    return {"seed": seed, "runtime": runtime}


def _write_model(path, version, metrics):
    path.mkdir(parents=True, exist_ok=True)
    for name in ("model.pkl", "scaler.pkl", "best_threshold.pkl"):
        (path / name).write_bytes(b"x")
    (path / "meta.json").write_text(json.dumps({"retrained_at": version}))
    if metrics is not None:
        (path / "metrics.json").write_text(json.dumps(metrics))


def _activate(runtime, run_id, version, metrics):
    active = runtime / "active"
    _write_model(active, version, metrics)
    (active / "activation.json").write_text(
        json.dumps({"run_id": run_id, "model_version": version})
    )


# --------------------------------------------------------------- serving_baseline


def test_baseline_is_the_active_champions_metrics(layout):
    from app.model_source import serving_baseline

    _activate(layout["runtime"], "11111111-1111-1111-1111-111111111111",
              "20260901_000000", {"roc_auc": 0.95})

    baseline = serving_baseline()
    assert baseline.ok and baseline.source == "active"
    assert baseline.auc == 0.95


def test_baseline_fails_closed_when_the_champion_has_no_metrics(layout):
    from app.model_source import serving_baseline

    # A complete, usable active model, but no metrics.json to compare against.
    _activate(layout["runtime"], "22222222-2222-2222-2222-222222222222",
              "20260901_000000", metrics=None)

    baseline = serving_baseline()
    assert baseline.source == "active"
    assert baseline.ok is False, "an unmeasurable champion must not be silently skipped"
    assert baseline.auc is None
    assert baseline.reason


def test_baseline_fails_closed_when_manifest_and_meta_disagree(layout):
    from app.model_source import serving_baseline

    _activate(layout["runtime"], "33333333-3333-3333-3333-333333333333",
              "20260901_000000", {"roc_auc": 0.95})
    # The manifest now names a different version than meta.json records.
    active = layout["runtime"] / "active"
    (active / "activation.json").write_text(
        json.dumps({"run_id": "33333333-3333-3333-3333-333333333333",
                    "model_version": "20260815_120000"})
    )

    baseline = serving_baseline()
    assert baseline.ok is False and baseline.auc is None
    assert "version" in (baseline.reason or "").lower()


def test_baseline_is_the_seed_when_nothing_is_active(layout):
    from app.model_source import serving_baseline

    (layout["seed"] / "metrics.json").write_text(json.dumps({"roc_auc": 0.905}))

    baseline = serving_baseline()
    assert baseline.ok and baseline.source == "seed"
    assert baseline.auc == 0.905


def test_no_metrics_anywhere_is_a_first_model_not_a_refusal(layout):
    from app.model_source import serving_baseline

    baseline = serving_baseline()
    assert baseline.ok is True, "a first deployment has no predecessor to compare against"
    assert baseline.auc is None


# ------------------------------------------------------------------ gated_decision


def _baseline(ok, auc, reason=None, source="active"):
    from app.model_source import Baseline

    return Baseline(auc=auc, source=source, ok=ok, reason=reason)


def test_gated_decision_refuses_when_the_baseline_is_indeterminate():
    from app.promotion import gated_decision

    good = {"roc_auc": 0.99, "test_positive": 500, "test_negative": 500, "registry_ok": True}
    decision = gated_decision(good, _baseline(ok=False, auc=None, reason="champion metrics unreadable"))
    assert decision.promoted is False
    assert "baseline" in (decision.reject_reason or "").lower()


def test_gated_decision_compares_against_the_baseline_auc():
    from app.promotion import gated_decision

    # 0.85 is a step down from a 0.95 champion: refused for degradation, not promoted.
    candidate = {"roc_auc": 0.85, "test_positive": 500, "test_negative": 500, "registry_ok": True}
    down = gated_decision(candidate, _baseline(ok=True, auc=0.95))
    assert down.promoted is False and "degrad" in (down.reject_reason or "").lower()

    # Against a weaker baseline the same candidate clears the degradation rule.
    up = gated_decision(candidate, _baseline(ok=True, auc=0.84))
    assert up.promoted is True


def test_the_callers_take_the_baseline_from_the_serving_model(layout):
    """Neither promotion caller reads its baseline from the retrain journal."""
    import inspect

    import app.scheduler as scheduler
    import app.api.infra as infra

    for func in (scheduler.closed_loop_retrain, infra.auto_retrain_on_drift):
        source = inspect.getsource(func)
        assert "serving_baseline" in source, (
            "%s no longer derives its baseline from the serving champion" % func.__name__
        )
        assert "_get_current_metrics" not in source, (
            "%s still reads the baseline from the latest training row" % func.__name__
        )
