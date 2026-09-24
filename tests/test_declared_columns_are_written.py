"""A column nothing writes is a field that looks like data and is not.

This repository has found the same defect three times, and `CLAUDE.md` names
two of them in its own text:

```
retrain_log.data_version   declared String(100), written by nobody until #354
year, quarter              computed from the retrain's own clock, so constant
                           across every row -- nine columns, seven that separate
legacy_hash_count()        cannot return anything but zero, by construction
```

Each one existed, looked like a feature, and did not do what its name said. A
reader querying `data_version` before #354 got NULL on every row and had no way
to tell "not recorded" from "recorded as nothing".

So the set of columns nothing writes is pinned here. Adding one is allowed --
schema often lands before its writer -- but it has to be named below with a
reason, which is the point at which someone says out loud whether the writer is
coming.

## The detector was wrong three times before it was right

Each was caught by a control or a mutation rather than by reading it:

1. **A regex over `col\\s*=` missed attribute assignment.** `(?<![\\w.])` was
   there to avoid matching `Model.col == x` in queries, and it also excluded
   `row.metrics_json = ...`, which is how most writes in this codebase happen.
   `metrics_json` was reported dead while `app/registry_retry.py` and
   `app/scheduler.py` both write it.

2. **Columns with a default are written by the ORM, not by any statement.**
   `BatchResultDB.job_type` carries `default="batch_evaluate"` and was reported
   dead; measured on the live table, 13 rows of 13 are filled.

3. **A bare `data_version = None` is a local variable, not a column write.**
   Counting plain name assignment made the check pass after every real writer
   of `data_version` had been deleted -- found by a mutation, not by reading.
   Only attribute assignment counts now.

The version here walks the AST for attribute assignment, constructor keywords
and dict literals, and skips any column declaring `default` or `server_default`.

Two of the three mutations that found these were themselves ill-posed on the
first attempt -- they removed one writer of a symbol that had several, so the
check was right to pass. A mutation has to remove *every* instance before its
survival means anything, and the run that produced the numbers below asserts
zero residual write forms before it starts.

## What this cannot see

A write through `setattr(row, name, value)` with a computed name, or a bulk
`update()` built from a dict this scan cannot resolve. Nothing in `app/` does
either today -- checked -- but a future one would read here as a dead column
and would have to be added to the allowlist with that as its reason. That is a
false positive, not a silent miss, which is the right direction for this check
to fail in.
"""
import ast
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATABASE = os.path.join(REPO, "app", "database.py")

#: Infrastructure columns: the ORM or the database supplies these.
SKIP = {"id", "created_at", "updated_at"}

#: Columns nothing writes, and why each is allowed to be here. A column must
#: earn its place in this list by someone stating what it is waiting for.
KNOWN_UNWRITTEN = {
    # #164, open: `country_indicator_history` records a value's vintage, but the
    # *period* assigned to that value is corrected in place -- the one exception
    # to the append-only trigger. These eight carry the provenance of that
    # assignment. The schema and its migration
    # (alembic/versions/e7b3c9d15f04_record_why_a_period_is_absent.py) landed
    # ahead of the writer because the form of the record -- an appended row or a
    # separate period-history table -- is the owner's open decision.
    "CountryIndicatorHistory.period_status",
    "CountryIndicatorHistory.period_run_id",
    "CountryIndicatorHistory.period_method",
    "CountryIndicatorHistory.period_rule_version",
    "CountryIndicatorHistory.period_candidates",
    "CountryIndicatorHistory.period_source_vintage",
    "CountryIndicatorHistory.period_response_sha256",
    "CountryIndicatorHistory.period_resolved_at",

    # No writer anywhere. `scheduled_run_ingesters` records a run's status and
    # its error, and how many rows the run actually wrote is exactly the number
    # an operator asks for first. Reported 2026-09-21; not fixed here, because
    # filling it is a change to the ingester runner rather than to this check.
    "IngesterRun.rows_written",

    # No writer anywhere, and measured empty: 491 rows in the local
    # `predictions_log`, 0 with a request_id. Correlating a prediction with the
    # request that caused it needs a request id to exist first, which is
    # middleware, not a column. Reported 2026-09-21.
    "PredictionLog.request_id",
}


def _parse(path):
    with open(path, encoding="utf-8") as handle:
        return ast.parse(handle.read())


