"""What kind of time a row carries, and the identity that follows from it.

`event_time` used to mean four different things at once, and persistence filled
it with `datetime.now()` whenever a source had nothing to offer (#121). A dict
of 85 constants was therefore recorded as measured today, and so was an offline
2024 snapshot. Nothing downstream could tell those rows from a real reading.

The kinds, and the rules that go with them. This table is the contract; the
validator below executes it and the database CHECKs mirror it, so there is one
statement of the rules rather than three.

    kind                   event_time   period_start/end        source_revision
    ------------------------------------------------------------------------
    observed               NOT NULL     both NULL               optional
    period                 NULL         both NOT NULL, s <= e   NOT NULL
    not_applicable         NULL         both NULL               NOT NULL
    legacy_ingestion_time  NOT NULL     both NULL               optional

`period` carries bounds rather than a date inside the range. Recording the 2024
snapshot as 2024-01-01 would replace one false precision with another, which is
the defect this module exists to end.

`legacy_ingestion_time` is not a kind anything may write. It labels rows that
already exist -- the ones stamped with an ingestion time and stored as though it
were an observation. They are kept as evidence of what was recorded, excluded
from the canonical dataset, and never rewritten into a shape they never had. No
revision is computed for them after the fact: a content hash would assert that
someone checked the contents at the time, and nobody did.

Identity is derived from the kind, because "the same row" means something
different for each:

    observed         region + metric + event_time, in the stored format
    period           source + region + metric + period bounds + revision, hashed
    not_applicable   source + region + metric + revision, hashed

The two are deliberately not alike. `observed` keeps `{region}_{metric}_{time}`
unchanged because those identities are already in the database: hashing them
would mean a re-fetched observation no longer matches its stored row, the unique
index would not fire, and the first run after deploy would duplicate everything
it re-read. That format is ambiguous -- `_` occurs inside region codes and metric
names -- and replacing it is worth doing, as its own migration with a mapping.

The new kinds have nothing stored to stay compatible with, so they start
canonical: deterministic serialisation, hashed, namespaced by kind. What they
must not do is embed a timestamp the way the old format did, since that is why a
re-stamped constant produced a fresh identity on every run and the unique index
never fired.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Optional


OBSERVED = "observed"
PERIOD = "period"
NOT_APPLICABLE = "not_applicable"
LEGACY = "legacy_ingestion_time"

#: Everything a row may be. `LEGACY` is included because rows carry it, not
#: because an ingester may produce one -- `validate` refuses it as input.
TEMPORAL_KINDS = (OBSERVED, PERIOD, NOT_APPLICABLE, LEGACY)

#: Bumped when the identity inputs change, so old and new identities cannot be
#: mistaken for each other. It is part of the hashed payload as well as the
#: prefix: without that, two schemes could agree on a digest and disagree on
#: what it meant.
IDENTITY_SCHEMA = "v2"

#: Same idea for revisions. A content hash says nothing on its own about how the
#: content was serialised.
REVISION_SCHEMA = "v1"


class TemporalContractError(ValueError):
    """A row that cannot be described honestly is refused rather than filled in.

    A ValueError subclass because persistence already raises ValueError for a
    malformed signal, and callers that catch that keep working.
    """


def validate(kind: str, *, event_time: Optional[datetime],
             period_start: Optional[datetime], period_end: Optional[datetime],
             source_revision: Optional[str]) -> None:
    """Apply the table above. Raises `TemporalContractError` on a violation.

    Application-level so the error names the problem before an INSERT, and
    mirrored by database CHECKs so a direct write cannot route around it.
    """
    if kind not in TEMPORAL_KINDS:
        raise TemporalContractError(
            f"unknown temporal_kind {kind!r}; expected one of {', '.join(TEMPORAL_KINDS)}"
        )
    if kind == LEGACY:
        raise TemporalContractError(
            "legacy_ingestion_time labels rows that already exist and may not "
            "be written; it records that an ingestion time was once stored as "
            "an observation"
        )

    has_period = period_start is not None and period_end is not None
    half_period = (period_start is None) != (period_end is None)

    if half_period:
        raise TemporalContractError(
            "a period needs both bounds; one alone describes nothing"
        )

    if kind == OBSERVED:
        if event_time is None:
            # Deliberately not inferred as `not_applicable`. Guessing the kind
            # from a missing field is how `now` came to be written in the first
            # place; a caller with no observation time has to say so.
            raise TemporalContractError(
                "observed requires event_time; a source with no observation "
                "time must declare period or not_applicable rather than leave "
                "it empty"
            )
        if has_period:
            raise TemporalContractError("observed must not carry a period")

    elif kind == PERIOD:
        if event_time is not None:
            raise TemporalContractError(
                "period must not carry event_time; the bounds are the claim"
            )
        if not has_period:
            raise TemporalContractError("period requires both bounds")
        if period_start > period_end:
            raise TemporalContractError(
                f"period starts after it ends ({period_start} > {period_end})"
            )
        if not source_revision:
            raise TemporalContractError(
                "period requires source_revision; without it two snapshots of "
                "the same period are indistinguishable"
            )

    elif kind == NOT_APPLICABLE:
        if event_time is not None:
            raise TemporalContractError(
                "not_applicable must not carry event_time; there is no "
                "observation to date"
            )
        if has_period:
            raise TemporalContractError("not_applicable must not carry a period")
        if not source_revision:
            raise TemporalContractError(
                "not_applicable requires source_revision; it is the only thing "
                "that distinguishes one version of a constant from the next"
            )


def _canonical(payload: Any) -> bytes:
    """One serialisation, stated once.

    `sort_keys` so key order cannot change the digest, the tightest separators
    so whitespace cannot, and explicit UTF-8 so the platform default cannot.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def content_revision(source: str, payload: Any) -> str:
    """A revision that is a fact about the content, not a promise from a person.

    A hand-written `v1` fails the way version numbers always fail: someone
    edits a constant and forgets to bump it, the new value collapses onto the
    old identity, and the edit disappears with no error anywhere.

    The whole snapshot is hashed rather than each row. If one value changes, the
    other rows belong to a new version of the set even though their own numbers
    did not move -- which is what "revision of a snapshot" means.
    """
    digest = hashlib.sha256(
        _canonical({"schema": REVISION_SCHEMA, "source": source, "payload": payload})
    ).hexdigest()
    return f"rev:{REVISION_SCHEMA}:{digest}"


