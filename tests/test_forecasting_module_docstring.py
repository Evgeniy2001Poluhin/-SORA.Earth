"""The module docstring must not describe a fallback chain that does not exist.

`app/services/forecasting/__init__.py` claimed:

    Fallback chain: Ensemble -> Prophet -> LinearTrend (never fails)

Three stages that are not stages, in a place that is not where degradation
happens (#123). The real production chain is MLflow -> on-demand Ensemble;
Prophet and LinearTrend live inside the ensemble as sample-size-weighted
members, and LinearTrend carries weight only in the two smallest tiers.

A documentation contract rather than a style check: the reason the wrong
version mattered is that "never fails" reads as a guarantee a caller can rely
on, while what actually happens on a member failure is a silent reweighting.
These assertions are about claims, so they check the text -- and each is
anchored to something checkable in the code beside it, so the docstring cannot
drift back without a test noticing.
"""
import inspect

from app.services import forecasting


DOC = inspect.getdoc(forecasting) or ""


def test_the_docstring_exists_at_all():
    """Guards the rest: every assertion below passes vacuously on an empty
    docstring, which is exactly how a "fix" could delete the problem."""
    assert len(DOC) > 400


def test_the_invented_chain_is_refuted_not_merely_absent():
    """Checked as a refutation, not as a missing substring.

    The docstring quotes the old claim in order to correct it, so "the words
    are gone" is the wrong test -- it would be satisfied by saying nothing,
    and it fails on text that says the opposite. The same over-clever check
    tripped once already in this codebase, on a register note that stated a
    distinction and was banned for containing the word.
    """
    lowered = DOC.lower()

    if "fallback chain: ensemble" in lowered:
        # Quoting it is allowed only alongside the correction.
        assert "does not exist" in lowered, \
            "the invented chain is repeated without being refuted"

    if "never fails" in lowered:
        assert 'is not "never fails"' in lowered, \
            "'never fails' appears as a guarantee rather than as the thing denied"


def test_it_names_the_two_real_stages_of_production_degradation():
    lowered = DOC.lower()
    assert "mlflow" in lowered
    assert "on demand" in lowered or "on-demand" in lowered


def test_it_says_the_ensemble_weights_rather_than_degrades():
    lowered = DOC.lower()
    assert "sample size" in lowered or "sample-size" in lowered


def test_it_places_lineartrend_in_the_small_tiers_not_after_a_failure():
    lowered = DOC.lower()
    assert "lineartrend" in lowered
    assert "smallest tiers" in lowered or "small tiers" in lowered


def test_it_warns_that_a_member_failure_redistributes_weight():
    lowered = DOC.lower()
    assert "redistribut" in lowered


def test_it_separates_returning_a_number_from_not_failing():
    lowered = DOC.lower()
    assert "always returns a number" in lowered


def test_lineartrend_really_is_weighted_only_in_the_small_tiers():
    """Anchors the claim above to the code, so the docstring is not merely
    self-consistent.

    Read from `ensemble.py` rather than restated: the assertion is that some
    tiers give LinearTrend weight and the majority give it none, which is what
    "only in the small tiers" means.
    """
    import re

    from app.services.forecasting import ensemble

    src = inspect.getsource(ensemble)
    weights = [float(m) for m in re.findall(r'"linear":\s*([0-9.]+)', src)]

    assert weights, "no linear weights found; the tier table moved"
    assert any(w > 0 for w in weights), \
        "LinearTrend is never weighted, so it is not an ensemble member"
    assert any(w == 0 for w in weights), \
        "LinearTrend is weighted in every tier, so 'only in the small ones' is wrong"


def test_production_degradation_really_has_two_stages():
    """The other anchor: Prophet and LinearTrend are not stages of it."""
    from app.services.forecasting import production_forecaster

    src = inspect.getsource(production_forecaster)

    assert "mlflow" in src.lower()
    assert "EnsembleForecaster" in src
    assert "ProphetForecaster" not in src, \
        "Prophet appears in the production chain; the docstring says it does not"
    assert "LinearTrendForecaster" not in src, \
        "LinearTrend appears in the production chain; the docstring says it does not"
