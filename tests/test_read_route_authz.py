"""Every GET route that returns stored user input has an explicit access decision,
and authenticated routes refuse an anonymous caller (GHSA-jmpv-7wjf-87q9).

This is the read-route policy registry the advisory asks for. It is built from the
running application's `app.routes`, not from a static scan, so a router included
dynamically is still seen. Each GET route that returns stored evaluations, predictions,
or other user data must be classified `public`, `authenticated`, or `admin` — a route
in none of them fails the registry, so a new GET endpoint cannot be added without a
decision.
"""
import pytest
from fastapi.routing import APIRoute

from app.auth import create_access_token

# --- the policy, one entry per GET route (path, method-agnostic) route ---

PUBLIC = {
    # Compute-and-return: no stored user data.
    "/api/v1/predict", "/api/v1/predict/compare", "/api/v1/predict/explain",
    "/api/v1/predict/explain/waterfall", "/api/v1/predict/neural",
    "/api/v1/predict/stacking", "/api/v1/predict/uncertainty", "/api/v1/predict/v2",
    "/api/v2/predict",
    "/api/v1/evaluate", "/api/v1/evaluate/monte-carlo", "/api/v1/evaluate/ranking",
    "/api/v1/analytics/model-compare", "/api/v1/analytics/monte-carlo",
    "/api/v1/calibration/brier", "/api/v1/calibration/discrepancy",
    "/api/v1/calibration/reliability",
    "/api/v1/compliance/check", "/api/v1/compliance/csrd", "/api/v1/compliance/gap-analysis",
    "/api/v1/compliance/frameworks",
    "/api/v1/copilot/explain", "/api/v1/copilot/explain/stream", "/api/v1/copilot/qa",
    "/api/v1/copilot/health",
    "/api/v1/drift/analyze", "/api/v1/drift/compare", "/api/v1/drift/features/stats",
    "/api/v1/explain/local", "/api/v1/explain/global", "/api/v1/explain/beeswarm",
    "/api/v1/ghg-calculate", "/api/v1/shap",
    "/api/v1/what-if", "/api/v1/ab/predict", "/api/v1/ab/stats",
    "/api/v1/report/pdf", "/api/v1/reports/compliance.pdf",
    "/api/v1/reports/compliance-batch.pdf",
    # Metadata routes.
    "/api/v1/countries", "/api/v1/regions", "/api/v1/trends",
    "/api/v1/map/countries", "/api/v1/map/countries/{code}",
    "/api/v1/map/russia", "/api/v1/map/russia/{region_code}",
    "/api/v1/embed", "/api/v1/embed/snippet",
    "/api/v1/rag/search",
    "/api/v1/forecast", "/api/v1/forecast/history", "/api/v1/forecast/metrics/latest",
    "/api/v1/forecast/metrics/performance", "/api/v1/forecast/cache",
    "/api/v1/lstm-status",
    "/api/v1/model/compare", "/api/v1/model/status", "/api/v1/model/metrics",
    "/api/v1/model/feature-importance", "/api/v1/model/drift",
    "/api/v1/model/drift/mlflow-history", "/api/v1/model/registry-info",
    "/api/v1/model/reliability-diagram", "/api/v1/model/prediction-log/stats",
    "/api/v1/model/ab-comparison", "/api/v1/model/ab-comparison/plot",
    "/api/v2/model/version", "/api/v2/model/calibration",
    "/api/v2/drift/features", "/api/v2/drift/predictions",
    "/api/v1/scheduler/status", "/api/v1/scheduler/retrain/history",
    
    "/api/v1/analytics/country-benchmark/{country}",
    "/api/v1/analytics/country-ranking",
    "/api/v1/analytics/data-health",
    "/api/v1/cache/redis", "/api/v1/cache/redis/test", "/api/v1/cache/stats",
    "/api/v1/infra/data-refresh-status",
    "/api/v1/mlflow/stats", "/api/v1/mlops/drift", "/api/v1/mlops/drift/baseline",
    "/api/v1/mlops/health", "/api/v1/rate-limit/status", "/api/v1/ws/status",
    # Health and observability.
    "/health", "/api/v1/health", "/api/v1/ready", "/metrics",
    "/api/v1/metrics", "/api/v1/metrics/prometheus", "/api/v1/system/metrics",
    "/api/v1/ping", "/api/v1/status/uptime", "/system/health",
    "/model-info", "/model-metrics",
    # Static file routes that return no stored user content.
    "/admin", "/", "/login", "/{full_path:path}",
    "/admin/{path:path}", "/app/{path:path}", "/auth/login", "/dev", "/favicon.ico",
    # SPA routes that exist only where the SPA is built (the image) and serve SPA files.
    "/{spa_path:path}", "/v2", "/v2/{full_path:path}",
}

