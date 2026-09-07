"""CLAUDE.md must point at symbols that exist, not at line numbers that drift.

Six references of the form `app/main.py:NNN` all pointed somewhere unrelated.
`app/main.py` is 1085 lines and every one of them missed by two or three
hundred:

    CLAUDE.md said                     actually at
    app/main.py:313  make_features()   ->  521   (a blank line)
    app/main.py:387  calculate_esg()   ->  633   (a comment about /v1/ routing)
    app/main.py:261  TreeExplainer     ->  508
    app/main.py:144-151  CORS origins  ->  368
    app/main.py:118-141  HEAD -> GET   ->  339
    app/main.py:199-237  model loading ->  427, 429, 443, 484

A line number cannot be kept true: nobody recomputes it when the file above it
changes, and the drift is silent. A symbol name can, and this file is what
keeps it so. Filed as #292.

The same edit corrected three counts. Only one of them was actually wrong in
the direction it looked: "162 published endpoints" turned out to be exactly
right for (path, method) pairs under /api/ excluding HEAD and OPTIONS -- the
document simply never said what it counted, and 180 unique paths and 157 /api/
paths are both plausible readings that do not match. The count of that is
asserted below so the definition and the figure cannot drift apart.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "CLAUDE.md"

#: `` `app/main.py` → `make_features()` `` -- the shape this file replaced the
#: line numbers with. The arrow is the marker: a bare mention of a module beside
#: a bare mention of a function is not a claim that one contains the other.
_SYMBOL_REF = re.compile(r"`([\w/]+\.py)`\s*→\s*`(\w+)\(\)`")

#: Any surviving `file.py:123` pointer, which is the thing being removed.
_LINE_REF = re.compile(r"`?([\w/]+\.py):(\d+)(?:-\d+)?`?")


def prose_only(text: str) -> str:
    """The document with fenced code blocks removed.

    A pasted traceback or a sample of program output legitimately contains
    `app/main.py:521`, and it is not a pointer the reader is meant to follow.
    Judging it as one makes the guard below fail on correct edits -- checked by
    appending a traceback to CLAUDE.md and watching it go red, which is how
    this function came to exist.
    """
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("```"):
            inside = not inside
            continue
        if not inside:
            out.append(line)
    return "\n".join(out)


def symbol_refs() -> list[tuple[str, str]]:
    return _SYMBOL_REF.findall(prose_only(DOC.read_text()))


def test_the_parser_finds_the_references_it_judges():
    """Negative control. The assertion below passes over an empty list."""
    refs = symbol_refs()
    assert len(refs) >= 2, (
        f"only {len(refs)} `file.py` → `symbol()` references parsed out of "
        "CLAUDE.md; the notation changed and this file judges nothing"
    )


def test_every_named_symbol_exists_in_the_named_file():
    """The defect: a pointer that lands somewhere unrelated.

    Resolved by AST rather than by grepping for `def <name>`, so a name that
    appears only in a comment or a string does not count as defined.
    """
    missing = []
    for module, symbol in symbol_refs():
        path = REPO / module
        if not path.exists():
            missing.append(f"{module}: no such file")
            continue
        defined = {
            node.name
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        if symbol not in defined:
            missing.append(f"{module} → {symbol}(): not defined there")

    assert not missing, "CLAUDE.md points at symbols that do not exist:\n  " + (
        "\n  ".join(missing)
    )


def test_no_line_number_pointers_come_back():
    """Line numbers are the failure mode, not a formatting preference.

    Six of them drifted silently. Allowing one back means allowing the class
    back, and nothing else in the repository would notice.
    """
    offenders = [
        f"{m}:{n}" for m, n in _LINE_REF.findall(prose_only(DOC.read_text()))
    ]
    assert not offenders, (
        "CLAUDE.md cites source lines by number in prose, and those drift the "
        "moment the file above them changes. Name the symbol instead. (Inside "
        "a fenced block a line number is sample output, not a pointer, and is "
        "not counted.):\n  "
        + "\n  ".join(offenders)
    )


def test_the_endpoint_count_still_means_what_it_says():
    """162 is right for one reading of "endpoint" and wrong for the others.

    The document now states the reading. If the app grows a route, the figure
    and the sentence explaining it have to move together -- which is the point
    of asserting it here rather than trusting the prose.

    Built from the route table, not from a hardcoded expectation of the app:
    the test fails when the number in the document stops matching the app, and
    says which way it moved.
    """
    import os

    os.environ.setdefault("SORA_OFFLINE", "1")
    os.environ.setdefault("RUN_SCHEDULER", "false")

    import app.main as main_module

    pairs = {
        (route.path, method)
        for route in main_module.app.routes
        if hasattr(route, "path")
        for method in (getattr(route, "methods", set()) or set())
        if method not in ("HEAD", "OPTIONS")
    }
    api_pairs = {p for p in pairs if p[0].startswith("/api/")}

    text = DOC.read_text()
    claimed = re.search(r"In production, (\d+) published endpoints", text)
    assert claimed, "CLAUDE.md no longer states the endpoint count in the form it did"

    assert int(claimed.group(1)) == len(api_pairs), (
        f"CLAUDE.md claims {claimed.group(1)} published endpoints; the app has "
        f"{len(api_pairs)} (path, method) pairs under /api/ excluding "
        f"HEAD/OPTIONS. Either the app changed or the definition did."
    )
    assert "(path,\n             method) pairs under /api/" in text or (
        "(path, method) pairs under /api/" in text
    ), "the sentence that says what the number counts is gone"


def test_a_line_number_inside_a_fence_is_output_not_a_pointer():
    """The discriminating case for `prose_only`.

    Without it the guard fails on a pasted traceback -- a correct edit that has
    nothing to do with the defect. Verified by appending one to CLAUDE.md and
    watching the guard go red before this function existed.
    """
    doc = (
        "See `app/main.py` → `make_features()`.\n"
        "\n"
        "```\n"
        'Traceback: File "app/main.py:521", line 521\n'
        "```\n"
        "\n"
        "And `app/telemetry.py:12` in prose, which is a pointer.\n"
    )

    found = [f"{m}:{n}" for m, n in _LINE_REF.findall(prose_only(doc))]

    assert found == ["app/telemetry.py:12"], found
    assert "app/main.py:521" not in found, "sample output counted as a pointer"
