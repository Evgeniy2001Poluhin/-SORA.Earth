"""What `predictions_log` holds, pinned, because its name says otherwise.

`docs/DEVELOPMENT_ROADMAP.md` defines the project as ready when a specialist can
follow an output back along the whole chain, and `forecast/decision → audit
trail` is its last link. Measured 2026-09-20, that link does not hold for the
endpoints the product is named for.

Three functions are called `log_prediction` and they do different things:

    app.mlflow_tracking.log_prediction   MLflow telemetry on a thread; a no-op
                                         under SORA_OFFLINE. Used by /predict,
                                         /predict/neural, /predict/stacking.
    app.main.log_prediction              writes the row in the predictions_log
                                         TABLE. One caller: /evaluate.
    app.obs.request_log.log_prediction   a JSONL file, and the only one of the
                                         three that takes model_version. Used by
                                         app/ml/routes.py, and off unless
                                         SORA_REQUEST_LOG=1.

So the durable table named for predictions is fed entirely by `/evaluate`, and
`/predict` leaves no row in it. What `/predict` does leave is `_log_csv` writing
four input columns -- no probability, no prediction, no timestamp, no model --
to `data/predictions_log.csv`, the file #281 established is destroyed on every
redeploy, rollback and `--force-recreate`.

This is not hypothetical confusion. `app/api/evaluate.py` carries a comment from
whoever removed that call once, having read it as duplicate telemetry, and found
out otherwise only because mutation testing left every assertion green when the
call came back.

These tests do not judge the wiring. They pin it, so that changing it is a
decision someone makes on purpose, and so the prose describing it has to move at
the same time.
"""
import ast
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def _collapse(text):
    """Hard-wrapped files split phrases across lines; match on prose, not layout."""
    return re.sub(r"\s+", " ", text)


def _prediction_log_constructions():
    """Every `PredictionLog(...)` call in app/, by file and line.

    Parsed rather than grepped: a name in a docstring or a comment is not a
    write, and the point here is to count writes.
    """
    found = []
    for root, _dirs, files in os.walk(os.path.join(REPO_ROOT, "app")):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            try:
                tree = ast.parse(open(path, encoding="utf-8").read())
            except SyntaxError:  # pragma: no cover
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                target = getattr(func, "id", None) or getattr(func, "attr", None)
                if target == "PredictionLog":
                    found.append((os.path.relpath(path, REPO_ROOT), node))
    return found


def test_exactly_one_place_writes_a_row_to_the_table():
    """One writer is what makes the rest of this file's claims checkable.

    If a second appears, every statement here about what the table contains --
    and the drift sample drawn from it -- has to be re-established rather than
    assumed.
    """
    constructions = _prediction_log_constructions()
    files = sorted({path for path, _ in constructions})

    assert files == ["app/main.py"], (
        f"PredictionLog rows are now written from {files}. The documentation "
        f"and the drift sample both describe a table fed from one place; "
        f"update them together with this list."
    )


def test_the_row_writer_is_called_only_from_evaluate():
    """`app.main.log_prediction` has one caller, and it is not a predict route."""
    callers = []
    for root, _dirs, files in os.walk(os.path.join(REPO_ROOT, "app")):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            source = open(path, encoding="utf-8").read()
            if re.search(r"from app\.main import[^\n]*\blog_prediction\b", source):
                callers.append(os.path.relpath(path, REPO_ROOT))

    assert callers == ["app/api/evaluate.py"], (
        f"the writer of the predictions_log table is imported by {callers}. "
        f"Adding a caller changes what the table contains and what drift "
        f"samples from it -- say so in CLAUDE.md in the same commit."
    )


def test_the_predict_routes_use_a_different_function_of_the_same_name():
    """The collision that has already cost someone a wrong reading.

    `app/api/predict.py` imports `log_prediction` from `app.mlflow_tracking`.
    Reading the call and assuming it writes the table is the mistake; reading it
    and assuming it is redundant telemetry is the opposite mistake, and
    `app/api/evaluate.py` records that one being made.
    """
    predict = _read("app", "api", "predict.py")

    assert re.search(r"from app\.mlflow_tracking import[^\n]*\blog_prediction\b",
                     predict), (
        "app/api/predict.py no longer takes log_prediction from "
        "app.mlflow_tracking; whichever symbol it binds now, the audit-trail "
        "note in CLAUDE.md describes the old one"
    )
    assert not re.search(r"from app\.main import[^\n]*\blog_prediction\b", predict), (
        "app/api/predict.py now imports the row writer. That is a change to "
        "what the table holds and to the drift sample -- welcome, but it has to "
        "move the documentation with it"
    )


