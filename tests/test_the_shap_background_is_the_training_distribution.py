"""A SHAP background must be a sample of what the model was trained on.

`app/api/explainability.py` serves `/explain/local` and `/explain/global`
through a `shap.KernelExplainer`. The explainer's background is the reference
distribution: every attribution it produces is *relative to it*. Get the
background wrong and the numbers are attributions against a world that does not
exist.

It was built from twelve hardcoded means and twelve hardcoded standard
deviations. Measured 2026-09-21 against `models/scaler_v2.pkl`, whose `mean_`
and `scale_` are the training distribution itself, the background's centre sat:

```
budget_efficiency        +99.45 sigma
social_impact             -7.02 sigma
duration_months           -1.78
category_enc              -1.70
region_enc                -1.02
everything else           within +-0.7
```

Ninety-nine standard deviations. `budget_efficiency` has a training mean of
0.0019 and a spread of about 0.1; the background put it at 10.

The feature order was checked and is correct -- `FEATURE_COLS` matches
`scaler.feature_names_in_` name for name and position for position. The values
were applied to the right columns; they were simply not the training
distribution.

## What this file does not fix

`/explain/global` computes "global feature importance" by explaining the
background against itself:

    sample_df = bg_df.head(20)
    sv = expl.shap_values(scaler.transform(sample_df), ...)

No real data enters that calculation, and a correct background does not change
that. Which real sample it should explain -- `data/projects.csv`, recent
`predictions_log` rows, or something else -- is a decision about where serving
code may read data from, not a bug with one right answer. It is stated here
rather than quietly fixed.

`/explain/local` is the endpoint this repairs: it explains a real project the
caller supplied, and against a background centred in the training distribution
that answer means something.
"""
import os
import pickle

import numpy as np
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCALER = os.path.join(REPO, "models", "scaler_v2.pkl")

#: How far the background's centre may sit from the training mean. A background
#: *drawn from* that distribution lands within a fraction of a sigma; one sigma
#: is generous and still refuses the +99 this was written for.
MAX_CENTRE_SIGMA = 1.0

#: How far its spread may be from the training spread, as a ratio. A sample of
#: 50 wobbles; an order of magnitude is not wobble.
MAX_SPREAD_RATIO = 3.0


@pytest.fixture(scope="module")
def scaler():
    if not os.path.exists(SCALER):
        pytest.skip("models/scaler_v2.pkl is not present")
    with open(SCALER, "rb") as handle:
        return pickle.load(handle)


@pytest.fixture(scope="module")
def background(scaler):
    from app.api.explainability import _make_background
    _scaled, frame = _make_background(scaler, n=500)
    return frame


def test_the_comparison_has_both_sides(scaler, background):
    """Neither side may be empty, or every check below passes on nothing."""
    from app.api.explainability import FEATURE_COLS

    assert getattr(scaler, "mean_", None) is not None, (
        "the scaler carries no mean_, so there is nothing to compare against"
    )
    assert len(scaler.mean_) == len(FEATURE_COLS) == 12
    assert list(scaler.feature_names_in_) == list(FEATURE_COLS), (
        "FEATURE_COLS and the scaler disagree about which column is which, so "
        "the per-feature comparison below would be comparing different features"
    )
    assert len(background) == 500, "the background was not generated"


def test_the_background_is_centred_in_the_training_distribution(scaler, background):
    """The reference point every attribution is measured from."""
    from app.api.explainability import FEATURE_COLS

    offenders = []
    for i, name in enumerate(FEATURE_COLS):
        sigma = float(scaler.scale_[i])
        if sigma == 0:
            continue
        z = (float(background[name].mean()) - float(scaler.mean_[i])) / sigma
        if abs(z) > MAX_CENTRE_SIGMA:
            offenders.append((name, z, float(scaler.mean_[i]), float(background[name].mean())))

    assert not offenders, (
        "the SHAP background sits outside the distribution the model was "
        "trained on:\n  "
        + "\n  ".join(
            f"{n}: {z:+.2f} sigma (training mean {m:.4g}, background {b:.4g})"
            for n, z, m, b in offenders)
        + "\nEvery attribution from /explain/local is measured against this "
        "point. `models/scaler_v2.pkl` carries the training mean and spread "
        "exactly; there is no need to guess them."
    )


def test_the_background_spread_matches_the_training_spread(scaler, background):
    """A centred background with the wrong width is still the wrong reference."""
    from app.api.explainability import FEATURE_COLS

    offenders = []
    for i, name in enumerate(FEATURE_COLS):
        train_sigma = float(scaler.scale_[i])
        bg_sigma = float(background[name].std())
        if train_sigma == 0 or bg_sigma == 0:
            continue
        ratio = bg_sigma / train_sigma
        if ratio > MAX_SPREAD_RATIO or ratio < 1 / MAX_SPREAD_RATIO:
            offenders.append((name, ratio, train_sigma, bg_sigma))

    assert not offenders, (
        "the SHAP background's spread does not match the training spread:\n  "
        + "\n  ".join(
            f"{n}: {r:.2f}x (training sigma {t:.4g}, background {b:.4g})"
            for n, r, t, b in offenders)
    )


def test_the_check_can_fail(scaler):
    """Positive control: the z-score machinery reports a displaced centre.

    Without this, a background that happened to be correct would make both
    checks above pass whether or not they compute anything.
    """
    import pandas as pd

    from app.api.explainability import FEATURE_COLS

    displaced = pd.DataFrame(
        {name: [float(scaler.mean_[i]) + 50 * float(scaler.scale_[i])]
         for i, name in enumerate(FEATURE_COLS)}
    )
    z = [(float(displaced[n].mean()) - float(scaler.mean_[i])) / float(scaler.scale_[i])
         for i, n in enumerate(FEATURE_COLS) if scaler.scale_[i]]
    assert z and min(abs(v) for v in z) > MAX_CENTRE_SIGMA, (
        "a background displaced by 50 sigma was not reported as displaced; the "
        "comparison above is not computing what it claims"
    )
