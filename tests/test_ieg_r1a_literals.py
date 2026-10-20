"""Offline tests for scripts/ieg_r1a_literals.py (R1a spec sections 1-7, amended by 8 and 9). Synthetic data only.

Nothing here reads real data. Every test records each file opened through builtins.open or io.open;
a test fails if any recorded path lies under the research root. The synthetic snapshot tree is built
by tests/r1_synthetic_harness.py, which the R1 tests use as well.
"""

from __future__ import annotations

import builtins
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "ieg_r1a_literals.py"
_HARNESS = _REPO / "tests" / "r1_synthetic_harness.py"
_FORBIDDEN_ROOT = "sora-research"

_spec_h = importlib.util.spec_from_file_location("r1_synthetic_harness", _HARNESS)
H = importlib.util.module_from_spec(_spec_h)
sys.modules[_spec_h.name] = H
_spec_h.loader.exec_module(H)
r1 = H.r1

_spec_a = importlib.util.spec_from_file_location("ieg_r1a_literals", _SCRIPT)
A = importlib.util.module_from_spec(_spec_a)
sys.modules[_spec_a.name] = A
_spec_a.loader.exec_module(A)

ABSENT = H.ABSENT
SYNTH_ISO = "2026-10-20T10:00:00Z"
OWNER_HEX = "ab" * 32
ALL_KINDS_TEXT = (b"Satisfactory", b"literals_only", b'"values"')


# ---------------------------------------------------------------- guards (autouse)

