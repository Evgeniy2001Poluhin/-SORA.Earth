"""The AI teammate reports drift only when the detector measured it.

`DriftDetector.check_drift()` answers in one of four ways, and only two of them
measured anything:

    insufficient_data   fewer observations than min_samples     -- nothing measured
    no_baseline         observations, but no baseline to compare -- nothing measured
    stable              compared, nothing drifted                -- measured
    drift_detected      compared, something drifted              -- measured

The two that measured nothing still carry `drift_detected: False` and
`drift_score: 0.0`. Those are placeholders, not results.

`AITeammate._check_drift` special-cased `insufficient_data` and let everything
else fall through to the measured path. So on `no_baseline` -- a detector that
has never been given a baseline, which is the state of a fresh deployment --
the teammate recorded

    "No drift detected (score=0.0000)"

an observation stating a measurement nobody made.

The skip stays `info`, as `insufficient_data`'s always was: `decide()` turns
any drift *warning* into a retrain, and a missing baseline is not a reason to
retrain. One consequence is not changed here: `run()` still summarises a report
with no warnings as "OK: All systems healthy", including one whose drift check
was skipped. Saying otherwise needs a severity between `info` and `warning`,
which is a design decision rather than a correction.

The `insufficient_data` message had the same shape on a smaller scale: it
printed `ref=0, cur=0` from `.get(key, 0)` on keys the baseline path never
sends, while the answer it was describing carried the real count under
`observations`.

## How this is tested

The real `DriftDetector`, with only its Redis replaced by a dictionary, driven
into each of its four answers. Nothing here hands the teammate a hand-written
copy of the detector's answer -- `tests/test_drift_simulate_contract.py` stubs
`check_drift` with `{"status": "ok"}`, which is not an answer the detector
can give.

The measured answers are the controls: a fix that stopped reporting drift at
all would pass the two unmeasured cases alone.

`test_every_status_the_detector_can_return_is_driven` reads the statuses out of
`_baseline_drift_check` by AST and requires a scenario for each, so a fifth
answer added to the detector fails here until someone decides whether it
measured anything.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


class _MemoryRedis:
    """The five calls `DriftDetector` makes, over two dictionaries."""

    def __init__(self):
        self.kv: dict = {}
        self.lists: dict = {}

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value):
        self.kv[key] = value if isinstance(value, str) else str(value)
        return True

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def ltrim(self, key, start, end):
        items = self.lists.get(key, [])
        self.lists[key] = items[start:] if end == -1 else items[start:end + 1]

    def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        return items[start:] if end == -1 else items[start:end + 1]

    def llen(self, key):
        return len(self.lists.get(key, []))


@pytest.fixture()
def detector(monkeypatch):
    """A real detector, installed where the teammate imports it from."""
    import app.drift_detection as dd

    memory = _MemoryRedis()
    monkeypatch.setattr(dd.redis, "from_url", lambda *a, **k: memory)
    real = dd.DriftDetector(min_samples=5)
    monkeypatch.setattr(dd, "drift_detector", real)
    return real


def _observe(detector, rows):
    for row in rows:
        detector.add_observation(row)


def _drift_observations(teammate):
    return [o for o in teammate.observations if o.category == "drift"]


def _run_check(detector):
    from app.agents.ai_teammate import AITeammate

    answer = detector.check_drift()
    teammate = AITeammate(mode="observe")
    teammate._check_drift(db=None)
    return answer, _drift_observations(teammate)


# ---- the four answers ------------------------------------------------------

def _insufficient_data(detector):
    _observe(detector, [{"budget": 100.0}] * 2)


def _no_baseline(detector):
    _observe(detector, [{"budget": 100.0 + i} for i in range(8)])


def _stable(detector):
    detector.set_baseline({"budget_mean": 103.5, "budget_std": 5.0})
    _observe(detector, [{"budget": 100.0 + i} for i in range(8)])


def _drift_detected(detector):
    detector.set_baseline({"budget_mean": 10.0, "budget_std": 1.0})
    _observe(detector, [{"budget": 100.0 + i} for i in range(8)])


SCENARIOS = {
    "insufficient_data": (_insufficient_data, False),
    "no_baseline": (_no_baseline, False),
    "stable": (_stable, True),
    "drift_detected": (_drift_detected, True),
}


def _statuses_the_detector_can_return() -> set[str]:
    """Every literal `status` value in `_baseline_drift_check`, IfExp included."""
    tree = ast.parse((REPO / "app" / "drift_detection.py").read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_baseline_drift_check"):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Dict):
                continue
            for key, value in zip(inner.keys, inner.values):
                if not (isinstance(key, ast.Constant) and key.value == "status"):
                    continue
                for leaf in ast.walk(value):
                    if isinstance(leaf, ast.Constant) and isinstance(leaf.value, str):
                        found.add(leaf.value)
    return found


def test_every_status_the_detector_can_return_is_driven():
    statuses = _statuses_the_detector_can_return()
    assert len(statuses) >= 2, f"the AST scan found {statuses}; it is broken"
    assert statuses == set(SCENARIOS), (
        "the statuses _baseline_drift_check can return are not the ones this "
        "file drives.\n"
        f"  in the detector, no scenario: {sorted(statuses - set(SCENARIOS))}\n"
        f"  scenario, not in the detector: {sorted(set(SCENARIOS) - statuses)}\n"
        "A new status needs a scenario here, and a decision whether it "
        "measured anything."
    )


@pytest.mark.parametrize("status", sorted(SCENARIOS))
def test_the_scenario_produces_the_status_it_names(status, detector):
    """The setup, checked on its own: each case below judges the teammate,
    so a scenario that failed to reach its status must fail here instead."""
    arrange, _measured = SCENARIOS[status]
    arrange(detector)
    assert detector.check_drift()["status"] == status


@pytest.mark.parametrize("status", ["insufficient_data", "no_baseline"])
def test_an_answer_that_measured_nothing_is_not_reported_as_no_drift(status, detector):
    SCENARIOS[status][0](detector)

    answer, observed = _run_check(detector)

    assert answer["status"] == status
    assert len(observed) == 1, observed
    message = observed[0].message
    assert "No drift detected" not in message, (
        f"on a {status!r} answer the teammate recorded {message!r}. The "
        f"detector compared nothing; drift_score=0.0 on this answer is a "
        f"placeholder, not a score."
    )
    assert "skipped" in message.lower(), message
    assert observed[0].severity == "info", (
        "an unmeasured drift check must stay `info`: decide() turns any drift "
        "warning into a retrain, and a missing baseline is not a reason to "
        "retrain"
    )


@pytest.mark.parametrize("status", ["insufficient_data", "no_baseline"])
def test_the_skip_message_quotes_only_counts_the_answer_carries(status, detector):
    """`ref=0, cur=0` was printed from keys the baseline path never sends."""
    SCENARIOS[status][0](detector)

    answer, observed = _run_check(detector)
    message = observed[0].message

    assert "ref=0" not in message and "cur=0" not in message, message
    assert f"observations={answer['observations']}" in message, (
        f"the answer carried observations={answer['observations']} and the "
        f"message says {message!r}"
    )


def test_a_stable_answer_is_still_reported_as_no_drift(detector):
    """Control: the measured negative must survive the fix."""
    _stable(detector)

    answer, observed = _run_check(detector)

    assert answer["status"] == "stable"
    assert len(observed) == 1, observed
    assert observed[0].message.startswith("No drift detected"), observed[0].message
    assert observed[0].severity == "info"


def test_a_detected_drift_is_still_a_warning(detector):
    """Control: the measured positive must survive the fix."""
    _drift_detected(detector)

    answer, observed = _run_check(detector)

    assert answer["status"] == "drift_detected"
    assert len(observed) == 1, observed
    assert observed[0].severity == "warning", observed[0]
    assert "budget" in observed[0].message, observed[0].message
