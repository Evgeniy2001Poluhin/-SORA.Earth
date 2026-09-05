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
import json
import subprocess
import sys
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


# --- resolving a declared path to the public one -----------------------------
#
# These exist because the ad-hoc version of this resolution, written while
# analysing the same data, matched by suffix alone and reported
# `app/api/drift.py`'s route as `/api/v1/mlops/drift`. It is
# `/api/v1/model/drift`. That is the second time the same confusion produced a
# wrong URL in this work -- the first put one into issue #239.

DRIFT_OPENAPI = {
    "paths": {
        "/api/v1/model/drift": {"get": {}},
        "/api/v1/mlops/drift": {"get": {}},
    }
}


def test_the_router_prefix_is_read_from_the_module(tmp_path):
    pkg = write(tmp_path, '''
        router = APIRouter(prefix="/model", tags=["mlops"])

        @router.get("/drift")
        def handler():
            return {"a": 1}
    ''')
    assert only(inv.collect(pkg)).router_prefix == "/model"


def test_the_prefix_disambiguates_two_paths_sharing_a_suffix(tmp_path):
    pkg = write(tmp_path, '''
        router = APIRouter(prefix="/model")

        @router.get("/drift")
        def handler():
            return {"a": 1}
    ''')
    routes = inv.collect(pkg)
    inv.resolve_public_paths(routes, DRIFT_OPENAPI)
    assert routes[0].public_path == "/api/v1/model/drift"


def test_a_genuine_ambiguity_is_recorded_not_guessed(tmp_path):
    # No prefix, so both candidates remain. Picking the first would send the
    # next reader to the wrong endpoint; saying so does not.
    pkg = write(tmp_path, '''
        router = APIRouter()

        @router.get("/drift")
        def handler():
            return {"a": 1}
    ''')
    routes = inv.collect(pkg)
    inv.resolve_public_paths(routes, DRIFT_OPENAPI)
    assert routes[0].public_path.startswith("AMBIGUOUS:")
    assert "/api/v1/model/drift" in routes[0].public_path
    assert "/api/v1/mlops/drift" in routes[0].public_path


def test_a_declared_route_absent_from_openapi_resolves_to_nothing(tmp_path):
    pkg = write(tmp_path, '''
        @router.get("/never-mounted")
        def handler():
            return {"a": 1}
    ''')
    routes = inv.collect(pkg)
    inv.resolve_public_paths(routes, DRIFT_OPENAPI)
    assert routes[0].public_path is None


def test_the_method_must_match_too(tmp_path):
    pkg = write(tmp_path, '''
        router = APIRouter(prefix="/model")

        @router.post("/drift")
        def handler():
            return {"a": 1}
    ''')
    routes = inv.collect(pkg)
    inv.resolve_public_paths(routes, DRIFT_OPENAPI)
    assert routes[0].public_path is None, "the document declares GET, not POST"


# --- consumers ---------------------------------------------------------------


def _web(tmp_path, files: dict[str, str]) -> Path:
    web = tmp_path / "web" / "src"
    for rel, body in files.items():
        f = web / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    return web


def test_a_consumer_outside_the_api_directory_is_found(tmp_path):
    # `LSTMProgressWidget` calls `api("/lstm-status")` inline. A scan limited
    # to web/src/api reported that route as having no consumer, which is how
    # it nearly got deprioritised.
    pkg = write(tmp_path, '''
        @router.get("/lstm-status")
        def handler():
            return {"a": 1}
    ''')
    routes = inv.collect(pkg)
    inv.resolve_public_paths(routes, {"paths": {"/api/v1/lstm-status": {"get": {}}}})
    web = _web(tmp_path, {"features/drift/Widget.tsx": 'api<S>("/lstm-status")'})
    inv.attach_consumers(routes, web)
    assert routes[0].consumers == 1


def test_a_template_literal_call_with_a_query_string_counts(tmp_path):
    pkg = write(tmp_path, '''
        @router.post("/simulate")
        def handler():
            return {"a": 1}
    ''')
    routes = inv.collect(pkg)
    inv.resolve_public_paths(routes, {"paths": {"/api/v1/simulate": {"post": {}}}})
    web = _web(tmp_path, {"api/x.ts": "api(`/simulate?${q}`, {method:'POST'})"})
    inv.attach_consumers(routes, web)
    assert routes[0].consumers == 1


