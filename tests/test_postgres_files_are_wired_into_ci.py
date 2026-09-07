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

**The same hole exists for `tests/*.sh`, one step further along.** The
`shell-scripts` job names each shell suite on its own line, and a file nobody
adds there runs nowhere -- the workflow says so itself: "Listed by name because
this job runs shell tests one at a time; a file that is not named here runs
nowhere." All seven are wired today and nothing was keeping them that way.

Being *shellchecked* is not being run, and the workflow lists the same files
under `shellcheck -S style`. So the shell extractor matches an invocation
(`bash tests/x.sh`, `sudo -E bash tests/x.sh`, `./tests/x.sh`) and not a bare
mention -- the same distinction the comment case below makes for pytest.
"""
import os
import re

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(REPO_ROOT, "tests")
WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml")
TOOLS = os.path.join(REPO_ROOT, "tools")

PATH_RE = re.compile(r"tests/(test_\w+_postgres\.py)")

#: An invocation, not a mention. `shellcheck -S style tests/test_x.sh` names the
#: file without running it, and counting that would report a suite as wired when
#: it is only linted -- the same failure as a path in a comment.
SHELL_RUN_RE = re.compile(
    r"(?:(?:bash|sh)\s+(?:-[\w-]+\s+)*|\./)tests/(test_\w+\.sh)"
)

#: `backend-tests` runs `pytest tests/` and excludes files by name. An excluded
#: file runs only if some other command names it, and nothing was checking that.
IGNORED_RE = re.compile(r"--ignore=tests/(test_\w+\.py)")

#: Any python test file named on a command line, except by `--ignore=`, whose
#: whole purpose is to say the file is *not* run. The lookbehind is fixed width,
#: which `--ignore=` is.
PY_RUN_RE = re.compile(r"(?<!--ignore=)tests/(test_\w+\.py)")


def _postgres_test_files():
    return sorted(
        f for f in os.listdir(TESTS)
        if f.startswith("test_") and f.endswith("_postgres.py")
    )


def _shell_test_files():
    return sorted(
        f for f in os.listdir(TESTS)
        if f.startswith("test_") and f.endswith(".sh")
    )


def _strip_shell_comments(script):
    """A `#` line inside a `run:` block is still a comment.

    YAML parsing removes YAML comments; it does not remove shell ones, and the
    workflow has several that name test files while explaining them.
    """
    return "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    )


def _executed_paths(pattern=PATH_RE):
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
        found.update(pattern.findall(_strip_shell_comments(script)))
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


def test_the_workflow_runs_every_shell_test_file():
    """`tests/*.sh` is listed by hand in `shell-scripts`, one line per file.

    A suite nobody adds there runs nowhere, exactly as an unlisted
    `*_postgres.py` file did from #90 to #146. Seven are wired today; before
    this test, nothing said the eighth would be.
    """
    files = _shell_test_files()
    assert files, "no tests/test_*.sh files found; this check would be vacuous"

    executed = _executed_paths(SHELL_RUN_RE)
    assert executed, (
        "no shell suite was extracted from any run: block, so this check would "
        "report every file as unwired for a reason that is not about the files"
    )

    unwired = [f for f in files if f not in executed]

    assert unwired == [], (
        f"{unwired} exist but are not invoked by any command in "
        f".github/workflows/ci.yml. The shell-scripts job runs its suites one "
        f"at a time by name, so an unlisted file runs nowhere. Being passed to "
        f"shellcheck is not being run."
    )


def test_the_workflow_does_not_invoke_shell_suites_that_are_gone():
    """A `bash tests/gone.sh` fails the job with 'No such file'.

    Which is a failure about arguments rather than about the code, and costs a
    run to read.
    """
    missing = sorted(
        name for name in _executed_paths(SHELL_RUN_RE)
        if not os.path.exists(os.path.join(TESTS, name))
    )

    assert missing == [], f"ci.yml invokes {missing}, which do not exist"


def test_being_shellchecked_does_not_count_as_being_run():
    """The discriminating case for the shell extractor.

    `shell-scripts` passes the same paths to `shellcheck -S style` that it
    later runs. A pattern matching the bare path would call a linted-but-never
    executed suite wired -- which is the defect this whole file exists to
    catch, one tool along.
    """
    linted_only = (
        "shellcheck -S style \\\n"
        "  tests/test_only_linted.sh \\\n"
        "  tests/test_also_run.sh\n"
        "bash tests/test_also_run.sh\n"
        "sudo -E bash tests/test_as_root.sh\n"
    )

    found = set(SHELL_RUN_RE.findall(_strip_shell_comments(linted_only)))

    assert found == {"test_also_run.sh", "test_as_root.sh"}, found
    assert "test_only_linted.sh" not in found


def _ignored_paths():
    """Files any job excludes from a pytest run.

    Every job, not `backend-tests` alone, though today it is the only one with
    an `--ignore=`. Scoping a guard to the place the defect happened to be is
    the mistake this file has now made twice; the cost of not doing it here is
    three lines.
    """
    with open(WORKFLOW) as fh:
        workflow = yaml.safe_load(fh)
    found = set()
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            run = step.get("run")
            if run:
                found.update(IGNORED_RE.findall(_strip_shell_comments(run)))
    return found


def test_every_ignored_file_is_run_somewhere_else():
    """`--ignore=` is a promise that another job runs it. Nothing checked.

    `backend-tests` collects `tests/` wholesale, so a file it excludes runs only
    where it is named. Read across every job, since an exclusion elsewhere would
    mean the same thing. `tests/test_api.py` is excluded and run by
    `integration-tests`, which is the intended arrangement.
    `tests/test_scoring_baseline.py` was excluded and named nowhere: 42 pinned
    ESG scores, five countries by four project types, executed by no job since
    the initial commit.
    """
    ignored = _ignored_paths()
    assert ignored, (
        "no --ignore= was found in any job, so this check is vacuous; the flag "
        "was renamed or the exclusions were restructured"
    )

    executed = _executed_paths(PY_RUN_RE)
    assert executed, "no pytest paths extracted from any run: block"

    unrun = sorted(f for f in ignored if f not in executed)

    assert unrun == [], (
        f"{unrun} are excluded from backend-tests and run by no other command "
        f"in .github/workflows/ci.yml, so they execute nowhere. Either name "
        f"them in a job or delete them; an excluded file that nothing runs is "
        f"a suite whose results nobody has ever seen."
    )


def test_ignore_does_not_count_as_running():
    """The discriminating case for the python extractor.

    Without the lookbehind, `--ignore=tests/x.py` would satisfy "x.py is named
    on a command line" and the test above would pass on the very arrangement it
    exists to reject.
    """
    block = (
        "python -m pytest tests/ \\\n"
        "  --ignore=tests/test_excluded.py \\\n"
        "  -q\n"
        "python -m pytest tests/test_really_run.py\n"
    )

    stripped = _strip_shell_comments(block)

    assert IGNORED_RE.findall(stripped) == ["test_excluded.py"]
    assert set(PY_RUN_RE.findall(stripped)) == {"test_really_run.py"}
