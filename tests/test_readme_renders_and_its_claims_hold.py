"""README.md must render, and the claims in it that can be checked must hold.

Two separate defects, both present since the initial commit of 2026-05-07 and
both invisible to every test the project had.

**It did not render.** One ```` ```text ```` fence was opened and never closed,
and it was the only fence in the file. Under CommonMark an unclosed fenced
block runs to the end of the document, so 64 of 116 lines -- "Требования",
"Документация", "Architecture diagrams", "Screenshots" and the production URL
-- came out as one unstyled code block. Measured with mistune: 2 of 6 `##`
headings rendered as headings, and 3 links out of the file's many.

**It claimed five Grafana alerts, one of which does not exist.** The line read
"5 алертов (drift, retrain fail, AUC drop, latency, app down)". There are ten,
and no alert on AUC: the nearest is `Retrain Failed`, on
`sora_model_rejected_total`. Same shape as #268, where the metrics section
named three metrics that did not exist and one of them had a Grafana alert
configured against it (#284).

The rendering test is here rather than in a linter because "the front page of
the project renders" is a claim about this repository, and nothing else was
making it.

The fence check sweeps **every** markdown file, not the one that prompted it.
Widened, it immediately found a second: `docs/RUNBOOK.md` carried an orphan
closing fence after an already-closed block. One file out of 79 would have been
missed by a check scoped to the file where the problem was noticed.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"
ALERTS = REPO / "grafana" / "provisioning" / "alerting" / "alerts.yml"

#: A fence at the start of a line. Indented fences inside list items exist in
#: CommonMark, and this file has none; if one is added, this becomes too strict
#: rather than too loose, which is the safe direction for a guard.
_FENCE = re.compile(r"^```")

#: `[file:892]`, `[web:1033]` -- markers left by whatever produced the original
#: text. They cite nothing a reader can open, and there were fourteen.
_CITATION_ARTEFACT = re.compile(r"\[(?:file|web):\d+\]")


def alert_titles() -> list[str]:
    """Alert titles as the provisioning file spells them.

    Read as text, not through a YAML parser: the file is Grafana's schema, and
    a parser here would need to know its shape to find `title` at the right
    depth. Every title in it is a `title:` key at one indentation.
    """
    return re.findall(r"^\s*title:\s*(.+?)\s*$", ALERTS.read_text(), re.M)


def test_the_parsers_find_what_they_judge():
    """Negative control. Two of the three tests below pass over empty input."""
    titles = alert_titles()
    assert len(titles) >= 5, (
        f"only {len(titles)} alert titles parsed out of {ALERTS.name}; the file "
        "changed shape and the claim below is being checked against nothing"
    )
    assert README.read_text().strip(), "README.md is empty"
    files = markdown_files()
    assert len(files) >= 50, (
        f"only {len(files)} markdown files found under {REPO}; the sweep below "
        "is passing over almost nothing"
    )
    assert README in files, "the sweep does not include README.md itself"


def markdown_files() -> list[Path]:
    """Every tracked markdown file, excluding vendored and scratch trees."""
    skip = {".git", "node_modules", ".claude", "venv", ".venv", ".venv311"}
    return sorted(
        p
        for p in REPO.rglob("*.md")
        if not skip & set(p.relative_to(REPO).parts)
    )


def test_every_fenced_block_is_closed():
    """An unclosed fence swallows the rest of the document.

    Over every markdown file, not only the README. Scoping this to the file
    that prompted it would have missed `docs/RUNBOOK.md`, which carried an
    orphan closing fence and was found by widening the same check to the other
    78 files -- the sampling mistake this project keeps making.

    Asserted on the count rather than by rendering, so the failure names the
    file's own structure: an odd number of fence lines is one unclosed block,
    and the line numbers say where to look.
    """
    broken = []
    for path in markdown_files():
        fences = [
            n
            for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1)
            if _FENCE.match(line)
        ]
        if len(fences) % 2:
            broken.append((path.relative_to(REPO), len(fences), fences))
    assert not broken, (
        "these markdown files have an unclosed code fence, so everything after "
        "it renders as code:\n  "
        + "\n  ".join(
            f"{p}: {n} fence lines, at {f}" for p, n, f in broken
        )
    )


def test_the_document_actually_renders_as_a_document():
    """The property the fence count is a proxy for, checked directly.

    Every `##` in the source must come out as a heading. The count test above
    would pass on a file with two unclosed fences; this one would not.
    """
    import mistune

    source = README.read_text()
    html = mistune.create_markdown()(source)
    in_source = len(re.findall(r"^## ", source, re.M))
    rendered = len(re.findall(r"<h2", html))
    assert rendered == in_source, (
        f"{in_source} '##' headings in README.md, {rendered} render as headings; "
        "the rest are inside a code block"
    )


def test_the_alert_count_and_names_match_the_provisioning_file():
    """A named alert that does not exist is worse than no list at all.

    The old line named "AUC drop", which nothing provisions, beside four that
    do -- so the invented one was indistinguishable from the real ones.
    """
    titles = alert_titles()
    text = README.read_text()

    claimed = re.search(r"\*\*(\d+) алертов\*\*", text)
    assert claimed, (
        "README.md no longer states how many Grafana alerts there are in the "
        "form '**N алертов**'; the count was wrong once and is worth pinning"
    )
    assert int(claimed.group(1)) == len(titles), (
        f"README.md claims {claimed.group(1)} alerts, "
        f"{ALERTS.relative_to(REPO)} provisions {len(titles)}"
    )

    missing = [t for t in titles if f"`{t}`" not in text]
    assert not missing, (
        "README.md lists the alerts by name but omits:\n  " + "\n  ".join(missing)
    )


def test_no_citation_artefacts_remain():
    """`[file:892]` and friends point at nothing a reader can open."""
    found = sorted(set(_CITATION_ARTEFACT.findall(README.read_text())))
    assert not found, (
        "README.md still carries citation markers that reference nothing: "
        + ", ".join(found)
    )
