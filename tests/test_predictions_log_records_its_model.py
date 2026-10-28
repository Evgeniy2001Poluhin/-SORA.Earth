"""`predictions_log.model_version` names the model that computed the probability.

Until 2026-10-26 every row carried the column default, the literal "v2.0", while
the probability beside it came from whichever champion was serving. These tests
pin what the writer records now, and that it records the model that actually
computed the number -- taken with the model, not re-read once a reload may have
replaced it.
"""
from types import SimpleNamespace
import itertools

import numpy as np
import pytest

from app.model_source import ModelSource


class _Main:
    """`app.main`, imported when first used rather than at collection.

    Importing it at module level made collection depend on which test file ran
    first: one that installs a stub `app.mlflow_tracking` leaves the import broken.
    """

    def __getattr__(self, name):
        import app.main as module
        return getattr(module, name)


main = _Main()

RUN = "3f1c2d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
OTHER_RUN = "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"
_budgets = itertools.count(301_000, 1_017)


def _project():
    # A fresh budget each call. /evaluate cached by payload once, and a cached
    # answer wrote no row; the cache is gone, the habit costs nothing.
    return {"name": "provenance", "budget": next(_budgets), "co2_reduction": 150,
            "social_impact": 7, "duration_months": 24, "category": "Solar Energy",
            "region": "Germany"}


def _real_main():
    import app.main as module
    return module


class _Model:
    """Stands in for the champion's estimator; optionally swaps the champion mid-call."""

    def __init__(self, p=0.7, on_predict=None):
        self.p, self.on_predict = p, on_predict

    def predict_proba(self, X):
        if self.on_predict:
            self.on_predict()
        return np.array([[1 - self.p, self.p]])


def _champion(model, source):
    return SimpleNamespace(model=model, source=source)


class _Saver:
    """POST /evaluate?save=true as one specialist; read back that owner's row.

    Only a save writes a predictions_log row now, and it is owned, so the row
    under test is found by its owner rather than as "the newest in the table".
    """

    def __init__(self, client, owner):
        self.client, self.owner = client, owner

    def __call__(self, payload):
        from tests._auth_tokens import bearer
        return self.client.post("/api/v1/evaluate?save=true", json=payload,
                                headers=bearer(self.owner.username))

    def last_row(self):
        from app.database import PredictionLog
        db = main.get_db_sync()
        try:
            return db.query(PredictionLog).filter(
                PredictionLog.endpoint == "evaluate",
                PredictionLog.owner_user_id == self.owner.id,
            ).order_by(PredictionLog.id.desc()).first()
        finally:
            db.close()


@pytest.fixture
def save(client, make_account):
    return _Saver(client, make_account(prefix="provenance"))


@pytest.fixture
def serving(monkeypatch):
    """Install a champion and its model together, as `reload_champion` leaves them."""
    def install(model, source):
        monkeypatch.setattr(_real_main(), "rf_model", model)
        monkeypatch.setattr(_real_main(), "champion", _champion(model, source))
    return install


# --- the identity itself --------------------------------------------------


def test_an_active_champion_is_recorded_by_its_run():
    m = _Model()
    assert main.model_identity(m, _champion(m, ModelSource("active", run_id=RUN, version="v"))) \
        == "active:" + RUN


def test_a_seed_champion_is_recorded_by_its_version():
    m = _Model()
    assert main.model_identity(m, _champion(m, ModelSource("seed", version="20260712_200332"))) \
        == "seed:20260712_200332"


def test_no_champion_is_unknown():
    assert main.model_identity(_Model(), None) == "unknown"


def test_a_model_that_is_not_the_champions_is_unknown():
    assert main.model_identity(_Model(), _champion(_Model(), ModelSource("active", run_id=RUN))) \
        == "unknown"


def test_a_champion_with_nothing_to_report_is_unknown():
    m = _Model()
    assert main.model_identity(m, _champion(m, ModelSource("seed", version=None))) == "unknown"


def test_a_value_longer_than_the_column_is_not_cut_short():
    m = _Model()
    assert main.model_identity(m, _champion(m, ModelSource("seed", version="x" * 60))) == "unknown"


# --- what /evaluate writes ------------------------------------------------


def test_evaluate_records_the_serving_model(save, serving):
    model = _Model()
    serving(model, ModelSource("active", run_id=RUN, version="20261020_010101"))

    r = save(_project())

    assert r.status_code == 201, r.text
    row = save.last_row()
    assert row.model_version == "active:" + RUN
    assert row.model_version != "v2.0"
    assert row.probability == pytest.approx(70.0)


def test_a_champion_replaced_after_the_prediction_does_not_rename_the_row(save, serving, monkeypatch):
    """The row names the model that computed it, not the one serving at write time."""
    replacement = _Model(p=0.2)

    def reload_now():
        monkeypatch.setattr(_real_main(), "rf_model", replacement)
        monkeypatch.setattr(_real_main(), "champion",
                            _champion(replacement, ModelSource("active", run_id=OTHER_RUN)))

    first = _Model(p=0.7, on_predict=reload_now)
    serving(first, ModelSource("active", run_id=RUN))

    r = save(_project())

    assert r.status_code == 201, r.text
    assert main.champion.source.run_id == OTHER_RUN, "the swap did not happen; the test proves nothing"
    row = save.last_row()
    assert row.probability == pytest.approx(70.0)
    assert row.model_version == "active:" + RUN


def test_a_reload_half_done_is_recorded_as_unknown(save, monkeypatch):
    """`reload_champion` assigns `rf_model` before `champion`; in between, nobody knows."""
    old, new = _Model(p=0.6), _Model(p=0.4)
    monkeypatch.setattr(_real_main(), "champion", _champion(old, ModelSource("active", run_id=RUN)))
    monkeypatch.setattr(_real_main(), "rf_model", new)

    r = save(_project())

    assert r.status_code == 201, r.text
    row = save.last_row()
    assert row.probability == pytest.approx(40.0)
    assert row.model_version == "unknown"


def test_the_macro_branch_is_recorded_as_a_formula(save, serving):
    serving(_Model(), ModelSource("active", run_id=RUN))
    payload = dict(_project(), co2_per_capita=5.0, renewable_share=30.0, gdp_per_capita=20000)

    r = save(payload)

    assert r.status_code == 201, r.text
    assert save.last_row().model_version == "formula:macro"


def test_the_row_cannot_be_built_without_being_told_the_model():
    """No default at all, so the old literal "v2.0" -- or any other -- cannot
    be recorded by a caller that forgot to say which model computed the row."""
    import inspect
    from app.services.evaluation_store import prediction_log_row
    default = inspect.signature(prediction_log_row).parameters["model_version"].default
    assert default is inspect.Parameter.empty


def test_reload_champion_publishes_the_champion_last():
    """The ordering the half-done test relies on: `champion` is assigned after `rf_model`."""
    import ast
    import inspect
    from app import model_loader

    tree = ast.parse(inspect.getsource(model_loader.reload_champion))
    targets = [t.attr for node in ast.walk(tree) if isinstance(node, ast.Assign)
               for t in node.targets if isinstance(t, ast.Attribute)
               and getattr(t.value, "id", None) == "running"]
    assert "rf_model" in targets and "champion" in targets
    assert targets.index("champion") > targets.index("rf_model")