def test_an_unconsumed_route_stays_at_zero(tmp_path):
    pkg = write(tmp_path, '''
        @router.get("/nobody-calls-this")
        def handler():
            return {"a": 1}
    ''')
    routes = inv.collect(pkg)
    inv.resolve_public_paths(routes, {"paths": {"/api/v1/nobody-calls-this": {"get": {}}}})
    web = _web(tmp_path, {"api/x.ts": 'api("/something-else")'})
    inv.attach_consumers(routes, web)
    assert routes[0].consumers == 0


def test_an_ambiguous_path_is_not_credited_with_consumers(tmp_path):
    # Counting references for a path we could not identify would attribute
    # them to the wrong endpoint.
    pkg = write(tmp_path, '''
        router = APIRouter()

        @router.get("/drift")
        def handler():
            return {"a": 1}
    ''')
    routes = inv.collect(pkg)
    inv.resolve_public_paths(routes, DRIFT_OPENAPI)
    web = _web(tmp_path, {"api/x.ts": 'api("/model/drift")'})
    inv.attach_consumers(routes, web)
    assert routes[0].consumers == 0


# --- provenance, determinism, and one address per route ----------------------


def test_two_declarations_at_one_address_are_reported(tmp_path):
    # FastAPI answers with the first registered match, so the second is
    # unreachable. Nine of these exist in this codebase, including two
    # /auth/login handlers whose request formats differ (JSON vs form).
    pkg = write(tmp_path, '''
        @router.post("/login")
        def a():
            return {"x": 1}
    ''')
    (tmp_path / "app" / "other.py").write_text(
        textwrap.dedent('''
        @router.post("/login")
        def b():
            return {"x": 1}
    '''),
        encoding="utf-8",
    )
    routes = inv.collect(tmp_path / "app")
    inv.resolve_public_paths(routes, {"paths": {"/api/v1/login": {"post": {}}}})
    s = inv.summarise(routes)
    assert s["duplicate_public_paths"] == ["/api/v1/login"]


def test_one_declaration_at_one_address_is_not_reported(tmp_path):
    pkg = write(tmp_path, '''
        @router.post("/login")
        def a():
            return {"x": 1}
    ''')
    routes = inv.collect(pkg)
    inv.resolve_public_paths(routes, {"paths": {"/api/v1/login": {"post": {}}}})
    assert inv.summarise(routes)["duplicate_public_paths"] == []


def test_the_same_path_under_different_methods_is_not_a_duplicate(tmp_path):
    pkg = write(tmp_path, '''
        @router.get("/thing")
        def a():
            return {"x": 1}

        @router.post("/thing")
        def b():
            return {"x": 1}
    ''')
    routes = inv.collect(pkg)
    inv.resolve_public_paths(routes, {"paths": {"/api/v1/thing": {"get": {}, "post": {}}}})
    assert inv.summarise(routes)["duplicate_public_paths"] == []


def test_route_order_is_deterministic(tmp_path):
    """`ast.walk` is breadth-first; the file is not.

    The first version of this test put both routes at module level, where BFS
    and source order happen to agree — so it passed with the sort removed, and
    mutation testing caught that it was proving nothing. Depth has to differ
    for walk order and line order to disagree at all: the nested route is
    declared first in the file but visited second.
    """
    pkg = write(tmp_path, '''
        if FEATURE:

            @router.get("/declared-first-visited-second")
            def nested():
                return {"x": 1}

        @router.get("/declared-second-visited-first")
        def top_level():
            return {"x": 1}
    ''')
    routes = inv.collect(pkg)
    assert len(routes) == 2
    rows = [(r.file, r.line) for r in routes]
    assert rows == sorted(rows), "output must follow the file, not the walk"
    assert routes[0].decl_path == "/declared-first-visited-second"


def test_a_test_file_reference_is_not_a_consumer(tmp_path):
    pkg = write(tmp_path, '''
        @router.get("/only-tested")
        def handler():
            return {"a": 1}
    ''')
    routes = inv.collect(pkg)
    inv.resolve_public_paths(routes, {"paths": {"/api/v1/only-tested": {"get": {}}}})
    web = _web(tmp_path, {"features/Thing.production.test.tsx": 'api("/only-tested")'})
    inv.attach_consumers(routes, web)
    assert routes[0].consumers == 0, "a route called only by its own test has no consumer"
    assert routes[0].test_references == 1, "but the reference is still visible"


