"""A path with no route answers 404, whatever the verb.

The SPA catch-all `/{full_path:path}` is a GET route. Starlette matches on path
first, so any unmatched POST in the API matched it, failed the method check and
came back **405 Method Not Allowed**:

    POST /api/v1/ab/compare          405     no such route
    POST /api/v1/totally/made/up     405     no such route
    GET  /api/v1/totally/made/up     404

405 asserts that the resource exists and the verb is wrong. That is a claim, and
it was false everywhere. It also cost an investigation: issue #4 records
`/api/v1/ab/compare` as "endpoint exists but doesn't accept POST -- need to add
POST handler". It does not exist; /api/v1/ab has predict, split and stats.

The risk in the fix is shadowing -- a blanket POST route that swallows real
ones. Most of this file is about that.
"""
import pytest


ABSENT = [
    "/api/v1/ab/compare",
    "/api/v1/totally/made/up",
    "/api/v1/xyz",
    "/nope",
    "/api/v1/predict/compare/extra/segment",
]

MUTATING = ["post", "put", "patch", "delete"]


@pytest.mark.parametrize("path", ABSENT)
@pytest.mark.parametrize("method", MUTATING)
def test_an_absent_path_is_not_found(client, method, path):
    # client.request, not client.delete(json=...): TestClient.delete takes no
    # body argument, and the first version of this file failed on a TypeError
    # rather than on anything about routing.
    response = client.request(method.upper(), path, json={})

    assert response.status_code == 404, (
        f"{method.upper()} {path} -> {response.status_code}; a path with no "
        f"route must not claim the verb is the problem"
    )


@pytest.mark.parametrize("path", ABSENT)
def test_get_on_the_same_paths_is_unchanged(client, path):
    """The SPA half still behaves as it did.

    A browser deep-link is a real client route and must keep reaching
    index.html (or 404 where the path is reserved) -- the fix must not make
    every GET a 404.
    """
    response = client.get(path)

    assert response.status_code in (200, 404), response.status_code


# --- the shadowing risk -----------------------------------------------------


def test_real_post_endpoints_still_reach_their_handlers(client):
    """The blanket route is last, so registration order must protect these.

    Asserted by status rather than by reading main.py: what matters is that the
    request reached a handler, not where the route is declared. A 404 here
    means the catch-all swallowed a real endpoint; 422 is a handler rejecting
    the empty body, which is proof it was reached.
    """
    real = [
        "/api/v1/evaluate",
        "/api/v1/predict",
        "/api/v1/ab/predict",
        "/api/v1/drift/compare",
        "/api/v1/predict/compare",
        "/api/v1/analytics/model-compare",
    ]

    swallowed = []
    for path in real:
        status = client.post(path, json={}).status_code
        if status == 404:
            swallowed.append(path)

    assert swallowed == [], (
        f"{swallowed} answered 404 to POST -- the catch-all is shadowing real "
        f"routes, which is worse than the 405 it replaced"
    )


def test_every_registered_post_route_is_still_reachable(client):
    """The whole set, not a sample.

    Picking six endpoints by hand tests the six I thought of. This walks every
    non-catch-all POST route the app declares and requires each to answer
    something other than 404.
    """
    from app.main import app

    unreachable = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if "POST" not in methods or "{full_path" in path or "{" in path:
            # Parameterised paths need a plausible value; they are covered by
            # their own tests. The catch-all is the thing under test.
            continue
        if client.post(path, json={}).status_code == 404:
            unreachable.append(path)

    assert unreachable == [], (
        f"{unreachable} are declared POST routes that now answer 404"
    )


def test_the_catchall_is_registered_last(client):
    """Order is what makes the above hold; state it directly.

    If a later refactor moves an include_router below the catch-all, those
    routes start returning 404 and the tests above would catch it -- but only
    for the endpoints they name. This states the invariant itself.
    """
    from app.main import app

    catchall_indexes = [
        i for i, route in enumerate(app.routes)
        if "{full_path" in getattr(route, "path", "")
        and getattr(route, "path", "") == "/{full_path:path}"
    ]
    api_indexes = [
        i for i, route in enumerate(app.routes)
        if getattr(route, "path", "").startswith("/api/")
    ]

    assert catchall_indexes, "the catch-all routes are gone"
    assert api_indexes, "no /api routes found, so this comparison proves nothing"
    assert min(catchall_indexes) > max(api_indexes), (
        f"a catch-all is registered at {min(catchall_indexes)}, before an API "
        f"route at {max(api_indexes)}; everything after it is shadowed"
    )


def test_a_wrong_verb_on_a_real_path_still_says_so(client):
    """405 has to keep meaning what it means.

    Turning every 405 into a 404 would be the same defect mirrored. `/api/v1/
    ab/stats` exists as GET, so POSTing to it is a genuine method error.
    """
    assert client.get("/api/v1/ab/stats").status_code != 404, (
        "the GET endpoint this case depends on has moved"
    )
    assert client.post("/api/v1/ab/stats", json={}).status_code == 405
