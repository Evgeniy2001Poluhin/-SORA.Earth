"""One way to reach `data/`, so an override cannot split writer from reader.

`app/paths.py` says why the overrides exist:

    The overrides exist so the test suite can point both roots at a temporary
    directory. Without them a test run rewrites ``models/meta.json`` and
    appends rows to ``data/projects.csv`` -- the tracked training set.

`SORA_DATA_DIR` did that for some paths and not others. Measured before this
change, over `projects.csv` alone:

    app/api/retrain.py      writes it       data_dir()          honours it
    app/api/ab_comparison   reads it        data_dir()          honours it
    app/api/calibration     reads it        data_dir()          honours it
    app/api/drift.py        reads it        ROOT_DIR/"data"     ignores it
    app/api/explain.py      reads it        ROOT/"data"         ignores it
    app/api/compare.py      reads it        ROOT_DIR/"data"     ignores it

So setting the override moved the writer and left three readers behind:
retrain would rewrite the training set in a temporary directory while drift,
explain and compare went on reading the tracked one. With the variable unset --
production -- all six agree, which is why nothing ever showed it.

`predictions_log.csv` had the same split with the roles reversed: the writer in
`app/api/predict.py` ignored the override and one reader honoured it. That one
is in #281, where "set `SORA_DATA_DIR` to a mounted path" is listed as a fix for
the drift input being ephemeral -- it would not have worked, because
`app/api/drift.py` did not read the variable at all.

**What this does not change: when the variable is read.** Most of these are
module-level constants, evaluated once at import. Setting `SORA_DATA_DIR` after
`app.main` is imported still moves nothing, exactly as before. The fix here is
that every path now reads the *same* variable; it does not make any of them
re-read it. That is enough for the two cases the override exists for -- a test
process that sets it before importing, and a container that sets it in the
environment -- and it is not enough for `monkeypatch.setenv` inside a test that
has already imported the app.

**`app/database.py` is left alone on purpose.** It builds a default sqlite URL,
and a database file is not a dataset: `DATABASE_URL` is already its override,
and adding a second lever that silently relocates the database is a change in
behaviour rather than a consistency fix.

`models/` is untouched here too. #191 is rebuilding those paths into seed,
staged and active directories, and moving them now would collide with it.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Modules allowed to build a `data/` path without `data_dir()`, and why.
ALLOWED = {
    "app/database.py": (
        "default sqlite URL; DATABASE_URL is its override and a database file "
        "is not a dataset"
    ),
}


def scanned_files() -> list[Path]:
    """The files both scans below read.

    One function, so narrowing the sweep shows up in the control instead of
    quietly exempting whatever fell outside it. Measured: pointing one of the
    scans at `app/api/` alone left every assertion green while `app/main.py`
    and `app/training.py` stopped being judged.
    """
    return [p for p in sorted((REPO / "app").rglob("*.py")) if p.name != "paths.py"]


def hand_rolled_data_paths() -> list[str]:
    """`os.path.join(..., "data", ...)` anywhere under `app/`."""
    found = []
    for path in scanned_files():
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "join"
            ):
                continue
            literals = [
                a.value for a in node.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)
            ]
            if "data" in literals:
                found.append(f"{path.relative_to(REPO)}:{node.lineno}")
    return found


def calls_to_data_dir() -> list[str]:
    """Where `data_dir()` is actually called, so the rule has a denominator."""
    found = []
    for path in scanned_files():
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id.endswith("data_dir")
            ):
                found.append(f"{path.relative_to(REPO)}:{node.lineno}")
    return found


def test_the_scan_finds_both_sides():
    """Negative control. "No offenders" must not be "nothing was parsed"."""
    files = {str(p.relative_to(REPO)) for p in scanned_files()}
    assert len(files) >= 30, f"only {len(files)} modules under app/ are scanned"
    for outside_api in ("app/main.py", "app/training.py", "app/database.py"):
        assert outside_api in files, (
            f"{outside_api} is not in the scan, so it is exempt from the rule "
            f"without an allowance saying so"
        )

    used = calls_to_data_dir()
    assert len(used) >= 8, (
        f"only {len(used)} calls to data_dir() found under app/ (12 when this "
        "was written); the scan is not reaching the modules it judges"
    )
    assert any(u.startswith("app/api/drift.py") for u in used), (
        "app/api/drift.py no longer calls data_dir(); it is the module whose "
        "split from the writer this file exists for"
    )


def test_every_data_path_goes_through_the_one_function():
    """The rule. An override that half the code honours is worse than none.

    It does not fail loudly: with `SORA_DATA_DIR` unset both spellings resolve
    to the same directory, so the defect is invisible in production and only
    appears where the override is used -- the test suite, and any deployment
    that tries #281's third option.
    """
    offenders = [
        hit for hit in hand_rolled_data_paths()
        if hit.split(":")[0] not in ALLOWED
    ]

    assert not offenders, (
        "these build a path into data/ without data_dir(), so SORA_DATA_DIR "
        "moves some of them and not the others:\n  " + "\n  ".join(offenders)
    )


def test_the_allowance_still_describes_something_real():
    """An allowlist entry that matches nothing is a note pretending to be a rule."""
    files = {hit.split(":")[0] for hit in hand_rolled_data_paths()}
    stale = sorted(name for name in ALLOWED if name not in files)

    assert not stale, (
        f"these are allowed to build their own data/ path and no longer do: "
        f"{stale}. Remove the entry rather than leaving an exemption nobody needs."
    )


def test_the_rule_catches_the_shape_it_is_written_for():
    """Discriminating case, because the sweep above now finds one file.

    Runs the same predicate over source that is not in the tree, so the rule
    is exercised whatever the repository happens to contain.
    """
    offending = ast.parse(
        'import os\nP = os.path.join(ROOT_DIR, "data", "projects.csv")\n'
    )
    clean = ast.parse(
        'import os\nP = os.path.join(data_dir(), "projects.csv")\n'
    )

    def flags(tree):
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "join"
                and any(
                    isinstance(a, ast.Constant) and a.value == "data"
                    for a in node.args
                )
            ):
                return True
        return False

    assert flags(offending), "the rule does not catch a hand-rolled data path"
    assert not flags(clean), "the rule flags a correct path built from data_dir()"
