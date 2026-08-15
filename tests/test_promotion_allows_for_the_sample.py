"""A model clears the bar once the sample it was measured on is allowed for.

The gate compared point estimates: `new_auc >= 0.80`. That treats a number
measured on 171 rows as interchangeable with one measured on 3,415.

Measured, and this is why the change exists:

    AUC 0.81 on 171 rows  (120 pos / 51 neg)  ->  95% lower bound 0.746
    AUC 0.81 on 3,415 rows                    ->  95% lower bound 0.796

The first was accepted as clearing 0.80 while being indistinguishable from
0.75. **The new rule rejects models the old one accepted**, which is its
purpose and will read like a regression at the first refusal.
"""
import pytest

from app.model_quality import auc_lower_bound, auc_standard_error, clears_threshold


def test_a_small_sample_widens_the_interval():
    """The whole point, as two numbers."""
    small = auc_lower_bound(0.81, 120, 51)
    large = auc_lower_bound(0.81, 2064, 1351)

    assert small < large
    assert small == pytest.approx(0.746, abs=0.002)
    assert large == pytest.approx(0.796, abs=0.002)


def test_the_case_the_old_rule_accepted():
    passed, why = clears_threshold(0.81, 120, 51, 0.80)

    assert passed is False
    assert "lower bound" in why and "171 test rows" in why


def test_a_genuinely_good_model_still_passes():
    """Otherwise the gate is satisfied by one that refuses everything."""
    passed, why = clears_threshold(0.9251, 2064, 1351, 0.80)

    assert passed is True and why is None


def test_one_class_cannot_be_bounded_and_is_refused():
    """"We could not tell" and "it passed" are different answers, and only one
    of them is a promotion."""
    passed, why = clears_threshold(0.95, 171, 0, 0.80)

    assert passed is False
    assert "needs both classes" in why


def test_a_missing_auc_is_refused_not_assumed():
    passed, why = clears_threshold(None, 120, 51, 0.80)

    assert passed is False
    assert "no AUC" in why


def test_the_standard_error_is_none_not_zero_when_undefined():
    """Zero would claim a perfectly precise estimate, which is the opposite of
    what an absent class means."""
    assert auc_standard_error(0.9, 0, 50) is None
    assert auc_standard_error(1.5, 10, 10) is None


def test_the_bound_is_clipped_at_zero():
    """A negative AUC bound invites the reader to treat the arithmetic as the
    measurement."""
    assert auc_lower_bound(0.5, 2, 2) >= 0.0


def test_both_refusals_are_reachable():
    """They were an if/elif chain, and putting the bound at the front of it
    disabled the degradation check: a model that cleared the absolute bar never
    reached the second test. Read from the source, because the defect is the
    control flow rather than any single value.
    """
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "app", "scheduler.py"), encoding="utf-8") as fh:
        body = fh.read()

    assert "if promoted and old_auc is not None" in body
    assert "elif old_auc is not None and new_auc is not None:" not in body
