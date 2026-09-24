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

**The rule existed and reached one file out of 98.** `DOC = REPO / "CLAUDE.md"`
scoped every test here to the document that prompted them, so the decision that
line numbers are forbidden was enforced nowhere else. Measured on `origin/main`:
78 such pointers survive in 21 of the 98 tracked markdown files, and the live
defect this missed is `docs/ENVIRONMENTAL_BASELINE_AUDIT.md`, which instructs a
reader to "Update `MIN_AUC_THRESHOLD` in `scheduler.py:382` if needed" --
`MIN_AUC_THRESHOLD` appears in `app/scheduler.py` zero times, line 382 is
`lock.release()`, and the constant lives in `app/promotion.py`. The same pointer
is copied into `IMPROVEMENTS_2026-07-17.md`.

The count was cross-checked two independent ways before any of this was written,
because the obvious way to strip code blocks is the fence-parity walk that
`tests/test_readme_renders_and_its_claims_hold.py` had just been shown to be
unreliable. A state walk over fences and a count taken from mistune's rendered
`<pre><code>` blocks both answer 78 over the same 98 files, and disagree on no
file.

**Not all 78 are defects, and that is the whole difficulty.** CLAUDE.md says of
the dated reports under `.claude/` that they "describe the world as it was and
are left alone". A pointer in a record of a moment is a fact about that moment;
a pointer in a document someone acts on today misdirects them. `DATED` below
draws that line, one entry per document, with the marker in the document that
puts it there -- and `test_an_exemption_keeps_the_marker_that_earned_it` fails
if a document loses the marker its exemption rests on.

`docs/ENVIRONMENTAL_BASELINE_AUDIT.md` is the document that prompted the
widening and is **exempt**, by the owner's decision and against the first
instinct. Its own first heading is `## 1. What Works in Production (file:line)`:
the pointers are its form, and rewriting them would rewrite the record. Its 22
pointers stay, and it is where the positive control below finds real content to
prove the detector on.

**Twenty-four pointers came into scope and sixteen of them were already wrong**,
measured by asking whether the cited line falls inside the construct the sentence
names -- resolved by AST, after a first pass using "the symbol within two lines"
condemned two pointers that were right: `app/api/evaluate.py:151` lands inside
`evaluate_project`, which is the handler its sentence names. Sixteen wrong out of
twenty-four means most repairs were not a moved number but "the thing moved, say
so", and that carries its own trap: turning a past-tense pointer into a symbol
name silently restates it as a claim about today. `app/auth.py:72-75` is the
clearest case -- as raised on 2026-07-24 it was the password path, and today the
`hashlib.sha256` on that line signs JWTs while passwords go through
`_hash_password`. Naming a symbol there would be false about both dates. So the
number simply went, and the prose stands as written, the row's own
`(as raised 2026-07-24)` carrying the tense.

**The replacements are checked, which is the other half of the repair.**
`test_every_named_symbol_exists_in_the_named_file` reads every document in scope
rather than `CLAUDE.md` alone, and the notation now covers classes as well as
functions. Without that, sixteen visibly stale numbers would have become
reference names nothing verifies -- a pointer that has stopped announcing its own
staleness rather than one that has been repaired.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "CLAUDE.md"

#: `` `app/main.py` → `make_features()` `` -- the shape this file replaced the
#: line numbers with. The arrow is the marker: a bare mention of a module beside
#: a bare mention of a function is not a claim that one contains the other.
#:
#: The trailing `()` is optional, so a class is a reference this can check too.
#: Without that, replacing `app/database.py:389` with
#: `` `app/database.py` → `EnvironmentalObservation` `` would swap a pointer that
#: was measurably wrong for one nothing verifies -- which is the same defect in a
#: different costume.
_SYMBOL_REF = re.compile(r"`([\w/]+\.py)`\s*→\s*`(\w+)(?:\(\))?`")

#: Any surviving `file.py:123` pointer, which is the thing being removed.
_LINE_REF = re.compile(r"`?([\w/]+\.py):(\d+)(?:-\d+)?`?")

#: The same pointer written in words, which the shape above cannot see. A guard
#: you can walk around by writing the thing out is not a guard, and the walk is
#: not adversarial -- `line 118` is how anyone writes it naturally.
#:
#: Enumerated from the corpus rather than guessed. Counted on 2026-09-20 over
#: the 98 markdown files then tracked, outside code blocks: 30 English, 1
#: Russian, 1 bare continuation, 0 GitHub-style. A dated figure, not a current
#: one: the corpus was 99 files two days later. Four shapes were looked for and found absent --
#: `at/near line N`, `см. строку N`, `N-я строка`, `row N` -- and two more were
#: looked for and rejected below.
_WORDED_LINE_REF = re.compile(r"\blines?\s+\d+(?:\s*[-–—]\s*\d+)?", re.I)

#: The Russian form. One instance, in a report that names itself dated.
_WORDED_LINE_REF_RU = re.compile(r"\bстрок[аеиуо]\w*\s+\d+", re.I)

#: `` `:335` `` -- a line number whose filename is carried over from an earlier
#: reference in the same sentence. `_LINE_REF` requires the `file.py:` prefix, so
#: the second of two pointers escapes it entirely. One instance, and it was
#: removed -- by the change that widened this rule to every document -- by
#: rewriting the row around it, not by seeing it.
#:
#: The lookbehind keeps `localhost:8000` and every other `host:port` out.
#: Measured over the corpus: one match, and it is the pointer.
_BARE_LINE_REF = re.compile(r"(?<![\w.:/])`?:\d{1,5}`?(?![\w.])")

