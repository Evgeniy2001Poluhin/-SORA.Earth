"""What the ingestion jobs record, pinned field by field.

Written *before* #81 moved `scheduled_openaq_ingestion` and
`scheduled_openmeteo_ingestion` onto `_run_ingestion`, and deliberately not
touched by that move. Its whole purpose is to fail if the refactor alters a row
that reaches `environmental_job_log`: a de-duplication that quietly changes what
is recorded costs more than the duplication it removes, and the change would be
invisible -- nothing reads these rows until someone is trying to explain an
outage with them.

The two jobs differ from `_run_ingestion` in more than the two metadata keys the
issue names, and every one of those differences is pinned below:

  * openaq writes **no** `regions_count`; the shared runner writes one.
  * both write their metric list as `list(set(...))`; the runner writes
    `sorted(...)`.
  * openaq carries `quality_valid_count` / `quality_invalid_count`.
  * the metric key is `parameters` for openaq and `variables` for openmeteo.
  * on an exception both **return** `{"status": "error"}`; the runner re-raises.
    That last one decides how many rows a failure writes -- one, or one per
    `@with_retry` attempt -- so it is a job-log contract, not a style question.

The five differences above describe the runner *as it was when these tests were
written*. #81 made each of them per-source configuration rather than a diverging
implementation: `describe` decides the metadata keys and the ordering of the
metric list, `result_extra` carries what only one job returns, and `reraise`
decides the exception behaviour. The contract these tests pin is unchanged --
what moved is where the difference is expressed.

`parameters` and `variables` are compared as sorted lists rather than by
identity. `list(set(...))` is not order-stable across processes -- str hashing
is seeded per interpreter -- so the *stored order* is already arbitrary today
and no test can pin it honestly. The membership is what these assertions hold
fixed; the ordering instability is reported separately rather than frozen here.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingesters.base import Signal
from app.ingesters.persist import PersistResult

FETCH = {
    "openaq": "app.ingesters.openaq.OpenAQIngester.fetch",
    "openmeteo": "app.ingesters.openmeteo.OpenMeteoIngester.fetch",
}

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def _signal(source, region, metric, quality="good"):
    # metadata is always set: Signal declares it `dict | None`, and the openaq
    # job reads `s.metadata.get("quality")` without a guard.
    return Signal(
        region_code=region, source=source, metric=metric, value=1.0,
        unit="u", observed_at=NOW, metadata={"quality": quality},
    )


def _persist(received, accepted, *, rejected=0, inserted=None, updated=0,
             duplicates=0, errors=None):
    return PersistResult(
        received=received,
        inserted=accepted if inserted is None else inserted,
        updated=updated, rejected=rejected, duplicates=duplicates,
        accepted=accepted, errors=list(errors or []),
    )


def _normalise(call):
    """job_name arrives positionally on the skip path, by keyword elsewhere."""
    kwargs = dict(call.kwargs)
    if call.args:
        kwargs["job_name"] = call.args[0]
    return kwargs


def _run(source, *, signals=(), persist_result=None, fetch_error=None,
         lock_acquired=True):
    """Run a job with every side effect captured, and return what it recorded.

    openaq is stood down by default since #57 -- its stations for the declared
    regions stopped reporting in 2017 -- so the job short-circuits before the
    shared runner unless the source is enabled. The adapter and its contract
    are deliberately kept, and this file is what keeps them honest, so it turns
    the source on rather than letting the disabled path stand in for a
    contract it does not exercise. That openaq is *not* scheduled by default
    is asserted in tests/test_openaq_stood_down.py.
    """
    import os
    from unittest.mock import patch as _patch
    from app.services.environmental import scheduler_jobs

    job = {
        "openaq": scheduler_jobs.scheduled_openaq_ingestion,
        "openmeteo": scheduler_jobs.scheduled_openmeteo_ingestion,
    }[source]

    fetch_kw = ({"side_effect": fetch_error} if fetch_error
                else {"return_value": list(signals)})

    lock = MagicMock()
    lock.acquire.return_value = lock_acquired

    with _patch.dict(os.environ, {"SORA_OPENAQ_ENABLED": "true",
                                  "OPENAQ_API_KEY": "test-key"}), \
         patch("app.locks.RedisLock", return_value=lock), \
         patch(FETCH[source], new_callable=AsyncMock, **fetch_kw), \
         patch("app.ingesters.persist.persist_environmental_observations",
               return_value=persist_result), \
         patch("app.prom_metrics.sora_environmental_ingestion_total", MagicMock()), \
         patch("app.prom_metrics.sora_environmental_ingestion_errors_total", MagicMock()), \
         patch("app.prom_metrics.sora_environmental_source_freshness_seconds", MagicMock()), \
         patch("app.prom_metrics.sora_environmental_observations_total", MagicMock()), \
         patch.object(scheduler_jobs, "_log_job_execution") as logged:
        returned = job()

    return returned, [_normalise(c) for c in logged.call_args_list], lock


def _metric_normalised(metadata, key):
    """The metadata dict with its metric list sorted -- see the module docstring."""
    out = dict(metadata)
    out[key] = sorted(out[key])
    return out


# --------------------------------------------------------------------- openaq


def test_openaq_success_row_is_exactly_these_fields():
    signals = [
        _signal("openaq", "DEU", "pm25_ugm3"),
        _signal("openaq", "FRA", "pm10_ugm3"),
    ]
    returned, logged, _ = _run(
        "openaq", signals=signals, persist_result=_persist(2, 2),
    )

    assert len(logged) == 1, "one run, one row"
    row = logged[0]

    assert row["job_name"] == "openaq_ingestion"
    assert row["status"] == "success"
    assert row["records_processed"] == 2
    assert row["records_rejected"] == 0
    assert row["error_message"] is None
    assert isinstance(row["duration_sec"], float) and row["duration_sec"] >= 0

    assert _metric_normalised(row["metadata"], "parameters") == {
        "fetched_count": 2,
        "quality_valid_count": 2,
        "quality_invalid_count": 0,
        "parameters": ["pm10_ugm3", "pm25_ugm3"],
        "persist_inserted": 2,
        "persist_updated": 0,
        "persist_duplicates": 0,
        "persist_errors": [],
    }
    assert isinstance(row["metadata"]["parameters"], list)

    assert returned == {
        "status": "success",
        "failure_reason": None,
        "signals_count": 2,
        "valid_count": 2,
        "rejected_count": 0,
        "persisted": 2,
        "duration_sec": returned["duration_sec"],
    }


def test_openaq_writes_no_regions_count():
    """The shared runner writes one; openaq never has.

    Asserted on its own because it is the difference most likely to be added by
    accident while moving this job across, and a metadata key that appears
    mid-history is exactly the kind of drift these rows are read to rule out.
    """
    _, logged, _ = _run(
        "openaq",
        signals=[_signal("openaq", "DEU", "pm25_ugm3")],
        persist_result=_persist(1, 1),
    )
    assert "regions_count" not in logged[0]["metadata"]


def test_openaq_quality_counters_split_the_fetched_batch():
    """quality_* count the fetch-time flag, not the persist outcome.

    Three fetched, one flagged invalid at fetch time, and separately two
    accepted by persist. The two accountings are independent and the row keeps
    both: `quality_invalid_count` is 1 while `records_rejected` is 1 for an
    unrelated reason.
    """
    signals = [
        _signal("openaq", "DEU", "pm25_ugm3", quality="excellent"),
        _signal("openaq", "FRA", "pm25_ugm3", quality="invalid"),
        _signal("openaq", "GBR", "pm10_ugm3", quality="good"),
    ]
    returned, logged, _ = _run(
        "openaq", signals=signals,
        persist_result=_persist(3, 2, rejected=1, errors=["signal_1: region_code required"]),
    )
    row = logged[0]

    assert row["metadata"]["fetched_count"] == 3
    assert row["metadata"]["quality_valid_count"] == 2
    assert row["metadata"]["quality_invalid_count"] == 1
    assert row["metadata"]["persist_errors"] == ["signal_1: region_code required"]

    # Partial persist -> degraded, and the reason travels with it.
    assert row["status"] == "degraded"
    assert row["error_message"] and "1 of 3" in row["error_message"]
    assert returned["valid_count"] == 2
    assert returned["rejected_count"] == 1


def test_openaq_empty_fetch_is_degraded_not_success():
    returned, logged, _ = _run("openaq", signals=[], persist_result=_persist(0, 0))
    row = logged[0]

    assert row["status"] == "degraded"
    assert row["error_message"]
    assert row["records_processed"] == 0
    assert _metric_normalised(row["metadata"], "parameters") == {
        "fetched_count": 0,
        "quality_valid_count": 0,
        "quality_invalid_count": 0,
        "parameters": [],
        "persist_inserted": 0,
        "persist_updated": 0,
        "persist_duplicates": 0,
        "persist_errors": [],
    }
    assert returned["status"] == "degraded"


def test_openaq_skip_row_when_the_lock_is_held():
    returned, logged, lock = _run("openaq", lock_acquired=False)

    assert logged == [{
        "job_name": "openaq_ingestion",
        "status": "skipped",
        "metadata": {"reason": "lock_held"},
    }]
    assert returned == {"status": "skipped", "reason": "lock_held"}
    lock.release.assert_not_called()


def test_openaq_failure_writes_one_row_and_returns_rather_than_raising():
    """The retry contract, recorded.

    `@with_retry` retries what raises. This job returns instead, so a failure
    writes exactly one row and makes one attempt. The shared runner re-raises
    and would write three. Whichever is right, the number of rows a failed run
    leaves behind is part of what this table means, so it is pinned here rather
    than changed in passing.
    """
    returned, logged, lock = _run(
        "openaq", fetch_error=RuntimeError("upstream 503"),
    )

    assert len(logged) == 1, "one attempt, not three"
    assert logged[0] == {
        "job_name": "openaq_ingestion",
        "status": "failed",
        "duration_sec": logged[0]["duration_sec"],
        "error_message": "upstream 503",
    }
    assert returned == {"status": "error", "error": "upstream 503"}
    lock.release.assert_called_once()


def test_openaq_fatal_persist_error_is_routed_through_the_failure_path():
    returned, logged, _ = _run(
        "openaq",
        signals=[_signal("openaq", "DEU", "pm25_ugm3")],
        persist_result=_persist(1, 0, errors=["persist_error: connection reset"]),
    )

    assert len(logged) == 1
    assert logged[0]["status"] == "failed"
    assert "persist_error: connection reset" in logged[0]["error_message"]
    assert "metadata" not in logged[0], "the failure row carries no metadata block"
    assert returned["status"] == "error"


# ------------------------------------------------------------------ openmeteo


def test_openmeteo_success_row_is_exactly_these_fields():
    signals = [
        _signal("openmeteo", "RU-MOW", "temperature_c"),
        _signal("openmeteo", "RU-SPE", "humidity_pct"),
        _signal("openmeteo", "RU-MOW", "humidity_pct"),
    ]
    returned, logged, _ = _run(
        "openmeteo", signals=signals, persist_result=_persist(3, 3, inserted=2, updated=1),
    )

    assert len(logged) == 1
    row = logged[0]

    assert row["job_name"] == "openmeteo_ingestion"
    assert row["status"] == "success"
    assert row["records_processed"] == 3
    assert row["records_rejected"] == 0
    assert row["error_message"] is None
    assert isinstance(row["duration_sec"], float) and row["duration_sec"] >= 0

    assert _metric_normalised(row["metadata"], "variables") == {
        "fetched_count": 3,
        "regions_count": 2,
        "variables": ["humidity_pct", "temperature_c"],
        "persist_inserted": 2,
        "persist_updated": 1,
        "persist_duplicates": 0,
        "persist_errors": [],
    }
    assert isinstance(row["metadata"]["variables"], list)

    assert returned == {
        "status": "success",
        "failure_reason": None,
        "signals_count": 3,
        "persisted": 3,
        "duration_sec": returned["duration_sec"],
    }


def test_openmeteo_carries_no_quality_counters():
    """Mirror of the openaq case: the quality hook is openaq's alone."""
    _, logged, _ = _run(
        "openmeteo",
        signals=[_signal("openmeteo", "RU-MOW", "temperature_c")],
        persist_result=_persist(1, 1),
    )
    assert "quality_valid_count" not in logged[0]["metadata"]
    assert "quality_invalid_count" not in logged[0]["metadata"]