def test_a_template_literal_must_end_where_the_path_ends(tmp_path):
    pkg = write(tmp_path, '''
        @router.get("/drift")
        def handler():
            return {"a": 1}
    ''')
    routes = inv.collect(pkg)
    inv.resolve_public_paths(routes, {"paths": {"/api/v1/drift": {"get": {}}}})
    web = _web(tmp_path, {"api/x.ts": "api(`/drifting-along`)"})
    inv.attach_consumers(routes, web)
    assert routes[0].consumers == 0, "/drifting-along is not /drift"


def test_a_snapshot_pinned_to_another_tree_is_refused(tmp_path):
    snap = tmp_path / "openapi.json"
    snap.write_text(json.dumps({"paths": {}}), encoding="utf-8")
    snap.with_suffix(".meta.json").write_text(
        json.dumps({"app_tree_sha": "a" * 40}), encoding="utf-8"
    )
    problem = inv.check_openapi_provenance(snap, "b" * 40)
    assert problem and "different tree" in problem


def test_a_snapshot_whose_bytes_changed_is_refused(tmp_path):
    snap = tmp_path / "openapi.json"
    snap.write_text(json.dumps({"paths": {}}), encoding="utf-8")
    snap.with_suffix(".meta.json").write_text(
        json.dumps({"sha256": "0" * 64}), encoding="utf-8"
    )
    problem = inv.check_openapi_provenance(snap, "b" * 40)
    assert problem and "digest" in problem


def test_a_matching_snapshot_passes(tmp_path):
    import hashlib

    snap = tmp_path / "openapi.json"
    snap.write_text(json.dumps({"paths": {}}), encoding="utf-8")
    snap.with_suffix(".meta.json").write_text(
        json.dumps(
            {"app_tree_sha": "c" * 40, "sha256": hashlib.sha256(snap.read_bytes()).hexdigest()}
        ),
        encoding="utf-8",
    )
    assert inv.check_openapi_provenance(snap, "c" * 40) is None


def test_an_unpinned_snapshot_is_allowed_but_unverified(tmp_path):
    snap = tmp_path / "openapi.json"
    snap.write_text(json.dumps({"paths": {}}), encoding="utf-8")
    assert inv.check_openapi_provenance(snap, "c" * 40) is None


def test_the_written_baseline_carries_no_generating_commit(tmp_path):
    """Otherwise committing the baseline makes it stale immediately.

    The generating commit changes with every commit, including the one that
    adds the baseline file, so storing it guarantees the committed copy names
    a commit that does not contain it -- and two runs of the same tree could
    never be byte-identical, which is the property the ratchet depends on.

    Asserted against the file the script actually writes, not against
    `summarise()`. The first version of this test checked `summarise()`, which
    never added the field -- `main()` did -- so putting the field back left it
    green. Mutation testing caught that; it is the fourth test in this work
    that turned out to be looking somewhere the defect could not be.
    """
    out = tmp_path / "inventory.json"
    subprocess.run(
        [sys.executable, str(SCRIPT), "--app-dir", "app", "--json", str(out)],
        cwd=str(SCRIPT.parents[1]),
        check=True,
        capture_output=True,
    )
    summary = json.loads(out.read_text(encoding="utf-8"))["summary"]

    assert "generator_commit" not in summary
    assert "commit" not in summary
    assert summary["app_tree_sha"], "the stable identity must still be recorded"


def test_two_runs_of_one_tree_write_identical_bytes(tmp_path):
    """The property the ratchet depends on, asserted on the artefact."""
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    for out in (first, second):
        subprocess.run(
            [sys.executable, str(SCRIPT), "--app-dir", "app", "--json", str(out)],
            cwd=str(SCRIPT.parents[1]),
            check=True,
            capture_output=True,
        )
    assert first.read_bytes() == second.read_bytes()


# --- the ratchet -------------------------------------------------------------
#
# A gate that only fails on new offences is a floor, not a ratchet: an
# improvement that is never recorded can be undone silently. Rule 2 below is
# what makes this hold in one direction, and it is the one worth breaking to
# check.


def covered_and_uncovered(tmp_path):
    return write(tmp_path, '''
        @router.get("/has-one", response_model=T)
        def a():
            return {"x": 1}

        @router.get("/has-none")
        def b():
            return {"x": 1}
    ''')


