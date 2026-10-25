"""Execute reload with synthetic objects, never pickle files or app startup."""
import ast
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

from fastapi import HTTPException
import pytest

import app
from app import model_loader


@pytest.fixture
def serving(monkeypatch):
    running = ModuleType("app.main")
    # conftest.clear_dependency_overrides imports `app` from app.main on teardown,
    # and it runs before monkeypatch restores sys.modules.
    running.app = SimpleNamespace(dependency_overrides={})
    running.rf_model = object()
    running.explainer_shap = object()
    running.make_features_base = lambda value: SimpleNamespace(columns=["budget"])
    monkeypatch.setitem(sys.modules, "app.main", running)
    monkeypatch.setattr(app, "main", running, raising=False)
    champion = SimpleNamespace(model=object(), scaler=object(), best_threshold=0.5,
                               meta={"version": "new"}, metrics={},
                               source=SimpleNamespace(run_id="synthetic-run", version="new"))
    calls = []

    def load(*, recover_first):
        calls.append(recover_first)
        return champion

    monkeypatch.setattr(model_loader, "load_champion", load)
    return running, champion, calls


def endpoint():
    """Compile the real handler body, isolating unrelated router imports/startup."""
    path = Path(__file__).resolve().parents[1] / "app/api/predict.py"
    tree = ast.parse(path.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "shap_explain")
    function.decorator_list = []
    namespace = {"HTTPException": HTTPException, "Project": object, "_to_legacy": lambda value: value}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["shap_explain"]


def test_failed_rebuild_does_not_explain_new_model_with_old_explainer(serving, monkeypatch, caplog):
    running, champion, calls = serving
    old_explainer = running.explainer_shap

    def broken(model):
        assert model is champion.model
        raise RuntimeError("synthetic unsupported model")

    monkeypatch.setitem(sys.modules, "shap", SimpleNamespace(TreeExplainer=broken))
    source = model_loader.reload_champion()
    assert running.rf_model is champion.model
    assert running.scaler is champion.scaler
    assert running.model_source is source is champion.source
    assert calls == [True]
    assert running.explainer_shap is None, "stale explainer survived champion replacement"
    assert running.explainer_shap is not old_explainer
    assert "SHAP could not be rebuilt" in caplog.text
    with pytest.raises(HTTPException) as exc:
        endpoint()(object())
    assert exc.value.status_code == 503


def test_successful_rebuild_uses_new_model_and_preserves_response(serving, monkeypatch):
    running, champion, _ = serving
    values = SimpleNamespace(tolist=lambda: [0.25])
    explainer = SimpleNamespace(shap_values=lambda features: [[values], [values]])
    models = []

    def build(model):
        models.append(model)
        return explainer

    monkeypatch.setitem(sys.modules, "shap", SimpleNamespace(TreeExplainer=build))
    model_loader.reload_champion()
    assert models == [champion.model]
    assert running.explainer_shap is explainer
    assert endpoint()(object()) == {"feature_names": ["budget"], "shap_values": [0.25]}


def test_later_reload_recovers_explanation_after_failed_rebuild(serving, monkeypatch):
    running, _, _ = serving

    def broken(model):
        raise RuntimeError("synthetic first attempt")

    module = SimpleNamespace(TreeExplainer=broken)
    monkeypatch.setitem(sys.modules, "shap", module)
    model_loader.reload_champion()
    assert running.explainer_shap is None
    replacement = object()
    module.TreeExplainer = lambda model: replacement
    model_loader.reload_champion()
    assert running.explainer_shap is replacement


def test_champion_load_failure_preserves_serving_pair(serving, monkeypatch):
    running, _, _ = serving
    old_pair = (running.rf_model, running.explainer_shap)

    def broken(*, recover_first):
        raise RuntimeError("synthetic champion load failure")

    monkeypatch.setattr(model_loader, "load_champion", broken)
    with pytest.raises(RuntimeError, match="champion load failure"):
        model_loader.reload_champion()
    assert (running.rf_model, running.explainer_shap) == old_pair


def test_unavailable_handler_refuses_before_feature_work(serving):
    running, _, _ = serving
    running.explainer_shap = None

    def unexpected(value):
        raise AssertionError("features should not be built when SHAP is unavailable")

    running.make_features_base = unexpected
    with pytest.raises(HTTPException) as exc:
        endpoint()(object())
    assert exc.value.status_code == 503
