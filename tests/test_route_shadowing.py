"""No two route decorators may claim the same path and method.

FastAPI matches in registration order, so the second registration never runs. It
is not an error, nothing warns, and the dead handler goes on looking like live
code: `app/main.py` carried `@app.get("/")` twice for some time, the second one
serving pages/landing.html, and the site never once answered with it.

Read from the source rather than from a live `app`. Importing app.main unpickles
models/*.pkl, which are Git LFS objects -- so an import-based test passes or
fails on whether LFS was materialised, which is a different question from this
one and has already cost this repository a broken CI job. ast needs neither the
models nor the dependencies.

The trade-off is stated rather than hidden: this sees decorators written in the
file, so it would miss a collision introduced through include_router() or
add_api_route(). It catches the shape that actually occurred.
"""
import ast
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _decorated_routes(path: Path):
    """(method, route) for every @app.<verb>("<path>") in a module."""
    tree = ast.parse(path.read_text())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            obj = dec.func.value
            if not isinstance(obj, ast.Name) or obj.id != "app":
                continue
            method = dec.func.attr
            if method not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            if not dec.args or not isinstance(dec.args[0], ast.Constant):
                continue
            found.append((method, dec.args[0].value, node.name, dec.lineno))
    return found


def test_no_route_is_registered_twice():
    seen = defaultdict(list)
    for module in sorted((REPO / "app").rglob("*.py")):
        for method, route, func, line in _decorated_routes(module):
            seen[(method, route)].append(
                f"{module.relative_to(REPO)}:{line} {func}()")

    shadowed = {k: v for k, v in seen.items() if len(v) > 1}
    assert not shadowed, "\n".join(
        f"{m.upper()} {r} is registered {len(w)} times; only the first runs:\n  "
        + "\n  ".join(w) for (m, r), w in sorted(shadowed.items()))


def test_the_check_can_see_the_routes_it_is_meant_to_guard():
    """A guard that finds nothing to look at is not a guard."""
    routes = _decorated_routes(REPO / "app" / "main.py")
    # Most API endpoints hang off routers in app/api/*, not off `app` itself, so
    # this count is small by design -- it covers the UI and health routes.
    assert len(routes) >= 10, f"only {len(routes)} decorated routes found in app/main.py"
    paths = {r for _, r, _, _ in routes}
    for expected in ("/", "/health", "/app/{path:path}"):
        assert expected in paths, f"{expected} not found; the walker is not seeing routes"