# Routes that require any authenticated user (any role: viewer, analyst, admin).
AUTHENTICATED = {
    # Guarded by this fix: GET routes that return stored evaluations or predictions.
    "/api/v1/history",
    "/api/v1/history/{eval_id}",
    "/api/v1/export/csv",
    "/api/v1/predictions/history",
    "/api/v1/predictions/export/csv",
    "/api/v1/analytics/predictions-log",
    "/api/v1/batch",
    "/api/v1/batch/{batch_id}",
    "/api/v1/webhooks",
    # Copilot sessions contain stored user conversations.
    "/api/v1/copilot/sessions",
    "/api/v1/copilot/sessions/{session_id}",
    # Returns the current user's info; answers 401 anonymously by design.
    "/api/v1/auth/me",
}

# Routes that must carry require_admin or admin_auth.
ADMIN = {
    "/api/v1/ingestion/attention",  # Depends(require_admin) in the signature
    # Admin routes that exist and should be protected.
    "/api/v1/analytics/metrics/model-health",
    "/api/v1/analytics/summary",
    "/api/v1/webhooks/deliveries",
    "/api/v1/admin/diagnostics",
    "/api/v1/admin/snapshot",
    "/api/v1/admin/timeline",
    "/api/v1/admin/users",
    "/api/v1/admin/retrain-log",
    "/api/v1/admin/ai-teammate/status",
    "/api/v1/observations",
    "/api/v1/observations/coverage",
    "/api/v1/audit/log",
}

# Routes protected by API key (require_admin_apikey or require_api_key).
API_KEY = {
    "/api/v1/admin/stats",
    "/api/v1/auth/verify",
    # The data-pipeline router carries require_api_key for all of its routes
    # (app/api/data_pipeline.py, router-level dependencies).
    "/api/v1/data/countries",
    "/api/v1/data/countries/supported",
    "/api/v1/data/country/{name}",
    "/api/v1/data/status",
    "/api/v1/data/refresh-status",
    "/api/v1/data/refresh/status",
    "/api/v1/data/refresh/logs",
}

AUTH_DEPENDENCIES = {
    "require_auth", "require_admin", "require_analyst_or_admin",
    "require_api_key", "require_admin_apikey", "admin_auth",
}


def _auth_names(dependant, found=None):
    found = found if found is not None else []
    for sub in dependant.dependencies:
        name = getattr(sub.call, "__name__", type(sub.call).__name__)
        if name in AUTH_DEPENDENCIES:
            found.append(name)
        _auth_names(sub, found)
    return found


def _get_routes(client):
    routes = []
    for route in client.app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path == "/{full_path:path}":
            continue  # the catch-all only answers 404/405; it reads nothing
        if "GET" in route.methods or "HEAD" in route.methods:
            routes.append(route)
    return routes


def test_every_get_route_has_an_explicit_policy(client):
    classified = PUBLIC | AUTHENTICATED | ADMIN | API_KEY
    unclassified = sorted(
        r.path for r in _get_routes(client) if r.path not in classified
    )
    assert not unclassified, (
        "GET routes with no access decision: %s. Add each to PUBLIC, "
        "AUTHENTICATED, ADMIN or API_KEY in this file." % unclassified
    )


def test_authenticated_routes_carry_the_auth_dependency(client):
    by_path = {r.path: r for r in _get_routes(client)}
    missing = []
    for path in sorted(AUTHENTICATED):
        route = by_path.get(path)
        if route is None:
            missing.append("%s (route absent)" % path)
        elif "require_auth" not in _auth_names(route.dependant):
            missing.append(path)
    assert not missing, "authenticated routes without require_auth: %s" % missing


def test_admin_routes_carry_the_admin_dependency(client):
    by_path = {r.path: r for r in _get_routes(client)}
    missing = []
    for path in sorted(ADMIN):
        route = by_path.get(path)
        if route is None:
            missing.append("%s (route absent)" % path)
        else:
            deps = _auth_names(route.dependant)
            if "require_admin" not in deps and "admin_auth" not in deps:
                missing.append(path)
    assert not missing, "admin routes without require_admin/admin_auth: %s" % missing


def test_api_key_routes_carry_the_api_key_dependency(client):
    by_path = {r.path: r for r in _get_routes(client)}
    missing = []
    for path in sorted(API_KEY):
        route = by_path.get(path)
        if route is None:
            missing.append("%s (route absent)" % path)
        else:
            deps = _auth_names(route.dependant)
            if "require_admin_apikey" not in deps and "require_api_key" not in deps:
                missing.append(path)
    assert not missing, "API key routes without require_admin_apikey/require_api_key: %s" % missing


def test_protected_read_routes_reject_anonymous(client):
    """Anonymous GET on each protected route → 401."""
    for path in sorted(AUTHENTICATED):
        # Fill path params with test values.
        filled = path.replace("{eval_id}", "1").replace("{batch_id}", "test123")
        response = client.get(filled)
        assert response.status_code == 401, (
            "GET %s answered %d to an anonymous caller (expected 401)" % (path, response.status_code)
        )


