"""Silver layer: aggregate environmental_observations -> region_esg_scores.

This read `region_signals` until #116. That table took its last row on
2026-07-30, when the ingester runner moved to `persist_environmental_observations`
(`app/ingesters/runner.py:160`), and nothing noticed: the aggregator kept
recomputing the same 85 scores from frozen data and returning `{"status": "ok"}`
after every run.

The formula is unchanged. Both tables carry the same six `source:indicator`
pairs for the same 85 regions, so this moves where the numbers come from, not
what is computed from them.
"""
from __future__ import annotations

import logging
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import case, and_, func, or_, select
from sqlalchemy.orm.attributes import flag_modified

from app.ingesters import temporal
from app.database import SessionLocal, EnvironmentalObservation, RegionESGScore

log = logging.getLogger(__name__)


# The (source, indicator) pairs _compute_one reads, declared rather than left
# implicit in the formula. A source that stops publishing then shows up as a
# key missing from a named set, instead of as a value that is quietly None.
#: What kind of time each source is expected to carry, by name. A source
#: defines its own temporal semantics: rosstat publishes a yearly snapshot, so
#: its rows are a period; sber_veb_baseline is a table of constants with no
#: observation date at all, so its rows are not_applicable.
#:
#: Stated per source rather than as one global ordering rule. A global rule
#: would have to decide whether an observation "beats" a period, which is a
#: question nobody asked and which has no answer -- they are claims of
#: different kinds, not the same claim at different times.
#:
#: A row whose kind is not the expected one is a data-contract violation. It is
#: reported as degraded rather than ranked, because ranking it would mean
#: choosing between two incompatible statements about the same metric.
EXPECTED_TEMPORAL_KIND = {
    "sber_veb_baseline": temporal.NOT_APPLICABLE,
    "rosstat": temporal.PERIOD,
}

REQUIRED_METRICS = (
    ("sber_veb_baseline", "esg_index_baseline"),
    ("rosstat", "unemployment_rate"),
    ("rosstat", "avg_income_rub"),
    ("rosstat", "life_expectancy"),
    ("rosstat", "budget_transparency"),
    ("rosstat", "digital_gov_index"),
)

# Air quality is deliberately absent, and this is the third time the platform
# has had to decide it explicitly (M1 withdrew `air_quality` and `carbon_price`
# from the forecasting regressors for the same reason).
#
# The formula used to read `openaq:pm25_ugm3`. openaq has never written a row
# -- 0 in every one of its 62 recorded runs (#117) -- so that term has been
# None for every score the platform has produced, and `env_score` has been the
# sber_veb baseline for all 85 regions.
#
# `openmeteo_air_quality:pm2_5` *is* live and current. It is not wired in here
# because it covers 21 regions, of which only RU-MOW and RU-SPE appear in this
# table: `env_score` would then mean a measured air quality for 2 regions and a
# baseline index for the other 83, under one column name. Which regions the
# platform commits to scoring is the declaration in #114, not a decision to
# make in passing here.

# The score this file produces is a **structural cross-section**, not a
# reading of anything that changes over time, and it says so in its own output
# rather than leaving that to be inferred.
#
# Both sources behind REQUIRED_METRICS are static literals in the source tree:
# `sber_veb_baseline` is a hardcoded dict of 85 constants with no network call,
# and `rosstat` makes no network call either, importing
# `data.rosstat_snapshot_2024` -- an offline 2024 snapshot refreshed roughly
# half-yearly, per its own docstring. Measured over the 9 days held in
# `environmental_observations`, every one of the six metrics has exactly **one
# distinct value per region**, against 99.9 for openmeteo temperature over the
# same window.
#
# So the score is real between regions and constant within one. It is an index,
# and #114 established that no forecasting target can be built from it.
SCORE_KIND = "structural"


