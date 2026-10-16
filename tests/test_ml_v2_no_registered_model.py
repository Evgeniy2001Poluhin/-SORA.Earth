"""GET /api/v2/model/version and POST /api/v2/model/reload must not 500
uncaught when no model has been registered under the configured
name/alias yet -- the state of every server before its first retrain
completes and registers a champion.

`registry_loader._load()` looks up `models:/{name}@{alias}` in the MLflow
Model Registry, a different thing entirely from the `models/model.pkl`
file that actually serves `/api/v1/predict`. Neither route function had a
try/except around it, so the registry's `RestException` ("Registered model
alias champion not found") propagated unhandled -- a 500 with no log line
at any level, since nothing on the way up ever called `logger.error` or
`logger.exception`. Reproduced live on production: every real page load
hit this (the frontend calls it "the primary, lightweight ... endpoint"
and treats a non-2xx as fatal for the call), and the operational error
counters had nothing to show for it -- confirmed by grepping ERROR-level
log lines over the same window: zero, despite ~220 real 500s in the
Prometheus counter.
"""
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from mlflow.exceptions import MlflowException
import pytest

from app.auth import create_access_token
from app.ml import routes


def test_model_version_returns_503_not_an_unhandled_500():
    with patch.object(routes, "get_version",
                       side_effect=MlflowException("Registered model alias champion not found")):
        with pytest.raises(HTTPException) as exc:
            routes.model_version()
    assert exc.value.status_code == 503


def test_model_reload_returns_503_not_an_unhandled_500():
    with patch.object(routes, "reload_model",
                       side_effect=MlflowException("Registered model alias champion not found")):
        with pytest.raises(HTTPException) as exc:
            routes.model_reload()
    assert exc.value.status_code == 503


def test_model_version_still_works_once_a_model_is_registered():
    """The fix must not turn a genuine success into an error -- only the
    specific MLflow lookup failure is caught."""
    with patch.object(routes, "get_alias", return_value="champion"), \
         patch.object(routes, "get_version", return_value="3"):
        result = routes.model_version()
    assert result == {
        "name": "esg-success-predictor",
        "alias": "champion",
        "version": "3",
    }


def test_a_non_mlflow_error_is_not_swallowed_as_a_registration_gap():
    """Only MlflowException is caught here -- a bug elsewhere in the call
    (e.g. a real TypeError) must still surface as itself, not be
    misreported as "no model registered yet."""
    with patch.object(routes, "get_version", side_effect=TypeError("boom")):
        with pytest.raises(TypeError):
            routes.model_version()


# --- no bundle loaded: 503, never the string "None" ---------------------------
#
# get_version() is None whenever no bundle is loaded (SORA_OFFLINE=1, or the
# loader declined). str(None) made that the version: a local backend answered
# 200 {"version": "None"} and the header read "@ champion vNone" on every page.
# Both routes now answer 503 with the same detail shape as their other
# not-available branches.

@pytest.fixture
def ml_client():
    """The v2 router alone. The `client` fixture imports app.main (torch, SHAP,
    transformers), which on a cold run costs more than CI's 60s timeout; these
    tests touch only the router and its require_admin dependency."""
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def _admin_headers():
    return {"Authorization": f"Bearer {create_access_token({'sub': 'admin', 'role': 'admin'})}"}


def test_model_version_answers_503_when_no_bundle_is_loaded(ml_client):
    with patch.object(routes, "get_version", return_value=None):
        r = ml_client.get("/api/v2/model/version")
    assert r.status_code == 503
    assert r.json() == {"detail": "no model bundle loaded"}
    assert "None" not in r.text


def test_model_version_still_answers_200_with_the_real_version(ml_client):
    with patch.object(routes, "get_version", return_value="3"), \
         patch.object(routes, "get_alias", return_value="champion"):
        r = ml_client.get("/api/v2/model/version")
    assert r.status_code == 200
    assert r.json() == {"name": "esg-success-predictor", "alias": "champion", "version": "3"}


def test_model_reload_answers_503_when_no_bundle_is_loaded(ml_client):
    with patch.object(routes, "reload_model", return_value=None), \
         patch.object(routes, "get_version", return_value=None):
        r = ml_client.post("/api/v2/model/reload", headers=_admin_headers())
    assert r.status_code == 503
    assert r.json() == {"detail": "no model bundle loaded"}
    assert "None" not in r.text


def test_model_reload_still_answers_200_with_the_real_version(ml_client):
    with patch.object(routes, "reload_model", return_value=None), \
         patch.object(routes, "get_version", return_value="4"):
        r = ml_client.post("/api/v2/model/reload", headers=_admin_headers())
    assert r.status_code == 200
    assert r.json() == {"status": "reloaded", "version": "4"}
