"""The retired quality claim is retired where a consumer can read it.

#232 closed as Option C -- *retire the quality claim*. `success` is derived from
a project's administrative status and closing date; `duration_months` is a
second reading of the same fact, so the headline ROC AUC is largely the leak.
Measured against the current file on 2026-09-20
(`docs/DATASET_ABLATION_2026-09-20_SYNTHETIC_ROWS.md`): 0.9165 with everything,
0.8700 from `budget` and `duration_months` alone, 0.6605 with the
closing-date-derived column removed.

The decision was recorded in the ablation document and in a comment on
`MIN_AUC_THRESHOLD` -- both places a developer looks. The OpenAPI schema, which
is what anyone integrating against this reads, still described the number as
"Probability that the project succeeds": a statement about the world, and the
one the resolution retired.

So these tests are about where a decision lands, not about whether it was taken.
A caveat that lives only where the people who already know it look is not a
caveat.

**When a real label arrives** -- an independent outcome rating, IEG being the
candidate named on #232 -- these descriptions should change and this test with
them. That is the point: it makes the wording a deliberate edit rather than
something that drifts back.
"""
import pytest


@pytest.fixture(scope="module")
def schema():
    from fastapi.openapi.utils import get_openapi

    import app.main as main

    return get_openapi(
        title="t", version="0", routes=main.app.routes,
    )["components"]["schemas"]


def _description(schema, model, field):
    assert model in schema, f"{model} is not in the published schema"
    properties = schema[model].get("properties", {})
    assert field in properties, f"{model}.{field} is not published"
    return properties[field].get("description", "")


@pytest.mark.parametrize("model", ["PredictResponse", "PredictV2Ok"])
def test_the_field_does_not_promise_a_probability_of_success(schema, model):
    """The exact phrasing #232 retired, and the ones that mean the same.

    "Probability that the project succeeds" describes an outcome in the world.
    What the model estimates is a label built from whether a project has a
    usable closing date.
    """
    text = _description(schema, model, "success_probability").lower()

    assert text, (
        f"{model}.success_probability carries no description at all, so the "
        f"OpenAPI page an integrator reads says nothing about what the number "
        f"is or is not"
    )
    for claim in ("probability that the project succeeds",
                  "probability the project succeeds",
                  "likelihood of success"):
        assert claim not in text, (
            f"{model}.success_probability still describes itself as {claim!r}, "
            f"which is the claim #232 retired"
        )


@pytest.mark.parametrize("model", ["PredictResponse", "PredictV2Ok"])
def test_the_field_says_what_the_label_actually_is(schema, model):
    """Naming the basis, not just avoiding the claim.

    Removing the sentence and leaving nothing would pass the test above and
    help nobody: a bare number with no description reads as self-evident.
    """
    text = _description(schema, model, "success_probability").lower()

    assert "heuristic" in text or "administrative" in text, (
        f"{model}.success_probability avoids the retired claim without saying "
        f"what the label is. A consumer cannot tell a validated outcome "
        f"probability from a status-derived heuristic by looking at a float."
    )
    assert "#232" in text or "232" in text, (
        f"{model}.success_probability does not point at the issue that holds "
        f"the measurement, so a reader who wants the figures has nowhere to go"
    )

def test_the_evaluate_route_publishes_no_schema_at_all(schema):
    """Recorded, not fixed here, because fixing it is an API change.

    `POST /api/v1/evaluate` declares no `response_model`, so the OpenAPI page
    describes none of what it returns -- and it is the endpoint an ESG user
    would reach for first. `ESGResult` in app/schemas.py looks like that
    schema and is wired to nothing: defined once, referenced by no route,
    absent from the published components.

    Giving the route a response_model is not a docstring change. FastAPI would
    then filter the response to the declared fields, so anything the handler
    returns and the model omits disappears -- a silent break for a consumer
    reading it today. That needs the field list checked against a real
    response, and it is the owner's call.

    This test fails the day someone wires it, which is the moment to delete the
    test and the note with it.
    """
    assert "ESGResult" not in schema, (
        "ESGResult is now published -- if /evaluate gained a response_model, "
        "check that every field the handler returns is declared, and remove "
        "this test"
    )
