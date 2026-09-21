"""The roadmap's ingester count must be the number of ingesters.

`docs/DEVELOPMENT_ROADMAP.md` has a table headed **Already true — do not
re-plan**, and one of its rows read:

    | EnvironmentalObservation schema | exists, six ingesters | ... |

Counted 2026-09-21 across the whole repository: **five** classes inherit
`BaseIngester`, and no sixth exists anywhere.

    app/ingesters/openaq.py                OpenAQIngester
    app/ingesters/openmeteo.py             OpenMeteoIngester
    app/ingesters/openmeteo_air_quality.py OpenMeteoAirQualityIngester
    app/ingesters/rosstat.py               RosstatIngester
    app/ingesters/sber_veb_baseline.py     SberVebBaselineIngester

The likely origin of the six is counting `BaseIngester` itself, which is
abstract and ingests nothing. Three other candidates were checked and are not
ingesters: `app/services/environmental/scheduler_jobs.py` drives the five
through `make_ingester().fetch()`, `app/services/esg_aggregator.py` only reads
`EnvironmentalObservation`, and `app/external_data.py` writes a different table
(`country_indicator_history`).

## Why this row matters more than a wrong number usually does

Every other figure in the roadmap invites a reader to check it. This table
tells them not to: its whole purpose is to stop work being re-planned that is
already done. A reader who trusts it and finds five would either hunt for a
sixth that does not exist, or -- worse -- assume a source is covered when it is
not. `docs/DEVELOPMENT_ROADMAP.md` says of these rows: *"Each row cites what
makes it true"*, and this one cited a count nothing computed.

So the number is bound to the code rather than restated: change the ingesters
and this fails until the document follows.
"""
import ast
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROADMAP = os.path.join(REPO, "docs", "DEVELOPMENT_ROADMAP.md")
INGESTER_DIR = os.path.join(REPO, "app", "ingesters")

#: The roadmap writes counts as words, so the check reads words.
_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def concrete_ingesters():
    """Every class that inherits `BaseIngester`, by file.

    Counted from the source rather than imported: importing the package pulls
    in httpx and the database layer, and a count should not need either.
    """
    found = {}
    for name in sorted(os.listdir(INGESTER_DIR)):
        if not name.endswith(".py") or name == "__init__.py":
            continue
        path = os.path.join(INGESTER_DIR, name)
        with open(path, encoding="utf-8") as handle:
            try:
                tree = ast.parse(handle.read())
            except SyntaxError:  # pragma: no cover - a broken file is louder elsewhere
                continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                bases = {ast.unparse(b) for b in node.bases}
                if "BaseIngester" in bases:
                    found[node.name] = name
    return found


def claimed_count():
    """The number the roadmap's `Already true` row states, as an integer."""
    with open(ROADMAP, encoding="utf-8") as handle:
        text = handle.read()
    match = re.search(
        r"\|\s*EnvironmentalObservation schema\s*\|\s*exists,\s*(\w+)\s+ingesters",
        text)
    assert match, (
        "docs/DEVELOPMENT_ROADMAP.md no longer has the "
        "'EnvironmentalObservation schema | exists, N ingesters' row in the "
        "form this reads; if the row was reworded, reword this check with it "
        "rather than deleting it"
    )
    word = match.group(1).lower()
    assert word in _WORDS, (
        f"the row says {word!r} ingesters, which this check cannot turn into a "
        f"number. Known words: {', '.join(sorted(_WORDS))}"
    )
    return _WORDS[word]


def test_the_parser_finds_ingesters_at_all():
    """A zero here would make the comparison below pass on an empty directory."""
    found = concrete_ingesters()
    assert len(found) >= 3, (
        f"only {len(found)} ingester(s) were found in app/ingesters; the count "
        "below would be comparing against nothing"
    )
    assert "BaseIngester" not in found, (
        "the abstract base was counted as an ingester, which is the most likely "
        "way to arrive at one more than there are"
    )


def test_the_roadmap_states_the_number_that_exists():
    found = concrete_ingesters()
    claimed = claimed_count()
    assert claimed == len(found), (
        f"docs/DEVELOPMENT_ROADMAP.md claims {claimed} ingesters; "
        f"{len(found)} inherit BaseIngester:\n  "
        + "\n  ".join(f"{cls} ({mod})" for cls, mod in sorted(found.items()))
        + "\nThis row sits under 'Already true -- do not re-plan', which is the "
        "one table a reader is told not to check."
    )


def test_the_check_would_notice_a_new_ingester():
    """The property the equality is a proxy for, stated where it can be read.

    Adding a sixth ingester without touching the document must fail, and it
    does: `claimed_count()` is fixed prose while `concrete_ingesters()` grows.
    Asserted here as an invariant rather than by mutating the tree, because a
    test that writes a file into app/ingesters to prove a point leaves debris
    when it fails halfway.
    """
    found = concrete_ingesters()
    assert claimed_count() == len(found)
    assert len(found) + 1 != claimed_count(), (
        "an extra ingester would still satisfy the document, which means the "
        "equality above is not binding"
    )