def test_openmeteo_return_value_has_no_quality_keys():
    returned, _, _ = _run(
        "openmeteo",
        signals=[_signal("openmeteo", "RU-MOW", "temperature_c")],
        persist_result=_persist(1, 1),
    )
    assert "valid_count" not in returned
    assert "rejected_count" not in returned


def test_openmeteo_empty_fetch_is_degraded_not_success():
    returned, logged, _ = _run("openmeteo", signals=[], persist_result=_persist(0, 0))
    row = logged[0]

    assert row["status"] == "degraded"
    assert row["error_message"]
    assert _metric_normalised(row["metadata"], "variables") == {
        "fetched_count": 0,
        "regions_count": 0,
        "variables": [],
        "persist_inserted": 0,
        "persist_updated": 0,
        "persist_duplicates": 0,
        "persist_errors": [],
    }
    assert returned["status"] == "degraded"


def test_openmeteo_skip_row_when_the_lock_is_held():
    returned, logged, lock = _run("openmeteo", lock_acquired=False)

    assert logged == [{
        "job_name": "openmeteo_ingestion",
        "status": "skipped",
        "metadata": {"reason": "lock_held"},
    }]
    assert returned == {"status": "skipped", "reason": "lock_held"}
    lock.release.assert_not_called()


def test_openmeteo_failure_writes_one_row_and_returns_rather_than_raising():
    returned, logged, lock = _run(
        "openmeteo", fetch_error=RuntimeError("upstream 503"),
    )

    assert len(logged) == 1, "one attempt, not three"
    assert logged[0] == {
        "job_name": "openmeteo_ingestion",
        "status": "failed",
        "duration_sec": logged[0]["duration_sec"],
        "error_message": "upstream 503",
    }
    assert returned == {"status": "error", "error": "upstream 503"}
    lock.release.assert_called_once()


