# Data Trust implementation specification

**Status:** approved direction; implementation not complete
**Roadmap phase:** 7 — Data Trust
**Baseline:** `main@5762cb9a0`

## Problem

SORA.Earth already records substantial provenance, but the guarantees stop at
different boundaries. `SOURCE_REGISTER` identifies every source and its
measurement kind; environmental observations preserve event, publication,
ingestion and update time; World Bank file pipelines write atomic manifests;
forecast reports carry a deterministic snapshot digest. These mechanisms do
not yet form one enforceable contract from source to every model run and user
visible result.

The remaining failure mode is a plausible output whose source is named but
whose schema, units, accepted range, freshness rule, licence, exact input bytes
or model-run linkage cannot be reconstructed.

## Goals

1. Every active source has one machine-readable contract covering access,
   rights, schema, units, temporal meaning, freshness, missingness and identity.
2. Every training or evaluation run references immutable input snapshots by
   digest; rebuilding a snapshot verifies its bytes rather than overwriting it.
3. Every material result can expose source, snapshot, as-of boundary, model/run
   and transformation identifiers without exposing credentials or server paths.
4. Contract, snapshot and lineage violations fail before model training or
   publication.
5. Existing provenance mechanisms remain the source of truth and are extended,
   not reimplemented beside a second registry.

## Non-goals

- Adding new external data sources without a declared product question.
- Claiming historical provenance that was never recorded.
- Reconstructing old mutable API responses from current publisher data.
- Storing credentials, complete raw production payloads or personal data in Git.
- Replacing the API contract inventory; data contracts describe datasets, not
  HTTP routing.

## Baseline inventory

The table is deliberately guarded against `SOURCE_REGISTER`. `Gap` describes
what remains unproven; it is not silently filled with an assumption.

| Source | Current kind | Current evidence | Gap before contract v1 |
|---|---|---|---|
| `openaq` | measured | source status, measured boundary, historical rows | endpoint/rights/schema/freshness contract |
| `openmeteo_air_quality` | modelled | CAMS distinction, hourly coverage, row kind | exact variables/units/ranges/model selection/licence |
| `openmeteo` | modelled | hourly regional coverage, temporal rows | exact variables/units/ranges/model selection/licence |
| `rosstat` | administrative snapshot | named 2024 module and period semantics | upstream references/licence/checksum/field contract |
| `sber_veb_baseline` | static baseline | explicit author-constant classification | origin/rights unresolved; literal schema and limitations |
| `world_bank` | administrative fetched | indicator source tags and periods | per-indicator units/ranges/licence/snapshot policy |
| `world_bank_projects` | administrative fetched | publisher project ids, lifecycle dates, sectors and financing | derived-field semantics/raw-byte retention/snapshot policy |
| `oecd` | administrative fetched | fallback source tags and source registration | period propagation, flow contracts, licence/snapshot policy |
| `benchmark` | administrative snapshot | per-field provenance notes and known unknowns | machine-readable field-level sources/periods/rights |
| `global_avg` | administrative snapshot | fallback boundary and known unknowns | machine-readable field-level sources/periods/rights |

## Users and stories

- As a specialist, I can open the evidence behind a value and determine who
  published it, what it measures, when it applied and what transformed it.
- As a model reviewer, I can reproduce the exact input snapshot and verify its
  digest before comparing a challenger with a baseline.
- As an operator, I see a clear contract failure when a publisher changes a
  schema or a dataset becomes stale; the pipeline does not silently adapt.
- As an auditor, I can trace an output to a release, model run, snapshot and
  registered source without access to production secrets.

## Contract v1

Each source contract must contain:

- stable source id, owner/publisher and SORA maintainer;
- measurement kind and whether the value is measured, modelled,
  administrative or an author constant;
- endpoint or repository artifact, authentication class and terms/licence URL;
- attribution requirements and a verification date; unknown rights are an
  explicit blocking state, not an empty string;
- geographic coverage, temporal frequency and temporal kind;
- freshness SLA and the axis used to measure it;
- field name, scalar type, unit, nullable state, valid range or enum;
- required identifiers and deterministic deduplication key;
- missing-value, fallback and source-disagreement policy;
- parser version and compatible contract version;
- redaction classification for raw payload fields.

Contract files are deterministic JSON validated by JSON Schema. The Python
source register remains authoritative for scheduling and measurement kind; a
test requires an exact one-to-one source set and matching status/kind.

