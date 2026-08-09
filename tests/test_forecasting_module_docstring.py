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


def _weights_at(n_samples, monkeypatch):
    """Weights the ensemble chooses for a sample of this size.

    Members' `fit` is stubbed out: the subject is the tier selection, and
    training a real LSTM five times would put this test into the timeout
    territory of #70 without testing anything more.
    """
    import pandas as pd

    from app.services.forecasting.ensemble import EnsembleForecaster

    forecaster = EnsembleForecaster()
    for member in ("lstm", "prophet", "linear"):
        monkeypatch.setattr(getattr(forecaster, member), "fit",
                            lambda *a, **k: None)

    df = pd.DataFrame({
        "ds": pd.date_range("2026-01-01", periods=n_samples, freq="D"),
        "y": [50.0 + (i % 7) for i in range(n_samples)],
    })
    forecaster.fit(df, "y")
    return forecaster.weights


def test_exactly_the_two_lowest_tiers_weight_lineartrend(monkeypatch):
    """"Only in the small tiers" asserted against the tiers, not the source text.

    Reading `"linear": 0.x` out of ensemble.py proves some tiers differ from
    others; it does not prove *which*, because the branches are not written in
    order of sample size. Driving fit() at a representative size per tier does.
    """
    positive = {n: _weights_at(n, monkeypatch)["linear"] for n in (5, 20)}
    zero = {n: _weights_at(n, monkeypatch)["linear"] for n in (40, 60, 120)}

    assert all(w > 0 for w in positive.values()), positive
    assert all(w == 0 for w in zero.values()), zero


def test_a_failed_member_hands_its_weight_to_another(monkeypatch):
    """The claim the docstring makes about silent reweighting.

    Asserted as behaviour: after an LSTM that cannot fit, its weight is gone
    and Prophet's has grown -- and the run does not raise.
    """
    import pandas as pd

    from app.services.forecasting.ensemble import EnsembleForecaster

    forecaster = EnsembleForecaster()
    monkeypatch.setattr(forecaster.prophet, "fit", lambda *a, **k: None)
    monkeypatch.setattr(forecaster.linear, "fit", lambda *a, **k: None)
    monkeypatch.setattr(
        forecaster.lstm, "fit",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("LSTM refused")),
    )

    df = pd.DataFrame({
        "ds": pd.date_range("2026-01-01", periods=120, freq="D"),
        "y": [50.0 + (i % 7) for i in range(120)],
    })
    forecaster.fit(df, "y")

    assert forecaster.weights["lstm"] == 0.0, "the failed member kept weight"
    assert forecaster.weights["prophet"] == 1.0, (
        "the surviving member does not carry the whole forecast, so the "
        "failure changed the result rather than the composition"
    )

    # Asserted as the observable outcome, deliberately. The explicit
    # `prophet += 0.7` in the failure branch turns out to have no observable
    # effect at all: `_normalize_weights()` divides by the total, and in every
    # tier where LSTM participates `linear` is 0.0, so Prophet reaches 1.0
    # with or without it -- 0.9/0.9 and 0.3/0.3 alike. Deleting that line
    # changes nothing measurable, which is why this asserts the end state.
    # The dead line is a finding about ensemble.py and does not belong in a
    # docstring change.


def test_a_failed_mlflow_load_selects_the_ensemble(monkeypatch):
    """The two stages, asserted by driving the degradation.

    Checking that both names appear in the module proves they are mentioned.
    This makes the first stage unavailable and asserts which model the second
    stage actually is.
    """
    import pandas as pd

    from app.services.forecasting import production_forecaster as pf
    from app.services.forecasting.ensemble import EnsembleForecaster

    monkeypatch.setattr(pf, "is_mlflow_available", lambda: True)
    monkeypatch.setattr(pf, "load_production_model", lambda *a, **k: None)
    monkeypatch.setattr(EnsembleForecaster, "fit", lambda self, *a, **k: None)

    forecaster = pf.ProductionForecaster(metric="score")
    forecaster.fit(
        pd.DataFrame({"ds": pd.date_range("2026-01-01", periods=40, freq="D"),
                      "y": [50.0] * 40}),
        "y",
    )

    assert forecaster.model_source == "fallback"
    assert isinstance(forecaster.fallback_model, EnsembleForecaster)


def test_prophet_and_lineartrend_are_not_stages_of_the_production_chain():
    """They exist only inside the ensemble, which is what the docstring says."""
    import inspect

    from app.services.forecasting import production_forecaster

    src = inspect.getsource(production_forecaster)

    assert "ProphetForecaster" not in src, \
        "Prophet appears in the production chain; the docstring says it does not"
    assert "LinearTrendForecaster" not in src, \
        "LinearTrend appears in the production chain; the docstring says it does not"
