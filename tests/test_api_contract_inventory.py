"""The inventory script is a measuring instrument, so it is measured first.

Every case here is a fixture whose correct answer is known by construction. The
number this script produces is about to be written into a roadmap and used as a
ratchet baseline, and a miscounting instrument would make both wrong in a way
nothing downstream could detect.

The last test is the negative control: the script must find real routes in the
real package. Without it, a `collect()` that silently returned nothing would
report "0 routes, 0% coverage" -- a clean-looking result from an empty set.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "api_contract_inventory.py"

spec = importlib.util.spec_from_file_location("api_contract_inventory", SCRIPT)
assert spec and spec.loader
inv = importlib.util.module_from_spec(spec)
# Registered before execution: @dataclass resolves annotations through
# sys.modules[cls.__module__], which is None for a module loaded by spec alone.
sys.modules[spec.name] = inv
spec.loader.exec_module(inv)


def write(tmp_path: Path, source: str) -> Path:
    pkg = tmp_path / "app"
    pkg.mkdir(exist_ok=True)
    (pkg / "routes.py").write_text(textwrap.dedent(source), encoding="utf-8")
    return pkg


def only(routes):
    assert len(routes) == 1, f"expected exactly one route, got {[r.decl_path for r in routes]}"
    return routes[0]


def test_a_declared_response_model_counts_as_covered(tmp_path):
    pkg = write(tmp_path, '''
        @router.get("/ok", response_model=Thing)
        def handler():
            return {"a": 1}
    ''')
    r = only(inv.collect(pkg))
    assert r.response_model == "Thing"
    assert r.covered is True
    assert r.exempt_reason is None


def test_a_route_without_a_model_counts_as_uncovered(tmp_path):
    pkg = write(tmp_path, '''
        @router.get("/bare")
        def handler():
            return {"a": 1}
    ''')
    assert only(inv.collect(pkg)).covered is False


def test_single_quoted_paths_are_found(tmp_path):
    # `@router.post('/split')` exists in this codebase. A regex written against
    # double quotes would miss it; the AST does not care.
    pkg = write(tmp_path, """
        @router.post('/split')
        def handler():
            return {"a": 1}
    """)
    assert only(inv.collect(pkg)).decl_path == "/split"


def test_a_websocket_is_exempt_not_uncovered(tmp_path):
    pkg = write(tmp_path, '''
        @router.websocket("/ws/live")
        async def handler(ws):
            await ws.accept()
    ''')
    r = only(inv.collect(pkg))
    assert r.exempt_reason == "websocket"
    assert r.methods == ["WS"]


@pytest.mark.parametrize(
    "cls", ["HTMLResponse", "PlainTextResponse", "FileResponse", "StreamingResponse", "RedirectResponse"]
)
def test_non_model_response_classes_are_exempt(tmp_path, cls):
    # Both of the classes this codebase actually uses are here, plus the three
    # the roadmap names as legitimate exemptions. Enumerating the set rather
    # than the two examples in front of me.
    pkg = write(tmp_path, f'''
        @router.get("/thing", response_class={cls})
        def handler():
            return "<html/>"
    ''')
    r = only(inv.collect(pkg))
    assert r.exempt_reason == f"response_class={cls}"


def test_a_plain_response_class_does_not_exempt_by_accident(tmp_path):
    # JSONResponse still serialises a body a model could describe, so it is a
    # normal route, not an exemption.
    pkg = write(tmp_path, '''
        @router.get("/thing", response_class=JSONResponse)
        def handler():
            return {"a": 1}
    ''')
    assert only(inv.collect(pkg)).exempt_reason is None


def test_returns_of_a_nested_function_are_not_the_route_s_returns(tmp_path):
    pkg = write(tmp_path, '''
        @router.get("/outer")
        def handler():
            def inner():
                return {"not": "mine"}
            return {"a": 1}
    ''')
    r = only(inv.collect(pkg))
    assert r.return_count == 1
    assert r.literal_shapes == [["a"]]


def test_disagreeing_literal_returns_are_flagged(tmp_path):
    # This is the shape of GET /api/v1/drift (#239): one status, several
    # incompatible bodies.
    pkg = write(tmp_path, '''
        @router.get("/drift")
        def handler(x: int):
            if x == 0:
                return {"status": "scipy_not_installed"}
            if x == 1:
                return {"status": "no_log", "drift": False}
            return {"status": "ok", "drift_detected": True}
    ''')
    r = only(inv.collect(pkg))
    assert r.return_count == 3
    assert r.shape_conflict is True


def test_agreeing_literal_returns_are_not_flagged(tmp_path):
    pkg = write(tmp_path, '''
        @router.get("/steady")
        def handler(x: int):
            if x:
                return {"status": "a", "n": 1}
            return {"n": 2, "status": "b"}
    ''')
    r = only(inv.collect(pkg))
    assert r.return_count == 2
    assert r.shape_conflict is False, "key order must not count as a disagreement"


def test_a_single_literal_return_cannot_conflict_with_itself(tmp_path):
    pkg = write(tmp_path, '''
        @router.get("/one")
        def handler():
            return {"a": 1}
    ''')
    assert only(inv.collect(pkg)).shape_conflict is False


def test_a_non_dict_return_is_counted_as_dynamic_not_as_a_shape(tmp_path):
    pkg = write(tmp_path, '''
        @router.get("/dyn")
        def handler():
            return build_the_body()
    ''')
    r = only(inv.collect(pkg))
    assert r.dynamic_returns == 1
    assert r.literal_shapes == []


def test_a_dict_with_a_computed_key_is_dynamic_not_a_known_shape(tmp_path):
    pkg = write(tmp_path, '''
        @router.get("/dyn2")
        def handler(k: str):
            return {k: 1}
    ''')
    r = only(inv.collect(pkg))
    assert r.dynamic_returns == 1
    assert r.literal_shapes == []


def test_decorators_that_are_not_routes_are_ignored(tmp_path):
    pkg = write(tmp_path, '''
        @app.middleware("http")
        async def mw(request, call_next):
            return await call_next(request)

        @router.on_event("startup")
        def boot():
            return None

        @something_else.get("/nope")
        def other():
            return {"a": 1}
    ''')
    assert inv.collect(pkg) == []


def test_status_code_is_recorded_when_declared(tmp_path):
    pkg = write(tmp_path, '''
        @router.post("/made", status_code=201, response_model=Thing)
        def handler():
            return {"a": 1}
    ''')
    r = only(inv.collect(pkg))
    assert r.status_code == 201


def test_summary_arithmetic_excludes_exempt_routes_from_the_denominator(tmp_path):
    pkg = write(tmp_path, '''
        @router.get("/a", response_model=T)
        def a():
            return {"x": 1}

        @router.get("/b")
        def b():
            return {"x": 1}

        @router.websocket("/ws")
        async def c(ws):
            await ws.accept()
    ''')
    s = inv.summarise(inv.collect(pkg))
    assert s["routes_total"] == 3
    assert s["exempt"] == 1
    assert s["considered"] == 2
    assert s["with_response_model"] == 1
    assert s["coverage_pct"] == 50.0, "the websocket must not dilute the percentage"


def test_the_instrument_finds_the_real_package():
    """Negative control: a collector that found nothing would report 0%.

    An empty result and a genuinely uncovered API are indistinguishable in the
    summary line, so the roadmap's baseline needs this assertion to mean
    anything at all.
    """
    app_dir = Path(__file__).resolve().parents[1] / "app"
    routes = inv.collect(app_dir)
    assert len(routes) > 100, f"expected the real app to declare many routes, found {len(routes)}"
    assert any(r.covered for r in routes), "some real route declares a response_model"
    assert any(not r.covered and r.exempt_reason is None for r in routes), "and some do not"