@pytest.fixture(autouse=True)
def file_opens(monkeypatch):
    """Record every path opened in this test. Fail at teardown if one lies under the research root."""
    current: list[str] = []
    real_open = builtins.open

    def recording_open(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            current.append(os.fsdecode(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", recording_open)
    monkeypatch.setattr(io, "open", recording_open)
    yield current
    bad = [p for p in current if _FORBIDDEN_ROOT in p]
    assert not bad, f"test opened a path under the research root: {bad}"


@pytest.fixture(autouse=True)
def core_dumps_stub(monkeypatch):
    """Tests must not change the core limit of the pytest process. The real call is tested separately."""
    calls: list[bool] = []

    def stub():
        calls.append(True)
        A.CORE_DUMPS_DISABLED = True
        return True

    monkeypatch.setattr(A, "disable_core_dumps", stub)
    return calls


# ---------------------------------------------------------------- synthetic trees

def _j(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _patched(obj: dict, changes) -> dict:
    for key, value in (changes or {}).items():
        if value is H.REMOVE:
            obj.pop(key, None)
        else:
            obj[key] = value
    return obj


def _own_sha() -> str:
    return H.sha(_SCRIPT.read_bytes())


class Tree:
    def __init__(self, root, world, paths, pins):
        self.root = root
        self.world = world
        self.paths = paths
        self.pins = pins

    @property
    def permit(self):
        return self.paths["permit"]

    def argv(self):
        return ["--real", "--r1a-permit", str(self.permit), "--out", str(self.paths["out"])]

    def install(self, monkeypatch):
        monkeypatch.setattr(A, "REAL_PINS", self.pins)
        monkeypatch.setattr(A, "REAL_SNAPSHOT_DIR", str(self.paths["snapshot"]))
        monkeypatch.setattr(A, "TRUST_PATH", self.paths["trust"])


def build_tree(root, projects=None, overrides=None, world_kwargs=None, world_hook=None) -> Tree:
    """A complete synthetic R1a chain: snapshot, R0, procedure, permit and trust. Each hash names the bytes written.

    overrides maps a document (r0, procedure, permit, trust) to field changes; H.REMOVE drops a field. A document
    that refers to another takes its hash from the bytes written, so one override is one defect. world_hook, if
    given, maps the harness World to a changed one before anything is written (its pins follow the new blobs)."""
    ov = dict(overrides or {})
    root = Path(root)
    world = H.make_world(projects or H.DEMO_PROJECTS, **(world_kwargs or {}))
    if world_hook is not None:
        world = world_hook(world)
    paths = {
        "snapshot": root / "snapshot", "r0": root / "r0.json", "procedure": root / "procedure.json",
        "permit": root / "permit.json", "trust": root / "trust.json", "out": root / "out",
    }
    for key, data in world.blobs.items():
        if key.startswith("snapshot/"):
            target = paths["snapshot"] / key[len("snapshot/"):]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    pins = A.Pins(snapshot_manifest_sha=world.pins.snapshot_manifest_sha,
                  snapshot_page_digest=world.pins.snapshot_page_digest,
                  structural_manifest_sha=world.pins.structural_manifest_sha)
    proc = _j(_patched({"allowed_r1a_script_sha256": _own_sha(), "allowed_result_kind": "literals_only"},
                       ov.get("procedure")))
    r0 = _j(_patched({
        "stage": "R0",
        "snapshot_manifest_sha256": pins.snapshot_manifest_sha,
        "snapshot_page_digest": pins.snapshot_page_digest,
        "structural_receipt_manifest_sha256": pins.structural_manifest_sha,
        "d1_policy_sha256": world.pins.d1_policy_sha,
        "owner_x25519_public_key_hex": OWNER_HEX,
        "r1a_procedure_sha256": H.sha(proc),
        "owner_decision": "подписываю R0",
        "date_utc": SYNTH_ISO,
    }, ov.get("r0")))
    permit = _j(_patched({
        "stage": "R1a-permit",
        "r0_receipt_sha256": H.sha(r0),
        "r1a_procedure_sha256": H.sha(proc),
        "script_sha256": _own_sha(),
        "owner_decision": "разрешаю R1a",
        "date_utc": SYNTH_ISO,
    }, ov.get("permit")))
    trust = _j(_patched({
        "r1a_permit_sha256": H.sha(permit),
        "r0_receipt_path": str(paths["r0"]),
        "r1a_procedure_path": str(paths["procedure"]),
    }, ov.get("trust")))
    for key, data in (("r0", r0), ("procedure", proc), ("permit", permit), ("trust", trust)):
        paths[key].write_bytes(data)
    return Tree(root, world, paths, pins)


def _proj(pid, country, *records):
    return {"pid": pid, "country": country, "records": list(records)}


def _rec(fy, outcome=ABSENT):
    rec = {"fy": fy}
    if outcome is not ABSENT:
        rec["outcome"] = outcome
    return rec


def _partial(out: Path) -> Path:
    return out.with_name(out.name + ".partial")


def _tree_files(root) -> set:
    return {p for p in Path(root).rglob("*") if p.is_file()}


def _run(tree, capsys=None):
    return A.main(tree.argv())


# ---------------------------------------------------------------- variants of a synthetic snapshot

def _digest(manifest: dict) -> str:
    lines = sorted(f"{p['sha256']}  {p['file']}"
                   for ds in manifest["datasets"].values() for p in ds.get("pages", []))
    return H.sha("".join(line + "\n" for line in lines).encode("utf-8"))


def _manifest_bytes(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, indent=1).encode("utf-8")


def _edit_page(manifest, blobs, name, fn):
    obj = json.loads(blobs[f"snapshot/{name}"].decode("utf-8"))
    fn(obj)
    _set_page_bytes(manifest, blobs, name, json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8"))


def _set_page_bytes(manifest, blobs, name, data: bytes):
    blobs[f"snapshot/{name}"] = data
    for entry in manifest["datasets"]["ieg"]["pages"]:
        if entry["file"] == name:
            entry["sha256"] = H.sha(data)


def _variant(world, edit, *, regen=True):
    """A copy of the world with edit(manifest, blobs) applied.

    With regen, the snapshot digest and both pin sets are recomputed from the edited manifest, so the edit is the
    only defect. Without it the stored digest is left as the edit leaves it, and the digest pin stays the honest one."""
    blobs = dict(world.blobs)
    manifest = json.loads(blobs[r1.K_SNAP_MANIFEST].decode("utf-8"))
    edit(manifest, blobs)
    if regen:
        manifest["snapshot_digest"] = _digest(manifest)
    page_digest = manifest["snapshot_digest"] if regen else world.pins.snapshot_page_digest
    manifest_bytes = _manifest_bytes(manifest)
    blobs[r1.K_SNAP_MANIFEST] = manifest_bytes
    pins = replace(world.pins, snapshot_manifest_sha=H.sha(manifest_bytes), snapshot_page_digest=page_digest)
    return blobs, pins


def _first(manifest) -> str:
    return manifest["datasets"]["ieg"]["pages"][0]["file"]


def _a_pins(pins) -> A.Pins:
    return A.Pins(snapshot_manifest_sha=pins.snapshot_manifest_sha,
                  snapshot_page_digest=pins.snapshot_page_digest,
                  structural_manifest_sha=pins.structural_manifest_sha)


def _ieg_pages(blobs, manifest_bytes) -> dict:
    names = [p["file"] for p in json.loads(manifest_bytes)["datasets"]["ieg"]["pages"]]
    return {name: blobs[f"snapshot/{name}"] for name in names}


def _r1_accepts(blobs, pins) -> bool:
    try:
        r1.validate_bytes(blobs, pins)
        return True
    except (r1.PinRefused, r1.IntegrityError):
        return False


def _r1a_accepts(blobs, pins) -> bool:
    manifest_bytes = blobs[r1.K_SNAP_MANIFEST]
    try:
        A.validate_snapshot_bytes(manifest_bytes, _ieg_pages(blobs, manifest_bytes), _a_pins(pins))
        return True
    except (A.PinRefused, A.IntegrityError):
        return False


def _literals_of(world) -> bytes:
    blobs = world.blobs
    items = A.validate_snapshot_bytes(blobs[r1.K_SNAP_MANIFEST], _ieg_pages(blobs, blobs[r1.K_SNAP_MANIFEST]),
                                      _a_pins(world.pins))
    return A.literals_bytes(A.extract_literals(items))


# ---------------------------------------------------------------- section 3: the result

DEMO_LITERAL_PROJECTS = [
    _proj("P000001", "ARG", _rec(2010, "Satisfactory")),
    _proj("P000002", "ARG", _rec(2011, "satisfactory")),
    _proj("P000003", "BRA", _rec(2012, "Unsatisfactory")),
    _proj("P000004", "BRA", _rec(2013, "Not Rated")),
    _proj("P000005", "CHL", _rec(2014, "  Highly   Satisfactory ")),
    _proj("P000006", "CHL", _rec(2015, "Highly Satisfactory")),
    _proj("P000007", "PER", _rec(2016, None)),
    _proj("P000008", "PER", _rec(2017, "   ")),
    _proj("P000009", "PER", _rec(2018, ABSENT)),
    _proj("P000010", "PER", _rec(2019, True)),
    _proj("P000011", "PER", _rec(2020, 3)),
    _proj("P000012", "PER", _rec(2021, 1.5)),
    _proj("P000013", "PER", _rec(2022, [1])),
    _proj("P000014", "PER", _rec(2023, {"a": 1})),
]

EXPECTED_LITERALS = (
    '{"kind":"literals_only","values":['
    '["nonstring","bool"],["nonstring","float"],["nonstring","int"],["nonstring","list"],["nonstring","object"],'
    '["string","Highly Satisfactory"],["string","Not Rated"],["string","Satisfactory"],'
    '["string","Unsatisfactory"],["string","satisfactory"]]}\n'
).encode("utf-8")


def test_result_is_the_sorted_distinct_typed_literals_and_nothing_else():
    world = H.make_world(DEMO_LITERAL_PROJECTS)
    assert _literals_of(world) == EXPECTED_LITERALS


def test_absent_null_and_empty_values_never_reach_the_result():
    data = _literals_of(H.make_world(DEMO_LITERAL_PROJECTS)).decode("utf-8")
    for kind in ("missing", "null", "empty"):
        assert f'"{kind}"' not in data


def test_records_below_the_maximum_fy_are_read_too():
    projects = [_proj("P000001", "ARG", _rec(2010, "Moderately Satisfactory"), _rec(2015, "Satisfactory"))]
    data = _literals_of(H.make_world(projects)).decode("utf-8")
    assert '["string","Moderately Satisfactory"]' in data and '["string","Satisfactory"]' in data


def test_a_frequency_is_not_in_the_result():
    projects = [_proj(f"P00{i:04d}", "ARG", _rec(2010, "Satisfactory")) for i in range(1, 6)]
    assert _literals_of(H.make_world(projects)) == (
        '{"kind":"literals_only","values":[["string","Satisfactory"]]}\n'.encode("utf-8"))


def test_canonical_outcome_equals_the_r1_canonicalization_on_a_grid():
    grid = [ABSENT, None, "", "   ", "Satisfactory", " Satisfactory ", "A  B\tC\n", "NULL", "null", "0",
            0, 1, True, False, 1.0, 0.5, [], [1], {}, {"k": "v"}, "é", " x "]
    for value in grid:
        present = value is not ABSENT
        val = None if value is ABSENT else value
        assert A.canonical_outcome(present, val) == r1.canonical_outcome(present, val), value


def test_safe_name_accepts_and_refuses_what_r1_does():
    names = ["a.json", "ieg/p.json", "", "/x", "a\\b", "../x", "a/./b", "a//b", "a/..", "x/y/z.json", 5, None]
    for name in names:
        r1_ok = _accepts(lambda: r1._safe_name(name))
        a_ok = _accepts(lambda: A._safe_name(name))
        assert r1_ok == a_ok, name


def _accepts(fn) -> bool:
    try:
        fn()
        return True
    except (r1.IntegrityError, A.IntegrityError):
        return False


def test_permuted_pages_and_records_give_the_same_literals():
    base = _literals_of(H.make_world(DEMO_LITERAL_PROJECTS))
    for seed in (1, 2, 3):
        assert _literals_of(H.make_world(DEMO_LITERAL_PROJECTS, shuffle_seed=seed, page_size=3)) == base


def test_a_permuted_chain_writes_byte_identical_literals(tmp_path, monkeypatch):
    first = build_tree(tmp_path / "a", DEMO_LITERAL_PROJECTS)
    second = build_tree(tmp_path / "b", DEMO_LITERAL_PROJECTS, world_kwargs={"shuffle_seed": 9, "page_size": 1})
    for tree in (first, second):
        tree.install(monkeypatch)
        assert _run(tree) == 0
    assert (first.paths["out"] / A.RESULT_FILE).read_bytes() == (second.paths["out"] / A.RESULT_FILE).read_bytes()


def test_the_same_literal_set_with_other_frequencies_and_windows_gives_identical_bytes(tmp_path, monkeypatch):
    dev_heavy = [
        _proj("P000001", "ARG", _rec(2010, "Satisfactory")),
        _proj("P000002", "ARG", _rec(2011, "Satisfactory")),
        _proj("P000003", "BRA", _rec(2012, "Satisfactory")),
        _proj("P000004", "BRA", _rec(2013, "Unsatisfactory")),
    ]
    hold_heavy = [
        _proj("P000005", "CHL", _rec(2022, "Unsatisfactory")),
        _proj("P000006", "CHL", _rec(2023, "Unsatisfactory")),
        _proj("P000007", "PER", _rec(2024, "Unsatisfactory")),
        _proj("P000008", "PER", _rec(2025, "Satisfactory"), _rec(2020, ABSENT)),
    ]
    trees = [build_tree(tmp_path / "x", dev_heavy), build_tree(tmp_path / "y", hold_heavy)]
    outputs = []
    for tree in trees:
        tree.install(monkeypatch)
        assert _run(tree) == 0
        outputs.append((tree.paths["out"] / A.RESULT_FILE).read_bytes())
    assert outputs[0] == outputs[1]
    assert outputs[0] == (
        '{"kind":"literals_only","values":[["string","Satisfactory"],["string","Unsatisfactory"]]}\n'
    ).encode("utf-8")


def test_result_is_one_document_with_exactly_two_keys_and_a_newline():
    data = _literals_of(H.make_world(DEMO_LITERAL_PROJECTS))
    assert data.endswith(b"\n") and not data.endswith(b"\n\n")
    assert set(json.loads(data.decode("utf-8"))) == {"kind", "values"}


def test_result_size_limit_is_exact(tmp_path, monkeypatch, capsys):
    tree = build_tree(tmp_path / "tree", DEMO_LITERAL_PROJECTS)
    tree.install(monkeypatch)
    size = len(EXPECTED_LITERALS)
    monkeypatch.setattr(A, "RESULT_LIMIT", size)
    assert _run(tree) == 0
    capsys.readouterr()
    shutil.rmtree(tree.paths["out"])
    monkeypatch.setattr(A, "RESULT_LIMIT", size - 1)
    before = _tree_files(tree.root)
    assert _run(tree) == 5
    captured = capsys.readouterr()
    assert captured.err == "result_limit_exceeded\n" and captured.out == ""
    _assert_no_result(tree, before)


def _assert_no_result(tree, before):
    out = tree.paths["out"]
    assert not out.exists() and not _partial(out).exists()
    for path in _tree_files(tree.root) - before:
        data = path.read_bytes()
        for text in ALL_KINDS_TEXT:
            assert text not in data, f"a literal reached a file the run created: {path.name}"


# ---------------------------------------------------------------- section 2: projection

def test_projection_hook_keeps_only_outcome_and_the_page_keys():
    page = b'{"count":1,"data":[{"project_id":"P000001","evaluation_fy":"2010","outcome":"A","x":{"y":1}}]}'
    obj = json.loads(page.decode("utf-8"), object_pairs_hook=A._projection_hook)
    assert set(obj) == {"count", "data"}
    assert obj["data"] == [{"outcome": "A"}]


def test_projection_keeps_an_absent_outcome_absent():
    obj = json.loads(b'{"count":1,"data":[{"project_id":"P000001"}]}', object_pairs_hook=A._projection_hook)
    assert obj["data"] == [{}]


def test_forbidden_markers_never_reach_the_output_stdout_or_stderr(tmp_path, monkeypatch, capsys):
    tree = build_tree(tmp_path / "tree")
    tree.install(monkeypatch)
    assert _run(tree) == 0
    captured = capsys.readouterr()
    result = (tree.paths["out"] / A.RESULT_FILE).read_bytes()
    texts = [str(v).encode("utf-8") for k, v in H.FORBIDDEN_MARKERS.items()]
    texts += [pid.encode("utf-8") for pid in ("P000001", "P000005", "ARG", "BRA", "CHL", "PER", "2012", "2024")]
    for blob in (result, captured.out.encode("utf-8"), captured.err.encode("utf-8")):
        for text in texts:
            assert text not in blob, text


# ---------------------------------------------------------------- section 5: files and receipt

def test_the_output_folder_holds_exactly_the_literals_and_the_receipt(tmp_path, monkeypatch, capsys):
    tree = build_tree(tmp_path / "tree", DEMO_LITERAL_PROJECTS)
    tree.install(monkeypatch)
    assert _run(tree) == 0
    out = tree.paths["out"]
    assert sorted(p.name for p in out.iterdir()) == sorted([A.RECEIPT_FILE, A.RESULT_FILE])
    receipt = json.loads((out / A.RECEIPT_FILE).read_text(encoding="utf-8"))
    assert set(receipt) == {
        "stage", "r0_receipt_sha256", "r1a_procedure_sha256", "snapshot_manifest_sha256", "snapshot_page_digest",
        "r1a_script_sha256", "result_sha256", "result_kind", "owner_decision", "date_utc"}
    assert receipt["stage"] == "R1a" and receipt["result_kind"] == "literals_only"
    assert receipt["result_sha256"] == H.sha((out / A.RESULT_FILE).read_bytes())
    assert receipt["r1a_script_sha256"] == _own_sha()
    assert receipt["snapshot_manifest_sha256"] == tree.pins.snapshot_manifest_sha
    assert receipt["owner_decision"] == "разрешаю R1a"
    assert receipt["date_utc"].endswith("Z") and len(receipt["date_utc"]) == 20
    captured = capsys.readouterr()
    assert captured.out == "ok\n" and captured.err == ""


def test_the_end_to_end_run_writes_the_expected_literals(tmp_path, monkeypatch):
    tree = build_tree(tmp_path / "tree", DEMO_LITERAL_PROJECTS)
    tree.install(monkeypatch)
    assert _run(tree) == 0
    assert (tree.paths["out"] / A.RESULT_FILE).read_bytes() == EXPECTED_LITERALS


# ---------------------------------------------------------------- section 8.2 and 9.2: the snapshot

def test_the_honest_snapshot_is_accepted_by_both_checks():
    world = H.make_world(DEMO_LITERAL_PROJECTS)
    assert _r1_accepts(world.blobs, world.pins) is True
    assert _r1a_accepts(world.blobs, world.pins) is True


def _set_count(o):
    o["count"] = o["count"] + 1


def _record_changed(m, b):
    m["datasets"]["ieg"]["pages"][0]["records"] += 1


def _api_total(m, b):
    m["datasets"]["ieg"]["api_total"] += 1


def _other_dataset(m, b):
    m["datasets"]["other"] = {"api_total": 1, "pages": [{"file": "other/p.json", "sha256": "1" * 64, "records": 1}]}


EQUIVALENCE = [
    # id, edit, regen, accepted by both checks
    ("ieg-page-bytes-changed-hash-stale",
     lambda m, b: b.__setitem__(f"snapshot/{_first(m)}", b[f"snapshot/{_first(m)}"] + b" "), True, False),
    ("manifest-page-hash-wrong",
     lambda m, b: m["datasets"]["ieg"]["pages"][0].__setitem__("sha256", "0" * 64), True, False),
    ("digest-stored-wrong",
     lambda m, b: m.__setitem__("snapshot_digest", "0" * 64), False, False),
    ("count-changed-consistently",
     lambda m, b: _edit_page(m, b, _first(m), _set_count), True, False),
    ("records-changed-consistently", _record_changed, True, False),
    ("api-total-changed-consistently", _api_total, True, False),
    ("page-not-json-hash-consistent",
     lambda m, b: _set_page_bytes(m, b, _first(m), b"{"), True, False),
    ("record-not-an-object",
     lambda m, b: _edit_page(m, b, _first(m), lambda o: o["data"].__setitem__(0, 5)), True, False),
    ("other-dataset-pages-are-not-read", _other_dataset, True, True),
    ("outcome-changed-consistently",
     lambda m, b: _edit_page(m, b, _first(m), lambda o: o["data"][0].__setitem__("outcome", "Unsatisfactory")),
     True, True),
]


@pytest.mark.parametrize("name, edit, regen, accepted", EQUIVALENCE, ids=[e[0] for e in EQUIVALENCE])
def test_r1_and_r1a_accept_and_refuse_the_same_snapshot_substitutions(name, edit, regen, accepted):
    world = H.make_world(DEMO_LITERAL_PROJECTS)
    blobs, pins = _variant(world, edit, regen=regen)
    assert _r1_accepts(blobs, pins) is accepted
    assert _r1a_accepts(blobs, pins) is accepted


@pytest.mark.parametrize("name", ["../x.json", "/x.json", "a\\b.json", "", "ieg//p.json", "./p.json",
                                  "ieg/../p.json", 5, None])
def test_an_unsafe_file_name_is_an_integrity_error_in_the_ieg_dataset(name):
    world = H.make_world(DEMO_LITERAL_PROJECTS)

    def edit(m, b):
        m["datasets"]["ieg"]["pages"][0]["file"] = name
    blobs, pins = _variant(world, edit)
    with pytest.raises(A.IntegrityError):
        A.validate_snapshot_bytes(blobs[r1.K_SNAP_MANIFEST], {}, _a_pins(pins))
    assert _r1_accepts(blobs, pins) is False


@pytest.mark.parametrize("name", ["../x.json", "/x.json", "a\\b.json", "ieg/../p.json"])
def test_an_unsafe_file_name_in_another_dataset_is_refused(name):
    world = H.make_world(DEMO_LITERAL_PROJECTS)

    def edit(m, b):
        m["datasets"]["other"] = {"api_total": 1, "pages": [{"file": name, "sha256": "1" * 64, "records": 1}]}
    blobs, pins = _variant(world, edit)
    with pytest.raises(A.IntegrityError):
        A.validate_snapshot_bytes(blobs[r1.K_SNAP_MANIFEST], {}, _a_pins(pins))


def _manifest_case(obj):
    """Bytes and pins for a manifest object. The digest is set when the object allows one."""
    try:
        obj["snapshot_digest"] = _digest(obj)
    except Exception:
        pass
    data = _manifest_bytes(obj)
    return data, A.Pins(snapshot_manifest_sha=H.sha(data), snapshot_page_digest=obj.get("snapshot_digest", "0" * 64),
                        structural_manifest_sha="0" * 64)


MALFORMED_MANIFESTS = [
    ("no-datasets", {"x": 1}),
    ("datasets-is-a-list", {"datasets": []}),
    ("ieg-block-is-a-string", {"datasets": {"ieg": "x"}}),
    ("ieg-pages-missing", {"datasets": {"ieg": {"api_total": 0}}}),
    ("ieg-api-total-is-bool", {"datasets": {"ieg": {"pages": [], "api_total": True}}}),
    ("ieg-api-total-is-string", {"datasets": {"ieg": {"pages": [], "api_total": "0"}}}),
    ("ieg-page-is-a-string", {"datasets": {"ieg": {"pages": ["x"], "api_total": 0}}}),
    ("other-page-without-sha", {"datasets": {"other": {"pages": [{"file": "a.json", "records": 0}]}}}),
    ("other-pages-not-a-list", {"datasets": {"other": {"pages": "x"}}}),
    ("unsafe-ieg-file", {"datasets": {"ieg": {"pages": [{"file": "/abs.json", "sha256": "0" * 64, "records": 0}],
                                              "api_total": 0}}}),
    ("no-ieg-dataset", {"datasets": {"other": {"pages": []}}}),
]


@pytest.mark.parametrize("name, obj", MALFORMED_MANIFESTS, ids=[m[0] for m in MALFORMED_MANIFESTS])
def test_a_malformed_manifest_is_an_integrity_error(name, obj):
    data, pins = _manifest_case(obj)
    with pytest.raises(A.IntegrityError):
        A.validate_snapshot_bytes(data, {}, pins)


def test_a_manifest_that_is_not_json_is_an_integrity_error():
    raw = b"{not json"
    with pytest.raises(A.IntegrityError):
        A.validate_snapshot_bytes(raw, {}, A.Pins(H.sha(raw), "0" * 64, "0" * 64))


def test_an_empty_ieg_snapshot_has_an_empty_result():
    data, pins = _manifest_case({"datasets": {"ieg": {"pages": [], "api_total": 0}}})
    items = A.validate_snapshot_bytes(data, {}, pins)
    assert items == []
    assert A.literals_bytes(A.extract_literals(items)) == b'{"kind":"literals_only","values":[]}\n'


def test_a_wrong_digest_stored_in_the_manifest_is_a_pin_refusal():
    obj = {"datasets": {"ieg": {"pages": [], "api_total": 0}}, "snapshot_digest": "0" * 64}
    data = _manifest_bytes(obj)
    with pytest.raises(A.PinRefused):
        A.validate_snapshot_bytes(data, {}, A.Pins(H.sha(data), H.sha(b""), "0" * 64))


@pytest.mark.parametrize("edit, expected", [
    (lambda m, b: _edit_page(m, b, _first(m), lambda o: o.pop("count")), A.IntegrityError),
    (lambda m, b: _edit_page(m, b, _first(m), lambda o: o.__setitem__("data", "x")), A.IntegrityError),
    (lambda m, b: _edit_page(m, b, _first(m), _set_count), A.IntegrityError),
    (_record_changed, A.IntegrityError),
    (lambda m, b: _edit_page(m, b, _first(m), lambda o: o["data"].__setitem__(0, "not an object")),
     A.IntegrityError),
    (lambda m, b: _set_page_bytes(m, b, _first(m), b"[]"), A.IntegrityError),
    (lambda m, b: _set_page_bytes(m, b, _first(m), b"\xff\xfe"), A.IntegrityError),
    (lambda m, b: b.__setitem__(f"snapshot/{_first(m)}", b[f"snapshot/{_first(m)}"] + b" "), A.PinRefused),
], ids=["no-count", "data-not-a-list", "count-wrong", "records-wrong", "record-not-object", "page-is-a-list",
        "page-not-utf8", "stale-page-hash"])
def test_a_malformed_or_substituted_page_is_refused(edit, expected):
    world = H.make_world(DEMO_LITERAL_PROJECTS)
    blobs, pins = _variant(world, edit)
    manifest_bytes = blobs[r1.K_SNAP_MANIFEST]
    with pytest.raises(expected):
        A.validate_snapshot_bytes(manifest_bytes, _ieg_pages(blobs, manifest_bytes), _a_pins(pins))


def test_each_pin_is_checked_and_an_unset_pin_refuses():
    world = H.make_world(DEMO_LITERAL_PROJECTS)
    manifest = world.blobs[r1.K_SNAP_MANIFEST]
    pages = _ieg_pages(world.blobs, manifest)
    good = _a_pins(world.pins)
    for bad in (replace(good, snapshot_manifest_sha="0" * 64),
                replace(good, snapshot_page_digest="0" * 64),
                replace(good, snapshot_manifest_sha=None),
                replace(good, snapshot_page_digest=None)):
        with pytest.raises(A.PinRefused):
            A.validate_snapshot_bytes(manifest, pages, bad)


# ---------------------------------------------------------------- section 4 and 8.1: chain, trust, exit 3

def test_the_shipped_trust_file_holds_three_nulls():
    assert json.loads((_REPO / "scripts" / "ieg_r1a_literals_trust.json").read_text(encoding="utf-8")) == {
        key: None for key in A.TRUST_KEYS}


def test_real_mode_refuses_with_the_shipped_trust_and_opens_only_the_trust(tmp_path, file_opens):
    permit = tmp_path / "permit.json"
    permit.write_bytes(b"{}")
    out = tmp_path / "out"
    file_opens.clear()
    assert A.main(["--real", "--r1a-permit", str(permit), "--out", str(out)]) == 3
    assert not out.exists()
    assert set(file_opens) <= {str(A.TRUST_PATH)}


def test_the_real_chain_on_a_synthetic_tree_exits_0(tmp_path, monkeypatch):
    tree = build_tree(tmp_path / "tree")
    tree.install(monkeypatch)
    assert _run(tree) == 0


def test_the_core_limit_is_set_before_the_first_page_is_read(tmp_path, monkeypatch, core_dumps_stub):
    tree = build_tree(tmp_path / "tree")
    tree.install(monkeypatch)
    real_read = A._read

    def checked_read(path, label):
        if label.startswith("snapshot"):
            assert core_dumps_stub, "a snapshot file was read before the core limit was set"
        return real_read(path, label)
    monkeypatch.setattr(A, "_read", checked_read)
    assert _run(tree) == 0


def test_a_core_limit_that_cannot_be_set_refuses_with_no_read(tmp_path, monkeypatch, file_opens, capsys):
    tree = build_tree(tmp_path / "tree")
    tree.install(monkeypatch)
    monkeypatch.setattr(A, "disable_core_dumps", lambda: False)
    file_opens.clear()
    assert _run(tree) == 3
    assert capsys.readouterr().err == "refused\n"
    assert file_opens == []


def _snapshot_touched(file_opens, tree):
    return [p for p in file_opens if str(tree.paths["snapshot"]) in p]


# "wrong" values: pins where R1a has one, and a malformed value where it has none. R1a does not pin
# R0.d1_policy_sha256 or R0.owner_x25519_public_key_hex (spec 8.1 pins only the snapshot and the structural
# receipt), so for those two only the format is checked, and their "wrong" value is not a well-formed hash.
WRONG = {
    "r0": {"stage": "R1", "snapshot_manifest_sha256": "0" * 64, "snapshot_page_digest": "0" * 64,
           "structural_receipt_manifest_sha256": "0" * 64, "d1_policy_sha256": "not-hex",
           "owner_x25519_public_key_hex": "not-hex", "r1a_procedure_sha256": "0" * 64,
           "owner_decision": "да", "date_utc": "2026-10-20"},
    "procedure": {"allowed_r1a_script_sha256": "0" * 64, "allowed_result_kind": "literals_with_counts"},
    "permit": {"stage": "R1", "r0_receipt_sha256": "0" * 64, "r1a_procedure_sha256": "0" * 64,
               "script_sha256": "0" * 64, "owner_decision": "да", "date_utc": "2026-10-20"},
}
FIELDS = {"r0": A.R0_FIELDS, "procedure": A.PROCEDURE_FIELDS, "permit": A.PERMIT_FIELDS}
ENVELOPE_CASES = [
    (doc, field, mode)
    for doc in ("r0", "procedure", "permit")
    for field in FIELDS[doc]
    for mode in ("remove", "null", "extra", "wrong")
    if not (mode == "wrong" and field not in WRONG[doc])
]


def _mode_change(doc, field, mode):
    if mode == "remove":
        return {field: H.REMOVE}
    if mode == "null":
        return {field: None}
    if mode == "extra":
        return {"note": "x"}
    return {field: WRONG[doc][field]}


@pytest.mark.parametrize("doc, field, mode", ENVELOPE_CASES, ids=[f"{d}-{f}-{m}" for d, f, m in ENVELOPE_CASES])
def test_each_field_of_r0_procedure_and_permit_refuses_with_exit_3_and_no_snapshot_read(
        doc, field, mode, tmp_path, monkeypatch, file_opens):
    tree = build_tree(tmp_path / "tree", overrides={doc: _mode_change(doc, field, mode)})
    tree.install(monkeypatch)
    file_opens.clear()
    assert _run(tree) == 3
    assert _snapshot_touched(file_opens, tree) == []
    assert not tree.paths["out"].exists()


TRUST_CASES = [
    ("permit-hash-wrong", {"trust": {"r1a_permit_sha256": "0" * 64}}, None),
    ("procedure-path-null", {"trust": {"r1a_procedure_path": None}}, None),
    ("r0-path-empty", {"trust": {"r0_receipt_path": ""}}, None),
    ("trust-extra-key", {"trust": {"note": "x"}}, None),
    ("trust-missing-key", {"trust": {"r0_receipt_path": H.REMOVE}}, None),
    ("permit-bytes-changed", {}, lambda t: t.paths["permit"].write_bytes(t.paths["permit"].read_bytes() + b" ")),
    ("r0-bytes-changed", {}, lambda t: t.paths["r0"].write_bytes(t.paths["r0"].read_bytes() + b" ")),
    ("procedure-bytes-changed", {}, lambda t: t.paths["procedure"].write_bytes(
        t.paths["procedure"].read_bytes() + b" ")),
]


@pytest.mark.parametrize("case", TRUST_CASES, ids=[c[0] for c in TRUST_CASES])
def test_each_trust_or_link_defect_refuses_with_exit_3_and_no_snapshot_read(case, tmp_path, monkeypatch,
                                                                            file_opens):
    _, overrides, after = case
    tree = build_tree(tmp_path / "tree", overrides=overrides)
    if after is not None:
        after(tree)
    tree.install(monkeypatch)
    file_opens.clear()
    assert _run(tree) == 3
    assert _snapshot_touched(file_opens, tree) == []


@pytest.mark.parametrize("field", ["snapshot_manifest_sha", "snapshot_page_digest", "structural_manifest_sha"])
def test_an_unset_pin_refuses_with_exit_3_and_no_snapshot_read(field, tmp_path, monkeypatch, file_opens):
    tree = build_tree(tmp_path / "tree")
    tree.install(monkeypatch)
    monkeypatch.setattr(A, "REAL_PINS", replace(tree.pins, **{field: None}))
    file_opens.clear()
    assert _run(tree) == 3
    assert _snapshot_touched(file_opens, tree) == []


def test_a_chain_that_is_not_the_trusted_one_refuses_with_exit_3(tmp_path, monkeypatch, file_opens):
    tree = build_tree(tmp_path / "tree")
    tree.install(monkeypatch)
    tree.paths["trust"].write_bytes(json.dumps({
        "r1a_permit_sha256": "0" * 64, "r0_receipt_path": str(tree.paths["r0"]),
        "r1a_procedure_path": str(tree.paths["procedure"])}).encode("utf-8"))
    file_opens.clear()
    assert _run(tree) == 3
    assert _snapshot_touched(file_opens, tree) == []


@pytest.mark.parametrize("argv", [
    [],
    ["--real"],
    ["--real", "--r1a-permit"],
    ["--real", "--r1a-permit", "p.json"],
    ["--real", "--out", "d", "--r1a-permit", "p.json"],
    ["--r1-permit", "p.json", "--out", "d"],
    ["--synthetic", "d"],
    ["--real", "--r1a-permit", "p.json", "--out", "d", "--extra"],
    ["--scenario", "blocked", "--out", "d"],
    ["-h"],
])
def test_any_other_command_line_exits_3_and_opens_nothing(argv, file_opens, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert A.main(argv) == 3
    assert file_opens == []
    assert not (tmp_path / "d").exists()


def test_help_exits_0_and_opens_nothing(file_opens, capsys):
    assert A.main(["--help"]) == 0
    assert file_opens == []
    assert "--real --r1a-permit FILE --out DIR" in capsys.readouterr().out


def test_an_existing_output_folder_refuses_before_any_data_read(tmp_path, file_opens):
    out = tmp_path / "out"
    out.mkdir()
    permit = tmp_path / "permit.json"
    permit.write_bytes(b"{}")
    file_opens.clear()
    assert A.main(["--real", "--r1a-permit", str(permit), "--out", str(out)]) == 3
    assert file_opens == []


def test_an_existing_partial_folder_refuses_before_any_data_read(tmp_path, file_opens):
    out = tmp_path / "out"
    _partial(out).mkdir()
    permit = tmp_path / "permit.json"
    permit.write_bytes(b"{}")
    file_opens.clear()
    assert A.main(["--real", "--r1a-permit", str(permit), "--out", str(out)]) == 3
    assert file_opens == []
    assert _partial(out).exists()


def test_the_script_hash_is_its_own_bytes_and_the_trust_file_is_not_the_script():
    assert A.own_script_sha() == H.sha(_SCRIPT.read_bytes())
    assert A.TRUST_PATH.name == "ieg_r1a_literals_trust.json"
    assert A.TRUST_PATH != A.BUILDER_PATH


def test_subprocess_no_arguments_and_real_without_permit_exit_3(tmp_path):
    base = [sys.executable, str(_SCRIPT)]
    proc = subprocess.run(base, capture_output=True, text=True)
    assert proc.returncode == 3 and proc.stderr == "refused\n"
    out = tmp_path / "out"
    proc = subprocess.run(base + ["--real", "--r1a-permit", str(tmp_path / "none.json"), "--out", str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 3 and proc.stderr == "refused\n"
    assert not out.exists()


def test_subprocess_help_exits_0():
    proc = subprocess.run([sys.executable, str(_SCRIPT), "--help"], capture_output=True, text=True)
    assert proc.returncode == 0 and "R1a literals-only script" in proc.stdout


def test_the_core_limit_is_really_set_to_zero_in_a_child_process():
    code = ("import importlib.util,resource,sys;"
            "s=importlib.util.spec_from_file_location('a',sys.argv[1]);m=importlib.util.module_from_spec(s);"
            "sys.modules['a']=m;s.loader.exec_module(m);m.disable_core_dumps();"
            "print(resource.getrlimit(resource.RLIMIT_CORE))")
    proc = subprocess.run([sys.executable, "-c", code, str(_SCRIPT)], capture_output=True, text=True)
    assert proc.stdout.strip() == "(0, 0)"


# ---------------------------------------------------------------- section 8.3, 9.3: no result on failure

def _failing_open(monkeypatch, target_name: str):
    real_open = builtins.open

    def failing(file, mode="r", *args, **kwargs):
        path = Path(os.fsdecode(file))
        if "w" in mode and path.name == target_name and path.parent.name.endswith(".partial"):
            raise OSError("write refused")
        return real_open(file, mode, *args, **kwargs)
    monkeypatch.setattr(A, "open", failing, raising=False)


@pytest.mark.parametrize("target", [A.RESULT_FILE, A.RECEIPT_FILE])
def test_a_write_error_leaves_no_result_and_no_literal_bytes(target, tmp_path, monkeypatch, capsys):
    tree = build_tree(tmp_path / "tree", DEMO_LITERAL_PROJECTS)
    tree.install(monkeypatch)
    _failing_open(monkeypatch, target)
    before = _tree_files(tree.root)
    assert _run(tree) == 5
    captured = capsys.readouterr()
    assert captured.err == "integrity_error\n" and captured.out == ""
    _assert_no_result(tree, before)


def test_a_rename_error_leaves_no_result_and_no_literal_bytes(tmp_path, monkeypatch, capsys):
    tree = build_tree(tmp_path / "tree", DEMO_LITERAL_PROJECTS)
    tree.install(monkeypatch)

    def refused(src, dst):
        raise OSError("rename refused")
    monkeypatch.setattr(A.os, "rename", refused)
    before = _tree_files(tree.root)
    assert _run(tree) == 5
    assert capsys.readouterr().err == "integrity_error\n"
    _assert_no_result(tree, before)


def test_a_partial_folder_left_by_a_handled_failure_is_removed(tmp_path, monkeypatch):
    tree = build_tree(tmp_path / "tree", DEMO_LITERAL_PROJECTS)
    tree.install(monkeypatch)
    _failing_open(monkeypatch, A.RECEIPT_FILE)
    _run(tree)
    assert not _partial(tree.paths["out"]).exists()


# ---------------------------------------------------------------- section 6: stdout and stderr

def _manifest_replaced(data: bytes):
    """A world hook that stores data as the snapshot manifest and pins it, so the chain reaches the manifest."""
    def hook(world):
        blobs = dict(world.blobs)
        blobs[r1.K_SNAP_MANIFEST] = data
        return replace(world, blobs=blobs, pins=replace(world.pins, snapshot_manifest_sha=H.sha(data)))
    return hook


def test_a_malformed_manifest_prints_only_the_fixed_code(tmp_path, monkeypatch, capsys):
    tree = build_tree(tmp_path / "tree", world_hook=_manifest_replaced(b'{"datasets": []}'))
    tree.install(monkeypatch)
    assert _run(tree) == 5
    captured = capsys.readouterr()
    assert captured.err == "integrity_error\n" and captured.out == ""


def test_an_internal_error_prints_only_the_fixed_code_and_no_marker(tmp_path, monkeypatch, capsys):
    tree = build_tree(tmp_path / "tree", DEMO_LITERAL_PROJECTS)
    tree.install(monkeypatch)

    def boom(_items):
        raise RuntimeError("MARKER-SECRET-4242 Satisfactory")
    monkeypatch.setattr(A, "extract_literals", boom)
    assert _run(tree) == 5
    captured = capsys.readouterr()
    assert captured.err == "integrity_error\n"
    assert "MARKER" not in captured.err and "Satisfactory" not in captured.err


def test_a_refusal_prints_only_refused(tmp_path, monkeypatch, capsys):
    tree = build_tree(tmp_path / "tree", overrides={"permit": {"stage": "R1"}})
    tree.install(monkeypatch)
    assert _run(tree) == 3
    captured = capsys.readouterr()
    assert captured.err == "refused\n" and captured.out == ""


# ---------------------------------------------------------------- section 9.1: compatibility with R1

OWNER_PIN_HEX = "ab" * 32


def _edited_json(data: bytes, changes: dict) -> bytes:
    """The same document with some fields changed, serialized as the synthetic writers do."""
    obj = json.loads(data.decode("utf-8"))
    obj.update(changes)
    return json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")


def r1_tree_carrying_the_r1a_output(tree, monkeypatch, verbatim=None):
    """An R1 real-mode tree whose R0, procedure, R1a receipt and R1a result are the bytes that the R1a run actually
    wrote (verbatim). The harness computes the approval, permit and trust hashes from those bytes, so a mistake in
    the R1a generator reaches R1 unchanged. R1 is configured by the test: its pins come from the R1a tree, its owner
    key is OWNER_PIN_HEX, and its core-limit call is stubbed (the real call is tested in the R1 file)."""
    out = tree.paths["out"]
    docs = {
        "r0": tree.paths["r0"].read_bytes(),
        "procedure": tree.paths["procedure"].read_bytes(),
        "r1a": (out / A.RECEIPT_FILE).read_bytes(),
        "r1a_result": (out / A.RESULT_FILE).read_bytes(),
    }
    docs.update(verbatim or {})
    fx = H.build_real_fixture(tree.root / "r1", DEMO_LITERAL_PROJECTS, verbatim=docs)
    fx.install(monkeypatch)
    monkeypatch.setattr(r1, "REAL_OWNER_PUBLIC_KEY_HEX", OWNER_PIN_HEX)
    monkeypatch.setattr(r1, "disable_core_dumps", lambda: True)
    return fx


def test_r1_load_real_accepts_the_r1a_output_byte_for_byte(tmp_path, monkeypatch):
    tree = build_tree(tmp_path / "tree", DEMO_LITERAL_PROJECTS)
    tree.install(monkeypatch)
    assert _run(tree) == 0
    fx = r1_tree_carrying_the_r1a_output(tree, monkeypatch)
    out = tree.paths["out"]
    assert fx.paths["r0"].read_bytes() == tree.paths["r0"].read_bytes()
    assert fx.paths["procedure"].read_bytes() == tree.paths["procedure"].read_bytes()
    assert fx.paths["r1a"].read_bytes() == (out / A.RECEIPT_FILE).read_bytes()
    inputs = r1.load_real(fx.permit, trust_path=fx.trust)
    assert inputs.input_sha256["chain/r1a_receipt"] == H.sha((out / A.RECEIPT_FILE).read_bytes())
    assert inputs.input_sha256["chain/r1a_result"] == H.sha((out / A.RESULT_FILE).read_bytes())


@pytest.mark.parametrize("field, value, label", [
    ("snapshot_manifest_sha256", "0" * 64, "R0 snapshot manifest pin"),
    ("snapshot_page_digest", "0" * 64, "R0 snapshot page digest pin"),
    ("structural_receipt_manifest_sha256", "0" * 64, "R0 structural pin"),
    ("d1_policy_sha256", "0" * 64, "R0 D1 pin"),
    ("d1_policy_sha256", "1" * 64, "R0 D1 pin"),
    ("owner_x25519_public_key_hex", "0" * 64, "R0 owner public key pin"),
    ("owner_x25519_public_key_hex", "1" * 64, "R0 owner public key pin"),
    ("r1a_procedure_sha256", "0" * 64, "R1a procedure bytes"),
])
def test_r1_refuses_an_r0_whose_pin_or_link_is_well_formed_and_wrong(field, value, label, tmp_path, monkeypatch):
    tree = build_tree(tmp_path / "tree", DEMO_LITERAL_PROJECTS)
    tree.install(monkeypatch)
    assert _run(tree) == 0
    r0 = _edited_json(tree.paths["r0"].read_bytes(), {field: value})
    fx = r1_tree_carrying_the_r1a_output(tree, monkeypatch, verbatim={"r0": r0})
    with pytest.raises(r1.PinRefused) as info:
        r1.load_real(fx.permit, trust_path=fx.trust)
    assert re.search(label, getattr(info.value, "check", "")), getattr(info.value, "check", "")


@pytest.mark.parametrize("field, value, label", [
    ("r0_receipt_sha256", "0" * 64, "R1a link to R0"),
    ("r1a_procedure_sha256", "0" * 64, "R1a link to the procedure"),
    ("snapshot_manifest_sha256", "0" * 64, "R1a snapshot manifest pin"),
    ("snapshot_page_digest", "0" * 64, "R1a snapshot page digest pin"),
    ("r1a_script_sha256", "0" * 64, "R1a script against the procedure"),
    ("result_sha256", "0" * 64, "R1a result bytes"),
    ("result_kind", "literals_with_counts", "R1a result kind against the procedure"),
])
def test_r1_refuses_an_r1a_receipt_field_the_r1a_run_would_write_wrong(field, value, label, tmp_path, monkeypatch):
    """The receipt is the R1a run's own bytes with one field changed; the permit, approval and trust are recomputed
    from them by the harness, so only the field itself is the defect."""
    tree = build_tree(tmp_path / "tree", DEMO_LITERAL_PROJECTS)
    tree.install(monkeypatch)
    assert _run(tree) == 0
    receipt = _edited_json((tree.paths["out"] / A.RECEIPT_FILE).read_bytes(), {field: value})
    fx = r1_tree_carrying_the_r1a_output(tree, monkeypatch, verbatim={"r1a": receipt})
    with pytest.raises(r1.PinRefused) as info:
        r1.load_real(fx.permit, trust_path=fx.trust)
    assert re.search(label, getattr(info.value, "check", "")), getattr(info.value, "check", "")


def _failing_rename(monkeypatch):
    def refused(src, dst):
        raise OSError("rename refused")
    monkeypatch.setattr(A.os, "rename", refused)


@pytest.mark.parametrize("mode", ["raises", "leaves"])
def test_a_failed_cleanup_is_cleanup_failed_and_the_leftover_is_reported(mode, tmp_path, monkeypatch, capsys,
                                                                       file_opens):
    """Review P2 and spec 9.5: the literals are written, the rename fails, and the removal of <out>.partial also
    fails. The run exits 5 with cleanup_failed, the leftover stays inside <out>.partial and nothing else is written,
    and a second run refuses before any data read (exit 3)."""
    tree = build_tree(tmp_path / "tree", DEMO_LITERAL_PROJECTS)
    tree.install(monkeypatch)
    _failing_rename(monkeypatch)
    before = _tree_files(tree.root)

    def failing_rmtree(path, *args, **kwargs):
        if mode == "raises":
            raise OSError("removal refused")
        return None
    monkeypatch.setattr(A.shutil, "rmtree", failing_rmtree)
    assert _run(tree) == 5
    captured = capsys.readouterr()
    assert captured.err == "cleanup_failed\n" and captured.out == ""
    partial = _partial(tree.paths["out"])
    assert partial.exists(), "the leftover must stay on disk, not be reported as clean"
    assert (partial / A.RESULT_FILE).read_bytes() == EXPECTED_LITERALS
    assert not tree.paths["out"].exists()
    created = _tree_files(tree.root) - before
    assert created and all(partial in f.parents for f in created), "a file was written outside <out>.partial"
    file_opens.clear()
    assert A.main(tree.argv()) == 3
    assert file_opens == [], "the next run must refuse before reading any data"


def test_every_code_the_script_prints_is_an_allowed_code(tmp_path, monkeypatch, capsys):
    """Spec 6 as amended by 9.5: stdout and stderr carry only the ALLOWED_CODES, one per run."""
    assert A.ALLOWED_CODES == ("ok", "refused", "integrity_error", "result_limit_exceeded", "cleanup_failed")
    printed = []

    assert A.main([]) == 3
    printed.append(capsys.readouterr().err)

    bad = build_tree(tmp_path / "bad", world_hook=_manifest_replaced(b'{"datasets": []}'))
    bad.install(monkeypatch)
    assert _run(bad) == 5
    printed.append(capsys.readouterr().err)

    good = build_tree(tmp_path / "good", DEMO_LITERAL_PROJECTS)
    good.install(monkeypatch)
    monkeypatch.setattr(A, "RESULT_LIMIT", 1)
    assert _run(good) == 5
    printed.append(capsys.readouterr().err)
    monkeypatch.setattr(A, "RESULT_LIMIT", 1048576)

    ok = build_tree(tmp_path / "ok", DEMO_LITERAL_PROJECTS)
    ok.install(monkeypatch)
    assert _run(ok) == 0
    printed.append(capsys.readouterr().out)

    cleanup = build_tree(tmp_path / "cleanup", DEMO_LITERAL_PROJECTS)
    cleanup.install(monkeypatch)
    _failing_rename(monkeypatch)
    monkeypatch.setattr(A.shutil, "rmtree", lambda path, *a, **k: None)
    assert _run(cleanup) == 5
    printed.append(capsys.readouterr().err)

    codes = [line for text in printed for line in text.splitlines()]
    assert set(codes) == {"refused", "integrity_error", "result_limit_exceeded", "ok", "cleanup_failed"}, codes
    for code in codes:
        assert code in A.ALLOWED_CODES, code


def test_the_cleanup_does_not_suppress_its_own_errors(tmp_path, monkeypatch):
    """The removal is called without ignore_errors, so a removal error reaches the caller (review P2)."""
    tree = build_tree(tmp_path / "tree", DEMO_LITERAL_PROJECTS)
    tree.install(monkeypatch)
    _failing_rename(monkeypatch)
    calls = []
    real_rmtree = shutil.rmtree

    def recording_rmtree(path, *args, **kwargs):
        calls.append(kwargs)
        return real_rmtree(path)
    monkeypatch.setattr(A.shutil, "rmtree", recording_rmtree)
    assert _run(tree) == 5
    assert calls == [{}]


def test_a_rename_failure_with_a_clean_removal_is_integrity_error(tmp_path, monkeypatch, capsys):
    tree = build_tree(tmp_path / "tree", DEMO_LITERAL_PROJECTS)
    tree.install(monkeypatch)
    _failing_rename(monkeypatch)
    assert _run(tree) == 5
    captured = capsys.readouterr()
    assert captured.err == "integrity_error\n"
    assert not _partial(tree.paths["out"]).exists()


def test_a_missing_parent_of_the_output_refuses_before_any_data_read(tmp_path, monkeypatch, file_opens):
    tree = build_tree(tmp_path / "tree")
    tree.install(monkeypatch)
    out = tmp_path / "missing" / "out"
    file_opens.clear()
    assert A.main(["--real", "--r1a-permit", str(tree.permit), "--out", str(out)]) == 3
    assert file_opens == []
    assert not (tmp_path / "missing").exists()


def test_a_lone_surrogate_outcome_is_integrity_error_with_no_file_and_no_value(tmp_path, monkeypatch, capsys):
    """Review P3: the page holds the escape \\ud800, which parses to a lone surrogate. The UTF-8 encoding of the
    result fails before any file is created."""
    raw = b'{"count": 1, "data": [{"project_id": "P000001", "outcome": "\\ud800"}]}'
    one = [_proj("P000001", "ARG", _rec(2012, "Satisfactory"))]
    tree = build_tree(tmp_path / "tree", one, world_hook=lambda w: H.replace_page(w, "ieg/page-00000.json", raw))
    tree.install(monkeypatch)
    before = _tree_files(tree.root)
    assert _run(tree) == 5
    captured = capsys.readouterr()
    assert captured.err == "integrity_error\n" and captured.out == ""
    assert not tree.paths["out"].exists() and not _partial(tree.paths["out"]).exists()
    assert _tree_files(tree.root) == before


def test_the_research_root_guard_recognises_a_research_path():
    assert _FORBIDDEN_ROOT in A.REAL_SNAPSHOT_DIR
    assert _FORBIDDEN_ROOT not in str(A.TRUST_PATH)
