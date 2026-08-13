"""Every `*_postgres.py` file has to be *executed* by CI, not merely mentioned.

`environmental-postgres-tests` lists its files one by one. `backend-tests` runs
on SQLite, where `requires_postgres` skips them. So a new PostgreSQL test file
that nobody adds to the workflow passes locally, is skipped in backend-tests,
and never executes anywhere -- a green suite that has not run the thing. That is
how `tests/test_point_in_time_postgres.py` sat unrun from #90 to #146.

The first version of this file searched the workflow text for the path. Review
pointed out that a filename left behind in a comment would satisfy it, which is
the same defect one level up: a check that passes without the thing being true.
So the paths are taken from the parsed YAML -- where comments no longer exist --
and only from the command lines that actually invoke pytest.

`tools/*.sh` is read too, because the mutation step runs pytest from a script; a
file wired only there is genuinely executed and must not be reported unwired.
"""
import os
import re

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(REPO_ROOT, "tests")
WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml")
TOOLS = os.path.join(REPO_ROOT, "tools")

PATH_RE = re.compile(r"tests/(test_\w+_postgres\.py)")


def _postgres_test_files():
    return sorted(
        f for f in os.listdir(TESTS)
        if f.startswith("test_") and f.endswith("_postgres.py")
    )


def _strip_shell_comments(script):
    """A `#` line inside a `run:` block is still a comment.

    YAML parsing removes YAML comments; it does not remove shell ones, and the
    workflow has several that name test files while explaining them.
    """
    return "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    )


def _executed_paths():
    """Test files named on a command line that runs them."""
    with open(WORKFLOW) as fh:
        workflow = yaml.safe_load(fh)

    scripts = []
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            run = step.get("run")
            if run:
                scripts.append(run)

    if os.path.isdir(TOOLS):
        for name in sorted(os.listdir(TOOLS)):
            if name.endswith(".sh"):
                with open(os.path.join(TOOLS, name)) as fh:
                    scripts.append(fh.read())

    found = set()
    for script in scripts:
        found.update(PATH_RE.findall(_strip_shell_comments(script)))
    return found


def test_the_workflow_runs_every_postgres_test_file():
    files = _postgres_test_files()
    assert files, "no *_postgres.py files found; this check would be vacuous"

    executed = _executed_paths()
    assert executed, (
        "no test paths were extracted from any run: block, so this check would "
        "report every file as unwired for a reason that is not about the files"
    )

    unwired = [f for f in files if f not in executed]

    assert unwired == [], (
        f"{unwired} exist but are not executed by any command in "
        f".github/workflows/ci.yml. They skip under backend-tests (SQLite) and "
        f"are not collected by environmental-postgres-tests, so they run "
        f"nowhere. Naming one in a comment does not run it."
    )


def test_the_workflow_does_not_name_files_that_are_gone():
    """The other direction: a command pointing at a deleted file.

    pytest exits non-zero on a missing path, so this would break the job rather
    than pass quietly -- but it breaks it with an error about arguments, not
    about the code, and that costs a run to understand.
    """
    missing = sorted(
        name for name in _executed_paths()
        if not os.path.exists(os.path.join(TESTS, name))
    )

    assert missing == [], f"ci.yml runs {missing}, which do not exist"


def test_a_path_in_a_comment_does_not_count_as_wired():
    """The discriminating case, on the extractor itself.

    Without this, the two tests above cannot tell the current implementation
    from the substring search it replaced -- both are green today.
    """
    commented = _strip_shell_comments(
        "# see tests/test_only_in_a_comment_postgres.py for why\n"
        "python -m pytest tests/test_really_run_postgres.py\n"
    )

    assert PATH_RE.findall(commented) == ["test_really_run_postgres.py"]
