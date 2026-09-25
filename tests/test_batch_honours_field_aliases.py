"""A batch row means what the same row means on /evaluate.

`ProjectInput` gives every field an alias and sets `populate_by_name=True`, so
`/evaluate` accepts both `budget` and `budget_usd`, both `region` and `country`
-- the second spelling is the one `/evaluate/monte-carlo` requires. The batch
endpoint built each project from

    {k: v for k, v in p.items() if k in Project.model_fields}

which keeps field *names* only. Every alias was discarded before validation, and
every field has a default, so a row spelled the other way was scored as an
empty project and counted under `successful`. Measured 2026-09-24, Sweden, the
same numbers both ways: 89.76 by field names, 34.66 by aliases -- identical to
the 34.66 of a row carrying no fields at all.

Asserted as equality with the field-name spelling, which is what the row means,
and inequality with the empty row, which is what it silently became. The second
guards the first: two rows that both fell to defaults would also be equal.
"""
import pytest


def _score(client, row):
    r = client.post("/api/v1/batch/evaluate", json={"projects": [row]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["successful"] == 1 and body["failed"] == 0, body
    return body["results"][0]["total_score"]


BY_NAME = {"name": "p", "budget": 900000, "co2_reduction": 800, "social_impact": 9,
           "duration_months": 12, "region": "Sweden"}
BY_ALIAS = {"name": "p", "budget_usd": 900000, "co2_reduction_tons_per_year": 800,
            "social_impact_score": 9, "project_duration_months": 12, "region": "Sweden"}
EMPTY = {"name": "p", "region": "Sweden"}


def test_a_row_spelled_with_aliases_scores_like_the_same_row_by_field_name(client):
    by_name, by_alias, empty = _score(client, BY_NAME), _score(client, BY_ALIAS), _score(client, EMPTY)

    assert by_name != empty, "the reference row itself scored as an empty project; nothing is being compared"
    assert by_alias == by_name, (
        f"the aliased row scored {by_alias} against {by_name} for the same numbers "
        f"by field name (an empty row scores {empty}). The batch dropped the "
        f"aliases that /evaluate accepts."
    )


def test_the_country_alias_reaches_the_score(client):
    """`country` is `region`'s alias; dropped, the row silently scores as Europe."""
    base = {k: v for k, v in BY_NAME.items() if k != "region"}
    as_region = _score(client, {**base, "region": "India"})
    as_country = _score(client, {**base, "country": "India"})
    default = _score(client, base)

    assert as_region != default, "India and the default region score alike; the test cannot tell them apart"
    assert as_country == as_region, (
        f"a row with country=India scored {as_country}, the default-region score is "
        f"{default}, region=India scores {as_region}"
    )
