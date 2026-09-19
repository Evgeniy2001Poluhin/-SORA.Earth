# M3 — forecast target declaration (preregistration)

    version              1.3
    declared             2026-08-14
    amended              2026-09-02  (1.1), 2026-09-19  (1.2, 1.3)
    clock start (§7)     2026-09-03 — measured, see Amendment 1.2
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

> **Read this section through Amendment 1.3 (2026-09-19).** "Observations" above
> means **distinct UTC hours**, not rows. The wording assumed one row per hour;
> the ingester can write several, and the two readings disagree on real data.
> The original text is left as declared -- a preregistration is not rewritten --
> and the arithmetic that supersedes it is in Amendment 1.3.

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

## Amendment 1.2 — the clock restarts, entered by measurement

**Date:** 2026-09-19. **Reason:** collection has resumed on the rebuilt
deployment, so the start that Amendment 1.1 deliberately left blank can now be
measured rather than chosen.

Amendment 1.1 fixed the rule in advance: the clock starts at **the first day on
the restored deployment that meets the coverage rule in §3**. Applying that rule
to production on 2026-09-19, over `environmental_observations` where
`source = 'openmeteo'` and `indicator = 'temperature'`, across all 21 declared
points:

| day | points meeting §3 | hours covered |
|---|---:|---|
| 2026-09-02 | **0 of 21** | 4 of 24 — collection began part-way through the day |
| 2026-09-03 | **21 of 21** | 24 of 24 at every point |

### Clock start: 2026-09-03

Not 2026-09-02, which is the first day with data and does not meet the rule.
Not the date of this amendment. Not the date the server was rebuilt.

The date is also **not sensitive to the ambiguity Amendment 1.3 resolves**:
2026-09-02 fails and 2026-09-03 passes under either reading of §3, so recording
the start here does not depend on that question being settled first.

### Earliest evidential runs

From the gate constants, which are untouched — `REQUIRED_WINDOWS = 12`,
`TRAINING_DAYS = {7: 90, 30: 180}`:

| horizon | days | earliest evidential run |
|---|---:|---|
| h=7 | 174 | **2027-02-24** |
| h=30 | 540 | **2028-02-25** |

The voided clock implied 2027-02-04 and 2028-02-05. The outage and the rebuild
cost **20 days**, day for day, which is what Amendment 1.1 said the restart
would cost and is recorded here so it is not understated later.

`tests/test_development_roadmap_dates.py` now carries `CLOCK_START` as a date
rather than `None`, which turns its inverse check back into the forward one: the
roadmap must state exactly these two dates and no others.

---

## Amendment 1.3 — §3 is read in hours, not in rows

**Date:** 2026-09-19. **Reason:** the data can satisfy §3 two ways, and they
disagree. Measured, not anticipated.

§3 says a day exists if there are "at least 19 of 24" observations, and that the
target is their mean. That wording assumes one observation per hour. The
ingester does not produce that: it reads Open-Meteo's `current` block, whose
`time` advances at **15-minute** resolution, so two runs more than 15 minutes
apart inside one hour write two rows with different `event_time`s. Restarting
the scheduler — which every deployment does, since `auto_openmeteo_ingestion` is
in `RUN_IMMEDIATELY_ON_STARTUP` — adds such a row.

Measured on production 2026-09-19:

```
3620 point-hours hold more than one row (3620 rows beyond one per hour)
DEU temperature 2026-09-04: 27 rows across 24 hours; 05:15 and 05:45 both
present, 16:15 and 16:45 both present
```

**What the two readings cost.** Counting rows, every one of 2026-09-03..09-18
passes for all 21 points. Counting hours, three days do not:

| day | points meeting the rule, by hours | worst point |
|---|---:|---:|
| 2026-09-09 | 19 of 21 | 18 hours |
| 2026-09-10 | 20 of 21 | 18 hours |
| 2026-09-14 | 20 of 21 | 16 hours |

So the row reading turns a gap in the day into a pass — the failure mode the
coverage floor exists to catch.

It also moves the target itself. The mean over rows weights an hour sampled
twice double; the mean over hours does not:

```
max difference on a point-day   0.665 C   (2026-09-02)
typical difference              0.05-0.19 C
difference on days with exactly one sample per hour   0.000 C
```

A target that moves by up to two thirds of a degree according to how often the
ingester was restarted that day is the defect §3's own rationale names: the
reason it chose the mean over the last reading was that "moving the scheduler by
an hour would change the target without the weather changing". The row reading
reintroduces exactly that.

### The rule, restated

```
hour(point, h)   = mean(temperature) over observations whose event_time falls in
                   UTC hour h
target(point,day)= mean(hour(point, h)) over the hours h present in that UTC
                   calendar day, if at least 19 of the 24 hours are present;
                   otherwise the day is absent
```

Unchanged: the target variable, the point set, the horizons, `MIN_COVERAGE`,
`REQUIRED_WINDOWS`, `TRAINING_DAYS`, and the rule that an absent day is absent
rather than interpolated.

**Why this is amendable now and would not be later.** No window has been
completed and no backtest has been run, so there is no result this could be a
choice of — the condition M2's §9 exists to protect. Once history accumulates
under the ambiguous reading, changing it becomes a choice made with results in
view, and the safe moment is gone. This is a tightening made in the safe window,
and the diff may be checked against that claim: nothing but §3's arithmetic and
the record below is edited.

## 5. What this declaration does not do

- It does not claim M2 demonstrated anything. M2 is closed with a negative
  result, and §10.6 of that protocol forbids citing it as validation.
- It does not make this an ESG forecast. §10.3 permits forecasting weather and
  air quality as a separate product and forbids the rename.
- It does not start any benchmark. It states what one would have to measure.

## 6. Amendment record

| version | date | reason |
|---|---|---|
| 1.1 | 2026-09-02 | Total loss of the accumulated observations: the production server was deleted for non-payment and `environmental_observations` went with it. The §7 clock was voided pending a measured restart. |
| 1.2 | 2026-09-19 | Collection resumed on the rebuilt deployment, so the clock start Amendment 1.1 deliberately left blank was entered by measurement: **2026-09-03**. |
| 1.3 | 2026-09-19 | §3 could be read in rows or in hours and the two disagree on production data. Read in hours. A tightening made before any window exists. |

This table read "None. This is version 1.0." until 2026-09-19 -- while Amendment
1.1 had been in the document since 2026-09-02, and a test asserted that sentence
was still there. The record section exists so that an amendment cannot be made
quietly, and it had already failed at that once. `tests/test_m3_declaration_is_recorded.py`
now reads the amendment headings out of the document and requires each one to
appear here with a date, so the omission cannot repeat.

An amendment tightening a condition before any run exists is the safe case; one
made quietly after results exist is what this record is for. Any change requires
a new version number, a date, and the reason, in this section.