#: `L118`, `#L118` -- GitHub's own notation. **Zero instances in this corpus**,
#: so unlike the three above it is pinned only by the sample in
#: `test_each_form_is_caught`, never by real content. Included because it costs
#: nothing and is the obvious way to write one next.
_GITHUB_LINE_REF = re.compile(r"#?\bL\d{2,5}\b")

#: Named, so a failure says which shape it matched rather than only where.
LINE_POINTER_FORMS = {
    "file.py:N": _LINE_REF,
    "line N": _WORDED_LINE_REF,
    "строка N": _WORDED_LINE_REF_RU,
    ":N continuation": _BARE_LINE_REF,
    "LN": _GITHUB_LINE_REF,
}

#: **A count is not a pointer, and word order is what separates them.**
#: `38 lines modified` is a measurement of a change; `lines 462-523` is a
#: position in a file. Measured over the corpus: 40 counts, 30 pointers, and
#: **not one line contains both**, so requiring the word before the number
#: separates them without a judgement call. This pattern is the one thing that
#: must never match, and `test_a_count_of_lines_is_not_a_pointer` is why.
_LINE_COUNT = re.compile(r"\b\d+\+?\s+lines?\b", re.I)

#: **`§7`, `§10.3` -- a section, and deliberately out of scope.** 102 instances,
#: and CLAUDE.md cites `docs/M2_EVALUATION_PROTOCOL.md` §10.3 and "M3 §7" as the
#: correct way to point at a place in a document. A section is a named division
#: that survives an edit above it; a line number is not. Forbidding it would
#: forbid the notation this repository chose, so it is recorded here as
#: considered and excluded rather than left to look like an oversight.
_SECTION_REF = re.compile(r"§\s?\d+(?:\.\d+)*")


#: An opening fence: up to three spaces of indentation, three or more backticks
#: or tildes, then an info string a backtick fence may not put a backtick in.
_FENCE_OPEN = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})(?P<info>.*)$")

#: A closing fence: whitespace only after the marker. The distinction matters --
#: ```` ``` `` followed by prose closes nothing, which is the defect 1b12efa fixed
#: in CLAUDE.md and which a parity walk cannot see.
_FENCE_CLOSE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})[ \t]*$")


def prose_lines(text: str) -> list[tuple[int, str]]:
    """The document's prose, as (line number, line), fenced blocks removed.

    A pasted traceback or a sample of program output legitimately contains
    `app/main.py:521`, and it is not a pointer the reader is meant to follow.
    Judging it as one makes the guard below fail on correct edits -- checked by
    appending a traceback to CLAUDE.md and watching it go red, which is how this
    function came to exist.

    A state walk, not a parity count, and three spaces of indentation are
    allowed. The version this replaces toggled on `line.startswith("```")`:
    blind to a fence indented inside a list item, and unable to tell a closing
    fence from one followed by prose. That is the method
    `tests/test_readme_renders_and_its_claims_hold.py` was widened away from,
    for a defect that had been green in CI, so it is not the method to count
    with here.

    Line numbers are kept because a pointer's value to whoever has to fix it is
    knowing where it is.
    """
    out: list[tuple[int, str]] = []
    opened_with = ""
    inside = False
    for n, line in enumerate(text.splitlines(), 1):
        if not inside:
            m = _FENCE_OPEN.match(line)
            if m is not None and not (
                m.group("marker")[0] == "`" and "`" in m.group("info")
            ):
                inside, opened_with = True, m.group("marker")
                continue
            out.append((n, line))
            continue
        m = _FENCE_CLOSE.match(line)
        if m is not None:
            marker = m.group("marker")
            if marker[0] == opened_with[0] and len(marker) >= len(opened_with):
                inside, opened_with = False, ""
    return out


def prose_only(text: str) -> str:
    """`prose_lines` as one string, for the callers that do not need numbers."""
    return "\n".join(line for _, line in prose_lines(text))


