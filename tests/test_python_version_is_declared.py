"""The required Python version is stated once and matches everywhere.

Before this, 3.11 was pinned in four places a machine reads -- the CI workflow,
twice in the Dockerfile -- and stated in none that a person reads. There was no
`pyproject.toml`, no line in the README, nothing in `conftest.py`. A developer
on the system Python of a current macOS (3.9) got forty-two collection errors,
each a TypeError from `app/rate_limit.py` or `app/secret_validation.py`, about
a test that has nothing to do with either.

A declaration nothing checks drifts from what is enforced -- which is exactly
how those two modules came to require 3.10 while the rest of the project said
nothing at all. So this binds the declaration to the two places that enforce it.
"""
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_toml(path):
    try:
        import tomllib  # 3.11+
    except ModuleNotFoundError:
        tomllib = pytest.importorskip(
            "tomli", reason="no TOML reader on this interpreter")
    with open(path, "rb") as fh:
        return tomllib.load(fh)


@pytest.fixture(scope="module")
def declared():
    """The floor from pyproject.toml, as (major, minor)."""
    data = _load_toml(os.path.join(REPO_ROOT, "pyproject.toml"))
    spec = data["project"]["requires-python"]
    match = re.fullmatch(r">=\s*(\d+)\.(\d+)", spec.strip())
    assert match, f"requires-python is {spec!r}; this test reads '>=X.Y'"
    return int(match.group(1)), int(match.group(2))


def test_the_workflow_uses_the_declared_version(declared):
    """Every `python-version:` in CI, not just the first.

    There are four. Checking one would let the others drift, and a job running
    a different interpreter than the rest is the kind of difference that shows
    up as one inexplicable failure.
    """
    workflow = os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml")
    with open(workflow, encoding="utf-8") as fh:
        pinned = re.findall(r'python-version:\s*"?(\d+\.\d+)"?', fh.read())

    assert pinned, "no python-version pin found in ci.yml"
    for version in set(pinned):
        major, minor = (int(p) for p in version.split("."))
        assert (major, minor) >= declared, (
            f"ci.yml runs {version} and pyproject.toml requires "
            f">={declared[0]}.{declared[1]}"
        )


def test_the_image_uses_the_declared_version(declared):
    """Both stages. The builder and the runtime diverging is a bug that only
    appears at run time, in production."""
    with open(os.path.join(REPO_ROOT, "Dockerfile"), encoding="utf-8") as fh:
        stages = re.findall(r"^FROM\s+python:(\d+\.\d+)", fh.read(), re.M)

    assert stages, "no python base image found in the Dockerfile"
    for version in set(stages):
        major, minor = (int(p) for p in version.split("."))
        assert (major, minor) >= declared, (
            f"the Dockerfile builds on {version} and pyproject.toml requires "
            f">={declared[0]}.{declared[1]}"
        )


def test_the_readme_says_which_version(declared):
    """Where a person looks first.

    Matched on the number rather than on a sentence, so the wording stays free.
    """
    with open(os.path.join(REPO_ROOT, "README.md"), encoding="utf-8") as fh:
        readme = fh.read()

    assert f"{declared[0]}.{declared[1]}" in readme, (
        f"README.md does not mention Python {declared[0]}.{declared[1]}"
    )


def test_the_declaration_is_not_below_what_the_code_needs(declared):
    """`X | None` in an annotation evaluated at run time needs 3.10.

    Two modules use it, and they are why the floor exists. Read from the source
    rather than restated, so removing the syntax and lowering the floor stays a
    decision someone makes together.
    """
    uses_pep604 = []
    for name in ("app/rate_limit.py", "app/secret_validation.py"):
        with open(os.path.join(REPO_ROOT, name), encoding="utf-8") as fh:
            body = fh.read()
        if re.search(r"->\s*\w+\s*\|\s*None|:\s*\w+\s*\|\s*None\s*=", body):
            uses_pep604.append(name)

    if uses_pep604:
        assert declared >= (3, 10), (
            f"{uses_pep604} annotate with `X | None`, evaluated at run time, "
            f"which needs 3.10 -- but the declared floor is "
            f"{declared[0]}.{declared[1]}"
        )
