"""Activation has one door, and this counts the doors rather than testing two.

`docs/DEVELOPMENT_ROADMAP.md` phase 5 states the property as its exit criterion:
*"No endpoint bypasses registry, CI bound, baseline and non-degradation."* It
holds today. Nothing was watching that it keeps holding.

Measured 2026-09-21: a function added to `app/api/analytics.py` that does

```python
from app.model_source import activate
return activate(run_id)
```

bypasses the gate completely, and **72 tests across the five files that cover
promotion passed with it in the tree** -- `test_one_promotion_gate.py`,
`test_training_does_not_promote.py`, `test_auto_retrain_activates.py`,
`test_promotion_path_is_described_as_it_is.py`, `test_model_source.py`.

Every one of them asserts that the two known paths behave. None asserts that
there are only two. That is the difference between testing the examples and
testing the space, and it is the shape of defect this repository keeps finding
in its own guards.

## The chain being pinned

```
app.model_source.activate(run_id)          the only writer of runtime/active/
  <- app.model_loader.activate_promoted_candidate   its only caller
       guards on `if not run_id: return None`
  <- app.api.infra.auto_retrain_on_drift   POST /mlops/auto-retrain
  <- app.scheduler.closed_loop_retrain     the daily and weekly jobs
       both pass `run_id if promoted else None`
       both compute `promoted` from gated_decision(...)
```

Three assertions, because breaking any one of them reopens the bypass:

1. `activate` is called from exactly one function;
2. `activate_promoted_candidate` is called from exactly the two gated ones;
3. each of those two also calls the gate, so passing `promoted` is not
   decorative.

## Why the sets are written out rather than counted

A count would pass if a caller were replaced rather than added. The names are
listed, so moving the door shows up as a named difference and has to be an
explicit edit to this file -- which is the point at which someone thinks about
whether the new caller is gated.

Aliased imports are resolved: `from app.model_source import activate as _a`
followed by `_a(run_id)` is the same bypass, and matching on the text `activate(`
would miss it.
"""
import ast
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(REPO, "app")

#: The function that writes `runtime/active/`, and the helper that guards it.
_ACTIVATE = ("app.model_source", "activate")
_HELPER = ("app.model_loader", "activate_promoted_candidate")
_GATE = {"gated_decision", "evaluate_promotion"}

#: Where each is defined. A module may call what it defines without that being
#: a second door.
_DEFINING_MODULE = {
    "activate": "app/model_source.py",
    "activate_promoted_candidate": "app/model_loader.py",
}

EXPECTED_ACTIVATE_CALLERS = {
    "app/model_loader.py::activate_promoted_candidate",
}

EXPECTED_HELPER_CALLERS = {
    "app/api/infra.py::auto_retrain_on_drift",
    "app/scheduler.py::closed_loop_retrain",
}


def _python_files():
    for dirpath, _dirs, names in os.walk(APP):
        for name in sorted(names):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _enclosing(tree, lineno):
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = node.end_lineno or node.lineno
            if node.lineno <= lineno <= end:
                if best is None or node.lineno > best.lineno:
                    best = node
    return best


def callers_of(module: str, symbol: str):
    """Every `path::function` that calls `module.symbol`, aliases resolved."""
    found = set()
    for path in _python_files():
        rel = os.path.relpath(path, REPO)
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover - a broken file fails louder elsewhere
            continue

        local_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == module:
                for alias in node.names:
                    if alias.name == symbol:
                        local_names.add(alias.asname or alias.name)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = None
            if isinstance(func, ast.Name) and func.id in local_names:
                called = func.id
            elif isinstance(func, ast.Attribute) and func.attr == symbol:
                # `model_source.activate(...)` -- an attribute on the module.
                called = symbol
            if called is None:
                continue
            if rel == _DEFINING_MODULE.get(symbol) and not local_names:
                continue
            enclosing = _enclosing(tree, node.lineno)
            name = enclosing.name if enclosing else "<module>"
            found.add(f"{rel}::{name}")
    return found


def _calls_the_gate(rel_path: str, func_name: str) -> bool:
    """Whether the function *calls* the gate -- by AST, not by substring.

    The first version searched the function's source text, and a well-posed
    mutation walked straight through it: deleting the `gated_decision(...)`
    call and hardcoding `promoted = True` left this returning True, because two
    lines above the deleted call there is a comment that says
    `gated_decision also refuses when the champion's score cannot be read`.

    The detector was matching the explanation of the check rather than the
    check. Measured while writing this file, and the reason it is written down
    here is that the same substring shortcut is what the first version of
    `planned_tasks` in tests/test_the_roadmap_now_is_still_outstanding.py did.
    """
    with open(os.path.join(REPO, rel_path), encoding="utf-8") as handle:
        source = handle.read()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func_name:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None)
            if name in _GATE:
                return True
        return False
    return False


def test_the_scan_finds_the_callers_it_judges():
    """A scan that found nothing would make all three checks below vacuous."""
    activate_callers = callers_of(*_ACTIVATE)
    helper_callers = callers_of(*_HELPER)
    assert activate_callers, (
        "no caller of app.model_source.activate was found at all; the scan is "
        "broken, not the code"
    )
    assert helper_callers, (
        "no caller of activate_promoted_candidate was found at all"
    )
    assert len(list(_python_files())) > 20, (
        "the walk over app/ returned almost nothing, so nothing was scanned"
    )


def test_activation_has_exactly_one_door():
    found = callers_of(*_ACTIVATE)
    assert found == EXPECTED_ACTIVATE_CALLERS, (
        "the set of functions calling app.model_source.activate has changed.\n"
        f"  expected: {sorted(EXPECTED_ACTIVATE_CALLERS)}\n"
        f"  found:    {sorted(found)}\n"
        "activate() is the only writer of runtime/active/. A caller that does "
        "not go through activate_promoted_candidate promotes a model the gate "
        "never saw. If the new caller is gated, add it here deliberately."
    )


def test_the_door_is_opened_only_by_the_two_gated_paths():
    found = callers_of(*_HELPER)
    assert found == EXPECTED_HELPER_CALLERS, (
        "the set of functions calling activate_promoted_candidate has changed.\n"
        f"  expected: {sorted(EXPECTED_HELPER_CALLERS)}\n"
        f"  found:    {sorted(found)}"
    )


def test_each_gated_path_actually_consults_the_gate():
    """Being in the set above is not enough; `promoted` has to come from somewhere.

    Both call sites pass `run_id if promoted else None`. If a path stopped
    computing `promoted` from the gate, the call would still be in the set and
    the argument would still be named the same.
    """
    ungated = []
    for entry in sorted(EXPECTED_HELPER_CALLERS):
        rel, func = entry.split("::")
        if not _calls_the_gate(rel, func):
            ungated.append(entry)
    assert not ungated, (
        "these functions activate without consulting the promotion gate: "
        + ", ".join(ungated)
        + f"\n(looked for any of {sorted(_GATE)} in the function body)"
    )


def test_the_gate_check_can_fail():
    """The positive control for the check above.

    A function that plainly does not consult the gate must be reported as such,
    or `test_each_gated_path_actually_consults_the_gate` proves nothing.
    """
    assert not _calls_the_gate("app/model_loader.py", "reload_champion"), (
        "reload_champion was reported as consulting the promotion gate, which "
        "it does not -- the detector answers yes to everything"
    )
