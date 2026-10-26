# Offline recovery inventory

`scripts/recovery_inventory.py <metadata.json>` checks one small, local metadata
document. It does not open the referenced archive, model, key or network address;
it does not start infrastructure, restore a database or deserialize a model.
It is not registered in the application or deployment path.

Exit 0 means **inventory complete**, not recovery ready or successful. Exit 3
means missing, contradictory or invalid declarations. Exit 2 means rejected input.
The output always has `referenced_artefacts_verified`, `recovery_verified`,
`rpo_verified` and `rto_verified` set to false, including on exit 0. A syntactically
valid hash does not establish provenance, authenticity, existence or access.

Schema version 1 is closed: all listed fields are required, extra fields and
duplicate JSON keys are refused. Hashes are lowercase SHA256 hex; Git SHA is
40 lowercase hex characters; image identity is `sha256:<64 hex>`.

| Object | Fields |
|---|---|
| root | format_version (integer 1), scenario (cold/warm), incident_at, release, database, model, dependencies_sha256, redis_policy_sha256, timing_plan_sha256 |
| release | git_sha, image_digest, config_sha256 |
| database | producer (daily_local/database_dump), snapshot_started_at, backup_sha256, includes_owners, includes_acl, roles_contract_sha256, owners_rebuild_sha256, acl_rebuild_sha256 |
| model | expected_source (active/seed), version (nonempty, ≤128 characters), run_id, bundle_sha256, artefact_sha256 |
| model.artefact_sha256 | model.pkl, scaler.pkl, best_threshold.pkl, meta.json; one SHA256 each |

The dependency contract identifies PostgreSQL version/extensions, runtime,
configuration and required services. The Redis policy and timing plan are
separate versioned documents; this helper cannot evaluate their content.

`daily_local` specifically describes `scripts/backup_local_daily.sh` in this
release: its dump excludes owners and ACL. Declaring either included is a
contradiction. When excluded, each requires a non-null reconstruction-contract
hash. A dump declared to include them may set the corresponding reconstruction
hash to null. Cluster roles always require a contract; a single-database dump
does not contain them. `database_dump` is only a declaration of another reviewed
producer; supplying it must not be used to relabel a daily-local archive.

Active requires a canonical UUID run_id. Seed requires null run_id and an
explicit version. The four listed champion files are the loader's required set;
the runtime bundle must cover the additional state and optional files needed
for the chosen recovery. This helper cannot enumerate that bundle, assess its
trust or establish consistency with the database. Unexpected seed fallback must
be caught by a real runtime check later.

Timestamps require explicit timezones. Snapshot start ≤ incident ≤ assessment
time, and the declared DB loss bound may not exceed 24 hours. This arithmetic
uses declarations, not a verified backup timestamp. Old incident scenarios are
allowed for rehearsal; passing one does not establish freshness today. RTO
requires an actual timed exercise, from the agreed start through usable service,
including provisioning, obtaining the backup and secrets, model restoration and
QA. A timing-plan hash is not a measured duration.

The synthetic test fixture in `tests/test_recovery_inventory.py` demonstrates
the schema without any real credentials or archive. Use only nonsensitive
metadata: never put tokens, passwords, private keys or production rows here.
Error output uses fixed codes and does not echo supplied values. Input is
limited to 64 KiB and regular files; on POSIX it refuses symlink input.

This inventory is one preparation step for M0. Owner/ACL access tests, sequence
behavior, model loading/provenance, state consistency, full RPO/RTO and independent
sign-off remain separate acceptance evidence. No M0 closure follows from exit 0.
