"""The numbered constraints in CLAUDE.md are a list, and lists lose entries.

Written after losing five of them. Extending item 1 with the startup-job
contract was done by slicing the file from the start of the new text to the
next heading -- which swallowed items 2 through 6, including the description of
what rate limiting actually enforces and where it does not. The line count
happened to come out the same, so nothing looked odd.

CI cannot notice a documentation section that stopped existing. This can: the
numbering has to run 1..N with nothing missing, and the constraints that took a
production incident to write have to still be there by name.
"""
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAUDE_MD = os.path.join(REPO_ROOT, "CLAUDE.md")

# Titles, not text: the wording is free to improve. Each of these was written
# because something went wrong, and losing one silently is the failure mode.
REQUIRED = (
    "Scheduler Architecture",
    "Feature Count Consistency",
    "Model Versioning",
    "Database Migrations",
    "CORS Configuration",
    "Rate Limiting",
    "Head Requests",
    "Frontend Port",
)


def _constraints():
    """(number, title) for the numbered items under Key Constraints."""
    text = open(CLAUDE_MD, encoding="utf-8").read()
    start = text.index("## Key Constraints")
    section = text[start:]
    end = section.find("\n## ", 1)
    if end != -1:
        section = section[:end]

    return [
        (int(number), title)
        for number, title in re.findall(r"^(\d+)\. \*\*([^*]+)\*\*", section, re.M)
    ]


def test_the_numbering_has_no_holes():
    """A deleted item shows up as a gap, whatever it said."""
    found = _constraints()

    assert found, "no numbered constraints found; the section moved or the heading changed"

    numbers = [n for n, _ in found]
    assert numbers == list(range(1, len(numbers) + 1)), (
        f"the constraint numbering is {numbers}; an item was removed or "
        f"renumbered. Compare against git rather than renumbering to fit."
    )


@pytest.mark.parametrize("title", REQUIRED)
def test_the_constraint_is_still_there(title):
    titles = [t.strip() for _, t in _constraints()]

    assert title in titles, (
        f"'{title}' is gone from CLAUDE.md. Each of these was written after "
        f"something went wrong; removing one is a decision, not an edit."
    )


def test_rate_limiting_still_says_what_it_enforces():
    """The one whose loss would be worst, pinned on content rather than title.

    Its own text records that this section once described limits as enforced
    while the middleware was a pass-through stub, and says why that is worse
    than describing none. A title alone would survive that being gutted.
    """
    text = open(CLAUDE_MD, encoding="utf-8").read()

    assert "SlowAPIMiddleware" in text
    assert "100 req/min" in text
    assert "worker count" in text, (
        "the note that the counter is per-process, so the effective budget "
        "multiplies by workers, is gone -- that is the part that stops someone "
        "reading it as a defence against a distributed flood"
    )
