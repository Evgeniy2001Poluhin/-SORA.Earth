"""Values the code produces must fit the columns they are stored in.

`source_revision` was `String(64)`. A content-hash revision is `rev:v1:` plus a
64-character sha256 -- 71 characters. Both literal ingesters were therefore
unable to write a single row on production: PostgreSQL raised
`StringDataRightTruncation` and the whole persist transaction rolled back.

Every test passed. **SQLite does not enforce `VARCHAR(n)` length**, so the
entire local and CI suite stored 71 characters in a 64-character column without
complaint, and only a real PostgreSQL write refused (#121).

That is why these assertions compare produced values against declared column
widths rather than trying to write them: the check has to hold on the engine
that does not enforce it, or it only fires where the damage already happened.
"""
import pytest

from app.database import EnvironmentalObservation
from app.ingesters import temporal


def _width(name):
    col = EnvironmentalObservation.__table__.columns[name]
    return getattr(col.type, "length", None)


def test_a_content_hash_revision_fits_source_revision():
    """The exact value the ingesters produce, against the declared width."""
    revision = temporal.content_revision("sber_veb_baseline", {"RU-MOW": 89.0})

    assert len(revision) <= _width("source_revision"), (
        f"a revision is {len(revision)} characters and the column holds "
        f"{_width('source_revision')}; PostgreSQL refuses the row and SQLite "
        f"does not, so this fails only in production"
    )


def test_a_hashed_identity_fits_source_record_id():
    """The other value derived from the same digest."""
    from datetime import datetime, timezone

    for kind, kw in [
        (temporal.NOT_APPLICABLE, {"source_revision": "rev:v1:" + "a" * 64}),
        (temporal.PERIOD, {"period_start": datetime(2024, 1, 1, tzinfo=timezone.utc),
                           "period_end": datetime(2024, 12, 31, tzinfo=timezone.utc),
                           "source_revision": "rev:v1:" + "a" * 64}),
        (temporal.OBSERVED, {"event_time": datetime(2026, 8, 13, tzinfo=timezone.utc)}),
    ]:
        identity = temporal.canonical_identity(
            source="openmeteo_air_quality", region_code="RU-MOW",
            metric="esg_index_baseline", kind=kind, **kw)

        assert len(identity) <= _width("source_record_id"), (
            f"{kind}: identity is {len(identity)} characters, column holds "
            f"{_width('source_record_id')}"
        )


@pytest.mark.parametrize("source,payload", [
    ("sber_veb_baseline", None),
    ("rosstat", None),
])
def test_every_literal_source_produces_a_revision_that_fits(source, payload):
    """Against the real snapshots, not a sample.

    A digest is fixed-length, so one example would do for the hash -- but the
    prefix is per-source and could grow, and the point of this file is that a
    value which does not fit is invisible until production.
    """
    if source == "sber_veb_baseline":
        from app.ingesters.sber_veb_baseline import BASELINE
        payload = BASELINE
    else:
        from data import rosstat_snapshot_2024 as snap
        payload = {"UNEMPLOYMENT": snap.UNEMPLOYMENT, "INCOME": snap.INCOME,
                   "LIFE_EXP": snap.LIFE_EXP,
                   "BUDGET_TRANSPARENCY": snap.BUDGET_TRANSPARENCY,
                   "DIGITAL_GOV": snap.DIGITAL_GOV}

    revision = temporal.content_revision(source, payload)

    assert len(revision) <= _width("source_revision"), (
        f"{source}: {len(revision)} > {_width('source_revision')}"
    )


def test_the_widths_are_declared_at_all():
    """A column with no length would make every assertion above vacuous.

    `None` compares as "no limit" only because the helper returns it; a String
    with no length is valid SQLAlchemy and would silently satisfy every check
    here.
    """
    for name in ("source_revision", "source_record_id"):
        assert _width(name) is not None, f"{name} declares no length"
        assert _width(name) >= 71, (
            f"{name} is {_width(name)}; a content-hash revision needs 71"
        )