#: Documents exempt from the line-number rule, and the marker in each that
#: earns the exemption. A document that records a moment states one; a pointer
#: in it is a fact about that moment, and CLAUDE.md says of the reports under
#: `.claude/` that they "describe the world as it was and are left alone".
#:
#: A denylist rather than an allowlist, so a markdown file added tomorrow is
#: covered by default. That is the safe direction: a new document escaping the
#: rule in silence is the failure this whole file exists to stop, and one that
#: has to be exempted deliberately is not.
#:
#: `test_an_exemption_keeps_the_marker_that_earned_it` reads each marker back out
#: of its document, so an exemption cannot outlive the reason recorded for it.
DATED: dict[str, str] = {
    ".claude/DEPLOYMENT_FINAL_REPORT.md": "**Date:** 2026-07-12/13",
    ".claude/GRAFANA_FORECAST_MONITORING.md": "# Grafana Forecast Monitoring Setup",
    ".claude/PROPHET_INTEGRATION_SUMMARY.md": "**Completed:** 2026-07-13",
    ".claude/SESSION_2_SUMMARY.md": "# Session 2 Summary - 2026-07-12/13",
    # Added when the rule grew its worded form. These three carry no
    # `file.py:NNN` at all, so the list -- built from where the old shape
    # happened to land -- had never seen them. An exemption list assembled from
    # one rule's footprint is not a description of which documents record a
    # moment, and widening the rule is what showed the difference.
    ".claude/SESSION_SUMMARY.md": "# Session Summary - 2026-07-12",
    ".claude/TASK2_COMPLETION_REPORT.md": "**Date:** 2026-07-12",
    "docs/ENGINEERING_REPORT_2026-09-06_07.md": "**SCOPED — not the project plan.**",
    "API_KEYS_USAGE_AUDIT.md": "**Date:** 2026-07-10",
    "IMPROVEMENTS_2026-07-17.md": "**Date:** 2026-07-17",
    "SECURITY_AUDIT_RESULTS.md": "**Audit Date:** 2026-07-10",
    "SECURITY_FIXES_SUMMARY.md": "**Date:** 2026-07-10",
    "docs/DATASET_AUDIT_2026-09-03.md": "# Dataset audit — 2026-09-03",
    "docs/ENVIRONMENTAL_BASELINE_AUDIT.md": "**Date:** 2026-07-17",
    "docs/incidents/2026-05-08-content-length-head.md": "**Date:** 2026-05-08",
    "docs/maximum/M0_BASELINE_AUDIT.md": "**Audit Date:** 2026-07-24",
    "docs/maximum/evidence/M0_REGION_ESG_SCHEMA.md": "**Collection date:** 2026-07-24",
    "sora_earth_audit.md": "**Дата:** 7 мая 2026",
}

#: The four `.claude/` entries above rest on the rule in CLAUDE.md rather than on
#: a date of their own; `GRAFANA_FORECAST_MONITORING.md` states no date at all
#: and is exempt only because of where it lives. Recorded separately because the
#: two justifications are not the same strength, and the weaker one is the one a
#: reviewer should look at first.
DATED_BY_LOCATION = tuple(f for f in DATED if f.startswith(".claude/"))

#: `docs/ENVIRONMENTAL_BASELINE_AUDIT.md` is the entry that does not sit still.
#: It states a date of record, which is what puts it above -- and its section 1
#: is titled "What Works in Production (file:line)" and line 210 tells a reader
#: to "Update `MIN_AUC_THRESHOLD` in `scheduler.py:382` if needed", which is an
#: instruction to act today. The date and the imperative disagree about what the
#: document is, and this constant is where a decision to move it goes.
#:
#: While it is exempt it serves as the corpus for the positive control below:
#: the detector is shown to print non-zero over real repository content rather
#: than only over a sample written to make it do so.
CONTESTED = "docs/ENVIRONMENTAL_BASELINE_AUDIT.md"


def markdown_files() -> list[Path]:
    """Every tracked markdown file, `.claude/` included.

    Tracked, from git, rather than globbed: an untracked scratch file in the
    working tree is not part of the repository's documentation and would make
    the sweep's reach depend on whose checkout it ran in.
    """
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    return sorted(REPO / name for name in out.split("\0") if name)


def sweep_for_line_pointers() -> tuple[int, list[str]]:
    """`(files visited, offenders)` over everything the rule applies to.

    The sweep is a function so its reach can be asserted from two places: the
    guard, which judges the offenders, and the positive control, which judges
    the reach. Deleting the coverage assertion inside the guard then no longer
    hides a narrowed sweep -- measured, because it did: with the sweep inline,
    removing that one assertion and pointing the loop at `CLAUDE.md` turned the
    whole file green, which is precisely the state `origin/main` was in.
    """
    visited, offenders = 0, []
    for path in files_in_scope():
        visited += 1
        for n, pointer, form in line_pointers(path):
            offenders.append(f"{path.relative_to(REPO)}:{n}: {pointer}  [{form}]")
    return visited, offenders


def files_in_scope() -> list[Path]:
    """Every tracked markdown file the line-number rule applies to.

    One definition, so the sweep and its positive control both stand on it. The
    rule was enforced on `CLAUDE.md` alone for months; narrowing it back has to
    break something in more than one place, or the narrowing is a one-line edit
    that looks like a passing test.
    """
    return [
        p
        for p in markdown_files()
        if p.relative_to(REPO).as_posix() not in DATED
    ]


def line_pointers(path: Path) -> list[tuple[int, str, str]]:
    """`(line number, pointer, form)` for every line pointer in this file's prose.

    Every shape in `LINE_POINTER_FORMS`, not only `file.py:NNN`. The rule was
    decided against a pointer that cannot be kept true, and the reason has
    nothing to do with punctuation: `line 118` drifts exactly as `auth.py:118`
    does, and `docs/maximum/M0_GAP_REGISTER.md` carried one of each -- the second
    inside a correction recording that the first had rotted.

    Each pointer is reported once because the forms are disjoint -- no two can
    match the same characters -- and `test_the_forms_do_not_overlap` holds them
    to that. An earlier version deduplicated overlapping matches here instead;
    measured, removing that code changed nothing, because nothing ever
    overlapped. A defence that no input can reach is a claim no test can check,
    so it went, and the property it stood for is now asserted where it can fail.
    """
    found = []
    for n, line in prose_lines(path.read_text(errors="replace")):
        for form, rx in LINE_POINTER_FORMS.items():
            for m in rx.finditer(line):
                found.append((n, m.group(0).strip("`"), form))
    return found


