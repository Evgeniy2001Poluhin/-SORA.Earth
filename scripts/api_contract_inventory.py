#!/usr/bin/env python3
"""Inventory every declared API route and what it promises about its output.

This is a measuring instrument, not a fix. It exists because the number that
motivated the contract work -- "22 of 186 routes declare a response_model" --
came from two independent greps, one counting decorators and one counting
`response_model=`, never matched to each other. That is an estimate. This
script derives the figure per route, from the AST, and writes it in a form
another run can reproduce and disagree with.

What it reports per route: the declared path, methods, status code, whether a
`response_model` is declared, whether the route is legitimately exempt (a
redirect, a stream, a file, plain text, HTML or a websocket), how many `return`
statements the handler has, and the key sets of those returns that are static
dict literals. That last column is the one that matters: a handler with four
returns carrying four different key sets publishes four shapes under one
status, and `response_model` alone does not prove otherwise.

Usage:
    python3 scripts/api_contract_inventory.py                    # summary
    python3 scripts/api_contract_inventory.py --json out.json
    python3 scripts/api_contract_inventory.py --csv out.csv
    python3 scripts/api_contract_inventory.py --fail-under 12    # ratchet use
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
DECORATOR_OBJECTS = {"router", "app", "api_v1", "api", "v1"}

# Response classes that make a declared `response_model` meaningless: the body
# is not a serialised model. These are exemptions, not defects -- but they must
# be named as such rather than silently counted as uncovered.
EXEMPT_RESPONSE_CLASSES = {
    "HTMLResponse",
    "PlainTextResponse",
    "FileResponse",
    "StreamingResponse",
    "RedirectResponse",
    "Response",
}


@dataclass
class Route:
    file: str
    line: int
    function: str
    decl_path: str
    methods: list[str]
    status_code: Optional[int]
    response_model: Optional[str]
    response_class: Optional[str]
    is_websocket: bool
    exempt_reason: Optional[str]
    return_count: int
    raise_count: int
    literal_shapes: list[list[str]] = field(default_factory=list)
    dynamic_returns: int = 0
    router_prefix: str = ""
    public_path: Optional[str] = None
    consumers: int = 0
    test_references: int = 0

    @property
    def covered(self) -> bool:
        return self.response_model is not None

    @property
    def shape_conflict(self) -> bool:
        """True when static returns disagree about their keys under one status.

        Two literal returns with different key sets mean a consumer written
        against one of them reads `undefined` from the other. This is the
        machine-checkable form of what #239 describes by hand.
        """
        if len(self.literal_shapes) < 2:
            return False
        first = set(self.literal_shapes[0])
        return any(set(s) != first for s in self.literal_shapes[1:])


def _name_of(node: ast.AST) -> Optional[str]:
    """Best-effort dotted name for a decorator target or annotation."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name_of(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _name_of(node.func)
    if isinstance(node, ast.Subscript):
        return _name_of(node.value)
    return None


def _const_str(node: ast.AST) -> Optional[str]:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _const_int(node: ast.AST) -> Optional[int]:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, int) else None


def _decorator_route(dec: ast.AST) -> Optional[tuple[str, str]]:
    """Return (method, path) if this decorator declares a route."""
    if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
        return None
    obj = _name_of(dec.func.value)
    if obj is None or obj.split(".")[-1] not in DECORATOR_OBJECTS:
        return None
    method = dec.func.attr
    if method not in ROUTE_METHODS and method != "websocket":
        return None
    path = _const_str(dec.args[0]) if dec.args else None
    if path is None:
        return None
    return method, path


def _returns_in(fn: ast.AST) -> tuple[list[list[str]], int, int, int]:
    """Walk a handler, skipping nested functions, and describe its returns.

    Nested defs are skipped because their returns belong to the inner callable,
    not to the response the route produces.
    """
    literal_shapes: list[list[str]] = []
    dynamic = 0
    returns = 0
    raises = 0

    def walk(node: ast.AST) -> None:
        nonlocal dynamic, returns, raises
        for child in ast.iter_child_nodes(node):
            # A nested def's returns belong to that callable, not to the
            # response this route produces.
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Return):
                returns += 1
                value = child.value
                if isinstance(value, ast.Dict):
                    keys = [k for k in (_const_str(k) for k in value.keys) if k is not None]
                    if len(keys) == len(value.keys):
                        literal_shapes.append(sorted(keys))
                    else:
                        dynamic += 1
                elif value is not None:
                    dynamic += 1
            elif isinstance(child, ast.Raise):
                raises += 1
            walk(child)

    walk(fn)
    return literal_shapes, dynamic, returns, raises


