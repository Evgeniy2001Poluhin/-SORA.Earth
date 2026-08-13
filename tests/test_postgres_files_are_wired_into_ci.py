"""Every `*_postgres.py` file has to be named in the workflow that runs them.

`environmental-postgres-tests` lists its files one by one. `backend-tests` runs
on SQLite, where `requires_postgres` skips them. So a new PostgreSQL test file
that nobody adds to the workflow passes locally, is skipped in backend-tests,
and never executes anywhere -- a green suite that has not run the thing.

The comment at that job already says this. It says it because it happened. A
comment is not a check, which is what this file is for.
"""
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(REPO_ROOT, "tests")
WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml")


def _postgres_test_files():
    return sorted(
        f for f in os.listdir(TESTS)
        if f.startswith("test_") and f.endswith("_postgres.py")
    )


def test_the_workflow_runs_every_postgres_test_file():
    workflow = open(WORKFLOW).read()

    files = _postgres_test_files()
    assert files, "no *_postgres.py files found; this check would be vacuous"

    unwired = [f for f in files if f"tests/{f}" not in workflow]

    assert unwired == [], (
        f"{unwired} exist but are not named in .github/workflows/ci.yml. "
        f"They skip under backend-tests (SQLite) and are not collected by "
        f"environmental-postgres-tests, so they run nowhere."
    )


def test_the_workflow_does_not_name_files_that_are_gone():
    """The other direction: a step pointing at a deleted file.

    pytest exits non-zero on a missing path, so this would break the job rather
    than pass quietly -- but it breaks it with an error about arguments, not
    about the code, and that costs a run to understand.
    """
    workflow = open(WORKFLOW).read()

    named = set(re.findall(r"tests/(test_\w+_postgres\.py)", workflow))
    missing = sorted(n for n in named if not os.path.exists(os.path.join(TESTS, n)))

    assert missing == [], f"ci.yml runs {missing}, which do not exist"
