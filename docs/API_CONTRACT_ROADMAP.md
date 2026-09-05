# API response contract — baseline and migration plan

**SCOPED — not the project plan.** This is a live plan for one subsystem: the
shape of what the API returns. The project roadmap is
`docs/DEVELOPMENT_ROADMAP.md`, and nothing here supersedes it.


This plan exists because #236 was fixed in the wrong place. Twelve frontend
components were taught to distrust their own API, and that was the right
emergency measure, but it treats a symptom: the server does not say what it
returns, so every consumer has to guess and then defend itself.

The rule this plan follows: **a client guard is a workaround; the contract is
the fix.** #238 was the last PR that pays for a missing response schema on the
client side.

Nothing here changes a handler, a response model or CI. This document and
`scripts/api_contract_inventory.py` are the measuring stage. The order is
deliberate — instrument first, then one migrated route as a worked sample, then
the gate — because a ratchet built on an unverified count would freeze the wrong
number into CI.

---

## 1. Baseline

Measured by `scripts/api_contract_inventory.py`, after the removal in issue 241:

| | |
|---|---|
| routes declared in `app/` | **178** |
| exempt (websocket / non-model `response_class`) | **6** |
| considered | **172** |
| declaring a `response_model` | **20** |
| **coverage** | **11.6 %** |
| handlers whose static returns disagree under one status | **11** |
| of those, with a live frontend consumer | **2** |
| declared but absent from the live OpenAPI document | **15** |
| declared paths that could not be resolved unambiguously | **8** |
| public addresses declared **twice** | **0** — nine were removed, see below |

Reproduce:

```bash
python3 scripts/api_contract_inventory.py
python3 scripts/api_contract_inventory.py --openapi docs/contract/openapi-ebea7f8.json \
    --json docs/contract/inventory.json --csv docs/contract/inventory.csv
```

The machine-readable baseline is committed at `docs/contract/inventory.json`
and `docs/contract/inventory.csv`. Regenerate it rather than editing it.

The figures describe `app/` at `ebea7f8`, the tip of `main`. The `commit`
field inside the JSON records the checkout that generated it, which is this
branch — the two differ by documents and this script, not by a route.

`docs/contract/openapi-ebea7f8.json` is a snapshot of
`https://sora-earth.online/openapi.json` taken on 2026-09-05, with production
running `ebea7f8`. It is committed so the resolution step is reproducible
without a running server; it goes stale when routes are mounted or moved, and
should be refreshed alongside the inventory.

### How it is counted

The script parses the AST of every `.py` under `app/`. It records a route for
any `@<router|app|api_v1>.<get|post|put|patch|delete|head|options|websocket>("…")`
decorator, reads `response_model`, `response_class` and `status_code` from the
decorator's keywords, counts the handler's own `return` and `raise` statements
(returns inside nested functions belong to those functions and are excluded),
and collects the key set of every return that is a static dict literal.

A route is **exempt** — not uncovered — when it is a websocket or declares a
`response_class` whose body is not a serialised model: `HTMLResponse`,
`PlainTextResponse`, `FileResponse`, `StreamingResponse`, `RedirectResponse`,
bare `Response`. Exempt routes are excluded from the denominator; a websocket
counted as a defect would understate coverage and invite a pointless fix.

### The instrument was verified before its number was used

`tests/test_api_contract_inventory.py` — 41 cases, each a fixture whose correct
answer is known by construction: coverage, exemption for every class in the
exempt set, single-quoted paths, nested-function returns, key-order-insensitive
shape comparison, non-route decorators, the summary's arithmetic, prefix-based
path resolution, recorded ambiguity, consumer detection, duplicate addresses,
deterministic ordering, and snapshot provenance. One case is a
negative control asserting the script finds real routes in the real package,
because a collector that silently returned nothing would print "0 routes,
0 % coverage" and look like a finding.

Eighteen mutations of the script's predicates were each confirmed to turn the
suite red before being reverted, covering every predicate above.

