"""GET /forecast/lstm-status must not crash when `evaluations` has no rows
in the last 60 days -- exactly the state of a freshly deployed server.

`MAX(created_at)::date` over zero matching rows is SQL NULL, but the
aggregate query still returns one row (`fetchone()` gives `(0, None)`, not
`None`). The endpoint's fallback checked `if result else ...` -- the row
itself, which is truthy -- so it never caught the `None` inside it, and
`last_date + timedelta(...)` crashed on every call on a server with no
evaluation history yet. Measured live on production immediately after a
deploy: two identical tracebacks in the first 20 seconds.

Mocked at the `db.execute(...).fetchone()` and `_query_time_series` seam
rather than through a real database, so this does not depend on (or
disturb) whatever other tests have left in the shared test database's
`evaluations` table.
"""
import asyncio
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd


def _call_lstm_status(last_date):
    """Call the real endpoint function with a mocked db and an empty
    time series, as if `evaluations` had `unique_days=0` and the given
    `MAX(created_at)::date` (None for zero matching rows).

    Returns `(status_code, body)`. The endpoint returns a Pydantic model on
    success and a `JSONResponse` on failure since its contract was declared,
    so this normalises both to a dict -- the assertions below are about the
    NULL-aggregate fix, not about the return type.
    """
    import json

    from fastapi.responses import JSONResponse

    from app.api.forecast import get_lstm_status

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = (0, last_date)

    with patch("app.api.forecast._query_time_series", return_value=pd.DataFrame()):
        returned = asyncio.run(get_lstm_status(db=mock_db))

    if isinstance(returned, JSONResponse):
        return returned.status_code, json.loads(returned.body)
    return 200, returned.model_dump()


def test_a_null_max_created_at_does_not_crash_the_endpoint():
    """The original assertion was `"error" not in result`.

    That field no longer exists on any branch -- the failure path is a 503 with
    a `reason_code` now -- so the assertion could not have failed. It asks the
    same question against the current contract: did the endpoint answer, or
    fall into its except-and-degrade path?
    """
    status_code, result = _call_lstm_status(None)
    assert status_code == 200, f"the endpoint degraded instead of answering: {result}"
    assert result["status"] == "ok", result


def test_a_null_max_created_at_falls_back_to_today():
    _status_code, result = _call_lstm_status(None)
    assert result["last_evaluation_date"] == date.today().isoformat()


def test_a_null_max_created_at_still_reports_days_remaining():
    """The specific fields this crash used to erase from the response.

    What separates the branches is no longer which keys are present -- the
    contract gives every branch the same set -- but `active`: a boolean when a
    verdict was reached, `null` when the check could not run. `is False` here
    would not hold on the degraded path.
    """
    _status_code, result = _call_lstm_status(None)
    assert result["active"] is False
    assert isinstance(result["days_remaining"], int)
    assert result["days_remaining"] > 0
    assert "weights" in result
    assert "models_active" in result


def test_a_real_last_date_is_still_used_not_overridden():
    """The fallback must trigger only for None, never mask a genuine value."""
    real_date = date(2026, 7, 1)
    _status_code, result = _call_lstm_status(real_date)
    assert result["last_evaluation_date"] == real_date.isoformat()
