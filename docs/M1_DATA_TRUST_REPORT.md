# M1 Data Trust — production sign-off

**Date:** 2026-08-06 MSK (2026-08-05 UTC)
**Production commit:** `1b0aacc` — deployed 2026-08-07 04:19Z
**Schema:** `c58e21a9f7d4`
**Scope:** the platform's external data — its sources, periods, provenance, coverage, and the model features built on them.

Every figure below was read from production after the deployment, not from a
test environment and not from an earlier note.

**The figures were measured on `0a1e458` and re-measured unchanged on
`1b0aacc`.** The two differ by one SQL filter — `source='world_bank'` in the
GDP query — which removes nothing that was in use, because every eligible row
was already `world_bank`. It converts that from a property of configuration
elsewhere into a property of the query. Re-measured on the deployed code:
coverage 1.00, variance > 0 across all 30 countries, zero non-`world_bank`
rows eligible.

---

## The claim being signed off

**Not** "the platform has all the data it wants". It does not, and several
gaps remain open.

What is signed off is narrower and checkable: **the platform no longer claims
to use data it does not have.** Every declared source publishes what is
attributed to it, every stored observation carries a period and a provenance,
and every declared model feature carries real, varying values.

---

## 1. Sources

Five indicators are collected from the World Bank. Two were removed.

`rows` is the raw row count and **includes duplicates left by the pre-#96
write path**; `points/country` is the average number of *distinct dated facts*
per country — `count(distinct (country, period)) / count(distinct country)` —
which is the figure that describes coverage.

| indicator code | rows | countries | points/country | range |
|---|---|---|---|---|
| `SP.DYN.LE00.IN` | 18,174 | 30 | 65.2 | 1960–2024 |
| `NY.GDP.PCAP.CD` | 18,090 | 30 | 63.4 | 1960–2025 |
| `EG.FEC.RNEW.ZS` | 17,171 | 30 | 32.4 | 1990–2022 |
| `SI.POV.GINI` | 15,887 | 28 | 26.4 | 1963–2025 |
| `NY.GDP.MKTP.KD.ZG` | 2,130 | 30 | 62.0 | 1961–2025 |

**Removed:** `EN.ATM.CO2E.PC` and `GE.EST`. Neither exists at the World Bank —
both return HTTP 200 with *"The indicator was not found. It may have been
deleted or archived"*, for every country. `GE.EST` is archived under source 57
along with every governance sibling (`GE.PER.RNK`, `CC.EST`, `RL.EST`,
`RQ.EST`).

Nothing had ever been fetched for either. All 15,456 rows of each came from
the static benchmark fallback, wearing a World Bank code. They are relabelled
`benchmark:co2_per_capita` and `benchmark:gov_effectiveness`, still served to
the API, no longer attributed to a source that does not publish them.

```
rows still carrying EN.ATM.CO2E.PC or GE.EST:  none
```

## 2. Periods

The claim is about **World Bank observations**, and only about sources that
publish a period:

```
world_bank rows with no period:        0
rows with no source or no fetch time:  0
```

Benchmark and global-average values carry **no period at all**, deliberately
and by the same measurement — those sources state none, and inventing one
would assert precision they did not give. They are excluded from the claim
above rather than counted as satisfying it.

## 3. Provenance

`source` is recorded on every row and distinguishes `world_bank`, `benchmark`
and `global_avg`.

**A residue, stated rather than glossed:** 1,937 rows carry a *live* World
Bank code with `source='benchmark'` or `global_avg` — fallback values written
for countries where a fetch failed. This is a weaker version of the problem
just fixed: the indicator is genuinely published by the World Bank and the
value is a stand-in for the same quantity, so the code is not a false claim in
the way `GE.EST` was. But a query filtering on `indicator_code` alone still
mixes measured and substituted values.

They affect `SI.POV.GINI` (1,355), `EG.FEC.RNEW.ZS` (291) and
`NY.GDP.PCAP.CD` (279). **None affects a model feature:** the only regressor
reads `NY.GDP.MKTP.KD.ZG`, which has no fallback row, and now filters on
source regardless. So this is a reporting concern, not a training one.
Recorded in #95.

## 4. Coverage and history

The write path stored the latest value re-inserted, not the series — measured
at **one dated point per country** across every indicator. #96 fixed the path
and the production one-shot filled the history.

```
before          1.0–1.4 points per country
after           26.4–65.2 points per country
unique (country, indicator, period)   7,386
duplicate keys created by the new path   0
```

161 keys carry duplicate rows. **All are from the old write path**
(`refresh_job_name = 'external_data_refresh'`, 50,635 rows) — the pre-#96
behaviour that appended the same value on every run. The new path
(`manual_history_refresh`) created **zero** duplicates, confirmed by a second
run reporting `inserted=0, unchanged=7386`.