def unjustified_exemptions(dated: dict[str, str]) -> list[str]:
    """Exemptions whose recorded reason no longer holds.

    Separated from its test so `test_the_exemption_check_can_fail` can hand it a
    broken entry. Without that, the check compares markers that all resolve and
    returns an empty list either way -- measured, by replacing the comparison
    with `if False` and watching nothing go red.

    The `.claude/` entries are checked for existence only: they rest on the rule
    in CLAUDE.md about where they live rather than on a marker of their own.
    """
    broken = []
    for name, marker in sorted(dated.items()):
        path = REPO / name
        if not path.exists():
            broken.append(f"{name}: exempt but no longer in the repository")
            continue
        if name in DATED_BY_LOCATION:
            continue
        head = "\n".join(path.read_text(errors="replace").splitlines()[:20])
        if marker not in head:
            broken.append(
                f"{name}: exempt because of {marker!r}, which is no longer in "
                "its first 20 lines"
            )
    return broken


def symbol_refs(path: Path = None) -> list[tuple[str, str]]:
    """`(module, symbol)` for every arrow reference in one document's prose."""
    return _SYMBOL_REF.findall(prose_only((path or DOC).read_text(errors="replace")))


def defined_in(module: str) -> set[str] | None:
    """Every function and class `module` defines, or `None` if it is not here.

    `None` is the third outcome, and it exists because the other two cannot
    express it. A reference into a dependency -- `blocking.py:30`, which is
    apscheduler's own module and is in neither git nor this tree -- is not
    correct and is not wrong: it is unverifiable, and a guard that folds it into
    "fine" has a hole exactly where it sees nothing, which looks like a clean
    run. Resolved by AST rather than by grepping for `def <name>`, so a name that
    appears only in a comment or a string does not count as defined.
    """
    path = REPO / module
    if not path.exists():
        return None
    try:
        tree = ast.parse(path.read_text(errors="replace"))
    except SyntaxError:
        return None
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def symbol_reference_problems(
    refs: list[tuple[str, str, str]]
) -> tuple[list[str], list[str]]:
    """`(wrong, unverifiable)` over `(where, module, symbol)` triples.

    Two buckets rather than one, so "the symbol is not there" and "this file is
    not ours to check" read differently in the failure. Both fail the test: the
    second is a claim the repository cannot stand behind, which is worth knowing
    and is not worth silence.
    """
    wrong, unverifiable = [], []
    for where, module, symbol in refs:
        defined = defined_in(module)
        if defined is None:
            unverifiable.append(f"{where}: `{module}` → `{symbol}` -- {module} is "
                                "not a file in this repository")
        elif symbol not in defined:
            wrong.append(f"{where}: `{module}` → `{symbol}` -- not defined there")
    return wrong, unverifiable


def symbol_refs_in_scope() -> list[tuple[str, str, str]]:
    """Every arrow reference in every document the line-number rule covers.

    The replacement notation has to be checked wherever it is written, not only
    in `CLAUDE.md`. Sixteen of the twenty-four line pointers this rule newly
    covers were measurably wrong, so most of them become a symbol name rather
    than a moved number -- and an unchecked symbol name is a pointer that has
    stopped announcing its own staleness.
    """
    refs = []
    for path in files_in_scope():
        for module, symbol in symbol_refs(path):
            refs.append((str(path.relative_to(REPO)), module, symbol))
    return refs


def test_the_parser_finds_the_references_it_judges():
    """Negative control. The assertion below passes over an empty list."""
    refs = symbol_refs()
    assert len(refs) >= 2, (
        f"only {len(refs)} `file.py` → `symbol()` references parsed out of "
        "CLAUDE.md; the notation changed and this file judges nothing"
    )
    in_scope = symbol_refs_in_scope()
    assert len(in_scope) >= len(refs), (
        f"{len(in_scope)} arrow references found across the documents in scope "
        f"but {len(refs)} in CLAUDE.md alone; the sweep is reading less than the "
        "single file it replaced"
    )
    documents = {where for where, _, _ in in_scope}
    assert len(documents) >= 2, (
        f"every arrow reference the sweep found is in {documents}. The notation "
        "is what the line numbers were replaced with, in six documents, so a "
        "sweep that sees one file has been narrowed back to where this rule "
        "started -- and >= would not have noticed, since one file is still not "
        "fewer than one file"
    )
    assert _SYMBOL_REF.findall("see `app/database.py` → `RegionESGScore` here") == [
        ("app/database.py", "RegionESGScore")
    ], (
        "the notation no longer parses a class, whose name carries no `()`. "
        "Three of the replacements written for this rule are classes, and a "
        "reference the pattern cannot see is one nothing checks"
    )
    assert symbol_reference_problems(
        [("a fabricated reference", "app/main.py", "there_is_no_such_function")]
    ) == (
        ["a fabricated reference: `app/main.py` → `there_is_no_such_function` "
         "-- not defined there"],
        [],
    ), "symbol_reference_problems() does not report a symbol that is not there"
    assert symbol_reference_problems(
        [("a fabricated reference", "blocking.py", "BlockingScheduler")]
    )[1], (
        "symbol_reference_problems() reports nothing for a file outside the "
        "repository, so the third outcome does not exist in practice and a "
        "reference into a dependency passes as though it had been checked"
    )


