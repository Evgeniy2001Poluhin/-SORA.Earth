"""One identity scheme for every dataset row, shared by both World Bank scripts.

`data/projects.csv` -- what the production model trains on today -- has no
project identifier at all, only free-text `name`. `fetch_wb_projects.py`
requests World Bank's own `id` from the API and reads it into a variable, but
never writes it to the output row; the variable is used only as a hash seed
for a synthetic duration jitter and then discarded. Deduplicating that file
against any future fetch by a stable key is not possible as things stand.
(docs/DATASET_AUDIT_2026-09-03.md has the full investigation.)

A bare `id` column is not the fix. Two pipelines write World Bank rows today
-- `fetch_wb_projects.py` and `enrich_worldbank_dataset.py` -- and a future
source is not ruled out; an unqualified ID column collides across sources by
construction. The identity is `(source, source_project_id)`, and the join key
used everywhere downstream is the composite string this module builds:

    worldbank:P505244

so a World Bank ID can never collide with some other source's own numbering,
whatever that numbering turns out to be.

## Synthetic rows do not get an invented stable ID

The temptation is to hash `name` and call that the ID. Refused here on
purpose: a hash of free text is a matching *heuristic* -- useful for
probabilistic overlap detection between two files -- and calling it identity
would let something that was never actually stable be relied on as if it
were. `data/projects.csv`'s existing synthetic rows have no generator-issued
ID recorded anywhere; there is nothing stable to recover. `synthetic_key()`
below returns `None` for exactly this reason, and every caller must handle
that explicitly rather than defaulting it to something that looks like a key.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable, Optional


class MissingSourceProjectId(ValueError):
    """A row from an external source carries no id. Refused, not filled in.

    An empty string and a present-but-blank field are the same failure: the
    row cannot be deduplicated against a future fetch, and silently accepting
    it reproduces the exact defect this module exists to close.
    """


def project_key(source: str, source_project_id: Optional[str]) -> str:
    """The composite identity key: `source:source_project_id`.

    Raises MissingSourceProjectId for an external source with no id, rather
    than returning a key built from an empty string -- `"worldbank:"` would
    look like a valid, if odd, key, and would silently collide with every
    other row that also failed to carry an id.
    """
    if not source:
        raise ValueError("source is required")
    sid = (source_project_id or "").strip()
    if not sid:
        raise MissingSourceProjectId(
            f"row from source={source!r} carries no source_project_id; "
            f"refusing to synthesise one"
        )
    return f"{source}:{sid}"


def synthetic_key(*_args, **_kwargs) -> None:
    """There is no stable ID for a synthetic row. This always returns None.

    Takes and ignores arguments so a caller migrating from "hash the name"
    can swap the call in place and see the signature stop making sense --
    rather than deleting the function and hand-rolling a hash again the next
    time someone needs "an ID, any ID" for a synthetic row.
    """
    return None


def name_match_heuristic(name: str) -> str:
    """A fuzzy matching key for probabilistic overlap detection ONLY.

    Not identity, not stored as `source_project_id`, and never compared with
    `==` to a real project_key. This exists because two files with no shared
    ID scheme still need *some* way to estimate overlap, and the audit
    document is explicit that such matching must be labelled as unproven
    rather than presented as deduplication.
    """
    normalised = " ".join(name.strip().lower().split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


def assert_unique_keys(keys: Iterable[str]) -> None:
    """Raise on the first duplicate composite key, naming it.

    Called after every row's key has already been computed (and therefore
    after MissingSourceProjectId would already have been raised for any row
    lacking one) -- so a duplicate found here is a genuine repeated
    (source, source_project_id) pair, not a collision between two rows that
    both lacked an id.
    """
    seen = set()
    for key in keys:
        if key in seen:
            raise ValueError(f"duplicate project_key: {key!r}")
        seen.add(key)


@dataclass(frozen=True)
class StageCounts:
    """Row counts at one point in a pipeline, for the manifest.

    A plain dict would do the same job with less typing at the call site, but
    it would also silently accept a misspelled stage name. This is the
    manifest's building block, not a general-purpose counter.
    """
    stage: str
    count: int
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        d = {"stage": self.stage, "count": self.count}
        if self.reason is not None:
            d["reason"] = self.reason
        return d


def content_sha256(path: str) -> str:
    """SHA-256 of a file's bytes, for the manifest's content_hash field."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def schema_hash(columns: Iterable[str]) -> str:
    """A hash of the column list, so a manifest can detect a schema change
    without a human diffing two long lists by eye."""
    joined = ",".join(columns)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def write_manifest(path: str, *, source_url: str, query_params: dict,
                    fetch_timestamp: str, commit_sha: str,
                    stage_counts: Iterable[StageCounts],
                    unique_ids: int, duplicate_ids: int,
                    columns: Iterable[str], output_path: Optional[str] = None) -> None:
    """Write the manifest atomically: a temp file, then one rename.

    A manifest left half-written by an interrupted run is worse than no
    manifest -- it would be read as if it described a completed dataset. The
    write goes to `path + '.tmp'` and is renamed into place only after the
    JSON is fully serialised and flushed, so a reader never observes a
    partial file at the final path.
    """
    columns = list(columns)
    manifest = {
        "source_url": source_url,
        "query_params": query_params,
        "fetch_timestamp": fetch_timestamp,
        "commit_sha": commit_sha,
        "stage_counts": [c.to_dict() for c in stage_counts],
        "unique_ids": unique_ids,
        "duplicate_ids": duplicate_ids,
        "schema_hash": schema_hash(columns),
        "columns": columns,
    }
    if output_path is not None:
        import os
        if os.path.exists(output_path):
            manifest["content_sha256"] = content_sha256(output_path)

    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        import os
        os.fsync(fh.fileno())
    import os
    os.replace(tmp_path, path)