## 5. Model features

One external regressor, not three.

| feature | status |
|---|---|
| `gdp_growth` | **live** — coverage 1.00 and variance > 0 across all 30 countries, measured values only |
| `air_quality` | **withdrawn** |
| `carbon_price` | **withdrawn** |

Both withdrawn features were declared and were `0.0` in every forecast the
platform has produced. `air_quality` read a table that has never held its
metric; `carbon_price` has no source anywhere in the database.

They were removed rather than left at zero because **zero is an economically
meaningful value for both** — clean air, no carbon price. A model could not
tell "we have no data" from "the figure is zero", and neither could anyone
reading the frame.

Verified on production after the deployment:

```
regressor columns:            ['gdp_growth']
countries with variance > 0:  30 of 30
```

**Fallback values cannot reach the regressor.** Running the feature builder's
exact filters grouped by source:

```
NY.GDP.MKTP.KD.ZG, dated, non-null, by source:
  world_bank   2,130 rows   30 countries
  (no other source)
```

No benchmark or global-average row is eligible, and none exists — `gdp_growth`
is in neither `BENCHMARKS` nor `GLOBAL_AVG`, so the fallback chain cannot
produce one, and `refresh_indicator_history` writes `source='world_bank'`
unconditionally.

That was a property of configuration elsewhere: adding the key to `BENCHMARKS`
would have been enough to change it silently. The query now filters on
`source='world_bank'`, so the guarantee belongs to the feature rather than to
an accident, with a test that fails if the filter is removed.

## 6. Run reporting

A run now reports what happened to each of its 210 (country, indicator) pairs,
not only how many rows moved:

```
pairs_attempted  pairs_succeeded  pairs_empty  pairs_refused  pairs_failed_transient
```

`empty` (the source has nothing), `refused` (unknown or archived code) and
`transient` (timeouts, 429s, 5xx, and any pair whose pagination stopped
part-way) are kept apart because only the last is worth retrying. A transient
failure sets the run to **degraded** — in the status, not in prose a reader has
to interpret.

## 7. Scheduled history refresh: OFF

```
scheduler log:  indicator history refresh skipped: disabled by SORA_HISTORY_REFRESH
```

Deliberate. `auto_refresh_external_data` is in the scheduler's immediate-run
set, so enabling it would make a mass ingestion a side effect of a deployment.
Two questions remain unanswered before it is turned on: the cadence (six hours
for annual series means four runs a day that will report `inserted=0`, and a
signal that is normal four times a day is not a signal), and confirmation that
the pair counters behave over repeated unattended runs.

## 8. Production health at sign-off

```
commit         1b0aacc          schema      c58e21a9f7d4
/health        200              unhealthy containers  0
rows           104,309          deploy exit code      0, no rollback
```

The source filter is present in the **running image**, not only in the
repository — the file's SHA inside the container matches the tree
(`cfd5912948498bc5`).

---

## What M1 does not cover

Open, and deliberately not blockers — the roadmap must not claim otherwise:

| issue | why it is not M1 |
|---|---|
| #75 | point-in-time backtesting — M2 |
| #84 | consumer/API surface — product |
| #74 | full structured run reporting — operations; #101 covers the part M1 needs |
| #57 | *measured* air quality — data sourcing. Modelled data is collected and declared as modelled; no model uses it. |
| #97 | adopting `EN.GHG.CO2.PC.CE.AR5` — it excludes LULUCF, a different measurement, so a data decision |
| #98 | certbot deploy hook — operations |
| #94 | two Prometheus endpoints — observability |
| #70, #54, #50 | separate streams |

## What was found and fixed getting here

Recorded because the pattern matters more than the individual defects: in
every case the platform reported success while producing nothing usable, and
in every case the evidence was already in the database or the API response.

- a regressor asked for indicator codes nothing collected — zero in every
  forecast ever produced (#86)
- the ingester discarded two of the three observations it had already fetched,
  storing one point per country ~450 times over (#96)
- a 200 response carrying no data returned silently, losing five countries'
  entire series in the first rehearsal run with no message at all
- two of seven configured indicators were never published by their nominal
  source (#97)
- the ORM stopped describing the database, so the documented way to create a
  migration generated one that dropped a duplicate-guard index and 90,461 rows
  of provenance (#88)
- the operational entry point was in the repository and not in the image (#99)

Every fix in this milestone was checked by reverting it and confirming the
test went red. Several tests passed without the fix on the first attempt and
were rewritten; a green test is not evidence until it has been shown able to
fail.