## Immutable snapshot v1

A snapshot manifest contains:

```text
snapshot_id = sha256(canonical manifest without snapshot_id)
contract_id + contract_version
source ids and publisher request parameters (secrets removed)
fetch/event/as-of timestamps in UTC
raw and normalised content digests
row counts at each transformation stage
schema digest and parser Git SHA
quality-check results and exclusions
storage locator (logical id, never an absolute server path)
```

Writing is content-addressed and fail-closed:

1. write bytes and manifest to temporary files in the target filesystem;
2. flush and fsync;
3. verify hashes and schema;
4. atomically publish under the snapshot id;
5. refuse replacement when an existing id has different bytes;
6. fsync the directory where supported.

Local tests use `tmp_path`; no test writes tracked `data/` or `models/`.

## Run linkage

Training and evaluation accept snapshot ids, resolve them before work starts and
persist the ordered id list with the run. A run with mutable paths only is
refused. Model metadata and evaluation reports repeat the same ids, so registry,
report and served model cannot describe different inputs.

Legacy runs remain readable and explicitly report `snapshot_provenance=unknown`.
No snapshot id is invented retroactively.

## Evidence surface

The existing authenticated observation and model-health surfaces gain stable
logical evidence identifiers. A result may expose source id, contract version,
snapshot id, run id, model version, event/as-of time and limitations. It must
not expose tokens, query credentials, filesystem paths or raw protected rows.

## Phased implementation

### PR 1 — source contracts

- JSON Schema and one contract per `SOURCE_REGISTER` source;
- exact source-set/status/kind guard;
- field and unit validation for ingester normalised output;
- explicit `unverified` rights state where evidence is absent.

### PR 2 — snapshot store

- content-addressed manifest library;
- atomic, immutable local storage interface;
- deterministic canonicalisation and conflict refusal;
- migrate World Bank manifest writers to the shared library.

### PR 3 — run linkage

- schema migration for ordered snapshot references;
- training/evaluation refusal without resolved snapshots for new runs;
- metadata/report consistency guards;
- backward-compatible unknown state for legacy rows.

### PR 4 — evidence API and UI

- authenticated evidence lookup by logical identifiers;
- evidence panel used by the UAT scenarios;
- redaction and authorization tests;
- CSV/PDF/API exports carry the same identifiers.

## Acceptance criteria

- Source contract set equals `SOURCE_REGISTER` exactly.
- 100% of contract fields validate; unknown rights/provenance are visible.
- ≥99% of ingested records validate against the applicable field contract;
  invalid records are quarantined before publication.
- Rebuilding the same snapshot produces the same id and normalized bytes.
- Reusing an id for different bytes fails before mutation.
- Every new training/evaluation run stores at least one verified snapshot id.
- A report, registry row and served model agree on ordered snapshot ids.
- A specialist can traverse result → run → snapshot → source contract.
- Offline tests perform zero external network calls and leave Git status clean.

## Verification matrix

| Layer | Required evidence |
|---|---|
| Contract | JSON Schema tests, exact source-set guard, units/ranges fixtures |
| Snapshot | determinism, corruption, concurrent writer and atomicity tests |
| Database | PostgreSQL migration, constraints, legacy-read compatibility |
| Pipeline | targeted training/evaluation tests with missing/tampered snapshots |
| API | auth, redaction, missing evidence and source-to-result traversal |
| Product | preregistered specialist UAT evidence-chain scenarios |

## Risks and open decisions

- **Legal/data owner:** confirm licences and attribution for Rosstat-derived,
  Sber/VEB literal, benchmark and global-average fields; until confirmed their
  contracts stay `unverified` and cannot claim redistributable source data.
- **Storage:** choose the production content-addressed backend and retention
  policy before PR 2; the interface must permit local disk and object storage.
- **Raw payloads:** determine which payloads may be retained and for how long;
  digests alone are insufficient for replay, while unrestricted retention may
  violate publisher terms or privacy obligations.
- **Historical runs:** preserve `unknown`; do not fabricate snapshot ids from
  current files.

## Success measures

Leading: contract coverage, snapshot-link coverage on new runs, validation
failure counts and evidence lookup completion. Lagging: UAT evidence-chain
success ≥90%, zero unreproducible released evaluations and zero undisclosed
source/schema changes in a release evidence pack.
