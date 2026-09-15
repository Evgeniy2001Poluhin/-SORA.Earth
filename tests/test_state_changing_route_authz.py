"""Every state-changing route has an explicit access decision, and the
administrative ones refuse an anonymous or non-admin caller (Security C).

This is the route-policy registry the advisory asks for. It is built from the
running application's `app.routes`, not from a static scan, so a router included
dynamically is still seen. Each route that changes or removes state must be
classified `public`, `admin`, `api_key`, or `gap` — a route in none of them
fails the registry, so a new endpoint cannot be added without a decision.

`gap` names a route left unguarded on purpose in this pass, each with a reason:
they belong to a different advisory (the webhook SSRF chain, the unauthenticated
writers branch) or need ownership scoping rather than a blanket admin check.
Listing them here is the decision that they are known and deferred, not that
they are safe.
"""
import pytest
from fastapi.routing import APIRoute

from app.auth import create_access_token

# --- the policy, one entry per state-changing (path, method-agnostic) route ---

PUBLIC = {
    # Authentication endpoints: cannot require a token to obtain a token.
    "/api/v1/auth/login", "/api/v1/auth/login-json", "/api/v1/auth/refresh",
    # Compute-and-return: the product's public surface. Cost is CPU, which the
    # rate limiter bounds; they change no server state.
    "/api/v1/predict", "/api/v1/predict/compare", "/api/v1/predict/explain",
    "/api/v1/predict/explain/waterfall", "/api/v1/predict/neural",
    "/api/v1/predict/stacking", "/api/v1/predict/uncertainty", "/api/v1/predict/v2",
    "/api/v2/predict",
    "/api/v1/evaluate", "/api/v1/evaluate/monte-carlo", "/api/v1/evaluate/ranking",
    "/api/v1/analytics/model-compare", "/api/v1/analytics/monte-carlo",
    "/api/v1/calibration/brier", "/api/v1/calibration/discrepancy",
    "/api/v1/calibration/reliability",
    "/api/v1/compliance/check", "/api/v1/compliance/csrd", "/api/v1/compliance/gap-analysis",
    "/api/v1/copilot/explain", "/api/v1/copilot/explain/stream", "/api/v1/copilot/qa",
    "/api/v1/drift/analyze", "/api/v1/drift/compare",
    "/api/v1/explain/local", "/api/v1/ghg-calculate", "/api/v1/shap",
    "/api/v1/what-if", "/api/v1/ab/predict",
    "/api/v1/report/pdf", "/api/v1/reports/compliance.pdf",
    "/api/v1/reports/compliance-batch.pdf",
}

# Routes that must carry require_admin: the ones already guarded (pinned here so
# a future change cannot quietly open them) and the ones this fix guards.
ADMIN = {
    # Already require_admin before this fix.
    "/api/v1/admin/ai-teammate/run", "/api/v1/admin/ai/full-pipeline",
    "/api/v1/admin/ai/refresh", "/api/v1/admin/ai/report", "/api/v1/admin/ai/retrain",
    "/api/v1/mlops/auto-retrain", "/api/v1/mlops/full-pipeline",
    "/api/v1/model/data/bulk-upload", "/api/v1/model/data/bulk-upload/content",
    "/api/v1/model/data/refresh", "/api/v1/model/retrain",
    "/api/v1/model/retrain/{retrain_log_id}/retry-registration",
    # Guarded by this fix.
    "/api/v1/cache/clear",
    "/api/v1/cache/redis/invalidate",
    "/api/v1/cache/redis/invalidate/{prefix}",
    "/api/v1/scheduler/retrain/trigger",
    "/api/v1/scheduler/refresh_external",
    "/api/v1/mlops/drift/baseline",
    "/api/v1/mlops/drift/baseline/fit",
    "/api/v1/mlops/drift/simulate",
    "/api/v1/forecast/pretrain",
    "/api/v1/forecast/cache",
    "/api/v2/model/reload",
    "/api/v1/ab/split",
    "/api/v1/drift/test-alert",
    # The database writers, guarded here: DELETE /history was an unauthenticated
    # mass delete of the evaluations table.
    "/api/v1/history",
    "/api/v1/history/{eval_id}",
    "/api/v1/infra/data-refresh/run",
    # Webhook registration/deletion: an open registration let anyone make the
    # server issue an outbound request to an address of their choosing (SSRF).
    "/api/v1/webhooks",
    "/api/v1/webhooks/{sub_id}",
}

