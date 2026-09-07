"""CLAUDE.md must name models and tables that exist.

The "Database Schema" section listed five models. Two of them did not exist:
`DriftLog` has no class and no table, and `RefreshJob` is called
`DataRefreshLog`. Two debugging commands in the same file ran
`SELECT * FROM drift_log`, which answers

    ERROR:  relation "drift_log" does not exist

and both were offered as the way to look at drift history -- read at the moment
somebody is already debugging something else. Measured on production
2026-09-07; filed as #282.

Third time in this document: #254 was three scheduler functions that do not
exist, written with call parentheses; #268 was four metric names of which three
did not exist, one with a Grafana alert configured against it. A list is read
as complete, so the invented entries are indistinguishable from the real ones
until somebody runs them.

The fix is a table in the document. This file is what keeps it true: the same
arrangement as `tests/test_scheduler_jobs_match_the_doc.py` for the job table
and `tests/test_key_metrics_contract.py` for the metrics.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "CLAUDE.md"
DATABASE = REPO / "app" / "database.py"

#: `| `Model` | `table` |` -- the shape of a row in the section's table.
_ROW = re.compile(r"^\|\s*`(\w+)`\s*\|\s*`(\w+)`\s*\|\s*$")

#: SQL as this file writes it: keywords in capitals. Lower-case "select ...
#: from ..." occurs in prose -- one sentence in the schema section says so --
#: and matching it read an English clause as a query.
_SQL_TABLE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-z_][a-z0-9_]*)")


def models_in_code() -> dict[str, str]:
    """Every declarative model in app/database.py, mapped to its table name.

    Parsed rather than imported: importing pulls SQLAlchemy, a database URL and
    whatever else the module touches at import time into a test about text.
    """
    tree = ast.parse(DATABASE.read_text())
    found: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == "__tablename__":
                    found[node.name] = ast.literal_eval(stmt.value)
    return found


def documented_models() -> dict[str, str]:
    """The model/table rows of the "Database Schema" section."""
    lines = DOC.read_text().splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.strip() == "### Database Schema"),
        None,
    )
    assert start is not None, "CLAUDE.md has no '### Database Schema' heading"
    rows: dict[str, str] = {}
    for line in lines[start:]:
        if line.startswith("### ") and line.strip() != "### Database Schema":
            break
        match = _ROW.match(line)
        if match and match.group(1) != "model":
            rows[match.group(1)] = match.group(2)
    return rows


def sql_tables_in_the_document() -> list[tuple[int, str]]:
    """Tables named by SQL anywhere in CLAUDE.md, with their line numbers."""
    found = []
    for n, line in enumerate(DOC.read_text().splitlines(), start=1):
        if "SELECT" not in line and "INSERT" not in line and "UPDATE" not in line:
            continue
        for table in _SQL_TABLE.findall(line):
            found.append((n, table))
    return found


def test_the_parsers_find_what_they_judge():
    """Negative control, first. Everything below is a claim about these lists.

    Both are "assert nothing is wrong" over a parsed set, and an empty set
    satisfies that without reading anything.
    """
    code = models_in_code()
    assert len(code) >= 15, (
        f"only {len(code)} models parsed out of app/database.py; the module "
        "changed shape and the assertions below are now about almost nothing"
    )
    doc = documented_models()
    assert len(doc) >= 15, (
        f"only {len(doc)} rows parsed out of the Database Schema table; the "
        "table's markup changed and this file has stopped reading it"
    )
    sql = sql_tables_in_the_document()
    assert len(sql) >= 2, (
        f"only {len(sql)} SQL table references found in CLAUDE.md; the "
        "documented psql commands are the reason this check exists"
    )


def test_every_documented_model_exists_with_that_table():
    """The defect itself: a name in the list that resolves to nothing."""
    code = models_in_code()
    wrong = []
    for model, table in documented_models().items():
        if model not in code:
            wrong.append(f"{model}: no such class in app/database.py")
        elif code[model] != table:
            wrong.append(f"{model}: table is {code[model]}, documented as {table}")
    assert not wrong, "CLAUDE.md's Database Schema does not match the code:\n  " + (
        "\n  ".join(wrong)
    )


def test_no_model_is_left_out():
    """The other direction, which the old five-item list failed silently.

    A list that omits eleven of sixteen models still reads as the schema. The
    omission is not a smaller error than the invention: both leave a reader
    confident about something they have not been told.
    """
    missing = sorted(set(models_in_code()) - set(documented_models()))
    assert not missing, (
        "these models are in app/database.py and not in CLAUDE.md's table:\n  "
        + "\n  ".join(f"{m} -> {models_in_code()[m]}" for m in missing)
    )


def test_documented_sql_names_a_table_that_exists():
    """Every documented query must be runnable.

    `SELECT * FROM drift_log` was documented twice, in the two sections an
    operator reaches for when something is wrong with drift.
    """
    tables = set(models_in_code().values()) | {
        # Created by alembic rather than by a model, and legitimately queried.
        "alembic_version",
    }
    wrong = [(n, t) for n, t in sql_tables_in_the_document() if t not in tables]
    assert not wrong, (
        "CLAUDE.md documents SQL against tables no model creates:\n  "
        + "\n  ".join(f"line {n}: {t}" for n, t in wrong)
    )