def test_an_unchanged_tree_passes(tmp_path):
    pkg = covered_and_uncovered(tmp_path)
    routes = inv.collect(pkg)
    assert inv.check_ratchet(routes, inv.build_manifest(routes)) == []


def test_a_new_route_without_a_contract_fails(tmp_path):
    pkg = covered_and_uncovered(tmp_path)
    manifest = inv.build_manifest(inv.collect(pkg))

    (pkg / "more.py").write_text(
        textwrap.dedent('''
        @router.get("/brand-new")
        def c():
            return {"x": 1}
    '''),
        encoding="utf-8",
    )
    problems = inv.check_ratchet(inv.collect(pkg), manifest)

    assert len(problems) == 1
    assert "/brand-new" in problems[0]


def test_a_covered_route_losing_its_model_fails(tmp_path):
    pkg = covered_and_uncovered(tmp_path)
    manifest = inv.build_manifest(inv.collect(pkg))

    routes = pkg / "routes.py"
    routes.write_text(routes.read_text().replace(', response_model=T', ''), encoding="utf-8")
    problems = inv.check_ratchet(inv.collect(pkg), manifest)

    assert any("/has-one" in p for p in problems)


def test_covering_a_route_without_recording_it_fails(tmp_path):
    """Rule 2, and the reason this is a ratchet.

    If an improvement may go unrecorded, the same route can return to uncovered
    later without tripping anything -- it is still in the pinned list. Locking
    the gain in is one command, and refusing until it is run is what keeps the
    baseline honest.
    """
    pkg = covered_and_uncovered(tmp_path)
    manifest = inv.build_manifest(inv.collect(pkg))

    routes = pkg / "routes.py"
    routes.write_text(
        routes.read_text().replace('@router.get("/has-none")', '@router.get("/has-none", response_model=T)'),
        encoding="utf-8",
    )
    problems = inv.check_ratchet(inv.collect(pkg), manifest)

    assert any("now covered but still listed" in p for p in problems)
    assert any("--update-manifest" in p for p in problems)


def test_a_route_moving_within_its_file_is_not_a_change(tmp_path):
    """Identity excludes the line number, or every insertion above a route
    would read as that route disappearing and a new one arriving."""
    pkg = covered_and_uncovered(tmp_path)
    manifest = inv.build_manifest(inv.collect(pkg))

    routes = pkg / "routes.py"
    routes.write_text("# a new comment\n\n" + routes.read_text(), encoding="utf-8")

    assert inv.check_ratchet(inv.collect(pkg), manifest) == []


def test_a_new_exemption_fails(tmp_path):
    pkg = covered_and_uncovered(tmp_path)
    manifest = inv.build_manifest(inv.collect(pkg))

    (pkg / "ws.py").write_text(
        textwrap.dedent('''
        @router.websocket("/live")
        async def w(ws):
            await ws.accept()
    '''),
        encoding="utf-8",
    )
    problems = inv.check_ratchet(inv.collect(pkg), manifest)

    assert any("new exemption" in p for p in problems)


def test_changing_why_a_route_is_exempt_fails(tmp_path):
    pkg = write(tmp_path, '''
        @router.get("/page", response_class=HTMLResponse)
        def a():
            return "<html/>"
    ''')
    manifest = inv.build_manifest(inv.collect(pkg))

    routes = pkg / "routes.py"
    routes.write_text(routes.read_text().replace("HTMLResponse", "FileResponse"), encoding="utf-8")
    problems = inv.check_ratchet(inv.collect(pkg), manifest)

    assert any("exemption reason changed" in p for p in problems)


def test_an_allowance_admits_a_route_the_baseline_does_not_have(tmp_path):
    pkg = covered_and_uncovered(tmp_path)
    manifest = inv.build_manifest(inv.collect(pkg))

    (pkg / "more.py").write_text(
        textwrap.dedent('''
        @router.get("/deliberate")
        def c():
            return {"x": 1}
    '''),
        encoding="utf-8",
    )
    routes = inv.collect(pkg)
    key = next(inv.route_key(r) for r in routes if r.decl_path == "/deliberate")
    manifest["allow"] = {key: {"reason": "migrating in the next PR", "issue": 999}}

    assert inv.check_ratchet(routes, manifest) == []


