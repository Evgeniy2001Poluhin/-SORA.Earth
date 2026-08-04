"""No autouse teardown may import the application.

`no_scheduler_left_running` did `from app.scheduler import scheduler` after every
test. That chain reaches app.main and its ML stack, which on a CI runner costs
more than the per-test timeout: the body passed, the teardown was killed
mid-import, the half-built module was dropped from sys.modules, and the next test
paid it again -- PASSED-then-ERROR at exactly the timeout, once per test, until
the job was cancelled. Twice, at two different job limits.

The rule is cheap to state and easy to lose, so it is asserted here.
"""

import ast
import os

CONFTEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conftest.py")
FORBIDDEN = ("app.main", "app.scheduler")


def _autouse_fixtures(tree):
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            if any(kw.arg == "autouse" and getattr(kw.value, "value", False)
                   for kw in dec.keywords):
                yield node


def test_no_autouse_fixture_imports_the_application():
    source = open(CONFTEST).read()
    tree = ast.parse(source)
    offenders = []
    for fn in _autouse_fixtures(tree):
        # An import guarded by a sys.modules lookup is cheap: it only runs when
        # the module is already loaded. restore_app_models does exactly that, and
        # is correct. What must not happen is importing unconditionally.
        # From the source text: ast.dump renders `sys.modules` structurally, so
        # looking for that substring in the dump silently found nothing and every
        # guarded fixture was reported as an offender.
        guarded = "sys.modules" in ast.get_source_segment(source, fn)
        if guarded:
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.ImportFrom) and node.module in FORBIDDEN:
                offenders.append("%s imports %s" % (fn.name, node.module))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in FORBIDDEN:
                        offenders.append("%s imports %s" % (fn.name, alias.name))
    assert not offenders, (
        "an autouse fixture imports the application without first checking "
        "sys.modules; it runs for every test, and on CI that import costs more "
        "than the per-test timeout: %s" % offenders)


def test_the_scheduler_check_looks_in_sys_modules():
    """The positive half: it must still actually check something."""
    source = open(CONFTEST).read()
    body = source.split("def no_scheduler_left_running", 1)[1].split("\n@", 1)[0]
    assert 'sys.modules.get("app.scheduler")' in body, body[:400]
    assert "scheduler.running" in body, "the check no longer verifies anything"
