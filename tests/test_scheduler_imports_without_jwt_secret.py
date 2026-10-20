"""The scheduler imports without the API's auth configuration.

The scheduler container runs with SORA_ENV=production and no SORA_JWT_SECRET,
by design: background jobs have no use for the JWT secret. `app/auth.py` refuses
to import in that combination, which is the protection that must stay.

What broke: the scheduler lazily imported `app.api.infra` (observation coverage)
and `app.api.retrain` (retraining). Both import `app.auth`, so every 15-minute
coverage pass failed and the first real retrain would have too. The fix moved
the functions into `app/services/`. These tests pin that the scheduler's import
closure does not reach `app.auth` again.

Every import check runs in a separate interpreter with the production
environment, so the import is the real one rather than whatever this test
process happened to load earlier.
"""
import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: Every module that can execute inside the scheduler process.
SCHEDULER_SOURCES = (
    REPO / "app" / "scheduler.py",
    REPO / "app" / "services" / "environmental" / "scheduler_jobs.py",
    REPO / "run_scheduler.py",
)

#: The modules this change moved out of app/api. They must be among the imports.
SERVICE_MODULES = {
    "app.services.retraining",
    "app.services.observation_coverage",
}


def _is_module(dotted: str) -> bool:
    base = REPO.joinpath(*dotted.split("."))
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def _scheduler_import_targets() -> list[str]:
    """Every `app.*` module the scheduler sources import, found by AST.

    Covers imports at module level and inside functions alike, which is where
    the scheduler keeps most of its imports. `from app.x import y` contributes
    `app.x`, and also `app.x.y` when that is a module of its own.
    """
    targets: set[str] = set()
    for path in SCHEDULER_SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "app" or alias.name.startswith("app."):
                        targets.add(alias.name)
            elif (isinstance(node, ast.ImportFrom) and node.level == 0
                  and node.module
                  and (node.module == "app" or node.module.startswith("app."))):
                targets.add(node.module)
                for alias in node.names:
                    sub = f"{node.module}.{alias.name}"
                    if _is_module(sub):
                        targets.add(sub)
    return sorted(t for t in targets if _is_module(t))


def _scheduler_env(tmp_path: Path) -> dict:
    """The scheduler container's environment, minus the JWT secret.

    Every SORA_* variable from the developer's shell is dropped first, so only
    what is named here decides the outcome. Data and model roots point into
    tmp_path so nothing reads or writes the real runtime.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("SORA_")}
    env.update({
        "SORA_ENV": "production",
        "SORA_OFFLINE": "1",
        "RUN_SCHEDULER": "false",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'scheduler.db'}",
        "REDIS_URL": "",
        "SORA_DATA_DIR": str(tmp_path / "data"),
        "SORA_MODELS_DIR": str(tmp_path / "models"),
        "PYTHONPATH": str(REPO),
    })
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "models").mkdir(exist_ok=True)
    return env


def _run_python(code: str, env: dict, *args: str, timeout: int = 50):
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=timeout,
    )


def test_the_enumeration_is_not_empty_and_names_the_moved_modules():
    targets = _scheduler_import_targets()
    assert targets, "the AST walk found no app.* imports in the scheduler sources"
    missing = SERVICE_MODULES - set(targets)
    assert not missing, (
        f"the scheduler does not import {sorted(missing)}; the coverage and "
        "retrain paths have moved back toward app.api"
    )


@pytest.mark.parametrize("module", _scheduler_import_targets())
def test_scheduler_import_closure_member_imports_without_jwt_secret(module, tmp_path):
    result = _run_python(
        "import importlib, sys; importlib.import_module(sys.argv[1])",
        _scheduler_env(tmp_path), module,
    )
    assert result.returncode == 0, (
        f"`import {module}` fails in the scheduler's environment "
        f"(SORA_ENV=production, no SORA_JWT_SECRET):\n{result.stderr[-2000:]}"
    )


def test_app_auth_still_refuses_to_import_in_production_without_a_secret(tmp_path):
    result = _run_python(
        "import app.auth",
        _scheduler_env(tmp_path),
    )
    assert result.returncode != 0, "app.auth imported in production with no secret"
    assert "SORA_JWT_SECRET" in result.stderr, result.stderr[-2000:]


def test_coverage_job_publishes_the_three_gauges(monkeypatch):
    """The job reads the report and publishes it, with the denominator alongside."""
    import app.api.infra  # noqa: F401  (loaded, as in the API process, so its re-export is bound first)
    import app.scheduler as scheduler_module
    import app.services.observation_coverage as coverage
    from app.schemas import ObservationCoverage

    source, indicator = scheduler_module.COVERAGE_WATCH
    report = ObservationCoverage(
        source=source, indicator=indicator, required_per_day=19,
        days_examined=16, points_examined=21, complete_days=13,
        gap_count=3, gaps=[],
    )
    monkeypatch.setattr(coverage, "coverage_report", lambda **kwargs: report)

    result = scheduler_module.scheduled_observation_coverage()

    assert result == {
        "status": "ok", "gap_count": 3,
        "days_examined": 16, "points_examined": 21,
    }
    labels = {"source": source, "indicator": indicator}
    published = {
        "gap_days": scheduler_module.sora_observation_coverage_gap_days
        .labels(**labels)._value.get(),
        "days_examined": scheduler_module.sora_observation_coverage_days_examined
        .labels(**labels)._value.get(),
        "points_examined": scheduler_module.sora_observation_coverage_points_examined
        .labels(**labels)._value.get(),
    }
    assert published == {"gap_days": 3, "days_examined": 16, "points_examined": 21}


def test_closed_loop_with_drift_reaches_the_training_call_in_production_mode(tmp_path):
    """Artificial drift must lead to _do_retrain being called, on the real import path.

    The heavy parts are stubbed: the Redis lock, the drift measurement, the
    baseline, the journal rows, and the training function itself. The scheduler
    still resolves `_do_retrain` through its own lazy import, which is the path
    the defect broke.
    """
    script = textwrap.dedent('''
        import json
        from unittest.mock import patch
        from app.model_source import Baseline
        from app.schemas import ModelDriftMeasured

        calls = []

        def fake_retrain(min_samples=50, trigger_source="manual"):
            calls.append({"min_samples": min_samples, "trigger_source": trigger_source})
            return {"status": "success", "metrics": {"roc_auc": 0.90}}

        class _Lock:
            def __init__(self, *args, **kwargs):
                pass
            def acquire(self):
                return True
            def release(self):
                pass

        drift = ModelDriftMeasured(status="ok", drift_detected=True, window=50,
                                   observations=100, features={}, reason_code=None)

        import app.scheduler as sched
        with patch("app.locks.RedisLock", _Lock), \\
             patch("app.api.drift.compute_drift", return_value=drift), \\
             patch("app.model_source.serving_baseline",
                   return_value=Baseline(auc=0.95, source="active", ok=True)), \\
             patch("app.services.retraining._do_retrain", fake_retrain), \\
             patch.object(sched, "_start_retrain_log", return_value=99), \\
             patch.object(sched, "_finish_retrain_log", lambda **kwargs: None):
            result = sched.closed_loop_retrain(trigger_source="test")
        print(json.dumps({"result": result, "calls": calls}, default=str))
    ''')
    result = _run_python(script, _scheduler_env(tmp_path))
    assert result.returncode == 0, result.stderr[-2000:]

    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["calls"] == [{"min_samples": 50, "trigger_source": "test"}], (
        "the drift did not reach the training call; scheduler result: "
        f"{payload['result']}"
    )
    assert payload["result"].get("drift_detected") is True
    assert payload["result"].get("retrained") is True, payload["result"]
