"""A closed issue does not track open work.

#354 deferred unfinished work to #199 -- "it belongs with the one-row-per-run
work (#199)" -- and #199 had closed eleven hours before that commit. A reader
follows the reference, finds it closed, and has no way to tell whether the
work landed somewhere else or nowhere. That was one instance on one branch.
This file is the sweep for the rest of them, and it found four already on
`main`, none touched by #354:

    app/database.py                        "belongs with #189 and #198" --
                                           #189 closed 2026-09-04; registry
                                           failure still does not reach
                                           `status`, measured by reading
                                           `project_status()` itself
    docs/maximum/M0_GAP_REGISTER.md        "is tracked separately as issue
                                           #26" -- closed 2026-09-19
    tests/test_bulk_upload_content.py      "evidence needs a release to
                                           accumulate (#26)" -- same issue,
                                           and its own closing comment offers
                                           to file the removal as a fresh
                                           issue "so it is not lost" -- an
                                           offer nothing shows was taken up
    tests/test_state_changing_route_authz.py
                                            an IDOR gap deferred as "needs
                                           owner-scope (#199 follow-up)" --
                                           #199 is the retrain/promotion
                                           state machine, a different topic
                                           entirely, closed the same day

All four are fixed alongside this file. What follows is how the sweep tells a
promise from a citation, and the two traps that would have made it lie.

## The corpus, before any filtering

`git ls-files` over `*.md`, `app/*.py`, `tests/*.py` -- 98 + 392 = 490 files,
matching the file count `test_docs_point_at_real_symbols.py` measured for
`*.md` alone (98), so the extraction agrees with the sibling guard on the
one number both can check. `#(\\d+)` against that corpus, fenced code
stripped from markdown the way `prose_only` already does for the sibling
guards -- fenced blocks hold pasted output and tracebacks, not citations --
finds 938 raw occurrences, 926 after stripping, naming 158 distinct numbers.
Checked for the false positives a bare `#\\d+` invites and none were
measured: no `[text](#123)` markdown anchor, no all-numeric hex colour, no
"item #3" ordinal, anywhere in this corpus. (One nearby thing *is* real and
excluded on purpose: `app/services/outbound.py` labels DNS resolution steps
"resolve #1", "resolve #2", "resolve #3" -- coincidentally the numbers of
three real, closed issues, and about none of them. The commitment-phrase
filter below never fires on it, so it needed no special case -- recorded here
because it is exactly the kind of near-miss this docstring exists to keep
from being rediscovered by surprise.)

## Trap 1 -- `gh issue view` does not error on a pull request, it lies

65 of the 158 numbers are pull requests, not issues -- 41%, not a rare edge.
`gh issue view 198` returns exit 0 and a JSON object with `"state": "CLOSED"`
indistinguishable in shape from a real issue; only its `url` field
(`.../pull/198`) admits it is not one. A classifier trusting `gh issue view`
would have silently mislabelled 65 numbers, including one in the exact
sentence this file fixes (`app/database.py` cites both #189, a real closed
issue, and #198, a closed-without-merge pull request, in the same breath).
The fix is `issueOrPullRequest` over GraphQL, which returns a typed node and
lets the cache say which it got. A reference that resolves to a pull request
is never a candidate here: a PR being closed or merged is its ordinary
healthy end state, not the "nobody is tracking this anymore" failure an issue
reference can have.

## Trap 2 -- most closed-issue citations are correct, and look identical to the ones that are not

93 of the 158 numbers are issues; 88 of those 93 are already closed. That
ratio is not noise -- this codebase cites closed issues constantly, as the
place a decision or a fix *was* recorded, which is a different speech act
from citing one as the place remaining work lives. `#199 phase 2A`,
`#199 phase 4`, `#191 phase 3`, `#199 contract point 10`: measured across
every one of #199's 39 references and #191's 1, this "phase N" / "contract
point N" notation names which piece of an already-decided, already-closed
contract a line of code implements -- retrospective every time it was read in
full. That qualifier is the one structural exemption below, and it is why the
search space for the real defect is 705 bare references, not 743.

Filtering bare references by commitment language -- "belongs with", "is
fixed with", "follow-up)", "the remaining", "needs X (#N", "tracked
separately", "still open" / "not yet" / "pending" / "outstanding",
"awaiting" / "yet to" / "will need" -- and then stripping quoted spans first
(an old xfail message quoting "endpoint pending v0.2.2" is not a live claim;
`test_a_quoted_figure_is_history_not_a_claim` in the symbols guard hit the
same shape) narrows 705 to 7 candidates, one appearing twice. Read against
the four above, two were false: `ROADMAP_ENV_CRISIS_2026.md`'s "pending" sits
in an unrelated clause about a cadence decision, not about the issue named
two clauses earlier; `test_every_python_file_parses.py`'s "not yet added"
describes a git-staging edge case in the test's own reasoning, and "#54 was
about" -- immediately after, in the same sentence the filter matched -- is
what the past tense there actually says.

## The decision the brief asked for: this can go red with no local diff

An issue can close the day after a reference to it is written. When that
happens the sweep below goes red on a commit that changed nothing -- not a
bug in the rule, the rule's entire job. A diff-scoped version (judge only
references added in the current change) would never have caught three of the
four defects above, all written well before their issue closed and stale
only in hindsight. So this stays whole-repository, and the state it checks
against is a cache (`closed_issue_reference_cache.json`), not a live call:
this suite runs offline elsewhere in the project (`SORA_OFFLINE=1` is the
standing convention), a GraphQL call per test run adds a real network
dependency and a rate limit to every `pytest tests/`, and the interesting
failure mode -- an issue closing after the reference was written -- is
exactly the one a cache miss cannot silently hide, because
`test_the_cache_covers_every_reference_in_the_corpus` fails on any number the
corpus names that the cache does not, which is what forces a refresh rather
than a stale pass. What a stale-but-complete cache cannot do is notice a
closure that happened after the last refresh; nothing here runs that refresh
on a schedule. That is the trade, made on purpose rather than discovered
later: fast and deterministic today, current only as of the last
`refresh_closed_issue_reference_cache.py` run.

## Archival reports were checked, not exempted

CLAUDE.md says of the dated reports under `.claude/` that they "describe the
world as it was and are left alone." Seventeen tracked `.claude/*.md` files
and one under `docs/archive/` are in scope here rather than carved out: none
of the 7 candidates were in either, so an exemption would be defended by
nothing this corpus contains today. `test_docs_point_at_real_symbols.py`
solved the equivalent problem for line-number pointers with a marker-verified
`DATED` allowlist because that rule's false-positive rate on dated documents
was measured and real; this rule's was measured and zero. If an archival
report ever does trip this, the shaped fix is the same kind of marker-checked
exemption, added against a real instance -- not built now against a
hypothetical one.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CACHE_PATH = Path(__file__).resolve().parent / "closed_issue_reference_cache.json"

#: Any `#123`-shaped citation. Deliberately not anchored to word-start, so it
#: also catches one immediately after a paren or a quote mark.
_REF = re.compile(r"#(\d+)\b")

#: `#199 phase 2A`, `#199 phase 4`, `#199 contract point 10`, `#191 phase 3` --
#: measured across every instance in the corpus (40 references, two issues)
#: and retrospective in all of them: this is how the codebase cites which
#: part of an already-settled, already-closed contract a line implements.
#: `was about` / `is about` joins it for the same reason on a smaller scale:
#: `tests/test_every_python_file_parses.py` names issue #54 and then narrates
#: its content in the past tense ("#54 was about") -- a citation of what the
#: issue said, not a claim about where unfinished work now lives.
_RETROSPECTIVE_SUFFIX = re.compile(
    r"^\s*(phase\s+\w+|contract point\s+\d+|was about\b|is about\b)",
    re.IGNORECASE,
)

#: `"..."` and the guillemets CLAUDE.md and the Russian-language issues use --
#: but only when a quoting verb or an em-dash aside introduces the quote.
#: Blanket-stripping every quoted span was tried first and broke on
#: `tests/test_state_changing_route_authz.py`: its GAP registry is a Python
#: dict whose *values* are the citations this file exists to judge, quoted
#: only because Python string literals are, and stripping them erased the
#: defect this file was written to catch. `read "..."` / `says "..."` and
#: friends are one real shape -- `tests/test_ab_deep.py`'s "the xfail here
#: read '...pending v0.2.2'" is the case this exists for. `-- "..." --` is
#: the other: CLAUDE.md's own self-correction idiom ("This sentence first
#: deferred it to #199 -- 'the same double-row shape #199 carries and is
#: fixed with it, not separately' -- and #199 was closed...", added by #354
#: fixing the very defect this file catches) quotes the wrong wording being
#: corrected between em-dashes, no verb involved -- found only once this file
#: was run against the corpus that now contains it.
_QUOTED_AFTER_VERB = re.compile(
    r'\b(?:read|reads|says|said|claimed|wrote)\s+("[^"]*"|«[^»]*»)'
    r'|[-–—]{1,2}\s*("[^"]*"|«[^»]*»)\s*[-–—]{1,2}',
    re.IGNORECASE,
)

#: Derived from the corpus (see the module docstring's Trap 2), not invented:
#: every phrase here is one that, in this codebase, was found attached to a
#: reference describing work that is not yet done and names where it belongs.
_COMMITMENT_PATTERNS = [
    re.compile(r"\bbelongs (with|to)\b", re.I),
    re.compile(r"\bis fixed with\b|\bfixed with (it|that|this)\b|\bshould be fixed with\b", re.I),
    re.compile(r"\bfollow-?up\)", re.I),
    re.compile(r"\bthe remaining\b|\bremaining half\b|\bremaining work\b", re.I),
    re.compile(r"\bneeds?\s+\S+.{0,40}\(#", re.I),
    re.compile(r"\btracked (in|by|as|separately)\b", re.I),
    re.compile(r"\bstill open\b|\bnot yet\b|\bpending\b|\boutstanding\b", re.I),
    re.compile(r"\bawaiting\b|\byet to\b|\bwill need\b|\bshould move\b", re.I),
    re.compile(r"\bonce (this|#|it)\b|\bwhen (this|#|it) (closes|merges|lands)\b", re.I),
]


def tracked_files() -> list[Path]:
    """`*.md`, `app/*.py`, `tests/*.py`, exactly as git tracks them.

    Matches the file-discovery `test_docs_point_at_real_symbols.py` uses for
    markdown, so the two guards' counts are cross-checkable against each
    other rather than each trusting its own glob.
    """
    out = subprocess.run(
        ["git", "ls-files", "*.md", "app/*.py", "tests/*.py"],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    paths = (REPO / line for line in out.stdout.splitlines() if line)
    # This file itself quotes the four real defects verbatim and cites #354
    # by way of explaining them -- the one file guaranteed to trip its own
    # rule by doing its job. The sibling line-pointer guard exempts
    # `ENVIRONMENTAL_BASELINE_AUDIT.md` for the identical reason: a document
    # (here, a test module) whose whole content is examples of the pattern
    # being judged is not evidence of the defect it quotes.
    return sorted((p for p in paths if p != Path(__file__).resolve()), key=str)


def prose_only(text: str) -> str:
    """Markdown with fenced code blocks removed -- pasted output is not a citation."""
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("```"):
            inside = not inside
            continue
        if not inside:
            out.append(line)
    return "\n".join(out)


def _scoped_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".md":
        text = prose_only(text)
    # Stripped on the whole file, not a per-match window: the verb that
    # gates a quote ("the xfail here read \"...\"") can sit well outside a
    # narrow window built around the reference that follows the quote.
    return _QUOTED_AFTER_VERB.sub(" ", text)


def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def referenced_numbers() -> set[int]:
    """Every distinct `#N` cited anywhere in scope."""
    nums = set()
    for path in tracked_files():
        for m in _REF.finditer(_scoped_text(path)):
            nums.add(int(m.group(1)))
    return nums


def _offenders_in_text(text: str, cache: dict) -> list[tuple[int, str]]:
    """The pure match: commitment language beside a bare citation of a closed issue.

    Split out from the file sweep so the control test below can hand it a
    fabricated sentence directly, the way `_LINE_REF.findall(prose_only(doc))`
    is tested on an inline string in the symbols guard rather than only
    through a real file.
    """
    offenders = []
    for m in _REF.finditer(text):
        entry = cache.get(m.group(1))
        if not entry or entry.get("type") != "Issue" or entry.get("state") != "CLOSED":
            continue
        after = text[m.end():m.end() + 25]
        if _RETROSPECTIVE_SUFFIX.match(after):
            continue
        # A window wide enough for every commitment phrase measured in the
        # corpus (all within ~40 characters of the reference) and narrow
        # enough not to cross into the next sentence -- ROADMAP_ENV_CRISIS_2026.md
        # puts an unrelated "pending a cadence decision" clause 55 characters
        # after a retrospective "(#74)", one sentence later, and a wider
        # window read it as attached to the reference.
        start, end = max(0, m.start() - 50), min(len(text), m.end() + 50)
        window = _collapse(_QUOTED_AFTER_VERB.sub(" ", text[start:end]))
        if any(p.search(window) for p in _COMMITMENT_PATTERNS):
            offenders.append((int(m.group(1)), window))
    return offenders


def commitment_references_to_closed_issues(cache: dict) -> list[tuple[str, int, str]]:
    offenders = []
    for path in tracked_files():
        rel = str(path.relative_to(REPO))
        for num, window in _offenders_in_text(_scoped_text(path), cache):
            offenders.append((rel, num, window))
    return offenders


def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        pytest.skip(f"{CACHE_PATH.name} missing -- run "
                    f"tests/refresh_closed_issue_reference_cache.py first")
    return json.loads(CACHE_PATH.read_text())


def test_the_parser_finds_references_to_judge():
    """Negative control. Every assertion below passes over an empty set."""
    nums = referenced_numbers()
    assert len(nums) > 100, (
        f"only {len(nums)} distinct #N references found across the corpus; "
        f"the pattern or the file discovery changed and this file judges "
        f"almost nothing"
    )


def test_the_cache_covers_every_reference_in_the_corpus():
    """A number the corpus names and the cache does not is a forced refresh.

    This is what keeps a stale-but-silent cache from being worse than no
    cache: a *new* reference cannot pass by simply being unresolved, only an
    already-resolved one can.
    """
    cache = _load_cache()
    live = referenced_numbers()
    cached = {int(k) for k in cache}
    missing = sorted(live - cached)
    assert not missing, (
        f"{len(missing)} number(s) are referenced in the corpus but not in "
        f"{CACHE_PATH.name}: {missing}. Run "
        f"tests/refresh_closed_issue_reference_cache.py to add them."
    )


def test_no_commitment_reference_names_a_closed_issue():
    """The defect: a promise pointing at an issue nobody is tracking anymore."""
    cache = _load_cache()
    offenders = commitment_references_to_closed_issues(cache)
    assert not offenders, (
        "these reference a closed issue with language that implies the "
        "issue still tracks the work -- point at where the work actually "
        "lives, or say plainly that nothing currently tracks it:\n  "
        + "\n  ".join(f"{f} #{n}: {w}" for f, n, w in offenders)
    )


def test_the_detector_fires_on_commitment_language_and_not_on_citation_language():
    """Five discriminating cases, none of them from a real file.

    Without this, `test_no_commitment_reference_names_a_closed_issue` passing
    would be consistent with the detector matching nothing at all -- the
    trap `test_an_exemption_keeps_the_marker_that_earned_it` in the symbols
    guard was caught by.
    """
    cache = {
        "1": {"type": "Issue", "state": "CLOSED", "title": "x"},
        "2": {"type": "Issue", "state": "OPEN", "title": "y"},
        "3": {"type": "PullRequest", "state": "CLOSED", "title": "z"},
    }

    commitment_to_closed = "the remaining half of this belongs with the cleanup work (#1)."
    assert _offenders_in_text(commitment_to_closed, cache), (
        "a commitment reference to a closed issue was not flagged"
    )

    retrospective_citation = "Filed as #1, and decided in the same pass (#1 phase 2A)."
    assert not _offenders_in_text(retrospective_citation, cache), (
        "a phase-qualified historical citation was flagged"
    )

    commitment_to_open = "still open, and belongs with #2, not fixed here."
    assert not _offenders_in_text(commitment_to_open, cache), (
        "a commitment reference to an OPEN issue was flagged -- an open "
        "tracker is exactly where remaining work should point"
    )

    pr_citation = "shipped in #3, and closed the same day it merged."
    assert not _offenders_in_text(pr_citation, cache), (
        "a pull-request citation was judged as though it named a closed issue"
    )

    quoted_history = 'The old xfail read "still pending" (#1), which was wrong.'
    assert not _offenders_in_text(quoted_history, cache), (
        "commitment language inside a quoted historical string was flagged"
    )


def test_the_detector_fires_on_the_four_defects_it_was_written_for():
    """The real wording found on `main`, frozen rather than left to the live sweep alone.

    All four are fixed elsewhere in this change; this is what regresses
    first if the phrasing comes back or the patterns are narrowed.
    """
    cache = {
        "26": {"type": "Issue", "state": "CLOSED", "title": "x"},
        "189": {"type": "Issue", "state": "CLOSED", "title": "y"},
        "199": {"type": "Issue", "state": "CLOSED", "title": "z"},
    }
    real_defects = {
        "docs/maximum/M0_GAP_REGISTER.md":
            "The remaining architectural change — replacing a server-side "
            "`file_path` with a multipart upload — is tracked separately as "
            "issue #26 and is not this gap.",
        "tests/test_bulk_upload_content.py":
            "the audit line it writes is the evidence for removing it, and "
            "evidence needs a release to accumulate (#26).",
        "app/database.py":
            "Making a failed registration visible in the top-level status is "
            "a contract change that belongs with #189 and #198, not with "
            "this split.",
        "tests/test_state_changing_route_authz.py":
            '"/api/v1/copilot/sessions/{session_id}": "needs owner-scope '
            '(#199 follow-up)",',
    }
    for path, text in real_defects.items():
        assert _offenders_in_text(text, cache), f"no longer catches the {path} wording"