def test_every_named_symbol_exists_in_the_named_file():
    """The defect: a pointer that lands somewhere unrelated.

    Over every document the line-number rule covers, not `CLAUDE.md` alone. This
    is the other half of widening that rule: sixteen of the twenty-four pointers
    it newly reaches were measurably wrong, so the repair is mostly a symbol name
    in place of a number -- and a symbol name nothing checks is a pointer that
    has merely stopped announcing when it goes stale. Replacing a number that was
    visibly wrong with a name that is quietly wrong is not progress.

    Three outcomes, not two. The symbol is there; the symbol is not there; or the
    file is not this repository's to check, which `defined_in` answers with
    `None` and which fails in its own bucket. `blocking.py:30` in
    `docs/THESIS_NOTES.md` was the case that forced it: apscheduler's own module,
    in neither git nor this tree, and a guard that reads "cannot resolve" as
    "nothing to report" is clean precisely where it is blind.

    **Both assertions below are vacuous on current content, and that is worth
    saying rather than dressing up.** There are no wrong references and no
    unresolvable ones -- the one into a dependency now names `BlockingScheduler`
    -- so neutering either assertion changes nothing that runs here. Measured, by
    replacing each with `assert True or ...` and watching the file stay green. An
    assertion over an empty set cannot be defended by a second assertion over the
    same empty set; that was tried and it did not work. What can be shown is that
    the detector produces each bucket on demand, which
    `test_the_parser_finds_the_references_it_judges` does with a fabricated
    reference of each kind. That is the evidence these two assertions rest on.
    """
    wrong, unverifiable = symbol_reference_problems(symbol_refs_in_scope())

    assert not wrong, (
        "these documents point at symbols that do not exist:\n  "
        + "\n  ".join(wrong)
    )
    assert not unverifiable, (
        "these references name a file this repository does not contain, so "
        "nothing here can say whether they are true. That is not the same as "
        "their being fine. Name a symbol of the dependency instead of a path "
        "into it -- `apscheduler.schedulers.blocking` → `BlockingScheduler` "
        "rather than `blocking.py:30`:\n  "
        + "\n  ".join(unverifiable)
    )


def test_the_sweep_can_print_a_non_zero(): 
    """Positive control. Two of them, because one is not enough here.

    "0 violations" is worth nothing until the detector has been seen to print
    something else, and this rule had reached one file out of 98 while reporting
    clean for months.

    The first control is a sample carrying the real sentence from
    `docs/ENVIRONMENTAL_BASELINE_AUDIT.md`. It survives that document being
    fixed, which a control reading the live line would not.

    The second reads real repository content: the exempt documents keep their
    pointers by design, so the detector must find some there. That makes the
    exemption list double as the corpus the detector is proven against -- if the
    list is ever emptied, this control says so instead of quietly passing.
    """
    sample = (
        "## 6. Recommendations\n"
        "\n"
        "- Update `MIN_AUC_THRESHOLD` in `scheduler.py:382` if needed\n"
        "\n"
        "```\n"
        "  File \"app/main.py\", line 521, in make_features\n"
        "```\n"
    )
    found = [m.group(0).strip("`") for m in _LINE_REF.finditer(prose_only(sample))]
    assert found == ["scheduler.py:382"], (
        f"the detector does not read the real defect as one pointer: {found}. "
        "It must find scheduler.py:382 in the prose and must not count the "
        "traceback line, which is sample output"
    )

    from collections import Counter

    corpus, by_form = {}, Counter()
    for name in DATED:
        path = REPO / name
        if path.exists():
            hits = line_pointers(path)
            if hits:
                corpus[name] = len(hits)
                by_form.update(form for _, _, form in hits)

    #: Which shapes the detector is proven on by real repository content, and
    #: which only by the samples in `test_each_form_is_caught`. Asserted as an
    #: exact set, so it cannot quietly become untrue in either direction: a form
    #: losing its last real instance says so, and a form gaining its first says
    #: so too -- at which point this list and the docstring above it move
    #: together. Measured: file.py:N 54, line N 29, строка N 1.
    backed = {form for form, n in by_form.items() if n}
    assert backed == {"file.py:N", "line N", "строка N"}, (
        f"the forms backed by real content are {sorted(backed)}, not the three "
        "recorded here. `:N continuation` and `LN` have no instance in this "
        "corpus and are pinned only by samples; if that changed, say so in the "
        "FORMS comments as well as here"
    )

    assert corpus, (
        "no pointer was found in any exempt document, so the sweep below has "
        "never been observed to print non-zero over real content and its green "
        "is not evidence. Exempt documents keep their pointers by design; if "
        f"that stopped being true for all {len(DATED)} of them, this control is "
        "the thing to reconsider, not to delete"
    )
    assert REPO / CONTESTED in markdown_files(), (
        f"{CONTESTED} is not in the sweep's file list at all, so neither the "
        "control nor the guard is judging the document that prompted this"
    )

    scope, all_md = files_in_scope(), markdown_files()
    assert len(all_md) >= 90, (
        f"git lists {len(all_md)} tracked markdown files; there were 98 when "
        "this was widened, and a number this low means the listing broke rather "
        "than that the documentation shrank"
    )
    reached, _ = sweep_for_line_pointers()
    assert reached == len(scope), (
        f"the sweep reaches {reached} files where the rule applies to "
        f"{len(scope)}. Asserted here as well as inside the guard, because with "
        "the loop written inline a single deleted assertion let a sweep narrowed "
        "to one document report clean -- measured"
    )
    assert len(scope) == len(all_md) - len(DATED) >= 60, (
        f"the rule applies to {len(scope)} of {len(all_md)} tracked markdown "
        f"files with {len(DATED)} exempt. Enforcing it on one file while "
        "reporting clean is the defect being repaired, so the reach is asserted "
        "here as well as inside the sweep -- narrowing it has to break two "
        "tests, not one"
    )


