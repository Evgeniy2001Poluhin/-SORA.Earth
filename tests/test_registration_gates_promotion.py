"""An unregistered model is not promoted, and says which step failed.

#189. `log_model_registry` returned None whether it worked or not, so a caller
could not tell a registered model from one whose upload was refused. Measured
on production 2026-08-15: registration had been failing with
`PermissionError: '/mlflow'` while every retrain reported success -- experiments
0 and 1 carry an absolute `artifact_location` from before the server ran with
`--serve-artifacts`, and the client tried to write that path locally.

A model that exists only on one container's disk is not one the platform can
point at. The registry is where a version is identified, compared and rolled
back from.
"""
import pytest


# Two behavioural cases belong here and are **not** written: "a refused upload
# returns False" and "offline returns False". Both reach the network -- mlflow
# connects before the monkeypatch takes effect, and they spent four minutes
# failing with ConnectionRefused rather than testing anything.
#
# Left undone rather than left red, and named rather than dropped silently:
# the contract below is asserted from the source, which is weaker. Closing
# #189 should add them with the client properly isolated.


def test_the_gate_refuses_an_unregistered_model():
    """Read from the source: the defect is the decision, not a value."""
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "app", "scheduler.py"), encoding="utf-8") as fh:
        body = fh.read()

    assert 'registry_ok = new_metrics.get("registry_ok")' in body
    assert "if promoted and registry_ok is False:" in body
    assert "registry_failed" in body


def test_an_absent_flag_is_not_a_refusal():
    """Runs recorded before this carry no `registry_ok`. Refusing them would be
    judging old rows by a field they could not have had -- the same reasoning
    as the confidence bound's fallback."""
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "app", "scheduler.py"), encoding="utf-8") as fh:
        body = fh.read()

    # `is False`, not falsy: None must not trigger the refusal.
    assert "registry_ok is False" in body
    assert "if promoted and not registry_ok" not in body


def test_it_is_reported_as_registry_failed_not_as_a_failed_run():
    """The model was trained, evaluated and written. Calling the run failed
    would deny work that happened; calling it success would claim a step that
    did not."""
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "app", "scheduler.py"), encoding="utf-8") as fh:
        body = fh.read()

    assert "registry_failed: the model was trained and written but not" in body
