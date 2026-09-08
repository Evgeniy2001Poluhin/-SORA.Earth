"""Content destroyed in the initial commit must be named, not merely absent.

The commit of 2026-05-07 left corrupted documentation across the repository:
`README.md` had an unclosed fence that swallowed 64 of its 116 lines (#284),
`docs/API.md` had three destroyed headings (#294), and seven numbered sections
were removed outright from four files (#296). The signature is always the same
— a heading breaks off and the tail of a neighbouring description leaks into it:

    docs/API.md   ### GET `/api/v1/model/ики модели.
    docs/DEMO.md  ### 3. Predic Germany
    README.md     - **Operations / Admin*тформу и инициирует действия.

Two mechanical signatures find all of it, and both are cheap enough to keep
running forever.

**What this file does not require.** It does not demand that the gaps be
closed. Renumbering the surviving sections would make the documents look
whole while hiding that content is missing, which is the same trade the
project keeps refusing elsewhere. What is forbidden is *silence*: a gap has to
carry a note saying which section is gone. Restoring the content itself is
authorship, and an invented section of an architecture document is
indistinguishable from a real one -- the defect of #282, where a list of five
models carried two that never existed.

Two of the seven were restorable and were restored, because the document
determined them rather than the author guessing: `docs/DEMO.md` sections 3 and
4 are fixed by its own screenshot list (`03-predict-explain.png`,
`04-country-benchmark.png`) and by the word "Germany" left stranded in the
corrupted heading.

**What this cannot see.** A *complete* renumber — removing section 5 and
shifting 6, 7, 8 down — leaves a sequence with no gap, and nothing in the text
distinguishes it from a document that only ever had seven sections. The check
catches a partial renumber, because that produces a gap somewhere else, and it
catches silence. It cannot catch a tidy erasure, and no check over the file
alone could: the evidence is gone by construction. Said out loud rather than
left for someone to discover, which is the habit this whole series is about.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKIP = {".git", "node_modules", ".claude", "venv", ".venv", ".venv311"}

#: `## 4. Карта России` -- a numbered section heading.
_NUMBERED = re.compile(r"^(#{2,4})\s+(\d+)\.\s")

#: The note a gap has to carry. Singular or plural, and it must name the number.
_LOST_NOTE = re.compile(r"[Рр]аздел(?:ы)?\s+(\d+)(?:\s*[–-]\s*(\d+))?\s+утрач")

#: How far above the heading that follows a gap the note may sit.
_WINDOW = 12


def markdown_files() -> list[Path]:
    return sorted(
        p for p in REPO.rglob("*.md") if not SKIP & set(p.relative_to(REPO).parts)
    )


def outside_fences(lines: list[str]) -> list[tuple[int, str]]:
    """Numbered lines that are not inside a fenced code block."""
    out, inside = [], False
    for n, line in enumerate(lines, 1):
        if line.startswith("```"):
            inside = not inside
            continue
        if not inside:
            out.append((n, line))
    return out


def numbering_gaps(path: Path) -> list[tuple[int, int, int]]:
    """(previous number, next number, line of the next heading) per gap."""
    lines = path.read_text(errors="replace").splitlines()
    seen = [
        (int(m.group(2)), n)
        for n, line in outside_fences(lines)
        if (m := _NUMBERED.match(line))
    ]
    gaps = []
    for (a, _), (b, line_no) in zip(seen, seen[1:]):
        if b != a + 1 and b != 1:
            gaps.append((a, b, line_no))
    return gaps


def test_the_scan_finds_the_documents_it_judges():
    """Negative control. Both assertions below pass over an empty scan."""
    files = markdown_files()
    assert len(files) >= 50, f"only {len(files)} markdown files found under {REPO}"

    # Counted per line, not by searching a joined string: `_NUMBERED` is
    # anchored with `^` and compiled without re.MULTILINE, so `search` over a
    # concatenation matches only at the very start and reported 3 files where
    # there are 31. The negative control had the defect it exists to catch.
    numbered = [
        p
        for p in files
        if any(
            _NUMBERED.match(line)
            for _, line in outside_fences(p.read_text(errors="replace").splitlines())
        )
    ]
    assert len(numbered) >= 20, (
        f"only {len(numbered)} files carry numbered sections (31 when this was "
        "written); the heading format changed and the gap check judges nothing"
    )


def test_every_gap_in_a_numbered_sequence_is_named():
    """A missing section must say it is missing.

    The gap itself is allowed: closing it by renumbering would make the
    document look whole while the content stayed lost.
    """
    unnamed = []
    for path in markdown_files():
        lines = path.read_text(errors="replace").splitlines()
        for previous, following, line_no in numbering_gaps(path):
            window = "\n".join(lines[max(0, line_no - 1 - _WINDOW) : line_no])
            named = {
                int(n)
                for start, end in _LOST_NOTE.findall(window)
                for n in range(int(start), int(end or start) + 1)
            }
            for missing in range(previous + 1, following):
                if missing not in named:
                    unnamed.append(
                        f"{path.relative_to(REPO)}:{line_no} — раздел {missing} "
                        f"пропущен между {previous} и {following} и не назван"
                    )

    assert not unnamed, (
        "these documents skip a numbered section without saying it was lost. "
        "Add a note naming the number rather than renumbering, which would hide "
        "that content is missing:\n  " + "\n  ".join(unnamed)
    )


def test_no_heading_has_an_unclosed_backtick():
    """The other signature of the same corruption.

    `### GET \\`/api/v1/model/ики модели.` — the path breaks off mid-word and
    the backtick never closes. Three headings in `docs/API.md` looked like that
    for four months; of 1353 headings in the repository, only those three did.
    """
    broken = []
    for path in markdown_files():
        for n, line in outside_fences(path.read_text(errors="replace").splitlines()):
            if line.lstrip().startswith("#") and line.count("`") % 2:
                broken.append(f"{path.relative_to(REPO)}:{n}: {line.strip()[:70]}")

    assert not broken, (
        "these headings have an odd number of backticks, which is how the "
        "destroyed headings of #294 read:\n  " + "\n  ".join(broken)
    )
