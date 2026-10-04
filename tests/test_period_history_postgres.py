"""Period corrections are append-only and reconstructible (issue #164)."""

from datetime import datetime
import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.services.point_in_time import series_as_of
from tests.postgres_scratch import (  # noqa: F401
    REPO_ROOT,
    requires_postgres,
    scratch_db,
)

pytestmark = requires_postgres

CODE = "NY.GDP.MKTP.KD.ZG"


@pytest.fixture
def db(scratch_db):
    engine, _ = scratch_db
    with Session(engine) as session:
        yield session


def _clock(db):
    return db.execute(
        text("SELECT clock_timestamp() AT TIME ZONE 'UTC'")
    ).scalar_one()


def _insert(db, *, period=None, status=None):
    row_id = db.execute(
        text(
            "INSERT INTO country_indicator_history "
            "(country_iso3, indicator_code, source, value, as_of_date, "
            " fetched_at, period_status) "
            "VALUES ('RUS', :code, 'world_bank', 4.92, :period, "
            "        '2026-01-01', :status) RETURNING id"
        ),
        {"code": CODE, "period": period, "status": status},
    ).scalar_one()
    db.commit()
    return row_id


def _correct(db, row_id, *, period, status, rule="value-match/2"):
    db.execute(
        text(
            "UPDATE country_indicator_history "
            "SET as_of_date = :period, period_status = :status, "
            "    period_run_id = 'test-run', period_method = 'value_match', "
            "    period_rule_version = :rule, period_candidates = 1, "
            "    period_source_vintage = '2026-01-01', "
            "    period_response_sha256 = 'abc123', "
            "    period_resolved_at = clock_timestamp() "
            "WHERE id = :row_id"
        ),
        {
            "period": period,
            "status": status,
            "rule": rule,
            "row_id": row_id,
        },
    )
    db.commit()


def _alembic(url, *args):
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "DATABASE_URL": url.render_as_string(hide_password=False),
        },
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_first_period_assignment_is_recorded_and_reconstructed(db):
    row_id = _insert(db)
    before = _clock(db)

    _correct(
        db,
        row_id,
        period=datetime(2024, 1, 1),
        status="recovered_inferred",
    )
    after = _clock(db)

    history = db.execute(
        text(
            "SELECT old_as_of_date, new_as_of_date, old_period_status, "
            "       new_period_status, database_actor "
            "FROM country_indicator_period_history "
            "WHERE history_row_id = :row_id"
        ),
        {"row_id": row_id},
    ).one()
    assert history.old_as_of_date is None
    assert history.new_as_of_date == datetime(2024, 1, 1)
    assert history.old_period_status is None
    assert history.new_period_status == "recovered_inferred"
    assert history.database_actor

    assert series_as_of(db, "RUS", CODE, before) == {}
    assert series_as_of(db, "RUS", CODE, after) == {
        datetime(2024, 1, 1): pytest.approx(4.92)
    }


def test_repeated_corrections_reconstruct_each_state(db):
    row_id = _insert(
        db,
        period=datetime(2023, 1, 1),
        status="recovered_inferred",
    )
    before_first = _clock(db)
    _correct(
        db,
        row_id,
        period=datetime(2024, 1, 1),
        status="recovered_inferred",
        rule="value-match/2",
    )
    between = _clock(db)
    _correct(
        db,
        row_id,
        period=None,
        status="ambiguous",
        rule="value-match/3",
    )
    after_second = _clock(db)

    assert series_as_of(db, "RUS", CODE, before_first) == {
        datetime(2023, 1, 1): pytest.approx(4.92)
    }
    assert series_as_of(db, "RUS", CODE, between) == {
        datetime(2024, 1, 1): pytest.approx(4.92)
    }
    assert series_as_of(db, "RUS", CODE, after_second) == {}

    transitions = db.execute(
        text(
            "SELECT old_as_of_date, new_as_of_date "
            "FROM country_indicator_period_history "
            "WHERE history_row_id = :row_id ORDER BY changed_at, id"
        ),
        {"row_id": row_id},
    ).all()
    assert transitions == [
        (datetime(2023, 1, 1), datetime(2024, 1, 1)),
        (datetime(2024, 1, 1), None),
    ]


