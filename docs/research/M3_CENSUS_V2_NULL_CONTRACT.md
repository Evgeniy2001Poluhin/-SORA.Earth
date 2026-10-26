# M3 census v2: the all-null answer and the 110 dates still owed

Draft leaf for GPT review. Code: `app/services/forecasting/replay_census_null_contract.py`.
Tests: `tests/test_forecasting_replay_census_null_contract.py`, all synthetic.

This document records a source outcome and derives a calendar. It does not
declare a census complete, a run accepted, or anything about M3 itself.

## What happened

| namespace | date | outcome | accounting |
|---|---|---|---|
| P (original) | 494 dates | accepted payloads | part of the 486 / 12678 baseline |
| P | 2025-08-05 | strict HTTP 400 "run not available" | in the baseline |
| G (gap companion) | 2025-08-06 | strict HTTP 400 "run not available" | in the baseline |
| C (census v1) | 2025-08-07 | HTTP 200, complete 21 points x 192 hours, all 4032 temperatures JSON `null` | 1 attempt, 21 units |

C stopped the way v1 is written to stop. The original weather validator refused
the body (`response_not_accepted:ReplayRefusal`), the pause was saved, and no
terminal receipt was written. C holds 0 accepted runs, 1 attempt and 21 units.
P, G and C are stopped and immutable. Nothing repeats 2025-08-05, -06 or -07.

Combined expense, P+G+C: **487 attempts, 12699 units**.

## Taxonomy

| status | meaning | where |
|---|---|---|
| `fetched_ok` | body passes the original weather validator | v1, unchanged |
| `recorded_run_unavailable` | strict HTTP 400 with the pinned reason text | v1, unchanged |
| `observed_all_null_response` | HTTP 200, the exact 21 x 192 grid for the reviewed request, every temperature `null` | this leaf |
| anything else | not classified; the caller stops | v1 rule, unchanged |

`observed_all_null_response` is a statement about what the source returned. It
is not proof that the run never existed, not a forecast and not scientific
acceptance. It carries no weather values: the record is the status, the date,
the request digest, the body's sha256 and byte count, and the fixed shape
(`point_count` 21, `hours_per_point` 192).

## `classify_all_null(raw, *, day, request)`

The caller is responsible for HTTP 200 and for passing the reservation's own
request. The function checks the second claim again: `request` must equal
`_request(date.fromisoformat(day))` from the reviewed plan, by value and by
canonical JSON (so `21.0` or `true` in place of `21` is refused). A request that
does not match raises `CacheRefusal`; it is a caller error, not a body to
classify. Its URL must name exactly the reviewed query: `temperature_2m`,
`ecmwf_ifs`, `timezone=UTC`, `timeformat=iso8601`, `forecast_days=8`, and the
21 registry coordinates in registry order.

The body returns a record only if all of these hold. Otherwise it returns `None`.

- At most `MAX_RAW` bytes. The JSON is strict: no duplicate keys, no `NaN` or
  `Infinity`. It uses `replay_p0.parse_json`, the original parser's own reader.
- The root is a list of exactly 21 objects, in requested point order.
- Every point has exactly the keys `latitude`, `longitude`, `timezone`,
  `timezone_abbreviation`, `utc_offset_seconds`, `hourly_units` and `hourly`.
  It may add `elevation` and `generationtime_ms`, which must be finite non-bool
  numbers. Any other key refuses the body.
- `location_id` numbers the points of a multi-location answer: it is absent on
  point 0 and required on every point `i` from 1 to 20, as the integer `i`
  (not a bool, not `3.0`). This rule was read off the retained 2025-08-07 body,
  which carries exactly that pattern.
- `latitude` and `longitude` are finite non-bool numbers within 0.3 degrees of
  the requested point. The tolerance is the original parser's; integers are
  accepted, as there.
- `timezone` and `timezone_abbreviation` are each `GMT` or `UTC`, and
  `utc_offset_seconds` is the integer 0. A bool or `0.0` is refused.
- `hourly_units` equals `{"time": "iso8601", "temperature_2m": "°C"}` exactly.
- `hourly` has exactly `time` and `temperature_2m`. Unknown hourly variables are
  refused because the pinned request asks for none.
- `time` is exactly the 192 strings `YYYY-MM-DDTHH:00`, from `day`T00:00 to
  (`day`+7)T23:00, in order. That one comparison covers count, distinctness,
  order, format and calendar.
