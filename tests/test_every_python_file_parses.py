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
import subprocess
import tokenize

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Directories that are not ours to parse: dependencies, build output, and the
# frontend. `web/` holds a node_modules of its own.
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "web",
    ".pytest_cache", ".mypy_cache", "htmlcov", "mlruns", ".ruff_cache",
}


def _skipped(relative):
    return any(part in SKIP_DIRS for part in relative.split(os.sep))


def _walked_files():
    """What this test will parse."""
    found = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith(".py"):
                found.append(os.path.join(root, name))
    return found


def _tracked_files():
    """What git says is in the repository -- an independent list.

    Compared against the walk rather than against a hard-coded count. Review
    asked for `== 334`, and that is a number someone bumps when it fails; this
    updates itself with the tree and still catches a walk that silently stops
    reaching a directory.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"],
        cwd=REPO_ROOT, capture_output=True, timeout=60,
    )
    if result.returncode != 0:
        pytest.skip("git ls-files unavailable; no independent manifest to compare against")

    return {
        p for p in result.stdout.decode().split("\0")
        if p and not _skipped(p)
    }


def _relative(path):
    return os.path.relpath(path, REPO_ROOT)


def test_the_sweep_reaches_exactly_what_git_tracks():
    """A denominator from a source other than the thing being measured.

    `len(found) > 250` -- the first version -- would pass on a walk that had
    stopped reaching eighty files. Comparing sets says which ones.

    Untracked files are allowed in the walk (a new file before `git add`); a
    tracked file the walk misses is not.
    """
    walked = {_relative(p) for p in _walked_files()}
    tracked = _tracked_files()

    assert tracked, "git tracks no .py files, so this comparison proves nothing"
    missed = sorted(tracked - walked)

    assert missed == [], (
        f"{len(missed)} tracked Python file(s) are not reached by the walk, so "
        f"they are never checked: {missed[:10]}"
    )
    assert "app/main.py" in walked and "tests/conftest.py" in walked
    assert any(f.startswith("scripts/") for f in walked), (
        "scripts/ is not being walked -- that is where #54 lived, and pytest "
        "never imports it"
    )
    assert any(f.startswith("alembic/versions/") for f in walked), (
        "migrations are not being walked"
    )


def test_no_python_file_is_a_symlink():
    """A symlinked .py would be read through, possibly from outside the tree.

    `os.walk(followlinks=False)` controls only whether *directories* are
    followed; a symlinked file is still yielded and still opened. There are
    none today (the two symlinks in the repo are app/static/assets and
    app/static/index.html), so this states the assumption the parse loop makes
    rather than hardening against a case that exists.
    """
    links = sorted(_relative(p) for p in _walked_files() if os.path.islink(p))

    assert links == [], (
        f"{links} are symlinks; the parse loop would read whatever they point "
        f"at, which need not be in this repository"
    )


@pytest.mark.parametrize("path", sorted(_walked_files()), ids=_relative)
def test_the_file_parses(path):
    # tokenize.open, not open(encoding="utf-8"): PEP 263 lets a file declare its
    # own encoding, and a `# coding: latin-1` header would raise
    # UnicodeDecodeError here -- a failure about this test's assumptions rather
    # than a verdict on the file.
    with tokenize.open(path) as handle:
        source = handle.read()

    try:
        ast.parse(source, filename=path)
    except SyntaxError as exc:
        pytest.fail(
            f"{_relative(path)}:{exc.lineno} does not parse: {exc.msg}\n"
            f"    {(exc.text or '').rstrip()}"
        )
