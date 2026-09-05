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


def collect(app_dir: Path) -> list[Route]:
    routes: list[Route] = []
    for path in sorted(app_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
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
                        return_count=n_ret,
                        raise_count=n_raise,
                        literal_shapes=shapes,
                        dynamic_returns=dynamic,
                    )
                )
    return routes


def summarise(routes: list[Route]) -> dict:
    considered = [r for r in routes if r.exempt_reason is None]
    covered = [r for r in considered if r.covered]
    conflicts = [r for r in considered if r.shape_conflict]
    return {
        "routes_total": len(routes),
        "exempt": len(routes) - len(considered),
        "considered": len(considered),
        "with_response_model": len(covered),
        "coverage_pct": round(100 * len(covered) / len(considered), 1) if considered else 0.0,
        "shape_conflicts": len(conflicts),
    }


def _head_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


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
    args = ap.parse_args()

    app_dir = Path(args.app_dir)
    if not app_dir.is_dir():
        print(f"no such directory: {app_dir}", file=sys.stderr)
        return 2

    routes = collect(app_dir)
    summary = summarise(routes)
    summary["commit"] = _head_sha()
    summary["app_dir"] = str(app_dir)

    payload = {"summary": summary, "routes": [asdict(r) | {"shape_conflict": r.shape_conflict} for r in routes]}

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if args.csv_out:
        with open(args.csv_out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(
                ["file", "line", "function", "decl_path", "methods", "status_code",
                 "response_model", "response_class", "exempt_reason", "return_count",
                 "raise_count", "dynamic_returns", "shape_conflict"]
            )
            for r in routes:
                w.writerow(
                    [r.file, r.line, r.function, r.decl_path, "|".join(r.methods), r.status_code or "",
                     r.response_model or "", r.response_class or "", r.exempt_reason or "",
                     r.return_count, r.raise_count, r.dynamic_returns, int(r.shape_conflict)]
                )

    print(f"commit              {summary['commit']}")
    print(f"routes declared     {summary['routes_total']}")
    print(f"  exempt            {summary['exempt']}  (websocket / non-model response_class)")
    print(f"  considered        {summary['considered']}")
    print(f"with response_model {summary['with_response_model']}")
    print(f"coverage            {summary['coverage_pct']}%")
    print(f"shape conflicts     {summary['shape_conflicts']}  (static returns disagreeing under one status)")

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
