"""An archived document must say so, and every link must land somewhere (#286).

`docs/RUNBOOK.md` carried "Версия: 1.0, Дата: 2026-05-16" and described
infrastructure that no longer exists. `docs/BACKUP_RESTORE.md` pointed at it as
the document covering day-to-day operations. Measured 2026-09-08, and every
entry point in its first table:

    https://sora-earth.ru            530   Cloudflare: tunnel up, origin gone
    https://api.sora-earth.ru        530
    https://grafana.sora-earth.ru    530
    ssh.sora-earth.ru:2222           no listener; cloudflared inactive
    109.73.194.26 (Timeweb)          not a host of this project
    /opt/sora_earth                  no such directory

against `https://sora-earth.online/health` → 200 on `77.110.118.93`, out of
`/opt/sora_earth_ai_platform`. A runbook is read in exactly one situation —
something is broken — and this one sent the reader to a tunnel that is down, a
directory that does not exist, and `docker compose up -d`, which the 2026-08-09
incident (#129) made a forbidden command.

It was moved to `docs/archive/` and marked rather than rewritten. Rewriting it
for the current host needs someone who knows which procedures are real now;
saying it is history needs only what was measured.

**What is checked here is offline and mechanical.** The issue's acceptance
criterion — every host, domain and path in the runbook answers — cannot live in
CI: it would make the suite depend on DNS and on someone else's uptime, and it
would have passed in May when all of it was true. These two properties are the
part a test can hold:

    every relative markdown link resolves to a path that exists
    every file under docs/archive/ says it is historical, with a date

The first is what makes moving a document safe; without it the move is the
thing most likely to leave a dead link behind.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARCHIVE = REPO / "docs" / "archive"
SKIP = {".git", "node_modules", ".claude", "venv", ".venv", ".venv311"}

#: `[text](path)` -- the target only.
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")

#: The marker an archived document has to carry, and the date it stopped being
#: true. Both, because "historical" without a date leaves the reader guessing
#: which half of the file to believe.
_HISTORICAL = re.compile(r"[Ии]сторический документ")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: What counts as saying "this is history" on the line that links to it.
_MARKS_HISTORY = re.compile(r"[Аа]рхив|[Ии]сторическ|historical|archive")


def _says_it_is_history(window: str, target: str) -> bool:
    """Whether the prose around a link marks it as historical.

    The link target is removed first, and that is the whole substance of this
    function: every path being judged contains the segment `archive`, so a
    search over the raw line matched its own target and the rule could not
    refuse anything. Found by running it on a deliberately unmarked citation,
    which passed.
    """
    return bool(_MARKS_HISTORY.search(window.replace(target, " ")))

#: How far into the file the marker may sit. It has to be the first thing a
#: reader meets; a note at the bottom of 300 lines is not a warning.
_HEAD = 30

#: How far after the marker the date may sit. Tight on purpose: over the whole
#: head window any date at all satisfied it, and this header cites the
#: 2026-08-09 incident twenty lines down -- so replacing every real date with
#: "прошлым летом" left the check green. Measured, not reasoned about.
_DATE_WINDOW = 4


def markdown_files() -> list[Path]:
    return sorted(
        p for p in REPO.rglob("*.md") if not SKIP & set(p.relative_to(REPO).parts)
    )


def relative_links() -> list[tuple[Path, str]]:
    """(file, target) for every link that points inside the repository."""
    found = []
    for path in markdown_files():
        for target in _LINK.findall(path.read_text(errors="replace")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            found.append((path, target))
    return found


def test_the_scan_finds_what_it_judges():
    """Negative control. Both assertions below pass over an empty scan."""
    files = markdown_files()
    assert len(files) >= 50, f"only {len(files)} markdown files found under {REPO}"

    links = relative_links()
    assert len(links) >= 20, (
        f"only {len(links)} relative links parsed out of {len(files)} files "
        "(26 when this was written); the link check judges almost nothing"
    )
    assert ARCHIVE.is_dir(), "docs/archive/ is gone, so the marker check is vacuous"
    assert list(ARCHIVE.glob("*.md")), "docs/archive/ holds no documents"


def test_every_relative_link_lands_somewhere():
    """A moved document must not leave a dead link behind.

    Nothing in the repository checked this. `docs/RUNBOOK.md` had exactly one
    inbound reference and it was in backticks rather than a link, so the move
    happened to be safe -- which is luck, not a property.
    """
    broken = []
    for path, target in relative_links():
        resolved = (path.parent / target.split("#")[0]).resolve()
        if not resolved.exists():
            broken.append(f"{path.relative_to(REPO)} → {target}")

    assert not broken, (
        "these links point at paths that do not exist:\n  " + "\n  ".join(broken)
    )


def test_every_archived_document_says_it_is_history():
    """Archived and unmarked is worse than not archived.

    The directory name is not visible from inside the file, and a reader who
    arrives from a search engine or a grep sees only "Версия: 1.0, Дата:
    2026-05-16" -- which reads as a current document that has not needed
    changing.
    """
    unmarked = []
    for path in sorted(ARCHIVE.rglob("*.md")):
        lines = path.read_text(errors="replace").splitlines()[:_HEAD]
        marker = next((n for n, line in enumerate(lines) if _HISTORICAL.search(line)), None)

        if marker is None:
            unmarked.append(f"{path.relative_to(REPO)}: no historical marker")
            continue

        beside = "\n".join(lines[marker: marker + 1 + _DATE_WINDOW])
        if not _DATE.search(beside):
            unmarked.append(
                f"{path.relative_to(REPO)}: marked historical without saying "
                f"when it stopped being true"
            )

    assert not unmarked, (
        f"these archived documents do not announce themselves in their first "
        f"{_HEAD} lines:\n  " + "\n  ".join(unmarked)
    )


def test_no_live_document_presents_an_archived_one_as_current():
    """The other half of the same defect, and the half that was actually wrong.

    `docs/BACKUP_RESTORE.md` named the runbook as the operations document. A
    link into `docs/archive/` is allowed -- history is worth citing -- but the
    line has to say what it is pointing at.
    """
    unmarked = []
    for path, target in relative_links():
        if path.is_relative_to(ARCHIVE):
            continue
        resolved = (path.parent / target.split("#")[0]).resolve()
        if not resolved.is_relative_to(ARCHIVE):
            continue

        text = path.read_text(errors="replace").splitlines()
        line = next((n for n, l in enumerate(text) if target in l), None)
        window = "\n".join(text[max(0, line - 1): line + 1]) if line is not None else ""
        if not _says_it_is_history(window, target):
            unmarked.append(f"{path.relative_to(REPO)} → {target}")

    assert not unmarked, (
        "these live documents link into docs/archive/ without saying the "
        "target is history:\n  " + "\n  ".join(unmarked)
    )


def test_the_marker_rule_separates_the_two_cases():
    """The discriminating case, because the sweep above currently finds none.

    No live document links into `docs/archive/` today, so
    `test_no_live_document_presents_an_archived_one_as_current` passes over an
    empty set — a green that proves nothing about the rule it states. This
    runs the rule on both answers directly.
    """
    marked = (
        "Историческое описание прежнего хоста:\n"
        "[RUNBOOK мая 2026](docs/archive/RUNBOOK-2026-05-16.md)\n"
    )
    unmarked = (
        "Повседневные операции описаны здесь:\n"
        "[RUNBOOK](docs/archive/RUNBOOK-2026-05-16.md)\n"
    )

    target = "docs/archive/RUNBOOK-2026-05-16.md"

    assert _says_it_is_history(marked, target), "a marked citation was rejected"
    assert not _says_it_is_history(unmarked, target), (
        "an unmarked reference passed the rule, so the sweep would allow the "
        "exact sentence #286 is about"
    )
