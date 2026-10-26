"""Check declared recovery metadata; never access or attest the referenced artefacts."""
from datetime import datetime, timezone
import re
from uuid import UUID


ARTEFACTS = {"model.pkl", "scaler.pkl", "best_threshold.pkl", "meta.json"}
TOP = {"format_version", "scenario", "incident_at", "release", "database",
       "model", "dependencies_sha256", "redis_policy_sha256", "timing_plan_sha256"}
FIELDS = {
    "release": {"git_sha", "image_digest", "config_sha256"},
    "database": {"producer", "snapshot_started_at", "backup_sha256", "includes_owners",
                 "includes_acl", "roles_contract_sha256", "owners_rebuild_sha256",
                 "acl_rebuild_sha256"},
    "model": {"expected_source", "version", "run_id", "bundle_sha256", "artefact_sha256"},
}


def _hex(value, length=64):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{%d}" % length, value) is not None


def _time(value):
    if not isinstance(value, str):
        raise ValueError("timestamp required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("timezone required")
    return result.astimezone(timezone.utc)


def inspect_inventory(manifest, *, now):
    """Return fixed diagnostic codes, never arbitrary input values or secret material.

    A complete inventory only means all required declarations were supplied.
    Their truth, provenance and referenced bytes have NOT been verified.
    """
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("aware assessment time required")
    result = {"status": "blocked", "metadata_complete": False,
              "referenced_artefacts_verified": False, "recovery_verified": False,
              "rpo_verified": False, "rto_verified": False, "errors": [],
              "declared_db_loss_bound_seconds": None}
    errors = result["errors"]
    if not isinstance(manifest, dict) or set(manifest) != TOP:
        errors.append("manifest.fields")
        return result
    for name, fields in FIELDS.items():
        if not isinstance(manifest[name], dict) or set(manifest[name]) != fields:
            errors.append(name + ".fields")
    if errors:
        return result
    release, db, model = (manifest[name] for name in ("release", "database", "model"))
    if type(manifest["format_version"]) is not int or manifest["format_version"] != 1:
        errors.append("manifest.format_version")
    if manifest["scenario"] not in ("cold", "warm"):
        errors.append("manifest.scenario")
    for key in ("dependencies_sha256", "redis_policy_sha256", "timing_plan_sha256"):
        if not _hex(manifest[key]):
            errors.append("manifest." + key)
    if not _hex(release["git_sha"], 40):
        errors.append("release.git_sha")
    image = release["image_digest"]
    if not isinstance(image, str) or not image.startswith("sha256:") or not _hex(image[7:]):
        errors.append("release.image_digest")
    if not _hex(release["config_sha256"]):
        errors.append("release.config_sha256")
    if db["producer"] not in ("daily_local", "database_dump"):
        errors.append("database.producer")
    for key in ("backup_sha256", "roles_contract_sha256"):
        if not _hex(db[key]):
            errors.append("database." + key)
    for included, rebuild in (("includes_owners", "owners_rebuild_sha256"),
                              ("includes_acl", "acl_rebuild_sha256")):
        flag = db[included]
        if type(flag) is not bool:
            errors.append("database." + included)
        elif not flag and not _hex(db[rebuild]):
            errors.append("database." + rebuild)
        if db[rebuild] is not None and not _hex(db[rebuild]):
            errors.append("database." + rebuild)
        if db["producer"] == "daily_local" and flag is True:
            errors.append("database.daily_local_excludes_" + included.removeprefix("includes_"))
    try:
        snapshot = _time(db["snapshot_started_at"])
        incident = _time(manifest["incident_at"])
        if snapshot > incident or incident > now.astimezone(timezone.utc):
            errors.append("timing.order")
        else:
            bound = (incident - snapshot).total_seconds()
            result["declared_db_loss_bound_seconds"] = bound
            if bound > 86400:
                errors.append("timing.declared_db_loss_exceeds_24h")
    except (ValueError, TypeError, OverflowError):
        errors.append("timing.timestamps")
    if model["expected_source"] not in ("active", "seed"):
        errors.append("model.expected_source")
    if not isinstance(model["version"], str) or not model["version"].strip() or len(model["version"]) > 128:
        errors.append("model.version")
    if not _hex(model["bundle_sha256"]):
        errors.append("model.bundle_sha256")
    artefacts = model["artefact_sha256"]
    if (not isinstance(artefacts, dict) or set(artefacts) != ARTEFACTS
            or not all(_hex(value) for value in artefacts.values())):
        errors.append("model.artefact_sha256")
    if model["expected_source"] == "active":
        try:
            if not isinstance(model["run_id"], str) or str(UUID(model["run_id"])) != model["run_id"]:
                raise ValueError("canonical UUID required")
        except (ValueError, AttributeError):
            errors.append("model.run_id")
    elif model["run_id"] is not None:
        errors.append("model.seed_run_id_must_be_null")
    result["errors"] = sorted(set(errors))
    if not errors:
        result.update(status="inventory_complete", metadata_complete=True)
    return result
