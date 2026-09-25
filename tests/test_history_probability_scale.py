"""`/history` returns the success probability on the scale `/evaluate` does.

The history page multiplied it by 100 and printed 27.5 % as "2750%", while the
evaluate and compare pages print the same field unmultiplied. Nothing declared
the scale -- `/history` has no response model, the frontend type is `number` --
which is how a frontend fixture came to use fractions the API never sends. This
pins the scale on the side that produces it, so the frontend has something to
be checked against.
"""


def test_history_reports_the_probability_evaluate_reported(client):
    body = {"name": "scale-check", "budget": 100000, "co2_reduction": 50,
            "social_impact": 7, "duration_months": 24, "region": "Germany"}
    evaluated = client.post("/api/v1/evaluate", json=body)
    assert evaluated.status_code == 200, evaluated.text
    p = evaluated.json()["success_probability"]

    items = client.get("/api/v1/history").json()["items"]
    row = next(i for i in items if i.get("name") == "scale-check")

    assert row["success_probability"] == p
    assert 1 < p <= 100, (
        f"success_probability is {p}; this test assumes a project the model rates "
        f"above 1 %, so that a percentage and a fraction cannot be confused"
    )