Two of them did not, at first, and both were tests of mine that could not
fail:

- Removing the route sort left the suite green, because the fixture put both
  routes at module level where breadth-first walk order and file order happen
  to agree. Depth has to differ for them to disagree; the fixture now nests one
  route inside an `if`, declared first and visited second.
- Putting the generating commit back into the baseline left the suite green,
  because the test asserted on `summarise()` and the field was added by
  `main()`. It now asserts on the file the script actually writes, and a second
  test asserts two runs of one tree write identical bytes.

Mutation testing found both; review found neither. That is the same defect this
document is about — a check that cannot distinguish the broken case from the
working one — one level up.

### Two baselines, pinned to each other

The inventory measures code; the OpenAPI snapshot describes a deployment. If
the snapshot came from a deployment running different code, every resolved path
and consumer count is attributed to a tree the snapshot never saw, and nothing
downstream could tell.

So the baseline's identity is `app_tree_sha` — the git hash of `app/`, naming
the code that was measured. The generating commit is printed for the run and
deliberately **not** stored: it changes with every commit, including the one
that adds the baseline, so a baseline carrying it is stale the moment it lands
and no two runs of one tree could ever agree. That was found by running the
reproducibility check the review asked for, which failed for exactly this
reason.

The snapshot carries a sidecar `openapi-ebea7f8.meta.json` pinning the tree it
belongs to and its own sha256, and the script **refuses** (exit 3) when either
disagrees. `--allow-openapi-drift` proceeds and says so, and then every resolved
path is unverified.

Both refusals are tested, and both were confirmed by mutating the manifest and
the snapshot.

The committed JSON and CSV are byte-identical across runs of the same tree:
routes are sorted by file and line, and no timestamp is recorded.

### What this baseline does **not** prove

- **A declared `response_model` does not prove every branch is covered.** It
  proves one shape was declared. A handler can declare a model and still
  `return {"error": …}` from an `except`; FastAPI will try to coerce it and the
  mismatch surfaces at runtime, not here.
- **Declared path ≠ public path.** The script records the string in the
  decorator. Real paths carry router prefixes, and this is not cosmetic:
  `app/api/drift.py` declares `/drift` but mounts under `prefix="/model"`, so
  the endpoint is `/api/v1/model/drift`. Issue #239 named the wrong URL for
  exactly this reason and had to be corrected.

  `--openapi` resolves this, using the module's `APIRouter(prefix=…)` to pick
  between candidates. Matching by suffix alone is **not** sufficient and gets
  it wrong — `/drift` is a suffix of both `/api/v1/model/drift` and
  `/api/v1/mlops/drift`, which are different endpoints with different
  contracts. The first, ad-hoc version of this resolution made exactly that
  mistake on exactly that route, which is why the rule now has a test. Where
  the prefix does not settle it, the row records `AMBIGUOUS:` and the
  candidates rather than guessing: a confidently wrong path sends the next
  reader to a 404.

- **15 declared routes are absent from the live document.** Declared in code,
  not mounted, or mounted somewhere the suffix rule does not reach. They are
  neither covered nor exempt; classifying them is Phase D work.
- **Shape conflicts are a lower bound.** Only static dict literals are
  compared. A handler that builds its body dynamically is recorded as
  `dynamic_returns` and cannot be checked this way.
- **Consumer counts are textual.** `--web-dir` counts references to a resolved
  public path across all of `web/src` — not just `web/src/api`, because several
  components call `api("/lstm-status")` inline and a scan limited to the
  endpoint modules reports those routes as unconsumed. A path assembled at
  runtime from fragments would still be missed, so a zero means "no literal
  reference found", not "no consumer".
- Side effects and existing contract tests are **not** yet in the inventory;
  today they are established per route by hand.

---

## 2. The target contract

1. **One serialisable response type per successful status.** If a handler can
   answer 200 in several ways, those ways share one schema.
