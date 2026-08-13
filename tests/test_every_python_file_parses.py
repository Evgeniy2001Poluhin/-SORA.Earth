"""Every Python file in the repository has to parse.

#54 found `scripts/fix_display_bugs.py` broken by a botched find/replace and
unrunnable since the repository's first commit -- fifteen months. Walking the
whole tree rather than checking that one file found a second: `tests/
build_baseline.py`, a dangling `except` from the same initial commit, equally
unreferenced.

Two is what the sweep found; one is what looking at the reported file would have
found. Nothing else in CI parses the tree: `ruff` and `flake8` are not
installed, `py_compile` is run against exactly one file (tools/wait_for.py), and
pytest only imports what it collects -- which excludes `scripts/` entirely and,
inside `tests/`, everything not named `test_*`.

This is deliberately the weakest possible check. It says nothing about whether
code is correct, only that it is code. That is the level at which both defects
lived.
"""
import ast
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Directories that are not ours to parse: dependencies, build output, and the
# frontend. `web/` holds a node_modules of its own.
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "web",
    ".pytest_cache", ".mypy_cache", "htmlcov", "mlruns", ".ruff_cache",
}


def _python_files():
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(root, name)


def _relative(path):
    return os.path.relpath(path, REPO_ROOT)


def test_the_sweep_actually_reaches_the_tree():
    """A denominator, so "0 unparseable" cannot come from an empty walk.

    A wrong REPO_ROOT, or one skip pattern too many, would make every assertion
    below pass by finding nothing.
    """
    found = {_relative(p) for p in _python_files()}

    assert len(found) > 250, f"only {len(found)} files walked; the sweep is not reaching the tree"
    assert "app/main.py" in found
    assert "tests/conftest.py" in found
    assert any(f.startswith("scripts/") for f in found), (
        "scripts/ is not being walked -- that is where #54 lived, and pytest "
        "never imports it"
    )
    assert any(f.startswith("alembic/versions/") for f in found), (
        "migrations are not being walked"
    )


@pytest.mark.parametrize("path", sorted(_python_files()), ids=_relative)
def test_the_file_parses(path):
    source = open(path, encoding="utf-8").read()

    try:
        ast.parse(source, filename=path)
    except SyntaxError as exc:
        pytest.fail(
            f"{_relative(path)}:{exc.lineno} does not parse: {exc.msg}\n"
            f"    {(exc.text or '').rstrip()}"
        )
