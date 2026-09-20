"""The local run CLAUDE.md prescribes and the run CI performs are the same run.

A developer who runs the suite with `pytest.ini`'s defaults gets failures CI does
not have, spends time deciding whether they are real, and -- measured twice in
one session on 2026-09-19/20 -- concludes "known pre-existing" without
establishing it. The difference was one flag.

    pytest.ini            timeout = 30
    .github/workflows     --timeout=60

Two of the four failures seen locally that night disappeared at 60 s. The other
two pass in CI and not on that machine, which is a fact about the machine; the
way to tell those apart is written into CLAUDE.md beside the command, because
inferring it from the failure text is exactly what does not work.

This test binds the documented flag to the workflow rather than to a copy of the
number. If CI's budget moves, the document fails here until it moves too -- the
defect that a separately-maintained "CI-equivalent command" always eventually
has.
"""
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def ci_timeout():
    """The budget the backend-tests job gives each test.

    Read out of the workflow, not restated. The step is the one that runs the
    whole suite: `python -m pytest tests/ --ignore=tests/test_api.py ...`.
    """
    workflow = _read(".github", "workflows", "ci.yml")
    step = workflow.split("python -m pytest tests/ \\", 1)
    assert len(step) == 2, (
        "the backend-tests job no longer runs `python -m pytest tests/` as one "
        "step; this test can no longer find the budget it is checking"
    )
    match = re.search(r"--timeout=(\d+)", step[1][:600])
    assert match, "the suite step declares no --timeout"
    return int(match.group(1))


def test_the_project_file_and_ci_disagree_on_purpose_and_it_is_written_down(
        ci_timeout):
    """They are allowed to differ. What is not allowed is for that to be a
    surprise, which is what it was."""
    ini = _read("pytest.ini")
    ini_timeout = int(re.search(r"^timeout\s*=\s*(\d+)", ini, re.M).group(1))

    claude = re.sub(r"\s+", " ", _read("CLAUDE.md"))

    if ini_timeout == ci_timeout:
        return  # nothing to warn about

    assert f"--timeout={ci_timeout}" in claude, (
        f"pytest.ini gives each test {ini_timeout}s and CI gives it "
        f"{ci_timeout}s, and CLAUDE.md does not mention the larger figure. A "
        f"local run is then stricter than CI and produces failures nobody can "
        f"account for."
    )


def test_the_documented_command_carries_the_flags_ci_uses(ci_timeout):
    """Not a transcription of the whole invocation -- the parts that change the
    result. `--ignore` decides what runs; `--timeout` decides what passes."""
    claude = re.sub(r"\s+", " ", _read("CLAUDE.md"))

    for fragment in (f"--timeout={ci_timeout}", "--ignore=tests/test_api.py"):
        assert fragment in claude, (
            f"the CI-equivalent command in CLAUDE.md omits {fragment}, so "
            f"following it does not reproduce what CI ran"
        )


def test_the_document_says_how_to_tell_a_local_failure_from_a_real_one():
    """The half that cannot be derived from a flag.

    Two tests fail on one machine and pass in CI. No amount of reading their
    output says which is which -- the answer is in the CI log, and knowing to
    look there is the thing worth writing down.
    """
    claude = re.sub(r"\s+", " ", _read("CLAUDE.md"))

    assert "gh run view" in claude, (
        "CLAUDE.md does not say how to check whether a locally failing test "
        "passes in CI. Without that the next session repeats the "
        "investigation, as this one did twice."
    )