2. **Genuinely different HTTP statuses get different models.** A 202 that means
   "queued" is not the 200 model with empty fields.
3. **`Optional` only where absence carries domain meaning** — and then the
   consumer must be able to tell absence from zero. `drift_score: Optional[float]`
   is acceptable; `drift_score: float = 0.0` filled in on the "no baseline"
   branch is the defect that produced a green `STABLE`.
4. **An error, an unavailable dependency and "no data yet" never arrive as a
   negative verdict.** Today they do. `POST /api/v1/ab/predict` and
   `POST /api/v1/evaluate/monte-carlo` both `return {"error": …}` from inside an
   `except`, which FastAPI serves as **HTTP 200**: a consumer reading
   `probability` or `mean` gets `undefined`, and `undefined` reads as "no".
5. **Every branch of a handler passes response validation**, including the
   branches reached only when something is broken.

### The class of defect this closes

Observed in production on 2026-09-05:

```
GET https://sora-earth.online/api/v1/model/drift
HTTP 200
{"status": "no_log", "drift": false}
```

No `drift_detected`. The frontend consumes this route
(`web/src/api/endpoints/driftBaseline.ts:29`) and renders
`Kolmogorov-Smirnov per-feature: 0 features`. Nothing crashes — that guard was
always correct — but **"0 features" reads as a measurement and means "there was
nothing to compute from"**. The `status` field never reaches the screen.

That is the same failure as the green `STABLE` in #236, entered through a
partial shape rather than an empty body, and it is live.

---

## 3. Migration phases

**Phase A — instrument (this PR).** Inventory script, its tests, this document,
committed baseline. No handler, model or CI change.