# The regions this index is defined over.
#
# Declared, not derived from whatever the table happens to hold: a set that
# follows the data cannot notice a region going missing, which is one of the
# two failures the completeness contract below exists to catch. Both sources
# cover exactly these 85 -- a test asserts that, so a divergence becomes a
# decision someone makes rather than a silent change in what "85 regions"
# means.
DECLARED_REGIONS = frozenset({
    "RU-AD", "RU-AL", "RU-ALT", "RU-AMU", "RU-ARK", "RU-AST", "RU-BA",
    "RU-BEL", "RU-BRY", "RU-BU", "RU-CE", "RU-CHE", "RU-CHU", "RU-CR",
    "RU-CU", "RU-DA", "RU-IN", "RU-IRK", "RU-IVA", "RU-KAM", "RU-KB",
    "RU-KC", "RU-KDA", "RU-KEM", "RU-KGD", "RU-KGN", "RU-KHA", "RU-KHM",
    "RU-KIR", "RU-KK", "RU-KL", "RU-KLU", "RU-KO", "RU-KOS", "RU-KR",
    "RU-KRS", "RU-KYA", "RU-LEN", "RU-LIP", "RU-MAG", "RU-ME", "RU-MO",
    "RU-MOS", "RU-MOW", "RU-MUR", "RU-NEN", "RU-NGR", "RU-NIZ", "RU-NVS",
    "RU-OMS", "RU-ORE", "RU-ORL", "RU-PER", "RU-PNZ", "RU-PRI", "RU-PSK",
    "RU-ROS", "RU-RYA", "RU-SA", "RU-SAK", "RU-SAM", "RU-SAR", "RU-SE",
    "RU-SEV", "RU-SMO", "RU-SPE", "RU-STA", "RU-SVE", "RU-TA", "RU-TAM",
    "RU-TOM", "RU-TUL", "RU-TVE", "RU-TY", "RU-TYU", "RU-UD", "RU-ULY",
    "RU-VGG", "RU-VLA", "RU-VLG", "RU-VOR", "RU-YAN", "RU-YAR", "RU-YEV",
    "RU-ZAB",
})


# How long ingestion may be silent before the run is degraded.
#
# This measures `ingested_at`: **whether the pipeline is running**, which is a
# real and previously invisible failure -- the aggregator read a table dead for
# eight days and reported ok (#116). It is deliberately *not* called freshness
# of the data, and it is not computed from `event_time`, because for these
# sources a current `event_time` means only that a constant was re-stamped.
_MAX_INGEST_AGE_ENV = "SORA_AGGREGATOR_MAX_INGEST_AGE_HOURS"
_DEFAULT_MAX_INGEST_AGE_HOURS = 48.0


class AggregatorConfigError(ValueError):
    """The aggregator's environment configuration cannot be used."""


def _parse_max_ingest_age_hours(raw: Optional[str] = None) -> float:
    """Read and validate the ingestion-silence bound.

    Called per run rather than at import. A typo in a production environment
    variable used to raise inside `float()` while this module was being
    imported, which takes down the backend and the scheduler at startup -- a
    configuration mistake becoming an outage, in two services, for a value
    consulted once per aggregation.

    It is still fail-fast: an unusable value raises rather than falling back to
    the default. Silently substituting 48h would mean a deployment believing it
    had set a bound it had not, and this is a production policy rather than a
    convenience setting. The difference is only in blast radius -- the run
    fails, loudly, and the process keeps serving.

    Absent or empty means "not configured" and takes the default. Zero and
    negative are rejected: a non-positive bound marks every run stalled, which
    would make the signal permanent and therefore worthless.
    """
    value = os.getenv(_MAX_INGEST_AGE_ENV) if raw is None else raw
    if value is None or not value.strip():
        return _DEFAULT_MAX_INGEST_AGE_HOURS

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise AggregatorConfigError(
            f"{_MAX_INGEST_AGE_ENV}={value!r} is not a number"
        ) from None

    if not math.isfinite(parsed):
        raise AggregatorConfigError(
            f"{_MAX_INGEST_AGE_ENV}={value!r} is not finite"
        )
    if parsed <= 0:
        raise AggregatorConfigError(
            f"{_MAX_INGEST_AGE_ENV}={value!r} must be greater than zero; "
            f"a non-positive bound reports every run as stalled"
        )
    return parsed


