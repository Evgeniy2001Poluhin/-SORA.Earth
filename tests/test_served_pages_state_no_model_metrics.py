"""No page the server hands out states a model metric it did not compute.

The legacy admin sidebar (`app/static/pages/admin.html`), served without login
at `GET /admin/{path}` and as `/static/pages/admin.html`, carried:

    v2.0 Apr 2026 · 100 runs
    Model AUC 0.919 · F1 0.855

written into the HTML and recomputed by nothing. Neither number described any
model in the repository -- `models/metrics.json` records roc_auc 0.905 and
f1_score 0.9016 -- so every visitor was told a performance figure no model has.

A metric belongs to a model and a date. Typed into markup it is neither, and it
stays on the page after the model it once described is retrained or replaced.
That is the same trap CLAUDE.md records for the suite size ("a ratio nobody
recomputed, wrong by a factor of eight"), in a place with more readers.

Scoped to the HTML under `app/static`, which is what the server returns as-is.
The React bundle is out of scope here: its equivalent, the home page's KPI
strip, is an open product decision rather than a defect to guard against.
"""
import pathlib
import re

import pytest

STATIC = pathlib.Path(__file__).resolve().parent.parent / "app" / "static"

# A metric name followed by a decimal, or a decimal followed by a metric name.
# `_` is a word character, so `\bAUC\b` never matches inside `roc_auc`: the
# separators are spelled out, and the positive control below would catch a
# regression to the narrower form.
METRIC = re.compile(
    r"(?:\b(?:ROC[ _-]?AUC|AUC|F1(?:[ _-]?score)?|accuracy|precision|recall)\b[^<\d]{0,12}\d\.\d{2,4})"
    r"|(?:\b\d\.\d{2,4}\s*(?:ROC[ _-]?AUC|AUC|F1)\b)",
    re.IGNORECASE,
)


def _served_html():
    """The HTML the repository ships, not what a build happens to leave behind.

    `app/static/spa/` is gitignored build output: present after `npm run
    build`, absent in the backend CI job. `app/static/index.html` is a tracked
    symlink into it. Following either would make the scan depend on whether a
    frontend build ran first -- the first version of this test died on that
    broken link with FileNotFoundError, which a glance at "failed" would have
    read as the defect it looks for.
    """
    return sorted(
        p for p in STATIC.rglob("*.html")
        if not p.is_symlink() and "spa" not in p.relative_to(STATIC).parts
    )


def test_the_pattern_recognises_the_defect_it_was_written_for():
    """Without this the scan below could pass by matching nothing at all."""
    assert METRIC.search("Model AUC 0.919 · F1 0.855")
    assert METRIC.search("roc_auc 0.905")
    assert METRIC.search("0.92 AUC")
    assert METRIC.search("f1_score: 0.9016")
    assert not METRIC.search("v2.0 build · 12 pages")


def test_no_served_html_page_states_a_model_metric():
    pages = _served_html()
    assert len(pages) >= 5, (
        f"found {len(pages)} HTML files under {STATIC}; the scan covers too few "
        f"pages to mean anything"
    )

    found = []
    for page in pages:
        for lineno, line in enumerate(page.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in METRIC.finditer(line):
                found.append(f"{page.relative_to(STATIC)}:{lineno}: {m.group(0)!r}")

    assert not found, (
        "a served page states a model metric it does not compute -- remove it, "
        "or render it from the model's own metrics:\n  " + "\n  ".join(found)
    )