def test_each_form_is_caught():
    """One sample per shape, and the shape it is reported as.

    The forms were enumerated from the corpus, but two of the five have no
    instance in it -- `:N continuation` had exactly one, removed when this rule
    first reached every document by rewriting the row around it rather than by
    seeing it; `LN` has never appeared. Those two are pinned
    only here, which `test_the_sweep_can_print_a_non_zero` states as a fact about
    the corpus rather than leaving to be discovered.
    """
    cases = {
        "file.py:N": "see `app/auth.py:397` for the set",
        "line N": "the original citation of line 118 no longer resolves",
        "строка N": "Строка 105 под комментарием предлагала `up -d`",
        ":N continuation": "`scheduler_jobs.py:215` (openaq) and `:335` (openmeteo)",
        "LN": "see L118 of the module",
    }
    for expected, text in cases.items():
        got = [
            (m.group(0).strip("`"), form)
            for form, rx in LINE_POINTER_FORMS.items()
            for m in rx.finditer(text)
        ]
        forms = {form for _, form in got}
        assert expected in forms, (
            f"the {expected!r} form is not caught in {text!r}; matched {got}. A "
            "shape the detector cannot see is a way around the rule, and this "
            "one was written down as covered"
        )


def overlapping_matches(forms: dict, lines: list[str]) -> list[str]:
    """Places where two different forms match overlapping characters.

    Separated from its test for the same reason as `unjustified_exemptions`: the
    real corpus gives an empty answer, and an empty answer from a function that
    has never been seen to give any other is not a measurement.
    """
    overlaps = []
    for text in lines:
        spans = [
            (form, m.start(), m.end(), m.group(0))
            for form, rx in forms.items()
            for m in rx.finditer(text)
        ]
        for i, (fa, sa, ea, ta) in enumerate(spans):
            for fb, sb, eb, tb in spans[i + 1:]:
                if fa != fb and sa < eb and sb < ea:
                    overlaps.append(f"{fa} {ta!r} and {fb} {tb!r} in {text[:70]!r}")
    return overlaps


def test_the_forms_do_not_overlap():
    """No two forms may match the same characters, or one pointer counts twice.

    Not hypothetical: the count that commissioned this widening scored one
    pointer twice. Its `at line N` and `line N` patterns both matched
    `at\nline 313` in `docs/DATASET_AUDIT_2026-09-03.md` -- the text was joined
    into one string, so `\\s+` crossed the line break -- and that file read 5
    where it holds 4. The total still came out right, because the same count
    lacked the bare `:N` form and missed one elsewhere: two errors in opposite
    directions, cancelling. Agreement between counts that are each wrong in
    composition is the agreement not to trust.

    Checked over every prose line of every tracked markdown file, and over the
    per-form samples, so a form added later that overlaps an existing one fails
    here and has to be made disjoint on purpose.
    """
    samples = [
        "see `app/auth.py:397` and `:335`, lines 462-523, Строка 105, L118",
        "`scheduler_jobs.py:215` (openaq) and `:335` (openmeteo)",
    ]
    lines = list(samples)
    for path in markdown_files():
        lines.extend(line for _, line in prose_lines(path.read_text(errors="replace")))

    overlaps = overlapping_matches(LINE_POINTER_FORMS, lines)

    # The assertion below is over an empty list on current content -- nothing
    # overlaps -- so on its own it would be green whether or not the check
    # works. What makes it evidence is that the same function, handed two forms
    # that do overlap, reports them. Asserted here rather than left to a
    # mutation run, which CI does not do.
    rigged = {
        "line N": _WORDED_LINE_REF,
        "a second line N": re.compile(r"\blines?\s+\d+", re.I),
    }
    assert overlapping_matches(rigged, ["the citation of line 118"]), (
        "overlapping_matches() reports nothing for two forms that match the same "
        "text, so the empty result below is not evidence of disjoint forms"
    )

    assert not overlaps, (
        "two forms match the same characters, so one pointer is reported twice "
        "and every count built on this detector is inflated:\n  "
        + "\n  ".join(overlaps[:10])
    )
    assert len(lines) > 1000, (
        f"only {len(lines)} lines were checked for overlap; the corpus did not "
        "load and this passed over nothing"
    )