def _clip(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def _norm(v, lo, hi):
    if v is None:
        return None
    return _clip(100 * (v - lo) / (hi - lo))


def _latest_by_region(db):
    """Newest valid observation per (region, source, indicator).

    Returns (metrics_by_region, newest_ingested_at, rows_examined).

    `ingested_at` rather than `event_time`, deliberately: the caller uses it to
    ask whether ingestion is still running, and `event_time` cannot answer that
    for a source that re-stamps a literal with `now`.

    `row_number()` rather than DISTINCT ON, matching app/services/point_in_time.py:
    production is PostgreSQL but the test suite runs on SQLite, and a read path
    that changes shape between them is worse than a longer query.
    """
    ranked = (
        select(
            EnvironmentalObservation.region_id,
            EnvironmentalObservation.source,
            EnvironmentalObservation.indicator,
            EnvironmentalObservation.value,
            EnvironmentalObservation.ingested_at,
            EnvironmentalObservation.temporal_kind,
            EnvironmentalObservation.period_start,
            EnvironmentalObservation.period_end,
            EnvironmentalObservation.source_revision,
            func.row_number()
            .over(
                partition_by=(
                    EnvironmentalObservation.region_id,
                    EnvironmentalObservation.source,
                    EnvironmentalObservation.indicator,
                ),
                # Ranked within one kind, never across kinds. The rows in
                # a partition all share the expected kind because the filter
                # below admits nothing else, so this key is unambiguous.
                #
                # `event_time DESC` alone was wrong once event_time became
                # nullable: PostgreSQL puts NULLs first on DESC, so a
                # not_applicable row would have outranked a real observation.
                # SQLite orders them last, so a test here would have been
                # green while production picked the wrong row.
                order_by=(
                    _ordering_key().desc(),
                    EnvironmentalObservation.ingested_at.desc(),
                    EnvironmentalObservation.id.desc(),
                ),
            )
            .label("rn"),
        )
        .where(
            EnvironmentalObservation.is_valid.is_(True),
            EnvironmentalObservation.value.isnot(None),
            # An or_ of and_ pairs rather than tuple_(...).in_(...): row-value
            # IN is a place PostgreSQL and SQLite differ, and the test suite
            # runs on SQLite while production is PostgreSQL.
            or_(
                *[
                    and_(
                        EnvironmentalObservation.source == src,
                        EnvironmentalObservation.indicator == ind,
                        # Only the kind this source is supposed to publish.
                        # legacy_ingestion_time is excluded by construction:
                        # it labels rows whose timestamp was an ingestion time
                        # stored as an observation, and they are evidence of
                        # what was recorded, not input to a score.
                        EnvironmentalObservation.temporal_kind
                        == EXPECTED_TEMPORAL_KIND[src],
                    )
                    for src, ind in REQUIRED_METRICS
                ]
            ),
        )
        .subquery()
    )

    result = defaultdict(dict)
    newest_ingest = None
    examined = 0

    vintage: dict[str, str] = {}

    for (region_id, source, indicator, value, ingested_at, kind,
         period_start, period_end, source_revision, _rn) in db.execute(
        select(ranked).where(ranked.c.rn == 1)
    ):
        examined += 1
        # Read off the row rather than declared in this module. A hardcoded
        # mapping was the workaround while the database could not express a
        # vintage: it said "2024 offline snapshot" because someone wrote that
        # here, not because any row claimed it. Now the row does.
        vintage.setdefault(source, _vintage_of(kind, period_start, period_end,
                                               source_revision))
        # `ingested_at` is carried per entry, not only as a global maximum:
        # the contract is checked per (region, source, indicator) pair, and a
        # maximum over all rows cannot see one pair that stopped.
        result[region_id][f"{source}:{indicator}"] = (value, source, ingested_at)
        if ingested_at is not None and (
            newest_ingest is None or ingested_at > newest_ingest
        ):
            newest_ingest = ingested_at

    return result, newest_ingest, examined, vintage


def _get(metrics, key):
    t = metrics.get(key)
    return t[0] if t else None


REQUIRED_METRIC_KEYS = tuple(f"{src}:{ind}" for src, ind in REQUIRED_METRICS)


def _missing_metrics(metrics) -> tuple:
    """Which of the six required metrics this region does not have."""
    return tuple(k for k in REQUIRED_METRIC_KEYS if metrics.get(k) is None)


def _ordering_key():
    """Which column means "most recent" for each kind.

    observed        event_time    -- when it was measured
    period          period_end    -- the end of the interval it describes
    not_applicable  ingested_at   -- nothing else is available; a constant has
                                     no date, so the only ordering left is when
                                     this platform last read it

    A `case` rather than `coalesce`, so the choice is stated per kind instead of
    falling through whichever column happens to be non-null.
    """
    return case(
        (EnvironmentalObservation.temporal_kind == temporal.OBSERVED,
         EnvironmentalObservation.event_time),
        (EnvironmentalObservation.temporal_kind == temporal.PERIOD,
         EnvironmentalObservation.period_end),
        else_=EnvironmentalObservation.ingested_at,
    )


def _vintage_of(kind, period_start, period_end, source_revision):
    """What the row itself says about how old its information is.

    Each kind answers differently, and none of them is `ingested_at`: when the
    platform last read a number says nothing about when the number was true.

        period          the interval it describes
        not_applicable  the revision of the constant, which is all there is
        observed        the row is as old as its observation

    Returned as text because this goes into a report a human reads. The
    machine-readable form is the row.
    """
    if kind == temporal.PERIOD and period_start and period_end:
        return f"period {period_start.date()}..{period_end.date()} ({source_revision})"
    if kind == temporal.NOT_APPLICABLE:
        return f"no observation date; snapshot {source_revision}"
    return "observed"


def _classify_pairs(metrics, limit_hours):
    """Sort one region's required pairs into missing, stale and fresh.

    Checked per pair rather than against a single newest-row timestamp, because
    a maximum over all rows only proves that *something* is being written. With
    509 pairs updating daily and one silent for a week, the region still looks
    complete, the global timestamp still looks fresh, the week-old value still
    enters the score, and the run still reports success. The same holds if
    rosstat stops entirely while sber_veb_baseline keeps writing its baseline
    every day.

    A pair with no `ingested_at` is counted missing rather than fresh: it
    cannot be shown to have arrived within the bound, and treating unknown as
    acceptable is how the original defect worked.

    This is still pipeline freshness -- age is measured on `ingested_at`.
    Vintage of the information is a separate field entirely, read off the
    selected rows: a period for rosstat, a snapshot revision for the constants.
    A pipeline that ran an hour ago can be delivering numbers from 2024, and
    both facts have to be reportable at once.
    """
    missing, stale, fresh = [], [], []
    for key in REQUIRED_METRIC_KEYS:
        entry = metrics.get(key)
        if entry is None:
            missing.append(key)
            continue
        ingested_at = entry[2] if len(entry) > 2 else None
        if ingested_at is None:
            missing.append(key)
            continue
        age = _age_hours(ingested_at)
        if age is not None and age > limit_hours:
            stale.append(key)
        else:
            fresh.append(key)
    return missing, stale, fresh


def _compute_one(metrics):
    """Compute one region's index. Requires the complete metric set.

    The `is not None` branches below are defensive, not permissive: a region
    missing any required metric is refused here and never reaches them. Before
    that gate existed, a region carrying only `sber_veb_baseline` produced
    `env = soc = gov = base`, a full-looking score from one input, and a run
    reporting success -- indistinguishable in the output from a region with all
    six. That is what makes REQUIRED_METRICS required rather than merely
    allowed.
    """
    if _missing_metrics(metrics):
        return {}

    base = _get(metrics, "sber_veb_baseline:esg_index_baseline")

    unemp = _get(metrics, "rosstat:unemployment_rate")
    inc = _get(metrics, "rosstat:avg_income_rub")
    life = _get(metrics, "rosstat:life_expectancy")
    s_parts = []
    if unemp is not None:
        s_parts.append(_clip(100 - unemp * 4))
    if inc is not None:
        s_parts.append(_norm(inc, 20000, 130000))
    if life is not None:
        s_parts.append(_norm(life, 65, 82))

    budget = _get(metrics, "rosstat:budget_transparency")
    digital = _get(metrics, "rosstat:digital_gov_index")
    g_parts = [p for p in (budget, digital) if p is not None]

    # No air-quality term reaches this: see the note beside REQUIRED_METRICS.
    env = base
    soc = (sum(s_parts) / len(s_parts)) if s_parts else base
    gov = (sum(g_parts) / len(g_parts)) if g_parts else base

    if env is None and soc is None and gov is None:
        return {}

    env_v = env if env is not None else base or 0
    soc_v = soc if soc is not None else base or 0
    gov_v = gov if gov is not None else base or 0

    total = env_v * 0.4 + soc_v * 0.35 + gov_v * 0.25

    # entry is (value, source, ingested_at); index rather than unpack so a
    # further field does not break this the way widening to three did.
    sources = {entry[1] for entry in metrics.values()}
    confidence = min(1.0, len(sources) / 3.0)

    return {
        "env_score": round(env, 2) if env is not None else None,
        "social_score": round(soc, 2) if soc is not None else None,
        "gov_score": round(gov, 2) if gov is not None else None,
        "total_score": round(total, 2),
        "confidence": round(confidence, 2),
        "sources_count": len(sources),
        "signals_used": len(metrics),
    }


def _stale_reason(missing, stale) -> str:
    parts = []
    if missing:
        parts.append(f"missing {len(missing)}: {', '.join(missing[:3])}")
    if stale:
        parts.append(f"stale {len(stale)}: {', '.join(stale[:3])}")
    return "; ".join(parts) or "unbacked"


def _mark_stale(db, region_code, missing, stale):
    """Record on the row itself that its score is no longer backed.

    The score is deliberately not overwritten -- recomputing from a partial set
    would replace a complete old value with an incomplete new one. But leaving
    the row untouched makes it indistinguishable from a current score to
    anything reading `region_esg_scores` directly, which `app/routes/map_russia.py`
    does. That is #116 at the consumer boundary rather than the producer's.

    `stale_since` is set once and preserved, so it answers "since when" rather
    than "as of this run". `updated_at` is left alone: it means "when the score
    last changed", and marking staleness does not change a score.
    """
    row = db.query(RegionESGScore).filter_by(region_code=region_code).first()
    if row is None:
        # Never written, so there is nothing that could be misread.
        return
    reason = _stale_reason(missing, stale)
    previous = row.updated_at
    if row.stale_since is None:
        row.stale_since = datetime.now(timezone.utc)
    row.stale_reason = reason
    # `onupdate=func.now()` fires whenever an UPDATE is emitted for this row,
    # and marking staleness emits one. Assigning `previous` is not enough --
    # an assignment of an equal value leaves the attribute clean, so the
    # column stays out of the SET clause and onupdate wins. flag_modified
    # forces it in, carrying the old value, so `updated_at` keeps meaning
    # "when the score changed" rather than "when the row was touched".
    row.updated_at = previous
    flag_modified(row, "updated_at")


def _clear_stale(row):
    """The region is backed again; the marks must not outlive the condition."""
    if row.stale_since is not None or row.stale_reason is not None:
        row.stale_since = None
        row.stale_reason = None
        return True
    return False


def _age_hours(newest):
    if newest is None:
        return None
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - newest).total_seconds() / 3600.0


