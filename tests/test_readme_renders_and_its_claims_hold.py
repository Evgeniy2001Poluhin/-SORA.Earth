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

**A third defect, which both of the guards above were measured blind to.** On
2026-09-20 `CLAUDE.md` carried a line reading ```` ``` Its own job name rather
than a ```` -- a closing fence with prose on the same line, two spaces of
indentation in, inside a list item. Under CommonMark a closing fence may be
followed only by spaces or tabs, so that line closed nothing: the block stayed
open and 24 lines of prose rendered inside `<pre><code>`. Fixed in 1b12efa; the
full suite, `required-checks` included, was green on the commit before it.

Why each guard missed it, both measured before the widening below:

* `test_every_fenced_block_is_closed` anchored its pattern at column 0, so
  neither of those two indented fences was counted at all -- and it compared the
  *parity* of the count, which two fence lines satisfy whatever they mean.

* `test_the_document_actually_renders_as_a_document` rendered `README.md` and
  nothing else, and compared `##` counts. Measured on the broken and the fixed
  `CLAUDE.md`: 13 h2 and 20 h3 in both. This defect does not move heading
  counts, so that proxy could not have seen it even aimed at the right file -- a
  confident false negative.

What replaced them, and the measurement behind each:

* A **state walk** in place of the parity count. It allows the three spaces of
  indentation CommonMark allows and tells an opening fence (info string
  permitted) from a closing one (whitespace only). This is the correct
  structural reading and it strictly covers what parity covered -- but on its
  own it does **not** see the 1b12efa defect, and that was measured, not
  assumed: the file's remaining fences re-pair around the broken line and
  nothing is left open at the end. Recorded here because a walk that sounds
  stricter is not the same as one that fires.

* An **info-string rule**, which is what does fire. Every info string in the 81
  markdown files here is one language tag: `bash` 181 times, `python` 76,
  `text` 35, and eight others. A fence line whose info string carries internal
  whitespace is a sentence, which is what a closing fence with prose after it
  looks like -- and there is exactly one in the repository's history, the line
  above. Zero on `origin/main`, so the rule costs nothing to hold.

* A **rendering sweep** over every file the walk sweeps, asserting the thing
  the defect moves rather than a proxy for it: a fence marker surviving inside
  `<pre><code>`, which is what a fence that failed to close leaves behind. One
  hit on the broken `CLAUDE.md`, none on the fixed one, none across the other
  80 files.

The three see different failures and none subsumes the others. The walk sees a
block left open at the end of a file, where nothing is left inside it that looks
like a fence. The rendering sweep resolves the list-relative indentation a
line-based walk cannot -- `DEPLOYMENT_STATUS.md` has four fence pairs indented
four and five spaces, which are fences only relative to their list item, and
`docs/archive/RUNBOOK-2026-05-16.md` carries a stray fence inside a list item
which the walk mis-pairs in silence: it reads lines 142 and 178 as one block,
where mistune closes 142 at the end of the list and pairs 178 with 175. Measured
both ways; the walk reports nothing there and nothing is swallowed, so the
mis-pairing costs no false alarm -- but it is why the walk's verdict is not the
last word on a fence inside a list.

The heading comparison is kept, scoped to `README.md` as before: it was a true
positive for the defect it was written for, and what it cannot see is now
written down instead of assumed. Every new assertion was confirmed red against
the pre-fix content and green against the fix, in that order, and
`test_the_detectors_fire_on_the_defect_they_were_written_for` keeps that
direction checkable without reaching for a commit that may be pruned.
"""

from __future__ import annotations

import html as _html
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"
ALERTS = REPO / "grafana" / "provisioning" / "alerting" / "alerts.yml"

#: An **opening** fence: up to three spaces of indentation -- which is what
#: CommonMark allows and what the old `^```` ``` ```` missed -- three or more
#: backticks or tildes, then an info string. A backtick fence's info string may
#: not itself contain a backtick, which keeps an inline span off this list.
_FENCE_OPEN = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})(?P<info>.*)$")

#: A **closing** fence: the same, with nothing but whitespace after the marker.
#: This is the distinction the old single pattern did not make, and the whole of
#: the defect in 1b12efa -- a marker followed by a sentence closes nothing.
_FENCE_CLOSE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})[ \t]*$")

#: A fence marker at the start of a line of rendered code-block content. mistune
#: strips the opening fence's indentation from the content it emits, so leading
#: whitespace is allowed rather than column 0 required.
_FENCE_IN_CONTENT = re.compile(r"^[ \t]*(?:`{3,}|~{3,})")

#: mistune's output for a code block, fenced or indented.
_RENDERED_CODE = re.compile(r"<pre><code[^>]*>(.*?)</code></pre>", re.S)

#: `[file:892]`, `[web:1033]` -- markers left by whatever produced the original
#: text. They cite nothing a reader can open, and there were fourteen.
_CITATION_ARTEFACT = re.compile(r"\[(?:file|web):\d+\]")

#: The shape of the 1b12efa defect: the indentation, the list item it sat in,
#: and prose on the closing fence's line. A sample to run the detectors
#: against, not a copy of any document -- what the documents say is read off
#: disk by the tests that judge them.
DEFECT_SAMPLE = """\
- A list item, so the fences below are indented and column 0 will not see them.

  ```bash
  echo measuring
  ``` Its own job name rather than a
  second target under `sora-app`: both processes publish metrics of the same
  names.

  No multiprocess directory there.