# Guarded already, by a different dependency, and left as they are.
API_KEY = {
    "/api/v1/data/refresh",
    "/api/v1/mlops/drift/observe",
}

# Known-unguarded and deliberately not touched here. Each names why.
GAP = {
    # IDOR: deletes any session by id. Needs ownership, not a blanket admin gate.
    "/api/v1/copilot/sessions/{session_id}": "needs owner-scope (#199 follow-up)",
    # Bulk write, abuse-bounded by the rate limiter; decision pending.
    "/api/v1/batch/evaluate": "bulk write, decision pending",
}

AUTH_DEPENDENCIES = {
    "require_auth", "require_admin", "require_analyst_or_admin",
    "require_api_key", "require_admin_apikey", "admin_auth",
}
READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def _auth_names(dependant, found=None):
    found = found if found is not None else []
    for sub in dependant.dependencies:
        name = getattr(sub.call, "__name__", type(sub.call).__name__)
        if name in AUTH_DEPENDENCIES:
            found.append(name)
        _auth_names(sub, found)
    return found


def _state_changing_routes(client):
    routes = []
    for route in client.app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path == "/{full_path:path}":
            continue  # the catch-all only answers 404/405; it changes nothing
        methods = sorted(set(route.methods) - READ_METHODS)
        if methods:
            routes.append(route)
    return routes


def test_every_state_changing_route_has_an_explicit_policy(client):
    classified = PUBLIC | ADMIN | API_KEY | set(GAP)
    unclassified = sorted(
        r.path for r in _state_changing_routes(client) if r.path not in classified
    )
    assert not unclassified, (
        "state-changing routes with no access decision: %s. Add each to PUBLIC, "
        "ADMIN, API_KEY or GAP in this file." % unclassified
    )


def test_admin_routes_carry_the_admin_dependency(client):
    by_path = {r.path: r for r in _state_changing_routes(client)}
    missing = []
    for path in sorted(ADMIN):
        route = by_path.get(path)
        if route is None:
            missing.append("%s (route absent)" % path)
        elif "require_admin" not in _auth_names(route.dependant):
            missing.append(path)
    assert not missing, "admin routes without require_admin: %s" % missing


def test_the_cache_invalidation_routes_reject_the_unauthorized(client):
    """Behavioural, on the routes at the centre of the advisory.

    An anonymous caller and a signed-in non-admin are both refused before the
    handler runs; only these two are exercised end to end because they are cheap
    and touch only the mocked cache.
    """
    viewer = create_access_token({"sub": "viewer"})
    for method, path in (
        ("DELETE", "/api/v1/cache/redis/invalidate"),
        ("DELETE", "/api/v1/cache/redis/invalidate/predict"),
        ("POST", "/api/v1/cache/clear"),
    ):
        anon = client.request(method, path)
        assert anon.status_code in (401, 403), (
            "%s %s answered %d to an anonymous caller" % (method, path, anon.status_code)
        )
        forbidden = client.request(method, path, headers={"Authorization": f"Bearer {viewer}"})
        assert forbidden.status_code == 403, (
            "%s %s answered %d to a viewer" % (method, path, forbidden.status_code)
        )


def test_gap_routes_are_still_unguarded_and_listed_with_a_reason(client):
    """A control: the deferred routes are genuinely unguarded, so the day one is
    fixed this test fails and forces it to move to the right bucket."""
    by_path = {r.path: r for r in _state_changing_routes(client)}
    for path, reason in GAP.items():
        assert reason, "a GAP entry needs a reason"
        route = by_path.get(path)
        if route is not None:
            assert "require_admin" not in _auth_names(route.dependant), (
                "%s is now guarded; move it out of GAP into ADMIN" % path
            )