def recalc_all_regions(max_ingest_age_hours: float | None = None):
    """Recompute every region's structural ESG index from the declared metrics.

    The report separates two things that a single "freshness" number ran
    together, and conflating them is what let an eight-day staleness pass
    unnoticed (#116):

    `pipeline_freshness` -- is ingestion still running? Measured on
    `ingested_at`. A stalled pipeline is a real failure and degrades the run.

    `source_data_vintage` -- how old is the *information*? Declared per source,
    because neither source reports it and both re-stamp their literals with
    `now`. No timestamp in the database can answer this, which is exactly why
    it is stated rather than measured.

    A fresh pipeline carrying 2024 numbers is the normal state here, not an
    anomaly, and the output must not read as though the values were observed
    today.

    `regions_written` counts rows the database actually changed. **Zero is the
    expected result for a structural index** whose inputs are static: the
    recomputed values are identical, SQLAlchemy emits no UPDATE, and
    `onupdate=func.now()` never fires. That is a property of the sources, not
    a fault in this job, so it is never treated as degraded.

    The completeness contract is strict. The loop walks **DECLARED_REGIONS**,
    not the regions the table happens to contain, so a region that vanishes
    from the source is visible as absent rather than simply unmentioned. A
    region missing any required metric is **not written**, because a partial
    score is indistinguishable in the output from a complete one, and the run
    is degraded.
    """
    db = SessionLocal()
    try:
        limit = (
            _parse_max_ingest_age_hours() if max_ingest_age_hours is None
            else max_ingest_age_hours
        )
        latest, newest_ingest, examined, vintage = _latest_by_region(db)

        computed = 0
        written = 0
        recovered = 0
        absent = []
        incomplete = {}
        pairs_missing = 0
        pairs_stale = 0
        pairs_fresh = 0

        for region_code in sorted(DECLARED_REGIONS):
            metrics = latest.get(region_code) or {}
            missing, stale, fresh = _classify_pairs(metrics, limit)
            pairs_missing += len(missing)
            pairs_stale += len(stale)
            pairs_fresh += len(fresh)

            if not metrics:
                absent.append(region_code)
                _mark_stale(db, region_code, missing, stale)
                continue
            if missing or stale:
                detail = {}
                if missing:
                    detail["missing"] = missing
                if stale:
                    detail["stale"] = stale
                incomplete[region_code] = detail
                _mark_stale(db, region_code, missing, stale)
                continue

            scores = _compute_one(metrics)
            if not scores:
                # Unreachable while _compute_one's only refusal is the
                # completeness gate already applied above; kept so a future
                # refusal cannot be silently counted as a computed region.
                incomplete[region_code] = {"refused_by_formula": True}
                continue

            computed += 1
            row = db.query(RegionESGScore).filter_by(region_code=region_code).first()
            if row:
                cleared = _clear_stale(row)
                if any(getattr(row, k) != v for k, v in scores.items()):
                    for k, v in scores.items():
                        setattr(row, k, v)
                    written += 1
                elif cleared:
                    # The score is unchanged but the row is: it stopped being
                    # marked stale, and that is a change a reader acts on.
                    recovered += 1
            else:
                db.add(RegionESGScore(region_code=region_code, **scores))
                written += 1
        db.commit()

        undeclared = sorted(set(latest) - DECLARED_REGIONS)
        ingest_age = _age_hours(newest_ingest)
        stalled = ingest_age is not None and ingest_age > limit

        reasons = []
        if examined == 0:
            reasons.append("no valid observations for any required metric")
        elif computed == 0:
            # Rows were read and no score came out: the same shape of defect
            # this file was fixed for, one layer in.
            reasons.append(
                f"read {examined} observations and computed 0 regions"
            )
        if stalled:
            reasons.append(
                f"ingestion silent for {ingest_age:.1f}h, limit {limit:.0f}h"
            )
        if pairs_stale:
            reasons.append(
                f"{pairs_stale} required pairs not ingested within {limit:.0f}h"
            )
        if pairs_missing:
            reasons.append(f"{pairs_missing} required pairs missing")
        if absent:
            reasons.append(f"{len(absent)} declared regions absent from the source")
        if incomplete:
            reasons.append(
                f"{len(incomplete)} declared regions missing required metrics"
            )

        result = {
            "status": "degraded" if reasons else "success",
            "score_kind": SCORE_KIND,
            "regions_declared": len(DECLARED_REGIONS),
            "regions_complete": computed,
            "regions_computed": computed,
            "regions_written": written,
            # Rows whose score was unchanged but which stopped being marked
            # stale -- a change a reader acts on, invisible in regions_written.
            "regions_unmarked_stale": recovered,
            "regions_absent": len(absent),
            "regions_incomplete": len(incomplete),
            "regions_undeclared": len(undeclared),
            "observations_examined": examined,
            # The contract, checked per (region, source, indicator). The
            # denominator is the declared set times the required metrics, so
            # it does not move with what the table happens to hold and an
            # undeclared region cannot pad it.
            "required_pairs_total": len(DECLARED_REGIONS) * len(REQUIRED_METRIC_KEYS),
            "required_pairs_fresh": pairs_fresh,
            "required_pairs_stale": pairs_stale,
            "required_pairs_missing": pairs_missing,
            # Diagnostic only. A maximum over all rows proves that something is
            # being written, never that every required pair is -- which is what
            # `required_pairs_stale` is for.
            "pipeline_freshness": {
                "newest_ingested_at": (
                    newest_ingest.isoformat() if newest_ingest else None
                ),
                "age_hours": round(ingest_age, 1) if ingest_age is not None else None,
                "stalled": stalled,
            },
            # Read off the selected rows. Until #121 this was a mapping
            # declared in this module, because no column could hold a vintage
            # and `event_time` said "today" for numbers from 2024. The rows now
            # carry it -- a period for rosstat, a snapshot revision for the
            # constants -- so the mapping is deleted rather than kept as a
            # second answer to the same question.
            "source_data_vintage": vintage,
            "vintage_provenance": "derived_from_observation_rows",
        }
        if absent:
            result["absent_regions"] = absent[:20]
        if incomplete:
            result["incomplete_regions"] = dict(sorted(incomplete.items())[:20])
        if undeclared:
            result["undeclared_regions"] = undeclared[:20]

        if reasons:
            result["reason"] = "; ".join(reasons)
            # Loud, because this is the case that used to be indistinguishable
            # from working.
            log.warning(
                "[esg_aggregator] degraded: %s (declared=%d computed=%d "
                "written=%d examined=%d absent=%d incomplete=%d "
                "pairs fresh=%d stale=%d missing=%d)",
                result["reason"], len(DECLARED_REGIONS), computed, written,
                examined, len(absent), len(incomplete),
                pairs_fresh, pairs_stale, pairs_missing,
            )
        else:
            log.info(
                "[esg_aggregator] %s index: %d/%d regions computed, %d changed, "
                "%d observations; vintage %s",
                SCORE_KIND, computed, len(DECLARED_REGIONS), written, examined,
                ", ".join(f"{k}={v}" for k, v in vintage.items()),
            )
        return result
    except AggregatorConfigError as e:
        db.rollback()
        log.error("[esg_aggregator] configuration error: %s", e)
        return {"status": "error", "error_kind": "configuration", "error": str(e)}
    except Exception as e:
        db.rollback()
        log.exception("[esg_aggregator] failed: %s", e)
        return {"status": "error", "error": str(e)}
    finally:
        db.close()