def test_a_count_of_lines_is_not_a_pointer():
    """The discriminating case, and the criterion is word order.

    `38 lines modified` measures a change; `lines 462-523` is a position in a
    file. Both are "lines" beside a number, and the rule must catch exactly one
    of them or it is useless in one direction or unusable in the other.

    Word order separates them, and that is measured rather than asserted: on
    2026-09-20, over the 98 markdown files then tracked, outside code blocks,
    there were 40 counts and 30 pointers, and **not one line contained both**. So requiring the word before
    the number needs no judgement about intent.

    The three counts below are the shapes that actually occur -- "+452 lines",
    "354 lines", "1 file, 38 lines modified".
    """
    counts = [
        "**Total:** 5 files, +452 lines",
        "`web/src/features/forecast/ForecastComparePage.tsx`** (NEW, 354 lines)",
        "**Total:** 1 file, 38 lines modified",
        "app/scheduler.py is 1342 lines",
    ]
    for text in counts:
        hits = [m.group(0) for m in _WORDED_LINE_REF.finditer(text)]
        assert not hits, (
            f"a count of lines was read as a pointer in {text!r}: {hits}. The "
            "rule would then fire on every changelog that measures a diff"
        )
        assert _LINE_COUNT.search(text), (
            f"the count pattern does not recognise {text!r}, so this case is not "
            "testing the distinction it names"
        )

    pointers = ["(lines 462-523)", "Line 574-580 (APScheduler job)", "(line 42)"]
    for text in pointers:
        assert _WORDED_LINE_REF.search(text), (
            f"a pointer was not caught in {text!r}, so the word-order rule is "
            "excluding what it is meant to include"
        )
        assert not _LINE_COUNT.search(text), (
            f"{text!r} reads as a count as well, so word order does not separate "
            "them and the criterion in this docstring is wrong"
        )


def test_a_section_reference_is_not_a_line_pointer():
    """`§7` stays legal, and that is a decision rather than an omission.

    102 instances across the corpus, and CLAUDE.md cites
    `docs/M2_EVALUATION_PROTOCOL.md` §10.3 and "M3 §7" as the way to point at a
    place in a document. A section is a named division that survives an edit
    above it; a line number is the thing that does not. Forbidding `§` would
    forbid the notation this repository chose.
    """
    text = "`docs/M2_EVALUATION_PROTOCOL.md` §10.3 states the one continuation, see §7"
    hits = [
        (m.group(0), form)
        for form, rx in LINE_POINTER_FORMS.items()
        for m in rx.finditer(text)
    ]
    assert not hits, (
        f"a section reference was read as a line pointer: {hits}. The repository "
        "points at sections on purpose and 102 references would fail"
    )
    assert _SECTION_REF.findall(text) == ["§10.3", "§7"], (
        "the section pattern no longer recognises the notation it exempts, so "
        "this test is not checking the distinction it names"
    )


def test_an_exemption_keeps_the_marker_that_earned_it():
    """An exemption may not outlive the reason recorded for it.

    Each entry in `DATED` names the marker that makes its document a record of a
    moment. If the document is rewritten into something a reader acts on today,
    or renamed, or the marker edited away, the exemption stops being justified --
    and nothing else would notice, which is the same failure as a line number
    nobody recomputes.

    The four `.claude/` entries are checked for existence only: they rest on the
    rule in CLAUDE.md about where they live, not on a marker of their own, and
    `GRAFANA_FORECAST_MONITORING.md` states no date at all.
    """
    broken = unjustified_exemptions(DATED)
    assert not broken, (
        "these exemptions no longer rest on anything:\n  " + "\n  ".join(broken)
    )


def test_the_exemption_check_can_fail():
    """Positive control for the check above, which otherwise cannot.

    Every marker currently resolves, so `unjustified_exemptions` returns an empty
    list whether it compares anything or not. Measured: replacing its comparison
    with `if False` left the whole file green apart from failures that have
    nothing to do with it. A check in that state is not evidence that the
    exemptions are justified -- it is an assertion about an empty list, which is
    the shape of defect this repository keeps finding.
    """
    fabricated = {
        "CLAUDE.md": "**Audit Date:** a marker CLAUDE.md does not carry",
        "docs/there-is-no-such-document.md": "**Date:** 2026-01-01",
    }
    broken = unjustified_exemptions(fabricated)
    assert len(broken) == 2, (
        f"unjustified_exemptions() found {len(broken)} of 2 fabricated problems "
        f"-- a missing marker and a missing file: {broken}"
    )
    assert any("no longer in the repository" in b for b in broken), broken
    assert any("no longer in" in b and "CLAUDE.md" in b for b in broken), broken

    assert not unjustified_exemptions(
        {CONTESTED: DATED[CONTESTED]}
    ), "a live, correct exemption was reported as unjustified"