## A heading that should not be inside a code block
"""

#: The repaired form of the same passage. The detectors must be silent on it,
#: or they fire on correct documents.
REPAIRED_SAMPLE = """\
- A list item, so the fences below are indented and column 0 will not see them.

  ```bash
  echo measuring
  ```

  Its own job name rather than a second target under `sora-app`: both
  processes publish metrics of the same names.

  No multiprocess directory there.

## A heading that should not be inside a code block
"""

#: The plainer failure, and the one this file was originally written for: a
#: fence opened and never closed. Nothing inside the block looks like a fence,
#: so the rendering sweep cannot see this one -- only the walk can.
UNCLOSED_SAMPLE = """\
# Title

```bash
echo measuring

Prose that should have rendered as prose.
"""


def alert_titles() -> list[str]:
    """Alert titles as the provisioning file spells them.

    Read as text, not through a YAML parser: the file is Grafana's schema, and
    a parser here would need to know its shape to find `title` at the right
    depth. Every title in it is a `title:` key at one indentation.
    """
    return re.findall(r"^\s*title:\s*(.+?)\s*$", ALERTS.read_text(), re.M)


def markdown_files() -> list[Path]:
    """Every tracked markdown file, excluding vendored and scratch trees."""
    skip = {".git", "node_modules", ".claude", "venv", ".venv", ".venv311"}
    return sorted(
        p
        for p in REPO.rglob("*.md")
        if not skip & set(p.relative_to(REPO).parts)
    )


def fence_defects(source: str) -> list[tuple[int, str, str]]:
    """Lines where this text's fences stop describing a code block.

    Two findings, both structural -- no parser, so each names a line number.

    **A block left open at the end.** A state walk, not a count. Parity was the
    old mistake twice over: it passes on two unclosed blocks, and it passed on
    the 1b12efa defect. CommonMark's rules as far as a line-based walk carries
    them: up to three spaces of indentation on either fence; a closing fence
    uses its opener's character and is at least as long, so ```` ``` ```` inside
    a ```` ```` ```` block is content; only whitespace may follow a close; and
    an orphan close is not a special case -- CommonMark reads it as an opening
    fence with an empty info string, and it is reported here as unclosed, which
    is the same thing.

    **An info string that is prose.** Measured over the 81 markdown files here,
    every info string is a single language tag -- `bash` 181, `python` 76,
    `text` 35, `json` 14, `yaml` 7, `sql` 7, and four more once or twice each.
    Internal whitespace means a sentence, which is what a closing fence with
    prose after it looks like, and it is the finding that fires on 1b12efa. It
    is checked on every fence line rather than only on those a walk believes are
    inside a block, because the walk's model of list containment is the thing
    that cannot be trusted here. If a fence ever needs a genuine multi-word info
    string, this is the check to widen -- deliberately, with the convention it
    pins written down.

    Indentation deeper than three spaces is a fence only relative to the list
    item containing it, and resolving that needs a block parser. Four such pairs
    live in `DEPLOYMENT_STATUS.md`; both halves of each are invisible here, so
    they raise no false alarm, and an unclosed one among them would be missed.
    `test_the_document_actually_renders_as_a_document` runs a real parser.
    """
    lines = source.splitlines()
    found: list[tuple[int, str, str]] = []
    opened_at: int | None = None
    opened_with = ""

    for n, line in enumerate(lines, 1):
        m = _FENCE_OPEN.match(line)
        if m is not None:
            marker, info = m.group("marker"), m.group("info")
            is_fence = not (marker[0] == "`" and "`" in info)
            if is_fence and re.search(r"\s", info.strip()):
                found.append(
                    (
                        n,
                        line,
                        "info string is prose, not a language tag: a closing "
                        "fence may be followed only by whitespace, so this "
                        "line closes nothing",
                    )
                )

        if opened_at is None:
            if m is None:
                continue
            marker, info = m.group("marker"), m.group("info")
            if marker[0] == "`" and "`" in info:
                continue
            opened_at, opened_with = n, marker
            continue

        closing = _FENCE_CLOSE.match(line)
        if closing is None:
            continue
        marker = closing.group("marker")
        if marker[0] == opened_with[0] and len(marker) >= len(opened_with):
            opened_at, opened_with = None, ""

    if opened_at is not None:
        found.append(
            (opened_at, lines[opened_at - 1], "opened and never closed")
        )
    return sorted(found)


def fences_rendered_as_content(source: str) -> list[str]:
    """Fence markers that came out *inside* a code block, rendered by mistune.

    A fence marker can only survive into code-block content if the parser did
    not read it as a fence -- which is what a closing fence with trailing text
    is. The prose after it is inside `<pre><code>` with it, and the marker is
    the part that names the cause.

    Measured against `CLAUDE.md` either side of 1b12efa: one hit before, none
    after. Across the 81 markdown files at `origin/main`: none. A file that
    quotes a fence inside an indented code block on purpose would land here too;
    none does, and the message says which line to look at.
    """
    import mistune

    found = []
    for block in _RENDERED_CODE.finditer(mistune.create_markdown()(source)):
        for line in block.group(1).splitlines():
            if _FENCE_IN_CONTENT.match(line):
                found.append(_html.unescape(line))
    return found


def _locate(source: str, rendered_line: str) -> str:
    """Best-effort source line number for a line mistune handed back.

    mistune strips the opening fence's indentation, so the text will not match
    a source line exactly. Compared stripped; the number is a pointer for the
    reader and the message stands without it.
    """
    wanted = rendered_line.strip()
    for n, line in enumerate(source.splitlines(), 1):
        if line.strip() == wanted:
            return f"line {n}"
    return "line not located"


def fence_lines_seen(source: str) -> int:
    """How many lines the fence patterns recognise at all.

    The negative control below leans on this: both sweeps judge fences, and a
    pattern that stopped matching would make both of them pass over nothing.
    """
    return sum(1 for line in source.splitlines() if _FENCE_OPEN.match(line))


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
    assert (REPO / "CLAUDE.md") in files, (
        "the sweep does not include CLAUDE.md, which is where the 1b12efa "
        "defect was"
    )
    fenced = [p for p in files if fence_lines_seen(p.read_text(errors="replace"))]
    assert len(fenced) >= 40, (
        f"only {len(fenced)} of {len(files)} markdown files have a fence the "
        "patterns can see; they stopped matching and the sweeps below are "
        "judging almost nothing"
    )


def test_the_detectors_fire_on_the_defect_they_were_written_for():
    """Positive control, and the one this file most needed.

    Both guards below ran green over the 1b12efa defect for as long as it was in
    the tree. A guard that cannot be shown to fail is not evidence that what it
    names is absent, so each detector is run here against the defect it was
    widened to catch, and then against the repaired text -- a detector that
    fires on both is not a detector.

    The samples are inputs. What the documents say is read off disk by the tests
    that judge them.
    """
    defects = fence_defects(DEFECT_SAMPLE)
    assert defects, (
        "fence_defects() sees nothing wrong with a closing fence that has "
        "prose on its line; this is the 1b12efa defect and the widening exists "
        "for it"
    )
    assert any(n == 5 and "Its own job name" in line for n, line, _ in defects), (
        f"fence_defects() does not name the offending line 5: {defects}"
    )

    swallowed = fences_rendered_as_content(DEFECT_SAMPLE)
    assert swallowed, (
        "fences_rendered_as_content() does not see the swallowed prose; with "
        "no hit here the rendering sweep cannot fail and is not evidence"
    )
    assert any("Its own job name" in line for line in swallowed), (
        f"the hit does not carry the offending line: {swallowed}"
    )

    unclosed = fence_defects(UNCLOSED_SAMPLE)
    assert any(reason == "opened and never closed" for _, _, reason in unclosed), (
        "fence_defects() does not see a fence opened at column 0 and never "
        f"closed -- the defect this file was originally written for: {unclosed}"
    )
    assert not fences_rendered_as_content(UNCLOSED_SAMPLE), (
        "the rendering sweep is expected to be blind to a plain unclosed "
        "fence, since nothing inside the block looks like a fence; if it now "
        "sees it, the walk and the sweep no longer cover different failures "
        "and this docstring is wrong"
    )

    assert REPAIRED_SAMPLE != DEFECT_SAMPLE, (
        "the two samples are identical, so the assertions below judge the "
        "broken text again"
    )
    assert not fence_defects(REPAIRED_SAMPLE), (
        "fence_defects() reports the repaired sample as broken; it would fire "
        f"on correct documents: {fence_defects(REPAIRED_SAMPLE)}"
    )
    assert not fences_rendered_as_content(REPAIRED_SAMPLE), (
        "fences_rendered_as_content() reports the repaired sample as broken; "
        "it would fire on correct documents"
    )


def test_every_fenced_block_is_closed():
    """An unclosed fence swallows the rest of the document.

    Over every markdown file, not only the README. Scoping this to the file that
    prompted it would have missed `docs/RUNBOOK.md`, which carried an orphan
    closing fence and was found by widening the same check to the other 78 files
    -- the sampling mistake this project keeps making.

    The judgement is `fence_defects`, which replaces a count of lines matching
    ```` ^``` ````. That count anchored at column 0, so it was blind to the two
    fences indented inside a list item in `CLAUDE.md`, and it compared parity,
    which those two lines would have satisfied even had it seen them.
    """
    files = markdown_files()
    visited = 0
    broken = []
    for path in files:
        visited += 1
        for n, line, reason in fence_defects(path.read_text(errors="replace")):
            broken.append((path.relative_to(REPO), n, line, reason))
    assert visited == len(files) >= 50, (
        f"the fence walk visited {visited} of {len(files)} markdown files; it "
        "is meant to cover every one, and a sweep narrowed to the file where a "
        "problem was noticed is the mistake this replaced"
    )
    assert not broken, (
        "these markdown files have a code fence that does not delimit a code "
        "block, so prose after it renders as code:\n  "
        + "\n  ".join(
            f"{p}:{n}: {reason}\n      {line!r}" for p, n, line, reason in broken
        )
    )


def test_the_document_actually_renders_as_a_document():
    """The property the fence walk is a proxy for, checked by a real parser.

    Over every markdown file the walk sweeps, because the defect that prompted
    this was in `CLAUDE.md` and this test read `README.md` only.

    What it asserts is that no fence marker survives inside a rendered code
    block. That is the thing the 1b12efa defect moves: its closing fence was
    read as content and took 24 lines of prose into `<pre><code>` with it.
    Heading counts do not move -- 13 h2 and 20 h3 either side of the fix, both
    measured -- so a heading proxy returns green on it, and did.

    A parser also resolves what a line-based walk cannot. `DEPLOYMENT_STATUS.md`
    fences four and five spaces in, which are fences only relative to their list
    item, and `docs/archive/RUNBOOK-2026-05-16.md` carries a stray fence inside
    a list item that the walk mis-pairs -- 142 with 178, where mistune closes
    142 at the end of the list and pairs 178 with 175. Measured: nothing is
    swallowed there and neither check reports it, which is the point. A walk
    can be wrong about a file without being loud about it.
    """
    files = markdown_files()
    visited = 0
    broken = []
    for path in files:
        visited += 1
        source = path.read_text(errors="replace")
        for line in fences_rendered_as_content(source):
            broken.append((path.relative_to(REPO), _locate(source, line), line))
    assert visited == len(files) >= 50, (
        f"the rendering sweep visited {visited} of {len(files)} markdown files; "
        "rendering README.md alone is precisely the blindness that let the "
        "1b12efa defect through, and narrowing it back is not a safe edit"
    )
    assert not broken, (
        "these markdown files render a code fence as code-block content, which "
        "means the fence did not close and the prose after it was swallowed:\n  "
        + "\n  ".join(f"{p}: {where}: {line!r}" for p, where, line in broken)
    )


def test_the_readme_headings_all_render():
    """Every `##` in README.md must come out as a heading.

    The original defect here: one unclosed ```` ```text ```` fence left 2 of 6
    `##` rendering as headings. Kept as its own test, scoped to the README as it
    always was, and worth knowing what it cannot do -- it is a count, so it is
    blind to any defect that swallows prose without swallowing a heading, which
    is exactly the 1b12efa defect. That one is caught above.
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