def canonical_identity(*, source: str, region_code: str, metric: str, kind: str,
                       event_time: Optional[datetime] = None,
                       period_start: Optional[datetime] = None,
                       period_end: Optional[datetime] = None,
                       source_revision: Optional[str] = None) -> str:
    """The value stored in `source_record_id`, which the unique index enforces.

    Built from the fields that make a row distinct *for its kind*. A static
    literal is identified by which revision it came from, so re-running the
    same snapshot collapses; a real observation is identified by when it was
    observed, so the existing behaviour is unchanged.
    """
    # `observed` keeps the identity it has always had, byte for byte. These
    # identities are already stored: hashing them would mean a re-fetched
    # observation no longer matches its row, the partial unique index would not
    # fire, and the first run after deploy would duplicate everything it re-read
    # -- the defect #121 exists to stop, reintroduced from the other side.
    if kind == OBSERVED:
        return f"{region_code}_{metric}_{event_time.isoformat()}"

    parts = {
        "schema": IDENTITY_SCHEMA,
        "source": source,
        "region": region_code,
        "metric": metric,
        "kind": kind,
    }
    if kind == PERIOD:
        parts["period_start"] = period_start.isoformat()
        parts["period_end"] = period_end.isoformat()
        parts["revision"] = source_revision
    elif kind == NOT_APPLICABLE:
        parts["revision"] = source_revision
    else:
        raise TemporalContractError(f"no identity is defined for kind {kind!r}")

    return f"obs:{IDENTITY_SCHEMA}:{hashlib.sha256(_canonical(parts)).hexdigest()}"