**Phase B — one vertical sample (#239). Done, merged in #244, deployed and
verified in production.** `GET /api/v1/model/drift` end to end: a single verdict field name, a declared `response_model`, the same
shape on all four branches, contract tests covering each branch, and the
frontend type derived from it. Decide in the same PR whether the parallel
`/api/v1/mlops/drift` should remain — two drift endpoints with different
contracts is itself the problem. The sample establishes the pattern every later
migration copies.

**Phase C — ratchet gate. Done.** A job in `ci.yml`, inside `required-checks`,
on the verified inventory. See §5.

**Phase D — migrate by risk.** Priority order in §4, smallest reviewable PRs,
each one re-running the inventory so the number moves visibly.

---

## 4. Priority

| | routes | why |
|---|---|---|
| **P0** | model verdicts, drift, promotion, retrain | a wrong or absent verdict is acted on as a decision |
| **P1** | evaluation, uncertainty, calibration, explain | numbers a user reads as measurements |
| **P2** | map, compliance, rankings, timeline | visible, but a wrong answer is not a decision |
| **P3** | admin and diagnostic endpoints | operator-facing, small blast radius |
| **exempt** | files, redirects, streams, metrics, websockets | classified, not defects |

### The nine dead routes are gone, and the metric got worse

Removed in issue 241: `app/api/auth.py` and `app/api/admin_ai.py`, nine routes
between them. `duplicate_public_paths` is now **0**.

They were never alive. Both modules arrive in the repository's **first commit**,
and `git log -S` finds no commit in the whole history that ever registered
either router — no `include_router(auth_api…)`, no `admin_ai.router`. This was
not the residue of a migration; it was duplication carried from day one for four
months. `app/main.py` imported one of them and used it for nothing.

Nothing was lost, established before deleting rather than assumed:

- Of `app/api/auth.py`'s five handlers, three are byte-identical to the live
  ones in `app/auth_routes.py`. Its JSON `/auth/login` already exists there as
  `/auth/login-json`. Its one unique line — an `isinstance(user, str)` branch in
  `/auth/verify` — guards a case that cannot occur: `require_api_key` returns
  `API_KEYS[key]`, and all four construction sites build dicts.
- All four of `app/api/admin_ai.py`'s handlers compare equal by AST to
  `admin_ai_control.py`'s, except that the **live** one passes `force=True` to
  `full_pipeline_run`. The deleted module was the older of the two.

### The prediction about the denominator was wrong

This document previously said that removing the nine would take the denominator
to 172 and read as **12.8 %** "without a single contract being added". The
denominator did go to 172. The rest was wrong, and measuring it says something
the guess could not:

| | before | after |
|---|---|---|
| considered | 181 | 172 |
| with a `response_model` | 23 | **18** |
| coverage | 12.7 % | **10.5 %** |

**Five of the nine dead routes declared a `response_model`** — all four in
`admin_ai.py` used `AIActionResponse`, and the dead `/auth/login` used `Token`.
The dead code was better typed than the living average, by a wide margin: 56 %
against 12.7 %. Deleting it made the measured coverage worse.

The guess assumed the nine were uncovered because they were dead, as though the
two properties were related. They are not. This is the caution the metric needs
stated next to it: **a percentage moves for reasons that have nothing to do with
the work**, and the only defence is to re-run the instrument instead of
reasoning about the number.

### Start here: disagreeing shapes that already have a consumer

Five of the fourteen are reached by the frontend today. These are the P0/P1
starting set — not because they are the worst code, but because a consumer is
already reading them.

| public path | file | |
|---|---|---|
| ~~`GET /api/v1/model/drift`~~ | `api/drift.py` | migrated in #244 |
| ~~`GET /api/v1/model/drift/mlflow-history`~~ | `api/drift.py` | migrated in #247 |
| ~~`POST /api/v1/evaluate/monte-carlo`~~ | `api/evaluate.py` | migrated here |
| `GET /api/v1/lstm-status` | `api/forecast.py:462` — consumed by `LSTMProgressWidget` |
| `POST /api/v1/evaluate/monte-carlo` | `api/evaluate.py:552` — `{"error": …}` at HTTP 200 |
| `POST /api/v1/mlops/drift/simulate` | `api/drift_baseline.py:91` |

All fourteen, by declared path — the remaining nine have no frontend consumer
found, which lowers their priority but does not make them correct:

| route (declared) | file | shapes |
|---|---|---|
| `POST /predict` | `api/ab_test.py:28` | `{error}` vs the prediction |
| `POST /split` | `api/ab_test.py:71` | `{error}` vs `{status, traffic_split}` |
| `GET /country/{name}` | `api/data_pipeline.py:87` | 2 static + 1 dynamic |
| `GET /drift` | `api/drift.py:17` | **4** shapes (#239) |
| `GET /drift/mlflow-history` | `api/drift.py:61` | 3 shapes |
| `POST /mlops/drift/simulate` | `api/drift_baseline.py:91` | 2 shapes |
| `POST /evaluate/monte-carlo` | `api/evaluate.py:552` | `{error}` vs the histogram |
| `GET /lstm-status` | `api/forecast.py:462` | 2 shapes |
| `DELETE /cache/redis/invalidate` | `api/infra.py:362` | `{cleared, error}` vs `{cleared, keys}` |
| `DELETE /cache/redis/invalidate/{prefix}` | `api/infra.py:372` | 2 shapes |
| `POST /mlops/auto-retrain` | `api/infra.py:523` | 2 shapes |
| `POST /predict/v2` | `api/predict_v2.py:20` | fallback vs prediction |
| `GET /prediction-log/stats` | `api/retrain.py:586` | 3 shapes |
| `GET /russia/{region_code}` | `routes/map_russia.py:139` | 4 shapes |

Per-route inventory before migrating one: HTTP methods and statuses, number of
return/raise branches, actual keys per branch, consumers, side effects,
existing contract tests, its `/openapi.json` entry, and either a response type
or a written exemption reason. The script now supplies the public path, the
consumer count and everything except side effects and existing tests.

---

## 5. The ratchet

A rule that failed CI for any route without a `response_model` would fail on
158 existing routes, block the repository, and be deleted within a week as
impractical. So the baseline is pinned in `docs/contract/ratchet.json` and a job
in `ci.yml` enforces movement in one direction only.

**It is a job in `ci.yml` rather than its own workflow, and that is a
deliberate departure from the precedent in `secret-scan.yml`.** That file
isolates itself to avoid another editor of `ci.yml`, and pays a price for it:
branch protection on `main` requires exactly one context, `required-checks`,
which is a job aggregating others through `needs:` — and `needs:` cannot reach
across workflows. A separate workflow therefore reports and does not block.
Acceptable for a scanner that also runs weekly over history; not acceptable for
a ratchet, which is only worth having if it stops the thing it names. This job
is listed in `required-checks`.

Worth stating plainly as a consequence: **`secret-scan` does not block a merge
today either.** It reports. Whether that is intended is the owner's call, and it
is not changed here.

Five rules, all of them tested and each confirmed by breaking it:

1. **A route may not arrive without a contract**, and a covered route may not
   lose the model it had. Both surface the same way: the route is uncovered and
   is not in the pinned set.
2. **Covering a route must be recorded.** This is what makes it a ratchet
   rather than a floor: an improvement left unrecorded lets the same route slip
   back to uncovered later without tripping anything, because it is still in
   the pinned list. The failure prints the one command that fixes it.
3. **Exemptions may only shrink**, and an exemption's reason may not change
   silently.
4. **A hand-written allowance states a reason and an issue number.** Without
   both, it is refused — an exception nobody can trace outlives the reason for
   it.
5. **A stale allowance must be removed.** One that matches no current route is
   dead weight that would silently admit a route if one ever reappeared at that
   address.

The workflow has a second step, and it guards the gate rather than the code:
the manifest is regenerated and compared byte for byte with the committed one.
The ratchet check alone cannot catch a hand-edited manifest, because editing
`uncovered` makes the comparison agree with whatever was written.

Identity is `METHOD router_prefix+path @file`, with the line number excluded so
a route that moves down its file is the same route, and with the path recorded
relative to the package so a scan by absolute path agrees with one by relative
path. That second point was a real defect, found by the control asserting the
committed manifest matches the tree it ships with: without it the manifest only
matched when the script was invoked from the repository root.

The gate imports nothing outside the standard library, so its job installs no
dependencies.

## 6. Done when

- Every new route has a declared contract or a classified exemption. **Enforced
  since Phase C** — no longer a wish.
- For each migrated route, every return branch is covered by a contract test —
  including the failure branches.
- No two branches of one status carry incompatible key sets.
- `/openapi.json` describes what the endpoints actually return.
- Frontend types are generated from, or checked against, `/openapi.json`
  instead of being written by hand.
- Coverage is re-measured and recorded at every phase, by the script, not by
  assertion. It moved 12.2 % → 12.7 % when #244 landed, then **12.7 % → 10.5 %**
  when issue 241 removed nine dead routes of which five were typed — contradicting
  the prediction written here beforehand — and 10.5 % → 11.0 % → **11.6 %** with
  the MLflow history and Monte Carlo migrations. The count that tracks the work
  better is *shape conflicts with a live consumer*: 5 → 4 → 3 → **2**.

---

## 7. Blockers held outside this plan

These are owner decisions and do not gate the contract work.

- **#232** — `duration_months` leaks the success label; ablation on the legacy
  dataset shows ROC AUC falling 0.9165 → 0.6605 without it. Needs a domain
  definition of `success` and its ground truth, an ablation on an
  id-carrying dataset, and a decision on whether the column can be re-derived
  or must be dropped. Until then the serving model's 0.905 is not evidence of
  quality.
- **S3 off-site backup** — provisioning is a paid decision.
- **`BACKUP_ALERT_HOOK`** — needs a destination.
- **reg.ru balance** — the previous host was deleted for non-payment and its
  data is unrecoverable.