def test_no_line_number_pointers_come_back():
    """Line numbers are the failure mode, not a formatting preference.

    Six of them drifted silently. Allowing one back means allowing the class
    back, and nothing else in the repository would notice.

    Over every tracked markdown file except the records of a moment in `DATED`,
    rather than over `CLAUDE.md` alone. The rule was decided here and enforced
    on one file out of 98; measured on `origin/main`, 78 pointers survived in 21
    files, and the one that matters instructs a reader to edit a constant at a
    line that holds `lock.release()` in a file the constant is not in.

    The visited count is asserted inside the sweep. Narrowing the file list back
    to one document is exactly what happened here, and a sweep that no longer
    sweeps has to fail rather than pass over nothing -- a mutation that scoped
    the equivalent check in
    `tests/test_readme_renders_and_its_claims_hold.py` back to a single file
    survived until its sweep counted what it had visited.

    Every shape in `LINE_POINTER_FORMS`, not only `file.py:NNN`. A guard you can
    walk around by writing "line 118" instead of "auth.py:118" is not a guard,
    and `docs/maximum/M0_GAP_REGISTER.md` carried one of each -- the worded one
    inside a correction recording that the numbered one had rotted.

    **This assertion is vacuous on current content: there is nothing left to
    find.** All 32 worded pointers are either in documents that record a moment
    or, for the single one that was not, repaired here. So what makes its green
    evidence is not this line, it is
    `test_the_sweep_can_print_a_non_zero` -- which found 84 pointers in the
    exempt documents when this was written, and asserts exactly which three of
    the five shapes real content proves; the set is checked, the count is not. Saying so is the point: an assertion over an empty set that
    presents itself as a measurement is the defect this file was written about.
    """
    visited, offenders = sweep_for_line_pointers()

    assert visited == len(files_in_scope()) >= 60, (
        f"the sweep visited {visited} of {len(files_in_scope())} markdown "
        "files; it is meant to cover every tracked one that is not a record of "
        "a moment, and this rule reaching a single file is the defect being "
        "repaired"
    )
    assert not offenders, (
        "these documents cite source lines by number in prose, and those drift "
        "the moment the file above them changes. Name the symbol instead -- "
        "`app/promotion.py` → `MIN_AUC_THRESHOLD`, as CLAUDE.md already does. "
        "(Inside a fenced block a line number is sample output, not a pointer, "
        "and is not counted. A document that records a moment is exempt; the "
        "list is DATED, above.):\n  "
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


def test_the_fence_walk_is_not_a_parity_count():
    """The two cases a parity count on column 0 gets wrong.

    `prose_lines` used to toggle on `line.startswith("```")`. Both samples here
    passed under it, for opposite reasons, and neither is hypothetical: the fence
    indented inside a list item and the closing fence followed by prose are the
    two shapes that were measured in `CLAUDE.md` while CI was green, and
    `tests/test_readme_renders_and_its_claims_hold.py` was widened for them.

    Without this test, reverting the walk to a parity count survives -- the 98
    files on `origin/main` happen to give the same answer either way, so nothing
    in the sweep would notice.
    """
    indented = (
        "- A list item:\n"
        "\n"
        "  ```\n"
        "  Traceback: app/main.py:521\n"
        "  ```\n"
        "\n"
        "  And `app/telemetry.py:12` in prose.\n"
    )
    found = [m.group(0).strip("`") for m in _LINE_REF.finditer(prose_only(indented))]
    assert found == ["app/telemetry.py:12"], (
        f"a fence indented inside a list item was not recognised: {found}. "
        "A column-0 pattern reads the traceback as prose and the sentence after "
        "it as code, which is both a false positive and a false negative in one "
        "sample"
    )

    trailing = (
        "```\n"
        "Traceback: app/main.py:521\n"
        "``` and then prose about `app/telemetry.py:12`\n"
        "\n"
        "More prose citing `app/cache.py:7`.\n"
    )
    found = [m.group(0).strip("`") for m in _LINE_REF.finditer(prose_only(trailing))]
    assert found == [], (
        f"a closing fence followed by prose was treated as closing: {found}. "
        "Under CommonMark it closes nothing, so everything after it is still "
        "inside the block -- counting it as prose is claiming a pointer where "
        "the document has sample output"
    )


#: `"reports 3082 cases"` -- a suite size stated as a current fact. Quoted
#: spans are stripped first, because the paragraph has to quote the wrong
#: numbers in order to explain them.
_SUITE_FIGURE = re.compile(r"\b\d[\d,]*\s+(?:cases|tests)\b")

#: `"..."` and `«...»`, the two the document uses.
_QUOTED = re.compile(r'"[^"]*"|«[^»]*»')


def suite_size_bullet(text: str) -> str:
    """The `- **Suite size:**` bullet, up to the next one."""
    lines = text.splitlines()
    start = next(n for n, l in enumerate(lines) if l.startswith("- **Suite size:**"))
    end = next(
        (n for n in range(start + 1, len(lines)) if lines[n].startswith("- **")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_the_suite_size_is_a_command_and_not_a_number():
    """The figure went stale twice, the second time in twelve hours.

    "375/384 tests passing (97.7%)" stood for months, wrong by a factor of
    eight. Replacing it with "reports 3082 cases" -- beside a sentence saying a
    copy in a document can only go stale -- put the same defect back: five
    merged pull requests the same day made it 3114.

    Quoted spans are stripped before judging, because the paragraph has to
    quote both wrong numbers to explain them. That is the whole subtlety, and
    `test_a_quoted_figure_is_history_not_a_claim` is the case that pins it.
    """
    bullet = suite_size_bullet(DOC.read_text())

    assert "--collect-only" in bullet, (
        "the Suite size bullet no longer names the command that counts the "
        "suite, so a reader has nothing but prose"
    )

    stated = _SUITE_FIGURE.findall(_QUOTED.sub(" ", bullet))
    assert not stated, (
        f"CLAUDE.md states a suite size as a current fact: {stated}. It cannot "
        f"stay true -- every added test moves it and nothing here recomputes "
        f"it. Name the command instead."
    )


def test_a_quoted_figure_is_history_not_a_claim():
    """The discriminating case for the stripping.

    Without it the bullet fails on its own explanation, which is a correct
    edit; with it, a fresh claim outside quotes is still caught.
    """
    history = 'It read "reports 3082 cases", which was wrong the same day.'
    claim = "`pytest --collect-only tests/` reports 3114 cases."

    assert not _SUITE_FIGURE.findall(_QUOTED.sub(" ", history)), (
        "a quoted historical figure was judged as a claim"
    )
    assert _SUITE_FIGURE.findall(_QUOTED.sub(" ", claim)), (
        "a bare figure passed, so the rule cannot catch what it is for"
    )
