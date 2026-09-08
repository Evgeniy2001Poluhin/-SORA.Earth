"""A document must not name artefacts the repository does not hold (#297).

`README.md` opened its "Screenshots" section with "9 thesis-grade артефактов с
подписями" and linked to `docs/screenshots/README.md`. That document describes
each screenshot in detail — "7 features в LOW severity, |z| <= 0.04, баннер
\"Baseline fitted: 734 samples\"" — and the catalogue holds **zero** image
files. Three numbers disagreed with each other and with the disk: nine
promised, seven described, none present.

Measured before the fix, and both measurements close an option the issue
listed:

    docs/screenshots/*.png in any commit, any branch or tag ....... 0
        git log --all --diff-filter=AMD --name-only
    the same names anywhere on the author's machine ............... 0
        mdfind -name 01-home -name 04-mlops-control ...

So they cannot be restored from the repository, and re-shooting them cannot
reproduce the captions: the database those figures were measured on — "734
samples", "11 MLflow events" — was destroyed with the previous host on
2026-08-16. A fresh screenshot under an old caption would be a fabrication of
the same kind as #282's list of five models that carried two which never
existed.

**What this file asserts** is a biconditional in each direction, so neither
half can go stale in silence:

    a named file is absent  <->  the document says the catalogue is empty
    the catalogue is empty  <->  README does not promise a count

Add the screenshots and both tests go red until the notices are removed. Remove
a notice while the files are still missing and they go red too.

**And the blind spot that hid section 9.** `tests/test_lost_doc_sections_are_named.py`
finds a lost section by the hole it leaves between two headings, which cannot
see one lost from the *end* — its own docstring says a tidy erasure leaves no
evidence. Here evidence survived in a different place: the mapping table names
chapters through `09` while the last heading was `8`. Section 9's text was
sitting inside section 8, joined mid-word at "consensus re|ion quality".
`test_every_chapter_in_the_mapping_table_has_a_section` is that check.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CATALOGUE = REPO / "docs" / "screenshots"
DOC = CATALOGUE / "README.md"
README = REPO / "README.md"

#: `## 8. 08-calibration-ensemble.png — Cross-Model Trust`, and also
#: `## 9. Calibration quality`, whose filename was destroyed with its heading.
_SECTION = re.compile(
    r"^## (\d+)\.\s+(?:(\d{2}-[\w.-]+\.png)\s+—\s+)?(.+)$", re.M
)

#: A row of the "Mapping в главы диплома" table: `| 04-05 | ... |`.
_MAPPING_ROW = re.compile(r"^\|\s*(\d{2})(?:-(\d{2}))?\s*\|", re.M)

#: The note a lost section carries, spelled as `test_lost_doc_sections_are_named`
#: requires it. Kept as a separate pattern rather than imported: these two files
#: must be able to disagree.
_LOST_NOTE = re.compile(r"[Рр]аздел(?:ы)?\s+(\d+)(?:\s*[–-]\s*(\d+))?\s+утрач")

#: The sentence that keeps the document honest while the files are missing.
_EMPTY_NOTICE = "нет ни одного снимка"

#: The one README must carry for as long as the same is true.
_README_NOTICE = "Ни одного снимка в репозитории нет"

#: The promise this issue is about, in the form it had.
_OLD_PROMISE = re.compile(r"(\d+) thesis-grade артефактов")


def sections() -> list[tuple[int, str, str]]:
    """(number, filename or "", title) for every numbered section."""
    return [(int(n), f or "", t) for n, f, t in _SECTION.findall(DOC.read_text())]


def images_present() -> list[Path]:
    return sorted(CATALOGUE.glob("*.png")) + sorted(CATALOGUE.glob("*.jpg"))


def test_the_parser_finds_the_sections_it_judges():
    """Negative control. Every assertion below passes over an empty document."""
    found = sections()
    assert len(found) >= 8, (
        f"only {len(found)} numbered sections parsed out of {DOC.name} (8 when "
        "this was written); the heading format changed and this file judges "
        "nothing"
    )
    assert any(f for _, f, _ in found), (
        "no section names an image file; the checks below have nothing to "
        "resolve against the disk"
    )
    assert _MAPPING_ROW.findall(DOC.read_text()), (
        "the mapping table is gone or changed shape, so the chapter check "
        "below judges nothing"
    )


def test_a_named_file_is_present_or_its_absence_is_stated():
    """The defect: a catalogue that reads as complete and holds nothing.

    Both directions. A missing file without the notice is the state that
    shipped; the notice without missing files is a document that has stopped
    being true the other way round, and it would sit there telling readers
    the screenshots are gone while they look at them.
    """
    named = {f for _, f, _ in sections() if f}
    missing = sorted(n for n in named if not (CATALOGUE / n).exists())
    says_empty = _EMPTY_NOTICE in DOC.read_text()

    if missing:
        assert says_empty, (
            f"{DOC.relative_to(REPO)} names {len(missing)} file(s) that are not "
            f"in {CATALOGUE.relative_to(REPO)} and does not say so: {missing}. "
            f"A description of a screenshot nobody can open reads as a "
            f"screenshot that exists."
        )
    else:
        assert not says_empty, (
            f"every named file is present, but {DOC.relative_to(REPO)} still "
            f"says the catalogue is empty. Remove the notice — including the "
            f"one in README.md."
        )


def test_the_readme_promises_no_count_it_cannot_show():
    """The front page is where the false number was.

    Also a biconditional: with files present the notice has to go, and the
    line that replaced the promise has to say something true instead.
    """
    text = README.read_text()
    present = images_present()

    promised = _OLD_PROMISE.search(prose_outside_the_correction(text))
    assert not promised, (
        f"README.md promises {promised.group(1) if promised else '?'} thesis "
        f"artefacts again while {CATALOGUE.relative_to(REPO)} holds "
        f"{len(present)} file(s). This is the claim of #297."
    )

    if not present:
        assert _README_NOTICE in text, (
            f"{CATALOGUE.relative_to(REPO)} is empty and README.md does not say "
            f"so. It links to the catalogue, so silence here reads as a promise."
        )
    else:
        assert _README_NOTICE not in text, (
            f"README.md says no screenshots exist, and {len(present)} do: "
            f"{[p.name for p in present]}"
        )


def prose_outside_the_correction(text: str) -> str:
    """README minus the sentence that quotes the old promise to correct it.

    The fix explains what the line used to say, and that quotation must not be
    read as the promise coming back. Anchored on the verb, so a genuine new
    promise elsewhere is still caught.
    """
    return re.sub(r"обещала «[^»]*»", "", text)


def test_every_chapter_in_the_mapping_table_has_a_section():
    """A section lost from the end leaves no hole between headings.

    Chapter 09 was named in the table while the last heading was 8, and the
    text of section 9 was joined onto section 8 mid-word. Nothing in the
    project could see that: the numbering check works on gaps between
    consecutive headings, and there is no gap after the last one.
    """
    text = DOC.read_text()
    chapters: set[int] = set()
    for start, end in _MAPPING_ROW.findall(text):
        chapters.update(range(int(start), int(end or start) + 1))

    have = {n for n, _, _ in sections()}
    named_lost = {
        n
        for start, end in _LOST_NOTE.findall(text)
        for n in range(int(start), int(end or start) + 1)
    }

    unaccounted = sorted(chapters - have - named_lost)
    assert not unaccounted, (
        f"the mapping table of {DOC.relative_to(REPO)} sends the reader to "
        f"chapters that have no section and are not named as lost: "
        f"{unaccounted}. Either the section is missing from the end of the "
        f"document or the table promises a chapter that was never written."
    )
