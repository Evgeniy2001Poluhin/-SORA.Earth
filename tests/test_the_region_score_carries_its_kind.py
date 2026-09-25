"""A region score travels with what kind of number it is.

The aggregator decides this deliberately: `SCORE_KIND = "structural"` sits
beside the formula, with the measurement that justifies it written next to it --
"one distinct value per region against 99.9 for openmeteo temperature over the
same window". Both sources behind the required metrics are static literals in
the tree: `sber_veb_baseline` is a hardcoded dict of 85 constants, and
`rosstat` imports `data.rosstat_snapshot_2024` with no network call at all.

The label went nowhere. `score_kind` appeared only in the aggregator's own run
result -- not in `RegionESGScore`, not in the `regional_esg_snapshot` view, not
in the map API, and nowhere in `web/src`. The card showed "ESG Score / Total
58.2" and a green "Confidence 67%", and a reader had nothing to tell them the
number is an index over static 2024 data.

Two rules are pinned here:

- the provenance is **read from the modules that decide it**, never written down
  in the route. A year typed into this file, or into the interface, is a figure
  that cannot stay true;
- **every** path that builds a region payload carries it. There are three --
  the asyncpg reader, the SQLAlchemy fallback and the single-region detail --
  and a label on one of them is the shape of defect this repository keeps
  finding.
"""
import ast
import pathlib

import pytest

from app.routes.map_russia import score_provenance

MODULE = pathlib.Path(__file__).resolve().parent.parent / "app" / "routes" / "map_russia.py"


def test_the_kind_is_read_from_the_aggregator():
    """Not a literal in the route: change the aggregator, the answer changes."""
    assert score_provenance()["score_kind"] == "structural"

    import app.services.esg_aggregator as agg

    original = agg.SCORE_KIND
    try:
        agg.SCORE_KIND = "measured"
        assert score_provenance()["score_kind"] == "measured", (
            "the route reported 'structural' while the aggregator said "
            "'measured', so the label is written in the route rather than read"
        )
    finally:
        agg.SCORE_KIND = original


def test_the_vintage_is_read_from_the_ingester():
    """Not a year typed anywhere: change the snapshot's period, the answer moves."""
    from datetime import datetime, timezone

    import app.ingesters.rosstat as rosstat

    assert score_provenance()["score_vintage"] == str(rosstat.PERIOD_START.year)

    start, end = rosstat.PERIOD_START, rosstat.PERIOD_END
    try:
        rosstat.PERIOD_START = datetime(2027, 1, 1, tzinfo=timezone.utc)
        rosstat.PERIOD_END = datetime(2027, 12, 31, tzinfo=timezone.utc)
        assert score_provenance()["score_vintage"] == "2027"

        rosstat.PERIOD_END = datetime(2028, 12, 31, tzinfo=timezone.utc)
        assert score_provenance()["score_vintage"] == "2027-2028", (
            "a snapshot spanning two years must say so rather than name one"
        )
    finally:
        rosstat.PERIOD_START, rosstat.PERIOD_END = start, end


def test_the_mock_fallback_declares_itself():
    """The two hardcoded regions served when both readers come back empty.

    The API already said so in `source: "mock-esg-v1-fallback"`, and the
    interface dropped it -- `useRussiaMap` keeps `j.regions` only. So the rows
    carry their own kind, which the card reads.
    """
    from app.routes.map_russia import _MOCK

    assert _MOCK, "no mock rows to check"
    for row in _MOCK:
        assert row["score_kind"] == "mock", row
        assert row.get("score_vintage") is None, row


def _payload_dicts():
    """Every dict literal in the route that describes a region's ESG score.

    Found by shape -- a dict with an `esg` key -- rather than by a list of
    function names, so a fourth reader added later is covered without this test
    being edited.
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        if "esg" in keys:
            out.append(node)
    return out


def test_the_scan_finds_the_payload_builders():
    """Denominator. An empty list would make the rule below vacuous."""
    found = _payload_dicts()
    assert len(found) >= 3, (
        f"expected the three region payload builders (asyncpg reader, "
        f"SQLAlchemy fallback, single-region detail); found {len(found)}"
    )


def test_every_payload_builder_declares_a_score_kind():
    """By either route: spreading `score_provenance()`, or naming the key.

    The mock rows name it, and must: their kind is "mock", not the aggregator's
    "structural". The rule is that a region payload never leaves without saying
    what kind of number it carries -- not that it must come from one helper.
    """
    missing = []
    for node in _payload_dicts():
        literal_keys = {
            k.value for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
        spreads = [
            ast.unparse(value) for key, value in zip(node.keys, node.values)
            if key is None
        ]
        declares = "score_kind" in literal_keys or any(
            "score_provenance()" in s for s in spreads)
        if not declares:
            missing.append(node.lineno)

    assert not missing, (
        f"region payloads built at line(s) {missing} declare no score_kind: the "
        f"card would caption the score on some paths and not others"
    )


def test_the_sqlalchemy_path_really_returns_it(tmp_path):
    """One path exercised for real, not only read.

    The fallback is the one of the three that runs without asyncpg, so it is the
    one a unit test can drive end to end.
    """
    from datetime import datetime, timezone

    from app.database import RegionESGScore, SessionLocal
    from app.routes.map_russia import _load_from_sqlalchemy

    db = SessionLocal()
    try:
        db.query(RegionESGScore).delete()
        db.add(RegionESGScore(
            id=1,
            region_code="RU-MOW", env_score=60.0, social_score=57.1,
            gov_score=55.4, total_score=58.2, confidence=0.67,
            sources_count=2, signals_used=6,
            updated_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
        ))
        db.commit()
    finally:
        db.close()

    regions = _load_from_sqlalchemy()
    assert regions, "the fallback returned nothing, so this asserts nothing"

    row = next(r for r in regions if r.get("code") == "RU-MOW")
    assert row["score_kind"] == "structural", row
    assert row["score_vintage"], row
