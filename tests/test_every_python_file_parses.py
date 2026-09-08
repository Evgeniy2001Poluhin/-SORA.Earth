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

**What it parses is decided by git, not by a list of directory names.** The
skip list held `.venv` and `venv`, which are two examples of a rule rather
than the rule. This repository's own workflow builds `.venv311` -- local
`python3` is 3.9.6 and cannot collect half the suite -- and with that
directory in the tree the sweep parsed 25,606 files instead of 487 and took
63 seconds instead of two. Worse than slow: a dependency shipping one
unparseable file would fail a test about *this* repository, and the failure
would name a path nobody here can fix.

`git ls-files` plus `--others --exclude-standard` is the same intent stated
once: everything tracked, plus new files not yet added, minus everything
`.gitignore` covers -- any venv, under any name, and every cache and build
directory already listed there. `os.walk` stays as the fallback for a tree
without git, and as the independent manifest the control below compares
against.
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


def _git_paths(*args):
    """`git ls-files` output as a set of repo-relative paths, or None."""
    result = subprocess.run(
        ["git", "ls-files", "-z", *args, "--", "*.py"],
        cwd=REPO_ROOT, capture_output=True, timeout=60,
    )
    if result.returncode != 0:
        return None
    return {
        p for p in result.stdout.decode().split("\0")
        if p and not _skipped(p)
    }


def _tracked_files():
    """What git says is in the repository -- an independent list.

    Compared against the walk rather than against a hard-coded count. Review
    asked for `== 334`, and that is a number someone bumps when it fails; this
    updates itself with the tree and still catches a walk that silently stops
    reaching a directory.
    """
    tracked = _git_paths()
    if tracked is None:
        pytest.skip("git ls-files unavailable; no independent manifest to compare against")
    return tracked


def _repository_files():
    """The files this repository is answerable for, as absolute paths.

    Tracked, plus untracked files git would not ignore -- a file written but
    not yet added is exactly the case #54 was about, and dropping it would
    narrow the check to what someone remembered to commit. Everything
    `.gitignore` covers is out, which is how a virtualenv in the tree stops
    being this test's problem without anyone maintaining a list of the names
    virtualenvs are given.

    Falls back to the walk when git cannot answer, so a tarball of the source
    is still checked -- with `SKIP_DIRS` doing the filtering it did before.
    """
    tracked = _git_paths()
    if tracked is None:
        return sorted(_walked_files())

    untracked = _git_paths("--others", "--exclude-standard") or set()
    return sorted(
        os.path.join(REPO_ROOT, p)
        for p in (tracked | untracked)
        if os.path.isfile(os.path.join(REPO_ROOT, p))
    )


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
    links = sorted(_relative(p) for p in _repository_files() if os.path.islink(p))

    assert links == [], (
        f"{links} are symlinks; the parse loop would read whatever they point "
        f"at, which need not be in this repository"
    )


#: Computed once, at collection. Named so the guards below and the
#: parametrize argument cannot drift apart -- and asserted inside
#: `test_the_file_parses` itself, because a guard that only inspects the
#: helper says nothing about what the parametrize was actually given.
#: Measured: swapping the parametrize back to the walk left every guard green.
REPOSITORY_FILES = _repository_files()


def test_the_parse_set_is_this_repository_and_not_its_dependencies():
    """Negative control, and the regression this shape of sourcing prevents.

    Both bounds matter. Too few and the sweep has stopped reaching somewhere;
    too many and it has wandered into a virtualenv, where a failure would be
    about a dependency. 450 tracked files when this was written; 25,606 with
    `.venv311` walked.
    """
    parsed = {_relative(p) for p in REPOSITORY_FILES}

    assert len(parsed) >= 300, (
        f"only {len(parsed)} Python files in the parse set; the sweep has "
        f"stopped reaching part of the tree"
    )
    assert len(parsed) < 5000, (
        f"{len(parsed)} Python files in the parse set. That is a dependency "
        f"tree, not this repository -- check that the virtualenv is covered "
        f"by .gitignore, and that git is answering here"
    )
    assert "app/main.py" in parsed and "tests/conftest.py" in parsed
    assert any(f.startswith("scripts/") for f in parsed), (
        "scripts/ is not in the parse set -- that is where #54 lived, and "
        "pytest never imports it"
    )
    assert any(f.startswith("alembic/versions/") for f in parsed), (
        "migrations are not in the parse set"
    )
    assert not any(f.startswith((".venv", "venv", "node_modules")) for f in parsed), (
        "the parse set reaches into a dependency directory"
    )


def _probe(relative, source):
    """Write a file under the repository and return its absolute path."""
    path = os.path.join(REPO_ROOT, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(source)
    return path


def test_the_rule_is_ignored_out_and_untracked_in():
    """The discriminating cases, and neither needs a venv to be present.

    The bounds above only separate the two sourcings on a machine that happens
    to have a virtualenv in the tree; in CI there is none and both answers are
    the same size. These create the conditions instead, one per direction:

        ignored, not in SKIP_DIRS   must be out -- under the walk it would be
                                    collected, and an unparseable file there
                                    reddens a test about this repository with
                                    a path nobody here can fix
        untracked, not ignored      must be in -- a file written and not yet
                                    added is exactly the case #54 was about,
                                    and `git ls-files` alone would drop it
    """
    ignored = _probe(os.path.join(".venv311", "not_parseable_probe.py"), "def (\n")
    fresh = _probe(os.path.join(".probe_parse_sweep", "written_not_added.py"), "x = 1\n")
    try:
        assert subprocess.run(
            ["git", "check-ignore", "-q", ignored],
            cwd=REPO_ROOT, capture_output=True, timeout=30,
        ).returncode == 0, (
            ".venv311/ is no longer ignored, so this case proves nothing -- "
            "the parse set would legitimately contain the probe"
        )
        assert subprocess.run(
            ["git", "check-ignore", "-q", fresh],
            cwd=REPO_ROOT, capture_output=True, timeout=30,
        ).returncode != 0, (
            ".probe_parse_sweep/ is ignored, so the other case proves nothing"
        )

        selected = _repository_files()

        assert ignored not in selected, (
            "an ignored directory is in the parse set, so this test can be "
            "reddened by a file nobody in this repository wrote"
        )
        assert fresh in selected, (
            "a written-but-not-yet-added file is not in the parse set. That "
            "is the file #54 was about, and narrowing to `git ls-files` "
            "without `--others` drops every one of them"
        )
    finally:
        for path in (ignored, fresh):
            os.remove(path)
            try:
                os.rmdir(os.path.dirname(path))
            except OSError:
                pass


@pytest.mark.parametrize("path", REPOSITORY_FILES, ids=_relative)
def test_the_file_parses(path):
    # Asserted here rather than only in a guard above: the guards inspect
    # `_repository_files()`, and a parametrize pointed at something else would
    # leave all of them green while this test judged 25,606 files it does not
    # own. Verified by making exactly that change and watching nothing redden.
    assert path in REPOSITORY_FILES, (
        f"{_relative(path)} is not a file this repository is answerable for"
    )

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
