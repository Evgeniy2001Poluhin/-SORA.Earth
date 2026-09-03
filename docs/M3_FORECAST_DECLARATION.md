# M3 — forecast target declaration (preregistration)

    version              1.1
    declared             2026-08-14
    amended              2026-09-02  (see "Amendment 1.1" below)
    clock start (§7)     VOID — pending restart, see Amendment 1.1
    supersedes           nothing
    amendable            only by a numbered version with a date and a reason

This is not the M2 protocol. `docs/M2_EVALUATION_PROTOCOL.md` is closed with a
negative result and is not edited. What this file does is name a target that
M2 established does not exist for the ESG score, so that the clock in §7 of that
protocol runs against something.

Everything below was decided **before any accumulation counted toward a
benchmark**, and that is the point. M2's §9 exists because a choice made after
results are visible is a choice of results.

---

## 1. What is forecast

`openmeteo:temperature`. One target.

Chosen by measurement, on production, 2026-08-14:

| candidate | distinct values per point | worst point |
|---|---:|---:|
| **temperature** | **132.67** | 77 |
| pressure_msl | 114.43 | 67 |
| wind_speed | 107.00 | 51 |
| humidity | 60.81 | 37 |
| *(the M2 ESG target, for contrast)* | *1.00* | *1.00* |

**One target, not a set.** Three targets are three champions, three comparisons
and three chances to find one that looks good; picking the best of them
afterwards is selection, not a result. Other variables are not forbidden —
each needs its own declaration and its own clock, and none may be added to this
run retroactively.

## 2. Where it is forecast

Point set **`openmeteo-points-v1`**, frozen at this version:

```
BRA CAN CHN DEU ESP FRA GBR IDN IND ITA JPN KEN KOR MEX NGA NLD POL USA ZAF
RU-MOW RU-SPE
```

Twenty-one points. Nineteen are country codes and two are Russian cities, and
**every one of them is the coordinates of a single capital** taken from
`REGION_CAPITALS` in `app/ingesters/openmeteo.py`.

So the target is the temperature **at twenty-one named points**, not a regional
temperature. A result from this run may not be described as regional.

Deliberately **not** `ru-regions-v1`. That names the 85 subjects of the Russian
Federation — a different population — and reusing the name would be the
comparison of two populations under one name that §1.4 of the M2 protocol
exists to forbid.

Adding, removing or renaming a point is a version bump. Results measured under
one version are not recomputed under another and are not compared across them.

## 3. How a day is formed

The source publishes hourly. Measured: 22.8 observations per point per day out
of 24, about 95%.

```
target(point, day) = mean(temperature) over observations whose event_time falls
                     in that UTC calendar day, if there are at least 19 of 24
                     (>= 80%); otherwise the day is absent
```

**Mean, not the last observation of the day.** The last one is sensitive to the
polling schedule: moving the scheduler by an hour would change the target
without the weather changing.

**Not minimum or maximum.** Those are two different targets, each with its own
champion and its own declaration.

**80% is `MIN_COVERAGE`**, the constant already declared in
`app/services/forecasting/entry_conditions.py`. One number rather than two that
could drift apart.

**An absent day is absent.** It is not interpolated: §7.1 of the M2 protocol
lists an interpolated point among the things that do not count as movement.

## 4. When the clock starts

At the merge commit of this file. Not at the first measurement, not at the date
in the header, and not derived from the data afterwards.

Until this file is merged, accumulation is data and not a milestone.

### What that leaves

The gate is `REQUIRED_WINDOWS = 12` with `TRAINING_DAYS = {7: 90, 30: 180}`, so:

| horizon | training | 12 windows | earliest evidential run |
|---|---:|---:|---:|
| h=7 | 90 days | 84 days | **174 days after the merge commit** |
| h=30 | 180 days | 360 days | **540 days after the merge commit** |

No amount of engineering moves those dates, and no result assembled earlier is
evidential.

---

## Amendment 1.1 — the clock is void and restarts

**Date:** 2026-09-02. **Reason:** total loss of the accumulated observations.

The production server was suspended for non-payment on or about 2026-08-16 and
the service was then deleted. Aeza confirmed on 2026-09-02 that deleted services
cannot be restored. `environmental_observations` is gone, and hourly readings
cannot be re-fetched because the hours have passed.

### What this costs, measured rather than estimated

The clock started 2026-08-14 and collection stopped on or about 2026-08-16, so
**about two days** of qualifying observations existed. No window was completed,
no backtest was run, and nothing was known about how the target behaves. There
is no result this amendment could be a choice of, which is the condition M2's §9
exists to protect and the reason this amendment is legitimate at all.

### What does not change

The target, the horizons, the coverage rule and the gate constants stay exactly
as preregistered in v1.0. `REQUIRED_WINDOWS = 12` and
`TRAINING_DAYS = {7: 90, 30: 180}` are untouched. **Only the clock moves, and
only because the data is physically absent.**

An amendment that also relaxed a threshold would be indistinguishable from one
that gained an advantage from the outage. This one may be checked against the
diff: nothing but the clock is edited.

### When the new clock starts

At the **first day on the restored deployment that meets the coverage rule in
§3** — at least 80% of the expected hourly observations. Not at the date the
server is created, not at the first row written, and not chosen afterwards.

That date is **not stated here because it has not happened yet.** It will be
entered by measurement once collection has resumed, in a numbered amendment
1.2. Until then the earliest evidential dates are unknown, and any figure quoted
for them is wrong.

`tests/test_development_roadmap_dates.py` enforces that: while the clock is
void, the roadmap may not carry an evidential date at all.

### What the delay is, so it is not understated later

The clock previously implied **2027-02-04** (h=7) and **2028-02-05** (h=30).
Restarting moves both later by however long the outage plus the rebuild takes,
day for day. The loss of two days of data is the small part; the outage is the
large one.

## 5. What this declaration does not do

- It does not claim M2 demonstrated anything. M2 is closed with a negative
  result, and §10.6 of that protocol forbids citing it as validation.
- It does not make this an ESG forecast. §10.3 permits forecasting weather and
  air quality as a separate product and forbids the rename.
- It does not start any benchmark. It states what one would have to measure.

## 6. Amendment record

None. This is version 1.0.

An amendment tightening a condition before any run exists is the safe case; one
made quietly after results exist is what this record is for. Any change requires
a new version number, a date, and the reason, in this section.
