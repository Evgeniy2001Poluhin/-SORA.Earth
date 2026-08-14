"""Nine columns, and the document says how many of them carry information.

`CLAUDE.md` said "9 features". Two of them -- `year` and `quarter` -- are
computed in `_do_retrain` from `datetime.utcnow()` at the moment of the retrain,
so every row in the frame gets the same value. A constant column separates
nothing; the model fits on seven.

That is not leakage and not a crash. It is the same shape as
`legacy_hash_count()`: code that exists, reads as a feature, and does not do
what its name says. The count in a document read by both people and models is
exactly where it does damage.

This pins the description against the code, so the two cannot drift the way the
README's test count did.
"""
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAUDE_MD = os.path.join(REPO_ROOT, "CLAUDE.md")


@pytest.fixture(scope="module")
def text():
    return re.sub(r"\s+", " ", open(CLAUDE_MD, encoding="utf-8").read())


def test_the_constant_columns_are_named(text):
    assert "Nine columns, seven of which carry information" in text
    assert "`year` and `quarter` are computed" in text


def test_the_reason_they_are_kept_is_recorded(text):
    """Otherwise the next reader deletes two columns and the pickled model
    refuses the frame."""
    assert "would refuse eight" in text


def test_the_unused_csv_columns_are_named(text):
    for column in ("category", "region", "country_gdp_per_capita"):
        assert column in text, column


def test_the_retrain_really_does_compute_them_per_run(text):
    """Read from the source, so the claim cannot outlive the code.

    If someone makes `year` a real observation year, this fails and the
    paragraph above has to be rewritten -- which is the point.
    """
    with open(os.path.join(REPO_ROOT, "app", "api", "retrain.py"),
              encoding="utf-8") as fh:
        body = fh.read()

    assert re.search(r'df\["year"\]\s*=\s*_dt\.utcnow\(\)\.year', body)
    assert re.search(r'df\["quarter"\]\s*=\s*\(_dt\.utcnow\(\)\.month', body)


def test_the_declared_order_matches_the_retrain(text):
    """The nine names, in order, as _do_retrain builds them."""
    with open(os.path.join(REPO_ROOT, "app", "api", "retrain.py"),
              encoding="utf-8") as fh:
        body = fh.read()

    block = body[body.index("feature_cols = ["):]
    block = block[:block.index("]")]
    in_code = re.findall(r'"([a-z0-9_]+)"', block)

    assert in_code == ["budget", "co2_reduction", "social_impact",
                       "duration_months", "budget_per_month", "co2_per_dollar",
                       "efficiency_score", "year", "quarter"]
    for name in in_code:
        assert name in text, name
