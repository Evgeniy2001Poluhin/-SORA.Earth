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


def test_period_history_revision_is_the_only_head():
    result = _alembic("heads")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "f4b7c2d91e06 (head)"


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
