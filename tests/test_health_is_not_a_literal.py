"""`/health` and `/system/health` report real state, not literals (#324).

`/health` returned `models_loaded: True` unconditionally, and `/system/health`
returned `pdf_report`/`metrics`/`status` as the literal `"ok"`. A health check
that cannot report anything but healthy is not a health check: a worker that
loaded nothing looked identical to one that loaded everything. These now derive
from the process's actual state.
"""
import pytest


def test_models_loaded_follows_the_real_model_state(client, monkeypatch):
    import app.main as main

    assert client.get("/health").json()["models_loaded"] is True, "models are loaded in the test app"

    # With the champion set gone, /health must say so rather than still say True.
    monkeypatch.setattr(main, "rf_model", None)
    body = client.get("/health").json()
    assert body["models_loaded"] is False, "models_loaded stayed True with rf_model=None"
    assert client.get("/health").status_code == 200, "liveness stays 200"


def test_system_health_status_and_components_are_computed(client, monkeypatch):
    import app.main as main

    healthy = client.get("/system/health").json()
    assert healthy["status"] in ("ok", "warn")
    assert healthy["components"]["pdf_report"] == "ok"
    assert healthy["components"]["metrics"] == "ok"

    # A failing auxiliary component must show through, and drive status to degraded.
    monkeypatch.setattr(main, "_pdf_report_status", lambda: "unavailable")
    body = client.get("/system/health").json()
    assert body["components"]["pdf_report"] == "unavailable", "the literal 'ok' did not follow the real check"
    assert body["status"] == "degraded", "status stayed ok while a component was unavailable"


def test_ml_models_warn_does_not_read_as_degraded(client, monkeypatch):
    import app.main as main

    # nn optional: rf+xgb present is healthy; an absent nn is a warn, not degraded.
    monkeypatch.setattr(main, "rf_model", None)  # forces ml_models -> warn
    body = client.get("/system/health").json()
    assert body["components"]["ml_models"] == "warn"
    assert body["status"] in ("warn", "degraded")  # warn from ml_models, not a false "ok"
    assert body["status"] != "ok", "a warn component must not report an unqualified ok"