def declared_columns():
    """`Model.column` for every Column in app/database.py, and those with defaults."""
    tree = _parse(DATABASE)
    columns, defaulted = {}, set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not (isinstance(item, ast.Assign) and isinstance(item.value, ast.Call)):
                continue
            func = item.value.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name != "Column":
                continue
            has_default = any(
                kw.arg in ("default", "server_default") for kw in item.value.keywords)
            for target in item.targets:
                if isinstance(target, ast.Name):
                    columns[f"{node.name}.{target.id}"] = target.id
                    if has_default:
                        defaulted.add(f"{node.name}.{target.id}")
    return columns, defaulted


def _source_files():
    for base in ("app", "scripts"):
        root = os.path.join(REPO, base)
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, names in os.walk(root):
            for name in sorted(names):
                if name.endswith(".py") and os.path.join(dirpath, name) != DATABASE:
                    yield os.path.join(dirpath, name)


def written_names():
    """Every name this codebase assigns, as an attribute, keyword or dict key."""
    found = set()
    for path in _source_files():
        try:
            tree = _parse(path)
        except SyntaxError:  # pragma: no cover - fails louder elsewhere
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    # Attribute assignment only. A bare `data_version = None`
                    # is a local variable that happens to share a column's
                    # name, and counting it made the check miss a column whose
                    # writer had been deleted -- measured with a mutation that
                    # removed every real write and still passed.
                    if isinstance(target, ast.Attribute):
                        found.add(target.attr)
            elif isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg:
                        found.add(keyword.arg)
            elif isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        found.add(key.value)
    return found


def unwritten_columns():
    columns, defaulted = declared_columns()
    written = written_names()
    return {
        qualified
        for qualified, attribute in columns.items()
        if attribute not in SKIP
        and qualified not in defaulted
        and attribute not in written
    }


def test_the_detector_sees_what_it_claims_to_see():
    """Both controls come from a false positive this check actually produced."""
    columns, defaulted = declared_columns()
    written = written_names()

    assert len(columns) > 100, (
        f"only {len(columns)} columns were parsed out of app/database.py; the "
        "comparison below would be against almost nothing"
    )

    # (1) attribute assignment: `row.metrics_json = ...` in registry_retry.py
    #     and scheduler.py. An earlier regex missed this and called it dead.
    assert "metrics_json" in written, (
        "metrics_json is written by app/registry_retry.py and app/scheduler.py "
        "as an attribute assignment; a scan that misses it will report most of "
        "this table as dead"
    )
    # (2) constructor keyword: `_finish_retrain_log(..., data_version=...)`
    assert "data_version" in written, (
        "data_version is written as a keyword argument; a scan that misses it "
        "would have reported the column #354 exists to fill"
    )
    # (3) column defaults: the ORM fills these, no statement does.
    assert "BatchResultDB.job_type" in defaulted, (
        "job_type declares default='batch_evaluate' and is filled by the ORM "
        "(measured: 13 of 13 rows); a scan that ignores defaults calls it dead"
    )


def test_every_declared_column_is_written_or_accounted_for():
    found = unwritten_columns()

    appeared = sorted(found - KNOWN_UNWRITTEN)
    assert not appeared, (
        "these columns are declared, have no default, and nothing writes them:\n  "
        + "\n  ".join(appeared)
        + "\n\nA column nothing writes reads as data and is not -- a query "
        "against it returns NULL on every row with no way to tell 'not "
        "recorded' from 'recorded as nothing'. If the writer is coming, add it "
        "to KNOWN_UNWRITTEN with what it is waiting for."
    )

    disappeared = sorted(KNOWN_UNWRITTEN - found)
    assert not disappeared, (
        "these columns are listed as unwritten but something now writes them:\n  "
        + "\n  ".join(disappeared)
        + "\n\nGood news, and the list should lose them -- left in place it "
        "would go on excusing a column that no longer needs excusing."
    )


def test_the_list_is_not_a_place_to_hide_a_whole_table():
    """A guard whose allowlist grows without limit stops being a guard.

    Not a hard cap on the count -- a legitimate feature can land eight columns
    at once, as #164 did. What is refused is a list that has drifted away from
    the schema: every entry must name a column that still exists.
    """
    columns, _ = declared_columns()
    unknown = sorted(entry for entry in KNOWN_UNWRITTEN if entry not in columns)
    assert not unknown, (
        "KNOWN_UNWRITTEN names columns that app/database.py no longer declares: "
        + ", ".join(unknown)
        + ". An entry for a column that was removed excuses nothing and hides "
        "the next one."
    )