def test_the_documentation_says_what_the_table_actually_holds():
    """Bound to the wiring, both directions.

    While `/predict` does not write a row, CLAUDE.md has to say so -- the
    endpoint that serves the table is called `predictions-log` and the command
    beside it used to read "View recent predictions", which is the reading this
    prevents. If `/predict` is ever wired in, the note must go.
    """
    predict = _read("app", "api", "predict.py")
    predict_writes_rows = bool(
        re.search(r"from app\.main import[^\n]*\blog_prediction\b", predict))

    claude = _collapse(_read("CLAUDE.md"))
    says_evaluate_only = "fed entirely by `/evaluate`" in claude

    if predict_writes_rows:
        assert not says_evaluate_only, (
            "CLAUDE.md says the table is fed entirely by /evaluate, and "
            "app/api/predict.py now writes rows too"
        )
    else:
        assert says_evaluate_only, (
            "CLAUDE.md does not record that predictions_log is fed entirely by "
            "/evaluate. Someone reading `analytics/predictions-log` will take "
            "it for a record of predictions, and so will anyone reading the "
            "drift sample drawn from it."
        )


def test_the_documentation_says_whether_model_version_carries_anything():
    """A column that looks like provenance and is a literal.

    `PredictionLog.model_version` defaults to "v2.0" and the one construction
    never assigns it, so every row carries that string -- and
    `/api/v1/analytics/predictions-log` serves it as though it identified a
    model.

    This docstring first said the rows "describe `/evaluate`, which runs a
    hardcoded ESG formula rather than a model at all", and used that to argue
    the blank was defensible. It is false. `calculate_esg` runs
    `rf_model.predict_proba` unconditionally and returns `success_probability`;
    the writer stores `result.get("probability") or result.get("success_probability")`
    and `calculate_esg` has no `probability` key, so the column holds the
    serving champion's output. The score is a formula, the probability is not.

    What this test requires is unchanged: that the document states which of the
    two the column is. The false premise was in the reasoning, not the check.
    """
    constructions = _prediction_log_constructions()
    assert constructions, "no PredictionLog construction found to inspect"

    assigns_version = any(
        any(kw.arg == "model_version" for kw in node.keywords)
        for _path, node in constructions
    )

    claude = _collapse(_read("CLAUDE.md"))
    calls_it_constant = "`model_version` on every row is the column default" in claude

    if assigns_version:
        assert not calls_it_constant, (
            "CLAUDE.md says model_version is always the column default, and the "
            "writer now sets it"
        )
    else:
        assert calls_it_constant, (
            "nothing assigns PredictionLog.model_version, so every row carries "
            "the literal default and the analytics endpoint serves it as "
            "provenance. CLAUDE.md has to say so."
        )


def test_drift_samples_the_table_without_filtering_by_endpoint():
    """What the drift check is actually looking at.

    `_recent_predictions` takes the last `window` rows in id order with no
    `endpoint` filter, so the sample is whatever the table holds -- today,
    `/evaluate` inputs. That is not wrong, and it is not what the function's own
    name suggests. Pinned so a filter added later is deliberate: filtering to
    `endpoint == "predict"` would silently reduce the sample to nothing.
    """
    drift = _read("app", "api", "drift.py")
    body = drift.split("def _recent_predictions", 1)[1].split("\ndef ", 1)[0]

    assert "PredictionLog.id.desc()" in body, (
        "the drift sample is no longer the most recent rows by id; the ordering "
        "is what makes it a sample of recent traffic rather than of the oldest"
    )
    assert "endpoint" not in body, (
        "the drift query now mentions `endpoint`. If it filters on it, check "
        "what the filter matches: every row in the table is written by "
        "/evaluate, so filtering to predictions selects nothing and a KS test "
        "over an empty frame is not a verdict"
    )