@pytest.mark.parametrize(
    "entry", [{"reason": "because"}, {"issue": 999}, {}, "just a string"]
)
def test_an_allowance_without_both_a_reason_and_an_issue_fails(tmp_path, entry):
    """An exception nobody can trace outlives the reason for it."""
    pkg = covered_and_uncovered(tmp_path)
    routes = inv.collect(pkg)
    manifest = inv.build_manifest(routes)
    key = next(inv.route_key(r) for r in routes if r.decl_path == "/has-none")
    manifest["uncovered"] = [k for k in manifest["uncovered"] if k != key]
    manifest["allow"] = {key: entry}

    problems = inv.check_ratchet(routes, manifest)

    assert any("needs both a reason and an issue" in p for p in problems)


def test_an_allowance_that_matches_nothing_must_be_removed(tmp_path):
    """The exemption list may only shrink.

    A stale allowance is dead weight that would silently admit a route if one
    ever reappeared at that address.
    """
    pkg = covered_and_uncovered(tmp_path)
    routes = inv.collect(pkg)
    manifest = inv.build_manifest(routes)
    manifest["allow"] = {"GET /gone @app/nowhere.py": {"reason": "old", "issue": 1}}

    problems = inv.check_ratchet(routes, manifest)

    assert any("no longer matches" in p for p in problems)


def test_the_committed_manifest_matches_the_tree_it_ships_with():
    """Negative control for the gate itself.

    If the committed baseline drifted from `app/`, every check above would still
    pass on its fixtures while the real gate reported a repository that does not
    exist.
    """
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "docs" / "contract" / "ratchet.json").read_text(encoding="utf-8"))
    problems = inv.check_ratchet(inv.collect(root / "app"), manifest)

    assert problems == [], "run: python3 scripts/api_contract_inventory.py --update-manifest docs/contract/ratchet.json"


def test_identity_does_not_depend_on_how_the_directory_was_named(tmp_path, monkeypatch):
    """A relative and an absolute scan of one tree must agree.

    They did not: the file path went into the key exactly as given, so a
    manifest written from the repository root did not match a run from
    anywhere else -- every route read as new. Caught by the committed-manifest
    control, not by any fixture here.
    """
    pkg = write(tmp_path, '''
        @router.get("/thing")
        def a():
            return {"x": 1}
    ''')
    absolute = {inv.route_key(r) for r in inv.collect(pkg)}

    monkeypatch.chdir(tmp_path)
    relative = {inv.route_key(r) for r in inv.collect(Path("app"))}

    assert absolute == relative
    assert absolute == {"GET /thing @app/routes.py"}


def test_a_deleted_route_is_not_reported_as_covered(tmp_path):
    """Leaving the uncovered set by deletion is not the same as by contract.

    The first message said "now covered" for a route that had been removed,
    which sends a reader looking for a `response_model` nobody wrote. Found
    while deleting the nine dead routes in issue 241 -- the gate was right to
    refuse, and wrong about why.
    """
    pkg = covered_and_uncovered(tmp_path)
    manifest = inv.build_manifest(inv.collect(pkg))

    routes = pkg / "routes.py"
    routes.write_text(
        routes.read_text().split('@router.get("/has-none")')[0], encoding="utf-8"
    )
    problems = inv.check_ratchet(inv.collect(pkg), manifest)

    assert any("no longer declared" in p for p in problems)
    assert not any("now covered" in p for p in problems)


def test_the_tree_digest_follows_the_files_not_the_last_commit(tmp_path):
    """It is the identity of what was parsed, so an edit must move it.

    The first version read `git rev-parse HEAD:app`, which describes the last
    commit. Those diverge exactly when someone edits `app/` and regenerates the
    baseline: deleting nine routes left the digest unchanged while the
    inventory under it had moved by nine.
    """
    pkg = covered_and_uncovered(tmp_path)
    before = inv._app_tree_sha(pkg)

    (pkg / "routes.py").write_text("# nothing at all\n", encoding="utf-8")
    after = inv._app_tree_sha(pkg)

    assert before != after
    assert len(after) == 64


def test_the_tree_digest_is_stable_for_an_unchanged_tree(tmp_path):
    pkg = covered_and_uncovered(tmp_path)
    assert inv._app_tree_sha(pkg) == inv._app_tree_sha(pkg)