def _router_prefix(tree: ast.AST) -> str:
    """The prefix this module's APIRouter declares, or "".

    Needed to disambiguate a declared path against the public one. Suffix
    matching alone is not enough and gets it wrong: `/drift` is a suffix of
    both `/api/v1/model/drift` and `/api/v1/mlops/drift`, which are different
    endpoints with different contracts. Issue #239 named the wrong URL for
    exactly this reason.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _name_of(node.func) == "APIRouter":
            for kw in node.keywords:
                if kw.arg == "prefix":
                    return _const_str(kw.value) or ""
    return ""


def resolve_public_paths(routes: list[Route], openapi: dict) -> None:
    """Attach the public path from an OpenAPI document, in place.

    Ambiguity is recorded as ambiguity, never resolved by picking the first
    candidate -- a wrong path sends the next reader to a 404.
    """
    paths = openapi.get("paths", {})
    for r in routes:
        method = r.methods[0].lower()
        if method == "ws":
            continue
        want = r.router_prefix + r.decl_path
        exact = [p for p in paths if p.endswith(want) and method in paths[p]]
        if len(exact) == 1:
            r.public_path = exact[0]
        elif len(exact) > 1:
            r.public_path = "AMBIGUOUS:" + ",".join(sorted(exact))
        else:
            r.public_path = None


def _is_test_source(path: Path) -> bool:
    """A reference from a test is not a consumer.

    A route called only by its own test would otherwise be reported as having
    a live consumer, which is the opposite of what the priority order needs.
    """
    name = path.name
    if ".test." in name or ".spec." in name or name.endswith(".d.ts"):
        return True
    return "test" in {part.lower() for part in path.parts}


def _count_references(blob: str, client_path: str) -> int:
    """References to a path that are actually calls, not prose.

    A quoted literal is one. A template literal is one only when the path ends
    where the path ends: `` `/drift?${q}` `` counts for /drift, `` `/drifting` ``
    does not.
    """
    total = blob.count(f'"{client_path}"') + blob.count(f"'{client_path}'")
    for match in re.finditer(re.escape("`" + client_path), blob):
        nxt = blob[match.end() : match.end() + 1]
        if nxt in {"?", "`", "#", "$", ""}:
            total += 1
    return total


def attach_consumers(routes: list[Route], web_dir: Path) -> None:
    """Count references to each public path in the frontend source.

    Scans the whole tree, not just the api/ directory: several components call
    `api("/lstm-status")` inline, and a scan limited to the endpoint modules
    reports them as having no consumer. Test sources are counted separately
    and never as consumers.
    """
    if not web_dir.is_dir():
        return
    src: list[str] = []
    tests: list[str] = []
    for path in sorted(web_dir.rglob("*")):
        if path.suffix not in {".ts", ".tsx"} or not path.is_file():
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        (tests if _is_test_source(path) else src).append(body)
    src_blob = "\n".join(src)
    test_blob = "\n".join(tests)
    for r in routes:
        if not r.public_path or r.public_path.startswith("AMBIGUOUS"):
            continue
        # The client prepends its own base, so it names the path without it.
        client_path = r.public_path.split("/api/v1", 1)[-1] if "/api/v1" in r.public_path else r.public_path
        r.consumers = _count_references(src_blob, client_path)
        r.test_references = _count_references(test_blob, client_path)


def collect(app_dir: Path) -> list[Route]:
    routes: list[Route] = []
    for path in sorted(app_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        prefix = _router_prefix(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                found = _decorator_route(dec)
                if not found:
                    continue
                method, decl_path = found
                assert isinstance(dec, ast.Call)
                kw = {k.arg: k.value for k in dec.keywords if k.arg}

                response_model = _name_of(kw["response_model"]) if "response_model" in kw else None
                response_class = _name_of(kw["response_class"]) if "response_class" in kw else None
                status_code = _const_int(kw["status_code"]) if "status_code" in kw else None
                is_ws = method == "websocket"

                exempt = None
                if is_ws:
                    exempt = "websocket"
                elif response_class and response_class.split(".")[-1] in EXEMPT_RESPONSE_CLASSES:
                    exempt = f"response_class={response_class}"

                shapes, dynamic, n_ret, n_raise = _returns_in(node)
                routes.append(
                    Route(
                        file=str(path),
                        line=dec.lineno,
                        function=node.name,
                        decl_path=decl_path,
                        methods=[method.upper()] if not is_ws else ["WS"],
                        status_code=status_code,
                        response_model=response_model,
                        response_class=response_class,
                        is_websocket=is_ws,
                        exempt_reason=exempt,
                        router_prefix=prefix,
                        return_count=n_ret,
                        raise_count=n_raise,
                        literal_shapes=shapes,
                        dynamic_returns=dynamic,
                    )
                )
    # Sorted so two runs of the same tree produce byte-identical output.
    # `ast.walk` is breadth-first, so handler order within a file follows the
    # tree's shape rather than the file's, which would make the committed
    # baseline churn for no reason.
    routes.sort(key=lambda r: (r.file, r.line, r.decl_path, tuple(r.methods)))
    return routes


def summarise(routes: list[Route]) -> dict:
    considered = [r for r in routes if r.exempt_reason is None]
    covered = [r for r in considered if r.covered]
    conflicts = [r for r in considered if r.shape_conflict]
    from collections import Counter

    # Two declarations resolving to one public path+method: only the first
    # registered answers, and the other is unreachable. Found nine of these on
    # the first run -- app/api/auth.py and app/api/admin_ai.py declare routes
    # their routers never register, including a JSON-body /auth/login while
    # the live one takes a form.
    addressed = Counter(
        (r.public_path, tuple(r.methods))
        for r in routes
        if r.public_path and not r.public_path.startswith("AMBIGUOUS")
    )
    duplicate_paths = sorted(p for (p, _m), n in addressed.items() if n > 1)
    return {
        "routes_total": len(routes),
        "exempt": len(routes) - len(considered),
        "considered": len(considered),
        "with_response_model": len(covered),
        "coverage_pct": round(100 * len(covered) / len(considered), 1) if considered else 0.0,
        "shape_conflicts": len(conflicts),
        "duplicate_public_paths": duplicate_paths,
    }


def _git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _head_sha() -> str:
    """The checkout that ran the script. Not what was measured."""
    return _git("rev-parse", "HEAD")


def _app_tree_sha(app_dir: Path) -> str:
    """The identity of the tree that was actually measured.

    The generator commit moves whenever a document changes; this does not. Two
    inventories with the same `app_tree_sha` measured the same code, whatever
    else the branch was carrying, and that is the value a manifest should pin.
    """
    return _git("rev-parse", f"HEAD:{app_dir.as_posix()}")


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_openapi_provenance(openapi_path: Path, app_tree_sha: str) -> Optional[str]:
    """Refuse to mix a code inventory with an OpenAPI snapshot of other code.

    The static inventory and the OpenAPI document are two baselines. If the
    snapshot was taken from a deployment running different code, every
    resolved path and consumer count is attributed to the wrong tree -- and
    nothing downstream could tell. A sidecar `<stem>.meta.json` pins the tree
    the snapshot belongs to; when it is present and disagrees, this refuses.

    Returns an error message, or None when the pairing is sound or unpinned.
    """
    meta_path = openapi_path.with_suffix(".meta.json")
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"{meta_path}: unreadable ({exc})"

    expected_digest = meta.get("sha256")
    actual_digest = _sha256(openapi_path)
    if expected_digest and expected_digest != actual_digest:
        return (
            f"{openapi_path.name} does not match its manifest digest\n"
            f"  manifest {expected_digest}\n  actual   {actual_digest}"
        )

    expected_tree = meta.get("app_tree_sha")
    if expected_tree and app_tree_sha != "unknown" and expected_tree != app_tree_sha:
        return (
            "the OpenAPI snapshot describes a different tree than the one being "
            f"inventoried\n  snapshot app_tree_sha {expected_tree}\n"
            f"  measured app_tree_sha {app_tree_sha}\n"
            "Refresh the snapshot against this tree, or pass --allow-openapi-drift "
            "and treat every resolved path as unverified."
        )
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app-dir", default="app", help="package to scan (default: app)")
    ap.add_argument("--json", dest="json_out", help="write the full inventory here")
    ap.add_argument("--csv", dest="csv_out", help="write the full inventory here")
    ap.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="exit 1 if coverage%% falls below this -- for the ratchet, once a baseline exists",
    )
    ap.add_argument("--list-uncovered", action="store_true", help="print every route with no response_model")
    ap.add_argument("--openapi", help="a saved /openapi.json; resolves declared paths to public ones")
    ap.add_argument("--web-dir", default="web/src", help="frontend source, scanned for consumers")
    ap.add_argument(
        "--allow-openapi-drift",
        action="store_true",
        help="proceed when the OpenAPI snapshot is pinned to a different app tree",
    )
    args = ap.parse_args()

    app_dir = Path(args.app_dir)
    if not app_dir.is_dir():
        print(f"no such directory: {app_dir}", file=sys.stderr)
        return 2

    routes = collect(app_dir)
    app_tree = _app_tree_sha(app_dir)
    if args.openapi:
        problem = check_openapi_provenance(Path(args.openapi), app_tree)
        if problem and not args.allow_openapi_drift:
            print(f"REFUSED: {problem}", file=sys.stderr)
            return 3
        if problem:
            print(f"WARNING (--allow-openapi-drift): {problem}", file=sys.stderr)
        try:
            resolve_public_paths(routes, json.loads(Path(args.openapi).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"could not read --openapi {args.openapi}: {exc}", file=sys.stderr)
            return 2
        attach_consumers(routes, Path(args.web_dir))
    summary = summarise(routes)
    # `app_tree_sha` is the baseline's identity and is stable: it names the
    # code that was measured. The generating commit is deliberately NOT stored
    # here. It changes every time anything is committed -- including the commit
    # that adds this very file -- so a baseline carrying it is stale the moment
    # it lands, and two runs of the same tree would never be byte-identical.
    # It is printed for the run instead.
    summary["app_tree_sha"] = app_tree
    summary["app_dir"] = str(app_dir)
    if args.openapi:
        summary["openapi_file"] = str(args.openapi)
        # Not `openapi_sha256`: gitleaks' generic-api-key rule fires on a long
        # hex value beside a field name containing "api", and "openapi" does.
        # The digest of a public document is not a credential, but allowlisting
        # it by value would rot the moment the snapshot is refreshed, and the
        # repository's own .gitleaks.toml prefers a name that says what it is
        # over an exception that outlives its reason. Do not rename this back.
        summary["snapshot_sha256"] = _sha256(Path(args.openapi))

    payload = {"summary": summary, "routes": [asdict(r) | {"shape_conflict": r.shape_conflict} for r in routes]}

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if args.csv_out:
        with open(args.csv_out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(
                ["file", "line", "function", "decl_path", "router_prefix", "public_path",
                 "consumers", "methods", "status_code", "response_model", "response_class",
                 "exempt_reason", "return_count", "raise_count", "dynamic_returns",
                 "shape_conflict"]
            )
            for r in routes:
                w.writerow(
                    [r.file, r.line, r.function, r.decl_path, r.router_prefix,
                     r.public_path or "", r.consumers, "|".join(r.methods), r.status_code or "",
                     r.response_model or "", r.response_class or "", r.exempt_reason or "",
                     r.return_count, r.raise_count, r.dynamic_returns, int(r.shape_conflict)]
                )

    print(f"generator commit    {_head_sha()}  (this run; not recorded in the baseline)")
    print(f"app tree            {summary['app_tree_sha']}  (what was measured; the baseline's identity)")
    print(f"routes declared     {summary['routes_total']}")
    print(f"  exempt            {summary['exempt']}  (websocket / non-model response_class)")
    print(f"  considered        {summary['considered']}")
    print(f"with response_model {summary['with_response_model']}")
    print(f"coverage            {summary['coverage_pct']}%")
    print(f"shape conflicts     {summary['shape_conflicts']}  (static returns disagreeing under one status)")
    if args.openapi:
        unresolved = [r for r in routes if not r.is_websocket and not r.public_path]
        ambiguous = [r for r in routes if r.public_path and r.public_path.startswith("AMBIGUOUS")]
        consumed = [r for r in routes if r.shape_conflict and r.consumers > 0]
        print(f"  unmounted         {len(unresolved)}  (declared but absent from the OpenAPI document)")
        print(f"  ambiguous         {len(ambiguous)}")
        dups = summary["duplicate_public_paths"]
        print(f"  duplicate paths   {len(dups)}  (two declarations, one address -- one is unreachable)")
        for d in dups:
            print(f"    {d}")
        print(f"  conflicts with a live frontend consumer: {len(consumed)}")
        for r in sorted(consumed, key=lambda r: r.public_path or ""):
            print(f"    {'|'.join(r.methods):6} {r.public_path}")

    if args.list_uncovered:
        print("\nuncovered:")
        for r in routes:
            if r.exempt_reason is None and not r.covered:
                flag = "  <-- shapes disagree" if r.shape_conflict else ""
                print(f"  {'|'.join(r.methods):5} {r.decl_path:42} {r.file}:{r.line}{flag}")

    if args.fail_under is not None and summary["coverage_pct"] < args.fail_under:
        print(f"\nFAIL: coverage {summary['coverage_pct']}% is below --fail-under {args.fail_under}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