- `temperature_2m` has 192 entries and every one is `null`. A single finite
  number, `0`, `false` or a string refuses the body.

"Every value in the list is null" is therefore never enough on its own. An empty
list, a short list, a missing or extra point, a shifted day and a duplicated
hour all return `None`.

### No effect on the weather validator

`replay_p0.parse_payload` and `CacheWorker.validate` are not modified, imported
for patching or wrapped. The tests pin both directions:

- the all-null grid is still refused by the original validator;
- an ordinary synthetic forecast passes the original validator and is not
  classified all-null.

## `remaining_census_dates(gap_contract_raw, stopped_census_state_raw, *, expected_state_sha256)`

The function first checks `sha256(stopped_census_state_raw) == expected_state_sha256`.
It then derives the reviewed 111-date census calendar with v1's
`source_calendar(gap_contract_raw)`. That calendar has 112 G dates, 2025-08-05
absent, 2025-08-06 removed. The function requires its first date to be
2025-08-07.

The stopped state must match v1's schema exactly (`STATE_FIELDS`, `EVENT_FIELDS`)
and be in this one shape:

- `schema` is the integer 1. `policy_sha256` is a sha256 and `execution_commit`
  is a 40-hex commit.
- Exactly one event: `kind` is `attempt`, `day` is 2025-08-07, `weight` is the
  integer 21, and `request_sha256` is the digest of the reviewed request URL.
- The timestamps are explicit UTC, in the order
  `enabled_at <= reserved_at <= at <= last_seen`. This is v1's rule.
- `terminal` is `{}`, so no adoption of any receipt, including an all-null one.
- `paused` is exactly `response_not_accepted:ReplayRefusal`.
- `inflight` has exactly `day`, `request_sha256`, `response_sha256` and
  `response_bytes`. `day` is 2025-08-07, the request digest matches the event,
  and the response is `de80dd0c…f291` at 102740 bytes, an integer.

The function returns the 110 remaining dates, in calendar order, unique, with
2025-08-05, -06 and -07 absent. It returns a plain list and declares nothing.

**The pin parameter exists for synthetic tests.** The approved C state is
`APPROVED_STOPPED_STATE_SHA256 = cc33f004…9819`. The successor v2 execution
policy must hard-pin that value and every inherited byte: P and G state, plan,
contracts, evidence, and C's state and retained response. It must not compute a
pin from the bytes it is checking. The function itself cannot tell a synthetic
pin from the real one.

The test calendar is synthetic: 2025-08-06, 2025-08-07, then 110 consecutive
days. The real 110 come only from the reviewed gap contract. This document does
not assume their endpoints.

## Accounting for a successor namespace D

| | attempts | units |
|---|---|---|
| P+G baseline (v1) | 486 | 12678 |
| C, inherited once | +1 | +21 |
| inherited by D | **487** | **12699** |
| D's own cap | 110 | 2310 |
| combined ceiling (unchanged) | **597** | **15009** |

Rolling limits are unchanged: 400 units per 60 s, 4000 per hour, 7000 per 24 h,
and at least 4 s from response completion. The module asserts the arithmetic
at import, and a test asserts it again against v1's `BASELINE` and
`COMBINED_LIMIT`.

## Not changed

These are not changed:

- the weather parser or validator;
- the scientific protocol: 365-day window, at least 180 pairs, 21 points,
  7 leads, 12 origins;
- acceptance, formulas and results;
- the v1 collector, P, G or C.

## Still pending

This is a leaf. It has no collector, dispatcher, namespace D, policy, freeze or
authorization. Before any request for the 110 dates, GPT must review and
integrate:

1. a D collector that uses this leaf;
2. a frozen policy pinning C and the inherited bytes;
3. an offline preflight with 0 GET;
4. the owner's or GPT's explicit "yes" on an exact commit and policy sha256.

Until then, no fetch is permitted.

## Open questions for review

- **Real payload metadata — checked.** With the task's allowlist alone the
  retained `de80dd0c…` body (102740 bytes) classified as `None`, solely because
  of `location_id`. A read-only script, run by GPT's permission and printing
  only key sets, time-zone fields and `location_id` placement (no weather
  values), established the rule above. With it, the retained body classifies as
  `observed_all_null_response`, sha256 and byte count matching the pin.
- **`timezone_abbreviation` is required.** The original parser does not read
  it. The leaf requires it because the task does.
- **A forged request raises rather than returning `None`.** That keeps "the
  caller passed the wrong request" apart from "the body is not all-null".