def test_correction_and_audit_row_roll_back_together(db):
    row_id = _insert(db)

    db.execute(
        text(
            "UPDATE country_indicator_history "
            "SET as_of_date = '2024-01-01', "
            "    period_status = 'recovered_inferred' "
            "WHERE id = :row_id"
        ),
        {"row_id": row_id},
    )
    db.rollback()

    assert db.execute(
        text(
            "SELECT as_of_date FROM country_indicator_history WHERE id = :row_id"
        ),
        {"row_id": row_id},
    ).scalar_one() is None
    assert db.execute(
        text(
            "SELECT count(*) FROM country_indicator_period_history "
            "WHERE history_row_id = :row_id"
        ),
        {"row_id": row_id},
    ).scalar_one() == 0


@pytest.mark.parametrize("statement", [
    "UPDATE country_indicator_period_history SET new_period_status = 'ambiguous'",
    "DELETE FROM country_indicator_period_history",
    "TRUNCATE country_indicator_period_history",
])
def test_audit_history_refuses_destructive_operations(db, statement):
    row_id = _insert(db)
    _correct(
        db,
        row_id,
        period=datetime(2024, 1, 1),
        status="recovered_inferred",
    )

    with pytest.raises(Exception, match="append-only"):
        db.execute(text(statement))
        db.commit()
    db.rollback()


def test_non_period_update_does_not_create_audit_noise(db):
    row_id = _insert(db)
    db.execute(
        text(
            "UPDATE country_indicator_history "
            "SET country_name = 'Russia' WHERE id = :row_id"
        ),
        {"row_id": row_id},
    )
    db.commit()

    assert db.execute(
        text("SELECT count(*) FROM country_indicator_period_history")
    ).scalar_one() == 0


def test_audit_context_survives_source_row_pruning(db):
    row_id = _insert(db)
    _correct(
        db,
        row_id,
        period=datetime(2024, 1, 1),
        status="recovered_inferred",
    )

    db.execute(
        text("DELETE FROM country_indicator_history WHERE id = :row_id"),
        {"row_id": row_id},
    )
    db.commit()

    context = db.execute(
        text(
            "SELECT country_iso3, indicator_code, source, value, fetched_at "
            "FROM country_indicator_period_history "
            "WHERE history_row_id = :row_id"
        ),
        {"row_id": row_id},
    ).one()
    assert context.country_iso3 == "RUS"
    assert context.indicator_code == CODE
    assert context.source == "world_bank"
    assert context.value == pytest.approx(4.92)
    assert context.fetched_at == datetime(2026, 1, 1)


def test_empty_audit_table_can_downgrade_and_upgrade(scratch_db):
    engine, url = scratch_db
    engine.dispose()

    downgraded = _alembic(url, "downgrade", "e8f9a1b2c3d4")
    assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr

    upgraded = _alembic(url, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr


def test_populated_audit_table_refuses_destructive_downgrade(scratch_db):
    engine, url = scratch_db
    with Session(engine) as db:
        row_id = _insert(db)
        _correct(
            db,
            row_id,
            period=datetime(2024, 1, 1),
            status="recovered_inferred",
        )
    engine.dispose()

    result = _alembic(url, "downgrade", "e8f9a1b2c3d4")
    assert result.returncode != 0
    assert "refusing to drop non-empty country_indicator_period_history" in (
        result.stdout + result.stderr
    )


def test_unknown_preexisting_audit_shape_fails_before_trigger_install(scratch_db):
    engine, url = scratch_db
    engine.dispose()
    downgraded = _alembic(url, "downgrade", "e8f9a1b2c3d4")
    assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE country_indicator_period_history "
            "(id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY)"
        ))
    engine.dispose()

    result = _alembic(url, "upgrade", "head")
    assert result.returncode != 0
    assert "has an unrecognised shape" in result.stdout + result.stderr
