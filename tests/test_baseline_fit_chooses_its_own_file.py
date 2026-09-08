"""The baseline endpoint must read this platform's dataset, not a path a
caller names (GHSA-2xr2-4767-23gm).

`POST /api/v1/mlops/drift/baseline/fit` took `csv_path` as a query parameter
and had no authentication. Read out of the route table rather than the source:

    POST /api/v1/mlops/drift/baseline/fit   query: csv_path   dependencies: 0
    POST /api/v1/mlops/drift/observe        query: -          require_api_key

Both handlers are in `app/api/drift_baseline.py`. The second requires an API
key; the first required nothing and then did `os.path.exists(csv_path)`,
`pd.read_csv(csv_path)`, and answered with the row count and the mean and
standard deviation of every column pandas read as numeric -- and repeated the
path back in its 404.

So, without credentials: ask whether any path exists, learn the shape of any
file that parses as CSV, and hand `pd.read_csv` a large file or a character
device.

**What these tests do not cover.** The endpoint is still unauthenticated and
still overwrites the process-wide drift baseline. The SPA's "Fit baseline"
button calls it from the browser and only sends a key when
`VITE_DEV_API_KEY` was set at build time, so adding `require_api_key` here
would break it. That is a product decision and it is in the advisory.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

FIT = "/api/v1/mlops/drift/baseline/fit"


def route():
    for r in app.routes:
        if getattr(r, "path", "") == FIT:
            return r
    raise AssertionError(f"{FIT} is not registered")


def test_the_endpoint_is_reachable_and_this_test_judges_it():
    """Negative control. Every assertion below is about this one route."""
    r = route()
    assert "POST" in r.methods, sorted(r.methods)

    answer = client.post(FIT)
    assert answer.status_code == 200, answer.text
    assert answer.json()["samples"] > 0, (
        "the platform's own dataset came back empty, so the assertions below "
        "would pass over nothing"
    )


def test_no_caller_supplied_path_reaches_the_handler():
    """The defect, at the route table rather than in prose.

    Asserted on the parameter list, not by sending a path and checking the
    answer: a handler could accept the parameter and ignore it today, and
    start using it tomorrow with nothing going red.
    """
    names = [q.name for q in route().dependant.query_params]

    assert "csv_path" not in names, (
        f"{FIT} still takes a server-side path from the caller: {names}"
    )
    assert names == [], (
        f"{FIT} grew a query parameter; if it is a path again this is the "
        f"defect returning: {names}"
    )


def test_a_path_sent_anyway_is_not_obeyed():
    """The other half: old callers keep working and are not followed.

    The SPA still appends `?csv_path=data/projects.csv`, and
    `scripts/smoke.sh` posts without one. FastAPI ignores unknown query
    parameters, so both get the same answer -- which is what makes this fix
    safe to deploy without touching the frontend.
    """
    plain = client.post(FIT)
    with_path = client.post(FIT, params={"csv_path": "/etc/hostname"})

    assert plain.status_code == with_path.status_code == 200
    assert plain.json()["samples"] == with_path.json()["samples"], (
        "the answer changed when a path was supplied, so it was read"
    )
    assert plain.json()["features"] == with_path.json()["features"]


def test_the_not_found_message_names_no_path(monkeypatch, tmp_path):
    """404 used to repeat the caller's path, which is what made it an oracle.

    Now the path is fixed, so echoing it would only describe this deployment's
    layout to anyone who asks.
    """
    import app.api.drift_baseline as module

    monkeypatch.setattr(module, "data_dir", lambda: str(tmp_path))

    answer = client.post(FIT)

    assert answer.status_code == 404, answer.text
    detail = answer.json()["detail"]
    assert str(tmp_path) not in detail and "/" not in detail, (
        f"the 404 discloses a filesystem path: {detail!r}"
    )


def test_the_baseline_comes_from_the_platform_dataset():
    """It must read the file the rest of the platform trains on.

    Bound to `data_dir()` rather than to a literal, so a deployment that sets
    `SORA_DATA_DIR` gets its own dataset and not the repository's -- the split
    between writer and reader that the same override causes elsewhere.
    """
    from app.paths import data_dir

    import pandas as pd

    expected = pd.read_csv(os.path.join(data_dir(), "projects.csv"))
    answer = client.post(FIT).json()

    assert answer["samples"] == len(expected), (
        f"the endpoint fitted on {answer['samples']} rows and "
        f"{data_dir()}/projects.csv has {len(expected)}"
    )