def test_openmeteo_fatal_persist_error_is_routed_through_the_failure_path():
    returned, logged, _ = _run(
        "openmeteo",
        signals=[_signal("openmeteo", "RU-MOW", "temperature_c")],
        persist_result=_persist(1, 0, errors=["persist_error: connection reset"]),
    )

    assert len(logged) == 1
    assert logged[0]["status"] == "failed"
    assert "persist_error: connection reset" in logged[0]["error_message"]
    assert returned["status"] == "error"


# ----------------------------------------------------- the shape they share

@pytest.mark.parametrize("source,metric_key", [
    ("openaq", "parameters"),
    ("openmeteo", "variables"),
])
def test_the_persist_block_is_identical_across_sources(source, metric_key):
    """Everything except the source-specific block is the same in both rows.

    This is the part #81 is de-duplicating; pinning it here means the move is
    only allowed to change which fields sit *around* it.
    """
    _, logged, _ = _run(
        source,
        signals=[_signal(source, "DEU", "m1")],
        persist_result=_persist(4, 3, rejected=1, inserted=2, updated=1,
                                duplicates=5, errors=["signal_3: value required"]),
    )
    metadata = logged[0]["metadata"]

    assert metadata["fetched_count"] == 1
    assert metadata["persist_inserted"] == 2
    assert metadata["persist_updated"] == 1
    assert metadata["persist_duplicates"] == 5
    assert metadata["persist_errors"] == ["signal_3: value required"]
    assert metric_key in metadata


@pytest.mark.parametrize("source", ["openaq", "openmeteo"])
def test_persist_errors_are_capped_at_five(source):
    errors = [f"signal_{i}: value required" for i in range(9)]
    _, logged, _ = _run(
        source,
        signals=[_signal(source, "DEU", "m1")],
        persist_result=_persist(9, 0, rejected=9, errors=errors),
    )
    assert logged[0]["metadata"]["persist_errors"] == errors[:5]
