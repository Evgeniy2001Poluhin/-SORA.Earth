# Specialist UAT protocol

**Status:** preregistered; not yet run
**Owner:** SORA.Earth product owner
**Target sample:** 5–10 independent specialists
**Planned workload:** 40 scenarios, with every scenario completed by at least
two specialists and every specialist completing at least six scenarios
**Version rule:** record the Git SHA, deployed release, API contract revision,
model/run identifiers and data snapshot identifiers before the first session.

This protocol defines the specialist assessment before any result is visible.
It does not report interviews, usability scores or product readiness. A later
UAT report must preserve this file and explain every deviation.

## 1. Question and boundary

The assessment asks whether an environmental or ESG specialist can reach a
decision and verify the origin, timing and uncertainty of every material value
used in that decision. It does not evaluate forecast quality before the M3
evidence gate, and it must not ask participants to treat an experimental
forecast as validated.

The product succeeds only when the participant can complete the task **and**
reconstruct its evidence chain:

```
source → observation → snapshot/as-of boundary → model/run
→ output and uncertainty → human decision → audit/export
```

## 2. Participants

Recruit 5–10 people who make, review or audit environmental or ESG decisions.
The final report records role, relevant years of experience and declared domain
speciality using participant codes; it must not publish names or employers
without separate consent.

The sample should include at least:

- two environmental-data or air-quality specialists;
- two ESG/project-assessment specialists;
- one risk, compliance or audit specialist;
- one operational user who consumes maps, exports or status information.

Employees who implemented the tested workflow may facilitate sessions but do
not count toward the specialist sample.

## 3. Frozen test context

Before recruitment, create a session manifest containing:

- protocol version and Git SHA;
- environment and application release identifier;
- OpenAPI/response-contract revision;
- model name, version, run id and active/staged status where applicable;
- immutable dataset snapshot ids and checksums;
- scenario allocation and expected evidence anchors;
- known limitations shown to participants;
- start/end timestamps in UTC.

No production credentials or personal data belong in the manifest. Use a
dedicated UAT environment or read-only production views. Scenarios that mutate
state must use synthetic records in an isolated environment.

## 4. Session procedure

1. Obtain consent for notes and optional screen recording.
2. Give the participant the role and decision goal, but not click-by-click
   instructions.
3. Ask the participant to think aloud. The facilitator may clarify the task but
   may not point to controls or explain a value's origin.
4. Record task outcome, elapsed time, navigation path, evidence-chain outcome,
   errors, assistance and participant confidence.
5. Ask the participant to identify the source, observation time/as-of boundary,
   uncertainty or limitation and the action they would take.
6. End with the standard interview questions in §8.

A scenario stops after 20 minutes, an unsafe action, unrecoverable product
error, or the participant saying they cannot proceed.

## 5. Scenario catalogue

`Variant` identifies a prepared fixture or region. Fixture contents and expected
evidence anchors are frozen in the session manifest. The table defines 40
scenarios in advance; the later report may mark a scenario not run but must not
replace it after seeing results.