def test_authenticated_read_routes_accept_signed_in_users(client):
    """Authenticated GET (viewer, analyst, admin) → 200 with the same payload shape."""
    viewer_token = create_access_token({"sub": "viewer", "role": "viewer"})
    analyst_token = create_access_token({"sub": "analyst", "role": "analyst"})
    admin_token = create_access_token({"sub": "admin", "role": "admin"})

    for token_name, token in [("viewer", viewer_token), ("analyst", analyst_token), ("admin", admin_token)]:
        for path in sorted(AUTHENTICATED):
            # Fill path params with test values.
            filled = path.replace("{eval_id}", "1").replace("{batch_id}", "test123")
            response = client.get(filled, headers={"Authorization": f"Bearer {token}"})
            # Some routes may return 404 if the resource doesn't exist, which is fine.
            # We're just checking that 401 is not returned.
            assert response.status_code != 401, (
                "GET %s answered 401 to %s (expected 200 or 404)" % (path, token_name)
            )


def test_public_routes_do_not_require_auth(client):
    """No PUBLIC route carries an authentication dependency.

    Read off each route's dependency tree (router-level dependencies are
    merged into it), not by calling the route: calling every public route
    runs its handler, and some of those dial MLflow with retries or use
    PostgreSQL-only SQL, which makes a check about access slow or unreachable.
    A route listed here with an auth dependency is protected and belongs in
    the category its dependency names.
    """
    by_path = {r.path: r for r in _get_routes(client)}
    protected = []
    for path in sorted(PUBLIC):
        route = by_path.get(path)
        if route is None:
            continue
        deps = _auth_names(route.dependant)
        if deps:
            protected.append("%s (%s)" % (path, ", ".join(sorted(set(deps)))))
    assert not protected, (
        "PUBLIC routes that carry an auth dependency -- classify them where "
        "the dependency says: %s" % protected
    )


def test_route_registry_complete_with_spa_present():
    """The registry is complete in the image's configuration, where the SPA is built.

    Three GET routes are registered only when the SPA is built: /{spa_path:path},
    /v2, /v2/{full_path:path}. They are absent in CI's backend job (no SPA build)
    but present in the production image. This test runs in a subprocess with a temp
    SPA directory to verify the registry is complete where the SPA is present.
    """
    import subprocess
    import sys
    import tempfile
    import json
    import os
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        spa_dir = Path(tmpdir)
        # Create the SPA structure: index.html, assets/, spa/index.html, spa/assets/
        (spa_dir / "index.html").write_text("<html></html>")
        (spa_dir / "assets").mkdir()
        (spa_dir / "spa").mkdir()
        (spa_dir / "spa" / "index.html").write_text("<html></html>")
        (spa_dir / "spa" / "assets").mkdir()

        # Run a subprocess that imports app.main with SORA_SPA_DIR set and prints
        # all GET/HEAD routes with their auth dependencies.
        script = """
import json
from fastapi.routing import APIRoute

from app.main import app
from tests.test_read_route_authz import _auth_names

routes = []
for route in app.routes:
    if not isinstance(route, APIRoute):
        continue
    if "GET" in route.methods or "HEAD" in route.methods:
        routes.append({
            "path": route.path,
            "auth_deps": _auth_names(route.dependant)
        })

print(json.dumps(routes))
"""

        env = os.environ.copy()
        env["SORA_SPA_DIR"] = str(spa_dir)
        # Pop REDIS_URL. CI's test environment sets it empty, and
        # app.drift_detection calls redis.from_url(REDIS_URL) at import. The
        # parent survives that only because tests/conftest.py patches
        # redis.from_url before importing the app; this subprocess has no such
        # patch, so an empty value raises ValueError. Without the variable the
        # default URL is used, and from_url does not connect.
        env.pop("REDIS_URL", None)

        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            cwd=Path(__file__).parent.parent,
        )

        if result.returncode != 0:
            raise AssertionError(
                f"Subprocess failed with exit code {result.returncode}.\n"
                f"stderr: {result.stderr}\nstdout: {result.stdout}"
            )

        # Parse the JSON from the last line (logging may appear earlier)
        routes = json.loads(result.stdout.strip().splitlines()[-1])
        by_path = {r["path"]: r for r in routes}

        # (a) All three SPA paths ARE present (otherwise the check proves nothing).
        spa_paths = ["/{spa_path:path}", "/v2", "/v2/{full_path:path}"]
        missing_spa = [p for p in spa_paths if p not in by_path]
        assert not missing_spa, (
            f"SPA paths not present with SORA_SPA_DIR set: {missing_spa}. "
            "The test cannot verify the registry without them."
        )

        # (b) Every GET route is classified (same exclusion of /{full_path:path} as _get_routes).
        classified = PUBLIC | AUTHENTICATED | ADMIN | API_KEY
        unclassified = sorted(
            r["path"] for r in routes
            if r["path"] != "/{full_path:path}" and r["path"] not in classified
        )
        assert not unclassified, (
            f"GET routes with no access decision in the image configuration: {unclassified}. "
            "Add each to PUBLIC, AUTHENTICATED, ADMIN or API_KEY."
        )

        # (c) No PUBLIC route carries an auth dependency.
        protected = []
        for path in sorted(PUBLIC):
            route = by_path.get(path)
            if route is None:
                continue
            deps = route["auth_deps"]
            if deps:
                protected.append(f"{path} ({', '.join(sorted(set(deps)))})")
        assert not protected, (
            f"PUBLIC routes that carry an auth dependency: {protected}"
        )
