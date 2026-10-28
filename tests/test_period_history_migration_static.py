"""Static Alembic checks for the issue #164 migration."""

import os
import subprocess
import sys

from tests.postgres_scratch import REPO_ROOT


DATABASE_URL = "postgresql://offline:offline@127.0.0.1/offline"


def _alembic(*args):
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": DATABASE_URL},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_period_history_revision_is_on_the_single_line_to_head():
    """It was the head when written; later revisions build on it.

    Pinning the literal head made every new migration fail this file for a
    reason unrelated to it. What the period-history change needs is that it is
    applied on the way to the one head, not that nothing ever follows it.
    """
    result = _alembic("heads")
    assert result.returncode == 0, result.stdout + result.stderr
    heads = result.stdout.strip().splitlines()
    assert len(heads) == 1 and heads[0].endswith("(head)"), heads

    lineage = _alembic("history", "-r", "f4b7c2d91e06:heads")
    assert lineage.returncode == 0, lineage.stdout + lineage.stderr
    assert "f4b7c2d91e06" in lineage.stdout


def test_period_history_revision_generates_offline_sql():
    result = _alembic(
        "upgrade",
        "e8f9a1b2c3d4:f4b7c2d91e06",
        "--sql",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CREATE TABLE IF NOT EXISTS country_indicator_period_history" in result.stdout
    assert "CREATE TRIGGER trg_cih_record_period_change" in result.stdout
    assert "CREATE TRIGGER trg_ciph_append_only" in result.stdout
    assert "CREATE TRIGGER trg_ciph_refuse_truncate" in result.stdout
