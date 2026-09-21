"""How many retrain rows were judged without knowing whether the model registered.

`docs/DEVELOPMENT_ROADMAP.md` lists this as the one P0 that remains: *"Measure
the ambiguity window -- the count of new rows without `registry_ok` is known"*.

## What the window is

`app/promotion.py` refuses to promote a model whose run did not register in
MLflow, and does it with `registry_ok is False` rather than a falsy test, on
purpose: a row written before the field existed carries no opinion, and absent
is not the same as failed. That deliberate blind spot is the window. It is
bounded in time -- the writer landed in `app/api/retrain.py` on 2026-08-15
(411badd, #190) and was corrected on 2026-08-16 (101577a, #201) -- so a row
started after that and still missing the field is a different thing from an old
one, and this reports them separately.

## Why this is a script and not a query in a document

A bare count here is the easiest kind of lie to tell by accident. `0 ambiguous
rows after the boundary` is produced by all of:

- there are no such rows, which is the answer we want;
- there are no rows after the boundary at all;
- `retrain_log` is empty;
- the deployment never ran the code that writes the field.

Measured 2026-09-21 on the local development database, the fourth case was the
true one: 49 rows, **none** carrying `registry_ok`, newest 2026-09-20 -- five
weeks after the writer landed. The checkout the containers mount was 266 commits
behind `origin/main`, so the writer had never executed there. A count alone
would have read as "no problem".

So every number here is printed with what it was drawn from, and the verdict
names which of the four cases holds.

## Usage

    DATABASE_URL=postgresql://... python scripts/measure_ambiguity_window.py

Read-only: one SELECT, no writes. Exit status is 0 when a window could be
measured -- including when it is empty -- and 2 when the answer is "not
measurable here", so a caller cannot mistake one for the other.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

#: The writer's arrival. A row started before this could not have carried the
#: field, and counting it as a defect would be blaming code for lacking a
#: column it predates.
WRITER_LANDED = datetime(2026, 8, 15, 13, 44, 4, tzinfo=timezone.utc)

#: The verdicts. They are distinct because a caller acting on them should act
#: differently, and a single number cannot tell them apart.
MEASURED = "measured"
NO_ROWS = "no_rows"
NONE_AFTER_BOUNDARY = "no_rows_after_boundary"
WRITER_NEVER_RAN = "writer_never_ran"


@dataclass(frozen=True)
class Window:
    total: int
    with_metrics: int
    carrying: int
    ambiguous: int
    ambiguous_after_boundary: int
    rows_after_boundary: int
    newest: Optional[str]
    first_carrying: Optional[str]
    verdict: str
    reason: str


def _to_dt(value):
    """A comparable, timezone-aware datetime, or None if it cannot be one.

    Drivers do not agree on this column: psycopg2 returns a datetime, sqlite3
    returns the string it stored. Comparing the two raises, which is how this
    was found -- by running the script against a real table, after every test
    had fed it datetimes and passed.

    None rather than an exception on a value that will not parse: an instrument
    that dies on one bad row reports nothing about the other forty-eight, and
    the row is counted in the total either way.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace(" ", "T"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _as_utc(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def classify(rows) -> Window:
    """Decide from `(started_at, metrics_json)` pairs alone.

    Kept free of the database so the four verdicts can be exercised directly;
    `tests/test_the_ambiguity_window_is_measurable.py` drives each one.
    """
    total = len(rows)
    with_metrics = 0
    carrying = 0
    ambiguous = 0
    rows_after = 0
    ambiguous_after = 0
    newest = None
    first_carrying = None

    for raw_started_at, metrics_json in rows:
        started_at = _to_dt(raw_started_at)
        if started_at is not None and (newest is None or started_at > newest):
            newest = started_at

        has_field = False
        if metrics_json:
            with_metrics += 1
            try:
                has_field = "registry_ok" in (json.loads(metrics_json) or {})
            except (ValueError, TypeError):
                has_field = False

        if has_field:
            carrying += 1
            if first_carrying is None or (started_at is not None
                                          and started_at < first_carrying):
                first_carrying = started_at
        else:
            ambiguous += 1

        after = started_at is not None and started_at > WRITER_LANDED
        if after:
            rows_after += 1
            if not has_field:
                ambiguous_after += 1

    if total == 0:
        verdict, reason = NO_ROWS, (
            "retrain_log is empty, so no window exists to measure and the zero "
            "below is the size of the table, not a result"
        )
    elif rows_after == 0:
        verdict, reason = NONE_AFTER_BOUNDARY, (
            f"all {total} rows predate {WRITER_LANDED.date()}, when the writer "
            "landed. None of them could have carried the field, so none is a "
            "defect and none is evidence either"
        )
    elif carrying == 0:
        verdict, reason = WRITER_NEVER_RAN, (
            f"{rows_after} rows were written after the writer landed and not "
            "one carries the field. That is not an ambiguity window; it is a "
            "deployment running code that predates the writer. Check the "
            "revision actually serving before reading the count below"
        )
    else:
        verdict, reason = MEASURED, (
            f"{ambiguous_after} of {rows_after} rows written after "
            f"{WRITER_LANDED.date()} carry no registry_ok"
        )

    return Window(
        total=total, with_metrics=with_metrics, carrying=carrying,
        ambiguous=ambiguous, ambiguous_after_boundary=ambiguous_after,
        rows_after_boundary=rows_after,
        newest=_as_utc(newest), first_carrying=_as_utc(first_carrying),
        verdict=verdict, reason=reason,
    )


def fetch(url: str):
    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT started_at, metrics_json FROM retrain_log"))
        return [(r[0], r[1]) for r in result]


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is unset; this reads a database and will not guess "
              "one", file=sys.stderr)
        return 2

    window = classify(fetch(url))

    if "--json" in argv:
        print(json.dumps(asdict(window), indent=2))
    else:
        print(f"retrain_log rows                    {window.total}")
        print(f"  with a metrics_json               {window.with_metrics}")
        print(f"  carrying registry_ok              {window.carrying}")
        print(f"  without it                        {window.ambiguous}")
        print(f"written after {WRITER_LANDED.date()}          "
              f"{window.rows_after_boundary}")
        print(f"  of those, without registry_ok     "
              f"{window.ambiguous_after_boundary}")
        print(f"newest row                          {window.newest}")
        print(f"first row carrying the field        {window.first_carrying}")
        print()
        print(f"verdict: {window.verdict}")
        print(f"         {window.reason}")

    return 0 if window.verdict == MEASURED else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
