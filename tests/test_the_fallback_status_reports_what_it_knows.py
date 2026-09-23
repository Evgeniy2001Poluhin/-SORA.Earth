"""The fallback branch reported two things it had not looked at (#270 again).

`get_scheduler_status()` reads the scheduler container's own status out of
Redis. When Redis is unavailable it falls back to the local scheduler, which in
the `app` container is empty by design -- `RUN_SCHEDULER=false` there. That
branch returned:

```python
{
    "running": False,
    "enabled": os.getenv("SORA_SCHEDULER", "1") == "1",
    "error": "Scheduler container unreachable or not running",
    "source": "local_fallback",
    "retrain_history_count": 0,
}
```

Two of those are asserted rather than read, in the same payload that says the
container could not be reached.

**`enabled`** comes from `SORA_SCHEDULER`, which nothing in this repository
sets: not `docker-compose.yml`, not `docker-compose.prod.yml`, not
`entrypoint.sh`, not any workflow. Its default is `"1"`, so the expression is
`True` on every deployment that exists. `web/src/features/mlops/SchedulerPanel.tsx`
renders it as a KPI reading `YES` -- a badge that cannot say `NO`. And it claims
to describe the scheduler container, which this branch has just failed to
reach.

**`retrain_history_count`** is a literal `0` while the database is reachable --
only Redis is not. The Redis branch of the same function counts it properly
through `count_physical_runs`. An operator whose Redis is down is told the
closed loop has never run.

## Why this survived #270

#270 removed a hardcoded job list from this function and pinned `running` and
`jobs`. The count was fixed in `tests/test_diagnostics_counts_runs_not_rows.py`,
whose own guard reads:

    assert status["source"] == "scheduler_container", (
        "the Redis branch must be the one under test; the fallback does not
         carry this field and the assertion below would pass vacuously")

The fallback **does** carry the field. Whoever wrote that believed it did not,
which is exactly how the literal stayed: the branch was excluded from the test
on the grounds that there was nothing there to test.

## What is asserted here

- the fallback must not state `enabled` at all, because it does not know it;
- the fallback must count the runs it can count.
"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine as _create_engine
from sqlalchemy.orm import sessionmaker as _sessionmaker

from app.database import Base, RetrainLog


def _two_physical_runs(session):
    """Four rows, two runs -- the closed loop writes training and decision."""
    for run_id in ("run-a", "run-b"):
        for job in ("train", "decide"):
            session.add(RetrainLog(
                run_id=run_id,
                job_name=job,
                status="success",
                started_at=datetime.utcnow(),
            ))
    session.commit()


@pytest.fixture()
def fallback_status(tmp_path, monkeypatch):
    """Drive the fallback: Redis unavailable, local scheduler empty.

    A file database rather than `:memory:`, because the function opens its own
    `SessionLocal` and an in-memory SQLite hands each connection its own empty
    schema -- the rows seeded here would not be there to count.
    """
    import types

    import app.database as database
    from app import scheduler as scheduler_module

    engine = _create_engine(f"sqlite:///{tmp_path/'fallback.db'}")
    Base.metadata.create_all(engine)
    Session = _sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", Session)

    seed = Session()
    _two_physical_runs(seed)
    assert seed.query(RetrainLog).count() == 4, "the seed did not land"
    seed.close()

    monkeypatch.setattr("app.redis_cache.REDIS_AVAILABLE", False, raising=False)
    monkeypatch.setattr(scheduler_module, "scheduler",
                        types.SimpleNamespace(get_jobs=lambda: [], running=False))

    return scheduler_module.get_scheduler_status()


def test_the_fallback_branch_is_the_one_under_test(fallback_status):
    """Without this, both checks below could be passing on the Redis branch."""
    assert fallback_status["source"] == "local_fallback", (
        f"expected the fallback, got source={fallback_status.get('source')!r}; "
        "the assertions below would be about a different code path"
    )
    assert fallback_status["running"] is False
    assert "error" in fallback_status, (
        "the fallback is supposed to say it could not reach the container"
    )


def test_it_does_not_claim_to_know_whether_the_scheduler_is_enabled(fallback_status):
    """It has just failed to reach the process it would be describing."""
    assert fallback_status.get("enabled") is None, (
        "the fallback reports enabled="
        f"{fallback_status.get('enabled')!r} in the same payload that says "
        "'Scheduler container unreachable or not running'. It reads SORA_SCHEDULER, "
        "which nothing in this repository sets, defaulting to '1' -- so the value "
        "is True on every deployment and the UI badge cannot say NO."
    )


def test_it_counts_the_runs_it_can_count(fallback_status):
    """Redis is down; the database is not."""
    assert fallback_status["retrain_history_count"] == 2, (
        "four rows, two physical runs, and the fallback reported "
        f"{fallback_status['retrain_history_count']}. The Redis branch of the "
        "same function counts this through count_physical_runs; only Redis is "
        "unavailable here, so an operator is told the closed loop never ran."
    )


def test_nothing_sets_the_variable_the_old_field_read():
    """The premise of this whole file, checked rather than asserted in prose."""
    import os
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    surface = [
        root / "docker-compose.yml",
        root / "docker-compose.prod.yml",
        root / "docker-compose.override.yml",
        root / "entrypoint.sh",
    ]
    surface += sorted((root / ".github" / "workflows").glob("*.yml"))
    present = [p.name for p in surface
               if p.is_file() and "SORA_SCHEDULER" in p.read_text(encoding="utf-8")]
    assert not present, (
        "SORA_SCHEDULER is set in " + ", ".join(present) + ". If it is a real "
        "switch now, the fallback may report it again -- but then it has to "
        "describe the process that was asked, not the one answering."
    )
    assert os.getenv("SORA_SCHEDULER") is None or True  # environment is not the claim