| ID | Route | Variant | Specialist goal | Required evidence |
|---|---|---|---|---|
| UAT-01 | `/evaluate` | ordinary project | Produce an ESG assessment | Inputs, component scores, model/rule boundary |
| UAT-02 | `/evaluate` | zero-value boundary | Decide whether zero is valid or missing | Preserved zero and validation message |
| UAT-03 | `/evaluate` | invalid input | Refuse an impossible assessment | Field-level reason; no partial result |
| UAT-04 | `/evaluate` | high-impact project | Explain the recommendation | Contributing values and limitations |
| UAT-05 | `/evaluate` | repeated case | Compare a repeated assessment | Stable inputs and identifiable result |
| UAT-06 | `/compare` | two ordinary projects | Choose between projects | Same metric definitions and units |
| UAT-07 | `/compare` | close scores | Explain a marginal difference | Uncertainty and no false precision |
| UAT-08 | `/compare` | missing field | Determine whether comparison is valid | Missingness visible; no silent imputation |
| UAT-09 | `/compare` | conflicting dimensions | Explain the trade-off | Environmental/social/governance components |
| UAT-10 | `/compare` | export review | Preserve comparison evidence | Export matches visible values |
| UAT-11 | `/map` | known region | Locate and interpret a regional signal | Region code, source and observation time |
| UAT-12 | `/map` | stale observation | Detect stale evidence | Freshness state and timestamp |
| UAT-13 | `/map` | missing region data | Distinguish absence from zero | Explicit missing state |
| UAT-14 | `/region/:code` | ordinary region | Reconstruct a regional value | Source, period/as-of boundary and units |
| UAT-15 | `/region/:code` | revised indicator | Identify the applicable vintage | Point-in-time provenance |
| UAT-16 | `/drift` | no drift | Verify why no action is required | Window, baseline and test result |
| UAT-17 | `/drift` | detected drift | Decide an escalation | Affected feature, magnitude and timestamp |
| UAT-18 | `/drift` | insufficient evidence | Refuse a drift conclusion | Sample/window limitation |
| UAT-19 | `/mlops` | healthy services | Verify operational readiness | Component status and measured time |
| UAT-20 | `/mlops` | degraded dependency | Find the failing dependency | Failure state without invented health |
| UAT-21 | `/explain` | ordinary prediction | Explain the leading factors | Model/run identity and factor direction |
| UAT-22 | `/explain` | weak contribution | Avoid overclaiming a small effect | Magnitude and limitation |
| UAT-23 | `/explain` | what-if zero | Preserve a deliberate zero | Submitted value and changed output |
| UAT-24 | `/explain` | non-finite input | Refuse invalid what-if input | Validation before request |
| UAT-25 | `/calibration` | calibrated range | Interpret p5/p95 | Interval definition and reliability context |
| UAT-26 | `/calibration` | near-deterministic result | Explain the badge without certainty claim | Width and limitation |
| UAT-27 | `/calibration` | sparse bin | Identify weak calibration evidence | Bin count/sample limitation |
| UAT-28 | `/history` | known evaluation | Find a prior decision | Timestamp and immutable identifier |
| UAT-29 | `/history` | similar names | Select the correct record | Stable identity, not display name alone |
| UAT-30 | `/history` | export reconciliation | Match history to export | Row/value parity and generation time |
| UAT-31 | `/forecast` | admissible horizon | Read a forecast as experimental | Horizon, issue time and target time |
| UAT-32 | `/forecast` | missing coverage | Detect an inadmissible window | Coverage denominator and failed gate |
| UAT-33 | `/forecast` | uncertainty interval | Make a bounded decision | Interval and baseline comparison |
| UAT-34 | `/forecast/compare` | baseline comparison | Identify whether challenger wins | Same snapshot/window and declared metric |
| UAT-35 | `/forecast/compare` | inconclusive difference | Refuse a winner | Confidence/significance limitation |
| UAT-36 | `/status` | all measured healthy | Verify what is actually measured | Check names and observation times |
| UAT-37 | `/status` | unknown component | Distinguish unknown from healthy | Explicit unknown state |
| UAT-38 | `/compliance` | evidence export | Verify an exported claim | Release SHA, snapshot and source chain |
| UAT-39 | `/copilot` | supported question | Verify every numerical answer | Tool/API source and citation |
| UAT-40 | `/copilot` | unsupported question | Obtain an explicit refusal | No fabricated number or citation |

## 6. Measures and acceptance gates

Use the first attempt for task metrics. A task is **complete** only when the
decision goal is met without facilitator navigation. Evidence-chain success is
scored separately; a correct-looking answer without provenance fails it.

| Measure | Calculation | Gate |
|---|---|---|
| Task completion | completed first attempts / attempted scenarios | ≥ 85% overall and ≥ 70% in every scenario family |
| Evidence-chain success | completed chains / attempted scenarios | ≥ 90% overall; 100% for values used in a final decision |
| Critical error rate | unsafe or materially misleading outcomes / attempts | 0 |
| Unassisted completion | attempts completed without navigation help / attempts | ≥ 80% |
| Time on task | median by scenario family | Reported, not silently pooled; no gate until baseline exists |
| Confidence | participant rating 1–5 after each task | Median ≥ 4; never substitutes for correctness |
| Provenance latency | time from request to opening the decisive source | Median ≤ 120 seconds |

Any security bypass, fabricated citation, hidden missing value, source/result
mismatch, or irreversible action in the wrong environment is a critical error.

## 7. Recording template

Record one row per participant/scenario:

```text
session_id, participant_code, scenario_id, protocol_version, git_sha,
release_id, snapshot_ids, model_run_ids, started_at_utc, ended_at_utc,
completed, evidence_chain_complete, assistance_count, critical_error,
error_codes, confidence_1_to_5, notes_ref
```

Notes use controlled error codes plus redacted free text. Raw recordings, if
consented to, live outside Git with access control and retention dates. The
report publishes aggregate counts and anonymised findings, including failures
and scenarios not run.

## 8. Standard interview questions

Ask every participant the same questions after their assigned scenarios:

1. Which value would you rely on for the decision, and why?
2. Which source, time boundary and transformation produced it?
3. What uncertainty or missing evidence changes your decision?
4. Where did the product imply more certainty than the evidence supports?
5. What would you need to export or hand to a reviewer?
6. Which action felt unsafe, ambiguous or irreversible?
7. What information was present but difficult to locate?

## 9. Analysis and change control

Publish denominators, missing sessions and per-family results. Do not discard a
participant after seeing performance unless a preregistered exclusion applies:
withdrawn consent, technical session loss, or failure to meet the specialist
eligibility screen. Report excluded sessions and reasons.

After the first session, changes require a numbered amendment with timestamp,
author, reason and affected scenarios. Results before and after a material
amendment are reported separately. The final sign-off requires all acceptance
gates, no unresolved critical errors and links to the frozen evidence pack.

## 10. Exit and follow-up

Passing this protocol is evidence for the specialist-workspace and evidence-pack
phases; it does not close M3 or authorize deployment. Failed gates become
tracked issues with severity, owner and regression evidence. A rerun uses a new
manifest and reports both attempts.
