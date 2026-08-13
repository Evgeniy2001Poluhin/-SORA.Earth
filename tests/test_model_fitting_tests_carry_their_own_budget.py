"""The heavy forecasting tests must state their own timeout.

#70: `test_ensemble_validate` timed out on the full suite and passed alone. The
cause was measured, not guessed -- the suite context costs 8%, CPU contention
costs 480% -- so the budget was wrong, and wrong asymmetrically: CI gave the
same tests 60 s in the step where they run alone and 30 s in the step where they
run alongside 1300 others.

A module-level `timeout` marker fixes that because it outranks both `pytest.ini`
and `--timeout`. These tests hold that arrangement in place: the marker is easy
to drop in a refactor, and dropping it would restore exactly the intermittent
failure that took two investigations to explain.
"""
import pytest

MODULES = [
    "tests.test_forecasting_ensemble",
    "tests.test_forecasting_cache",
]

BUDGET = 120


def _marks(module_name):
    import importlib

    module = importlib.import_module(module_name)
    marks = getattr(module, "pytestmark", [])
    return marks if isinstance(marks, list) else [marks]


@pytest.mark.parametrize("module_name", MODULES)
def test_the_module_declares_a_timeout(module_name):
    timeouts = [
        m.args[0] for m in _marks(module_name)
        if m.name == "timeout" and m.args
    ]

    assert timeouts == [BUDGET], (
        f"{module_name} no longer declares timeout({BUDGET}). Every test in it "
        f"fits a real model, and without the marker it inherits pytest.ini's "
        f"30 s inside backend-tests -- the exact configuration that produced "
        f"#70."
    )


def test_the_marker_outranks_the_command_line(tmp_path):
    """The property the fix depends on, run rather than assumed.

    If a future pytest-timeout changed the precedence, the marker would still be
    present, both tests above would still pass, and the budget in the full-suite
    run would silently be 30 s again.

    So: a real pytest under `--timeout=1`. The marked case has to survive it,
    and an unmarked case of the same length has to be killed by it -- without
    the second half, "survived" could just mean the option was ignored.
    """
    import os
    import subprocess
    import sys

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    control = tmp_path / "test_unmarked_control.py"
    control.write_text(
        "import time\n\n\ndef test_sleeps_past_one_second():\n    time.sleep(3)\n"
    )

    def run(target, cwd):
        return subprocess.run(
            [sys.executable, "-m", "pytest", target, "-q", "-p", "no:randomly",
             "--timeout=1", "-p", "no:cacheprovider"],
            cwd=cwd, capture_output=True, text=True, timeout=300,
            env={**os.environ, "SORA_OFFLINE": "1", "RUN_SCHEDULER": "false"},
        )

    marked = run(
        "tests/test_forecasting_ensemble.py::test_ensemble_auto_weights_small_sample",
        repo_root,
    )
    unmarked = run(str(control), str(tmp_path))

    assert unmarked.returncode != 0 and "Timeout" in unmarked.stdout, (
        "--timeout=1 did not kill an unmarked 3-second test, so this check "
        "cannot tell precedence from the option being ignored:\n"
        + unmarked.stdout[-1500:]
    )
    assert marked.returncode == 0, (
        "--timeout=1 killed a test carrying timeout(120): the marker no longer "
        "outranks the command line, and inside backend-tests these tests are "
        "back on pytest.ini's 30 s\n" + marked.stdout[-1500:]
    )


def test_the_case_that_was_failing_is_covered():
    """Named explicitly, so renaming it away from the marked module is visible."""
    from tests import test_forecasting_ensemble as mod

    assert hasattr(mod, "test_ensemble_validate"), (
        "test_ensemble_validate has moved out of the module that carries the "
        "budget; #70 would come back with it"
    )
