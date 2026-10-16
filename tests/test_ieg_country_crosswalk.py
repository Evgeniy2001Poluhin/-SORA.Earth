"""scripts/ieg_country_crosswalk.py against small synthetic worlds. Nothing here reads the real data.

The script is loaded from its file. Each test writes its own snapshot, registry, OGHIST
file, alias table and regional table under tmp_path, with pins computed here by code
written separately from the script's. The autouse fixture makes any socket use fail.
"""
import ast
import csv
import hashlib
import importlib.util
import io
import json
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = REPO_ROOT / "scripts" / "ieg_country_crosswalk.py"
_spec = importlib.util.spec_from_file_location("ieg_country_crosswalk", _SCRIPT)
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)

AS_OF_TEXT = "13-Oct-2026"
LABEL_VALUES = ("Satisfactory", "Moderately Satisfactory", "Unsatisfactory", "PPAR-LABEL-SENTINEL")
DATA_FILES = (cw.OUT_ROWS, cw.OUT_CONFLICTS, cw.OUT_ALIASES, cw.OUT_QA)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a unit test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def sha(data):
    return hashlib.sha256(data).hexdigest()


# --- the synthetic registry, corpus and tables -----------------------------------------------


def reg_rec(rid, iso2, name, region="Sub-Saharan Africa"):
    return {"id": rid, "iso2Code": iso2, "name": name, "region": {"id": "X", "iso2code": "X", "value": region}}


REGISTRY = (
    reg_rec("AGO", "AO", "Angola"),
    reg_rec("ZAF", "ZA", "South Africa"),
    reg_rec("KEN", "KE", "Kenya"),
    reg_rec("AFG", "AF", "Afghanistan", "South Asia"),
    reg_rec("COG", "CG", "Congo, Rep."),
    reg_rec("COD", "CD", "Congo, Dem. Rep."),
    reg_rec("KOR", "KR", "Korea, Rep.", "East Asia & Pacific"),
    reg_rec("PRK", "KP", "Korea, Dem. People's Rep.", "East Asia & Pacific"),
    reg_rec("VNM", "VN", "Viet Nam", "East Asia & Pacific"),
    reg_rec("XKX", "XK", "Kosovo", "Europe & Central Asia"),
    reg_rec("STP", "ST", "Sao Tome and Principe"),
    reg_rec("SRB", "RS", "Serbia", "Europe & Central Asia"),
    reg_rec("WLD", "1W", "World", "Aggregates"),
    reg_rec("ECA", "7E", "Europe & Central Asia (excluding high income)", "Aggregates"),
    reg_rec("EMU", "XC", "Euro area", "Aggregates"),
)

OGHIST_ROWS = (
    ("AGO", "Angola"), ("ZAF", "South Africa"), ("AFG", "Afghanistan"), ("COG", "Congo, Rep."),
    ("KOR", "Korea, Rep."), ("VNM", "Viet Nam"), ("XKX", "Kosovo"), ("SRB", "Serbia"),
    ("YUG", "Serbia and Montenegro (former)"), ("SUN", "USSR (former)"),
)

# Base P&O corpus. Every regional code and alias target in the default tables is here.
BASE_PNO = (
    ("ANCH-PNO-AO", ["AO"], ["Republic of Angola"]),
    ("ANCH-PNO-ZA", ["ZA"], ["Republic of South Africa"]),
    ("ANCH-PNO-KE", ["KE"], ["Republic of Kenya"]),
    ("ANCH-PNO-AF", ["AF"], ["Islamic  Republic of Afghanistan"]),
    ("ANCH-PNO-CG", ["CG"], ["Republic of Congo"]),
    ("ANCH-PNO-KR", ["KR"], ["Republic of Korea"]),
    ("ANCH-PNO-VN", ["VN"], ["Socialist Republic of Vietnam"]),
    ("ANCH-PNO-XK", ["XK"], ["Republic of Kosovo"]),
    ("ANCH-PNO-ST", ["ST"], ["Democratic Republic of Sao Tome and Prin"]),
    ("ANCH-PNO-ZR", ["ZR"], ["Democratic Republic of the Congo"]),
    ("ANCH-PNO-YU", ["YU"], ["Socialist Federal Republic of Yugoslavia"]),
    ("ANCH-PNO-3A", ["3A"], ["Africa"]),
    ("ANCH-PNO-3W", ["3W"], ["Western and Central Africa"]),
    ("ANCH-PNO-1W", ["1W"], ["World"]),
    ("ANCH-PNO-7E", ["7E"], ["Europe and Central Asia"]),
)

# IEG names that exist only in IEG (no P&O record), so that every regional name is in the corpus.
BASE_IEG_ANCHORS = (
    ("ANCH-IEG-1", "Africa"),
    ("ANCH-IEG-2", "Western and Central Africa"),
    ("ANCH-IEG-3", "World"),
    ("ANCH-IEG-4", "Europe and Central Asia"),
    ("ANCH-IEG-5", "Latin America and Caribbean"),
    ("ANCH-IEG-6", "Viet Nam, Cambodia, Laos CMU"),
)

ALIAS_HEADER = cw.ALIAS_COLUMNS
REGIONAL_HEADER = cw.REGIONAL_COLUMNS


def alias_row(raw, target, code, wb, registry_name, method, *, rule="ALIAS-T1", ext="no",
              eff_start="", eff_end="", status="proposed", evidence="synthetic evidence"):
    return {
        "raw_ieg_name": raw, "normalized_name": cw.norm(raw), "target_pno_name": target,
        "target_pno_code": code, "target_wb_code": wb, "registry_name": registry_name,
        "evidence_method": method, "evidence": evidence, "needs_external_source": ext,
        "effective_start": eff_start, "effective_end": eff_end, "rule_id": rule,
        "rule_version": "1", "status": status,
    }


DEFAULT_ALIASES = (
    alias_row("Socialist Republic of Viet Nam", "Socialist Republic of Vietnam", "VN", "VNM", "Viet Nam",
              "space_insensitive_equal", rule="ALIAS-01"),
    alias_row("Democratic Republic of Sao Tome and Principe", "Democratic Republic of Sao Tome and Prin", "ST",
              "STP", "Sao Tome and Principe", "truncation_40", rule="ALIAS-02"),
)


def regional_row(kind, key, display, cls, rule, status="proposed"):
    return {"kind": kind, "key": key, "display": display, "class": cls, "reason": "synthetic",
            "evidence": "synthetic", "rule_id": rule, "status": status}


DEFAULT_REGIONAL = (
    regional_row("pno_code", "3A", "Africa", "region", "REG-01"),
    regional_row("pno_code", "3W", "Western and Central Africa", "region", "REG-02"),
    regional_row("pno_code", "1W", "World", "registry_aggregate", "REG-03"),
    regional_row("pno_code", "7E", "Europe and Central Asia", "registry_aggregate", "REG-04"),
    regional_row("name", "Latin America and Caribbean", "Latin America and Caribbean", "region", "REG-05"),
    regional_row("name", "Viet Nam, Cambodia, Laos CMU", "Viet Nam, Cambodia, Laos CMU", "region", "REG-06"),
)


def ieg_rec(pid, country, **labels):
    """An IEG record. The label fields are present and must never reach an output."""
    rec = {
        "project_id": pid, "country": country, "approval_fy": 2002, "evaluation_fy": "2008",
        "as_of_date": AS_OF_TEXT, "outcome": "Satisfactory", "rating_ppp": "Moderately Satisfactory",
        "evaluation_type": "PPAR",
    }
    rec.update(labels)
    return rec


def pno_rec(pid, codes=None, names=None, **extra):
    rec = {"id": pid, "totalamt": 1000, "boardapprovaldate": "2001-07-01T00:00:00Z", "approvalfy": "2002"}
    if codes is not None:
        rec["countrycode"] = list(codes)
    if names is not None:
        rec["countryname"] = list(names)
    rec.update(extra)
    return rec


def _write_table(path, header, rows):
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=list(header), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    path.write_bytes(buf.getvalue().encode("utf-8"))


@dataclass(frozen=True)
class World:
    snapshot: Path
    countries: Path
    oghist: Path
    aliases: Path
    regional: Path
    pins: object


def _write_snapshot(base, ieg, pno, ieg_per_page, pno_per_page):
    snap = base / "snap"
    (snap / "ieg").mkdir(parents=True)
    (snap / "projects").mkdir()
    ieg_pages, pno_pages = [], []
    for n, start in enumerate(range(0, len(ieg), ieg_per_page)):
        chunk = ieg[start:start + ieg_per_page]
        rel = f"ieg/page-{n:05d}.json"
        body = json.dumps({"count": len(ieg), "data": chunk}, ensure_ascii=False, indent=1).encode("utf-8")
        (snap / rel).write_bytes(body)
        ieg_pages.append({"file": rel, "sha256": sha(body), "bytes": len(body), "records": len(chunk)})
    for n, start in enumerate(range(0, len(pno), pno_per_page)):
        chunk = pno[start:start + pno_per_page]
        rel = f"projects/page-{n:05d}.json"
        mapping = {rec["id"]: rec for rec in chunk}
        body = json.dumps({"total": str(len(pno)), "rows": pno_per_page, "projects": mapping},
                          ensure_ascii=False, indent=1).encode("utf-8")
        (snap / rel).write_bytes(body)
        pno_pages.append({"file": rel, "sha256": sha(body), "bytes": len(body), "records": len(chunk)})
    manifest = {
        "datasets": {
            "ieg": {"api_total": len(ieg), "sum_records": len(ieg), "as_of_dates": [AS_OF_TEXT], "pages": ieg_pages},
            "projects": {"api_total": len(pno), "sum_records": len(pno), "pages": pno_pages},
        },
    }
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    (snap / "manifest.json").write_bytes(manifest_bytes)
    lines = [f"{p['sha256']}  {p['file']}" for p in ieg_pages + pno_pages]
    digest = sha("".join(line + "\n" for line in sorted(lines)).encode("utf-8"))
    return sha(manifest_bytes), digest


def _write_countries(base, records, total=None, iso_check=True):
    d = base / "countries"
    (d / "raw").mkdir(parents=True)
    meta = {"page": 1, "pages": 1, "per_page": "1000", "total": len(records) if total is None else total,
            "lastupdated": "2026-10-16"}
    body = json.dumps([meta, list(records)], ensure_ascii=False).encode("utf-8")
    (d / "raw" / "countries.json").write_bytes(body)
    aggregates = sum(1 for r in records if r["region"]["value"] == "Aggregates")
    manifest = {
        "raw": {"file": "raw/countries.json", "sha256": sha(body), "bytes": len(body)},
        "validated": {"record_count": len(records), "country_count": len(records) - aggregates,
                      "aggregate_count": aggregates},
    }
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    (d / "manifest.json").write_bytes(manifest_bytes)
    return sha(manifest_bytes), sha(body)


def _write_oghist(base, rows, header_has_wb_code=True):
    d = base / "oghist"
    d.mkdir(parents=True)
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    head = ["wb_code" if header_has_wb_code else "code", "code_system", "iso3", "country_name",
            "effective_fy", "income_group"]
    writer.writerow(head)
    for code, name in rows:
        writer.writerow([code, "WB", "", name, "2000", "LABEL-NOT-READ"])
    body = buf.getvalue().encode("utf-8")
    (d / "income_history.csv").write_bytes(body)
    manifest = {"outputs": {"income_history.csv": {"sha256": sha(body), "bytes": len(body)}}}
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
    (d / "manifest.json").write_bytes(manifest_bytes)
    return sha(manifest_bytes)


def make_world(root, ieg, pno=(), *, anchors=True, aliases=None, regional=None, registry=None,
               oghist_rows=None, ieg_per_page=3, pno_per_page=4, countries_total=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    ieg_all = ([ieg_rec(pid, name) for pid, name in BASE_IEG_ANCHORS] if anchors else []) + list(ieg)
    pno_all = ([pno_rec(pid, codes, names) for pid, codes, names in BASE_PNO] if anchors else []) + list(pno)
    ids = [r["id"] for r in pno_all]
    if len(ids) != len(set(ids)):
        raise AssertionError("a test gave two P&O records one id")
    snap_manifest_sha, digest = _write_snapshot(root, ieg_all, pno_all, ieg_per_page, pno_per_page)
    countries_manifest_sha, countries_raw_sha = _write_countries(
        root, REGISTRY if registry is None else registry, total=countries_total)
    oghist_sha = _write_oghist(root, OGHIST_ROWS if oghist_rows is None else oghist_rows)
    aliases_path = root / "aliases.csv"
    _write_table(aliases_path, ALIAS_HEADER, DEFAULT_ALIASES if aliases is None else aliases)
    regional_path = root / "regional.csv"
    _write_table(regional_path, REGIONAL_HEADER, DEFAULT_REGIONAL if regional is None else regional)
    pins = cw.Pins(snap_manifest_sha, digest, countries_manifest_sha, countries_raw_sha, oghist_sha)
    return World(root / "snap", root / "countries", root / "oghist", aliases_path, regional_path, pins)


def run(world, out, **kwargs):
    return cw.build(world.snapshot, world.countries, world.oghist, world.aliases, out,
                    regional_path=kwargs.pop("regional_path", world.regional), pins=kwargs.pop("pins", world.pins),
                    **kwargs)


def _read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def rows_of(out):
    return _read(out / cw.OUT_ROWS)


def row_of(out, pid):
    found = [r for r in rows_of(out) if r["project_id"] == pid]
    assert len(found) == 1, f"expected one row for {pid}, found {len(found)}"
    return found[0]


def all_rows_of(out, pid):
    return [r for r in rows_of(out) if r["project_id"] == pid]


def conflicts_of(out):
    return _read(out / cw.OUT_CONFLICTS)


def qa_of(out):
    return json.loads((out / cw.OUT_QA).read_text(encoding="utf-8"))


def manifest_of(out):
    return json.loads((out / cw.OUT_MANIFEST).read_text(encoding="utf-8"))


def one(root, ieg, pno=(), **kwargs):
    """Build a world, run it once, and return (out, world)."""
    world = make_world(root / "w", ieg, pno, **kwargs)
    out = root / "out"
    run(world, out)
    return out, world


# --- normalization --------------------------------------------------------------------------


def test_normalization_collapses_every_whitespace_kind_and_keeps_words():
    assert cw.norm(" Islamic  Republic \t of Afghanistan \n") == "islamic republic of afghanistan"
    assert cw.norm("Republic of Congo") == "republic of congo"
    assert cw.norm("Democratic Republic of the Congo") == "democratic republic of the congo"


def test_normalization_applies_nfkc_and_casefold_but_keeps_accents_and_punctuation():
    assert cw.norm("Ｒｅpublic of Angola") == "republic of angola"
    assert cw.norm("São Tomé, Principe") == "são tomé, principe"


def test_afghanistan_spacing_variants_resolve_to_the_same_country(tmp_path):
    variants = [
        "Islamic  Republic of Afghanistan",
        " Islamic Republic of Afghanistan ",
        " Islamic Republic of Afghanistan ",
        "Islamic Republic　of Afghanistan",
        "Ｉslamic Republic of Afghanistan",
    ]
    ieg = [ieg_rec(f"PAF{i}", v) for i, v in enumerate(variants)]
    pno = [pno_rec(f"PAF{i}", ["AF"], ["Islamic  Republic of Afghanistan"]) for i in range(len(variants))]
    out, _ = one(tmp_path, ieg, pno)
    for i, raw in enumerate(variants):
        row = row_of(out, f"PAF{i}")
        assert row["status"] == "resolved_agree"
        assert row["wb_code"] == "AFG"
        assert row["ieg_country_norm"] == "islamic republic of afghanistan"
        assert json.loads(row["ieg_country_raw"]) == raw  # the raw value is kept exactly


# --- the decision table ---------------------------------------------------------------------


def test_missing_pno_code_with_an_exact_name_resolves_by_name(tmp_path):
    ieg = [ieg_rec("P-NAME1", "Republic of Angola"), ieg_rec("P-NAME2", "Republic of Angola")]
    pno = [pno_rec("P-NAME1")]  # a P&O record with no country fields
    out, _ = one(tmp_path, ieg, pno)
    row = row_of(out, "P-NAME1")
    assert row["status"] == "resolved_by_name" and row["rule_id"] == "CW-02" and row["wb_code"] == "AGO"
    assert row["pno_countrycode_raw"] == cw.ABSENT_TEXT
    assert row_of(out, "P-NAME2")["status"] == "resolved_by_name"  # no P&O record at all


def test_agreeing_name_and_code_resolve_as_agree(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-AGREE", "Republic of South Africa")],
                 [pno_rec("P-AGREE", ["ZA"], ["Republic of South Africa"])])
    row = row_of(out, "P-AGREE")
    assert row["status"] == "resolved_agree" and row["rule_id"] == "CW-01"
    assert row["wb_code"] == "ZAF" and row["code_system"] == "WB" and row["iso3"] == ""


def test_an_unresolvable_name_with_a_country_code_resolves_by_code_and_is_not_a_regex(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-CODE", "South Africa")],
                 [pno_rec("P-CODE", ["ZA"], ["Republic of South Africa"])])
    row = row_of(out, "P-CODE")
    assert row["status"] == "resolved_by_code" and row["rule_id"] == "CW-03" and row["wb_code"] == "ZAF"
    assert row["name_evidence"] == "none"


def test_name_and_code_that_disagree_are_a_conflict_with_no_winner(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-CONF", "Republic of Kenya")],
                 [pno_rec("P-CONF", ["ZA"], ["Republic of South Africa"])])
    row = row_of(out, "P-CONF")
    assert row["status"] == "conflict" and row["reason"] == "name_code_disagree" and row["rule_id"] == "CW-04"
    assert row["wb_code"] == "" and row["resolved_pno_code"] == ""
    conflict = [c for c in conflicts_of(out) if c["project_id"] == "P-CONF"]
    assert len(conflict) == 1
    assert conflict[0]["disposition"] == "unreviewed"
    assert conflict[0]["name_pno_code"] == "KE" and conflict[0]["code_pno_code"] == "ZA"


def test_a_region_name_against_a_country_code_is_a_conflict(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-RC", "Latin America and Caribbean")],
                 [pno_rec("P-RC", ["AO"], ["Republic of Angola"])])
    assert row_of(out, "P-RC")["status"] == "conflict"


def test_a_regional_name_resolves_to_a_regional_exclusion_not_a_country(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-REG", "Western and Central Africa")])
    row = row_of(out, "P-REG")
    assert row["status"] == "excluded_regional" and row["reason"] == "regional_pno_code"
    assert row["regional_entry"] == "REG-02" and row["wb_code"] == ""


def test_a_regional_pno_code_on_a_non_regional_name_is_excluded(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-RC2", "Africa (continental)")],
                 [pno_rec("P-RC2", ["3A"], ["Africa"])])
    assert row_of(out, "P-RC2")["status"] == "excluded_regional"


def test_listed_registry_aggregate_is_excluded_with_its_reason(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-WLD", "World")], [pno_rec("P-WLD", ["1W"], ["World"])])
    row = row_of(out, "P-WLD")
    assert row["status"] == "excluded_regional" and row["reason"] == "registry_aggregate_listed"


def test_unlisted_registry_aggregate_is_quarantined_not_a_country(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-XC", "Euro area")], [pno_rec("P-XC", ["XC"], ["Euro area"])])
    row = row_of(out, "P-XC")
    assert row["status"] == "quarantine" and row["reason"] == "registry_aggregate_not_listed"
    assert row["wb_code"] == ""


def test_congo_and_korea_names_that_are_not_exact_are_not_guessed(tmp_path):
    ieg = [ieg_rec("P-CG1", "Congo"), ieg_rec("P-KR1", "Korea"),
           ieg_rec("P-KR2", "Democratic Republic of Korea")]
    out, _ = one(tmp_path, ieg)
    for pid in ("P-CG1", "P-KR1", "P-KR2"):
        row = row_of(out, pid)
        assert row["status"] == "quarantine" and row["reason"] == "neither_usable_source"
        assert row["wb_code"] == ""


def test_a_name_resolved_by_code_is_not_redirected_by_the_name(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-CG2", "Republic of Congo")], [pno_rec("P-CG2", ["CG"], ["Republic of Congo"])])
    assert row_of(out, "P-CG2")["wb_code"] == "COG"


def test_retired_zr_code_is_quarantined_and_no_successor_is_assigned(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-ZR", "Democratic Republic of the Congo")],
                 [pno_rec("P-ZR", ["ZR"], ["Democratic Republic of the Congo"])])
    row = row_of(out, "P-ZR")
    assert row["status"] == "quarantine" and row["reason"] == "pno_code_not_in_registry"
    assert row["wb_code"] == "" and row["wb_code"] != "COD"


def test_an_unknown_code_is_quarantined_even_when_both_sources_agree(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-QQ", "Unknown Republic")],
                 [pno_rec("P-QQ", ["QQ"], ["Unknown Republic"])])
    row = row_of(out, "P-QQ")
    assert row["status"] == "quarantine" and row["reason"] == "pno_code_not_in_registry"
    assert row["rule_id"] == "CW-06" and row["wb_code"] == ""


def test_kosovo_is_a_wb_identity_with_iso3_null_and_is_flagged_non_iso(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-XK", "Republic of Kosovo")], [pno_rec("P-XK", ["XK"], ["Republic of Kosovo"])])
    row = row_of(out, "P-XK")
    assert row["status"] == "resolved_agree" and row["wb_code"] == "XKX"
    assert row["code_system"] == "WB" and row["iso3"] == "" and row["non_iso_wb_code"] == "true"


def test_a_historical_successor_is_not_assigned(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-YU", "Socialist Federal Republic of Yugoslavia")],
                 [pno_rec("P-YU", ["YU"], ["Socialist Federal Republic of Yugoslavia"])])
    row = row_of(out, "P-YU")
    assert row["status"] == "quarantine" and row["reason"] == "pno_code_not_in_registry"
    assert row["wb_code"] not in ("SRB", "MNE")


def test_a_former_entity_code_is_routed_to_review_as_historical(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-HIST", "Serbia and Montenegro")],
                 [pno_rec("P-HIST", ["YUG"], ["Serbia and Montenegro"])])
    row = row_of(out, "P-HIST")
    assert row["status"] == "quarantine" and row["reason"] == "historical_former_entity"


def test_a_multi_country_record_is_not_resolved_to_its_first_code(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-MULTI", "Republic of Angola")],
                 [pno_rec("P-MULTI", ["AO", "ZA"], ["Republic of Angola", "Republic of South Africa"])])
    row = row_of(out, "P-MULTI")
    assert row["status"] == "quarantine" and row["reason"] == "pno_code_multiple"
    assert row["wb_code"] == "" and row["resolved_pno_code"] == ""


def test_a_multi_country_name_is_an_explicit_regional_exclusion(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-CMU", "Viet Nam, Cambodia, Laos CMU")])
    row = row_of(out, "P-CMU")
    assert row["status"] == "excluded_regional" and row["reason"] == "regional_name"


def test_the_organization_name_is_a_regional_exclusion_when_listed(tmp_path):
    regional = DEFAULT_REGIONAL + (
        regional_row("name", "Organization of Eastern Caribbean States (OECS)",
                     "Organization of Eastern Caribbean States (OECS)", "region", "REG-07"),
    )
    out, _ = one(tmp_path, [ieg_rec("P-OECS", "Organization of Eastern Caribbean States (OECS)")], regional=regional)
    assert row_of(out, "P-OECS")["status"] == "excluded_regional"


# --- aliases ------------------------------------------------------------------------------


def test_alias_resolves_a_name_without_a_pno_code_and_records_its_rule(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-ALS", "Socialist Republic of Viet Nam")])
    row = row_of(out, "P-ALS")
    assert row["status"] == "resolved_by_name" and row["wb_code"] == "VNM"
    assert row["name_evidence"] == "alias" and row["name_evidence_rule"] == "ALIAS-01"


def test_alias_agreeing_with_its_pno_code_resolves_as_agree(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-ALS2", "Socialist Republic of Viet Nam")],
                 [pno_rec("P-ALS2", ["VN"], ["Socialist Republic of Vietnam"])])
    assert row_of(out, "P-ALS2")["status"] == "resolved_agree"


def test_alias_that_disagrees_with_the_pno_code_is_a_conflict(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-ALS3", "Socialist Republic of Viet Nam")],
                 [pno_rec("P-ALS3", ["KE"], ["Republic of Kenya"])])
    assert row_of(out, "P-ALS3")["status"] == "conflict"


def test_truncated_alias_resolves_a_name_longer_than_forty_characters(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-STP", "Democratic Republic of Sao Tome and Principe")])
    row = row_of(out, "P-STP")
    assert row["wb_code"] == "STP" and row["name_evidence_rule"] == "ALIAS-02"


def test_aliases_applied_are_listed_with_their_row_counts(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-AL1", "Socialist Republic of Viet Nam"),
                            ieg_rec("P-AL2", "Socialist Republic of Viet Nam")])
    applied = _read(out / cw.OUT_ALIASES)
    assert [(a["rule_id"], a["applied_rows"]) for a in applied] == [("ALIAS-01", "2")]


# --- duplicates ---------------------------------------------------------------------------


def test_repeated_project_ids_with_different_countries_are_both_conflicts_and_both_kept(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-DUP", "Republic of Angola"), ieg_rec("P-DUP", "Republic of South Africa")])
    rows = all_rows_of(out, "P-DUP")
    assert len(rows) == 2
    assert all(r["status"] == "conflict" and r["reason"] == cw.R_DUP and r["rule_id"] == "CW-07" for r in rows)
    assert all(r["duplicate_project_id_count"] == "2" and r["wb_code"] == "" for r in rows)
    assert len([c for c in conflicts_of(out) if c["project_id"] == "P-DUP"]) == 2


def test_repeated_project_ids_with_the_same_country_are_kept_and_not_conflicts(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-DUP2", "Republic of Kenya"), ieg_rec("P-DUP2", "Republic of Kenya")])
    rows = all_rows_of(out, "P-DUP2")
    assert len(rows) == 2
    assert all(r["status"] == "resolved_by_name" and r["wb_code"] == "KEN" for r in rows)
    assert not [c for c in conflicts_of(out) if c["project_id"] == "P-DUP2"]


# --- accounting, order, determinism -----------------------------------------------------------


def test_no_record_is_lost_and_the_counts_add_up(tmp_path):
    ieg = [ieg_rec("P-A", "Republic of Angola"), ieg_rec("P-B", "Republic of Kenya"),
           ieg_rec("P-C", "Democratic Republic of the Congo")]
    out, world = one(tmp_path, ieg)
    qa = qa_of(out)
    expected = len(BASE_IEG_ANCHORS) + len(ieg)
    assert qa["counts"]["ieg_records_in"] == expected
    assert qa["counts"]["rows_out"] == expected
    assert sum(qa["status_counts"].values()) == expected
    assert qa["conservation"]["ok"] is True
    assert qa["conservation"]["distinct_locators"] == expected
    assert len(rows_of(out)) == expected


def test_quarantine_reason_counts_match_the_rows(tmp_path):
    ieg = [ieg_rec("P-Q1", "Democratic Republic of the Congo"), ieg_rec("P-Q2", "Congo")]
    out, _ = one(tmp_path, ieg, [pno_rec("P-Q1", ["ZR"], ["Democratic Republic of the Congo"])])
    qa = qa_of(out)
    quar = [r for r in rows_of(out) if r["status"] == "quarantine"]
    assert qa["reason_counts"].get("quarantine:pno_code_not_in_registry") == sum(
        1 for r in quar if r["reason"] == "pno_code_not_in_registry")
    assert qa["reason_counts"].get("quarantine:neither_usable_source") == sum(
        1 for r in quar if r["reason"] == "neither_usable_source")


def test_rows_come_out_in_project_id_order(tmp_path):
    ieg = [ieg_rec("P-ZZZ", "Republic of Kenya"), ieg_rec("P-AAA", "Republic of Angola"),
           ieg_rec("P-MMM", "Republic of South Africa")]
    out, _ = one(tmp_path, ieg)
    pids = [r["project_id"] for r in rows_of(out)]
    assert pids == sorted(pids)


def test_two_builds_of_the_same_bytes_are_byte_identical(tmp_path):
    ieg = [ieg_rec("P-D1", "Republic of Angola"), ieg_rec("P-D2", "Socialist Republic of Viet Nam"),
           ieg_rec("P-D3", "Democratic Republic of the Congo")]
    world = make_world(tmp_path / "w", ieg)
    first, second = tmp_path / "out1", tmp_path / "out2"
    run(world, first)
    run(world, second)
    for name in (*DATA_FILES, cw.OUT_MANIFEST):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_the_output_depends_on_no_label_value(tmp_path):
    base_ieg = [ieg_rec("P-L1", "Republic of Angola"), ieg_rec("P-L2", "Republic of Kenya"),
                ieg_rec("P-L3", "Socialist Republic of Viet Nam")]
    labelled_a = [dict(r, outcome="Satisfactory", rating_ppp="Moderately Satisfactory",
                       bank_performance="Satisfactory", me_quality="PPAR-LABEL-SENTINEL",
                       quality_at_entry="Satisfactory",
                       quality_of_supervision="Satisfactory", evaluation_type="PPAR", closing_date="2009-01-01")
               for r in base_ieg]
    labelled_b = [dict(r, outcome="Unsatisfactory", rating_ppp="Unsatisfactory", bank_performance="Unsatisfactory",
                       me_quality="Negligible", quality_at_entry="Unsatisfactory",
                       quality_of_supervision="Unsatisfactory", evaluation_type="ICR", closing_date="2015-06-30")
               for r in base_ieg]
    out_a, _ = one(tmp_path / "a", labelled_a)
    out_b, _ = one(tmp_path / "b", labelled_b)
    for name in DATA_FILES:
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes(), name
    for value in LABEL_VALUES:
        for name in DATA_FILES:
            assert value.encode("utf-8") not in (out_a / name).read_bytes()


def test_the_derived_files_contain_only_the_declared_columns(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-COLS", "Republic of Angola")])
    with open(out / cw.OUT_ROWS, encoding="utf-8", newline="") as fh:
        assert tuple(next(csv.reader(fh))) == cw.ROW_COLUMNS
    with open(out / cw.OUT_CONFLICTS, encoding="utf-8", newline="") as fh:
        assert tuple(next(csv.reader(fh))) == cw.CONFLICT_COLUMNS


# --- row accounting guard -------------------------------------------------------------------------


def test_a_dropped_row_refuses_the_build_and_writes_nothing(tmp_path, monkeypatch):
    real = cw._build_rows

    def drop_one(*args, **kwargs):
        return real(*args, **kwargs)[:-1]

    monkeypatch.setattr(cw, "_build_rows", drop_one)
    world = make_world(tmp_path / "w", [ieg_rec("P-DROP", "Republic of Angola")])
    with pytest.raises(cw.SourceError, match="row accounting failed"):
        run(world, tmp_path / "out")
    assert not (tmp_path / "out").exists()


# --- pins and source refusals ---------------------------------------------------------------------


def _pins(world, **changes):
    return cw.Pins(**{**world.pins.__dict__, **changes})


def test_a_snapshot_manifest_that_is_not_the_pinned_one_is_refused(tmp_path):
    world = make_world(tmp_path / "w", [ieg_rec("P-PIN", "Republic of Angola")])
    with pytest.raises(cw.PinMismatch, match="snapshot manifest"):
        run(world, tmp_path / "out", pins=_pins(world, snapshot_manifest_sha256="0" * 64))


def test_a_page_digest_that_is_not_the_pinned_one_is_refused(tmp_path):
    world = make_world(tmp_path / "w", [ieg_rec("P-PIN2", "Republic of Angola")])
    with pytest.raises(cw.PinMismatch, match="page digest"):
        run(world, tmp_path / "out", pins=_pins(world, page_digest="0" * 64))


def test_a_countries_manifest_that_is_not_the_pinned_one_is_refused(tmp_path):
    world = make_world(tmp_path / "w", [ieg_rec("P-PIN3", "Republic of Angola")])
    with pytest.raises(cw.PinMismatch, match="countries manifest"):
        run(world, tmp_path / "out", pins=_pins(world, countries_manifest_sha256="0" * 64))


def test_a_countries_raw_that_is_not_the_pinned_one_is_refused(tmp_path):
    world = make_world(tmp_path / "w", [ieg_rec("P-PIN4", "Republic of Angola")])
    with pytest.raises(cw.PinMismatch, match="raw sha256"):
        run(world, tmp_path / "out", pins=_pins(world, countries_raw_sha256="0" * 64))


def test_an_oghist_manifest_that_is_not_the_pinned_one_is_refused(tmp_path):
    world = make_world(tmp_path / "w", [ieg_rec("P-PIN5", "Republic of Angola")])
    with pytest.raises(cw.PinMismatch, match="OGHIST"):
        run(world, tmp_path / "out", pins=_pins(world, oghist_manifest_sha256="0" * 64))


def test_a_tampered_page_is_refused_before_any_record_is_read(tmp_path):
    world = make_world(tmp_path / "w", [ieg_rec("P-TAMPER", "Republic of Angola")])
    page = world.snapshot / "ieg" / "page-00000.json"
    page.write_bytes(page.read_bytes() + b" ")
    with pytest.raises(cw.SourceError, match="bytes or sha256 differ"):
        run(world, tmp_path / "out")


def test_a_countries_total_that_disagrees_with_its_records_is_refused(tmp_path):
    world = make_world(tmp_path / "w", [ieg_rec("P-TOT", "Republic of Angola")], countries_total=99)
    with pytest.raises(cw.SourceError, match="countries"):
        run(world, tmp_path / "out")


def test_an_iso2_shared_by_two_registry_records_is_refused(tmp_path):
    registry = REGISTRY + (reg_rec("DUPX", "AO", "Second Angola"),)
    world = make_world(tmp_path / "w", [ieg_rec("P-DISO", "Republic of Angola")], registry=registry)
    with pytest.raises(cw.SourceError, match="shared by two"):
        run(world, tmp_path / "out")


def test_the_registry_loader_separates_countries_from_aggregates(tmp_path):
    world = make_world(tmp_path / "w", [ieg_rec("P-REGL", "Republic of Angola")])
    registry, _ = cw._load_countries(world.countries, world.pins)
    assert "AO" in registry.country_by_iso2 and "1W" not in registry.country_by_iso2
    assert "1W" in registry.aggregate_by_iso2 and "AO" not in registry.aggregate_by_iso2


def test_the_oghist_former_set_is_taken_from_the_entity_names(tmp_path):
    world = make_world(tmp_path / "w", [ieg_rec("P-OG", "Republic of Angola")])
    oghist = cw._load_oghist(world.oghist, world.pins)
    assert oghist.former_codes == frozenset({"YUG", "SUN"})
    assert "AGO" in oghist.wb_codes and "KEN" not in oghist.wb_codes


def test_an_oghist_without_a_wb_code_column_is_refused(tmp_path):
    world = make_world(tmp_path / "w", [ieg_rec("P-OGH", "Republic of Angola")])
    # Rewrite the file with the wrong header, then pin its new manifest, so only the header check can refuse it.
    body = _write_oghist_bytes(OGHIST_ROWS, header_has_wb_code=False)
    (world.oghist / "income_history.csv").write_bytes(body)
    manifest_bytes = json.dumps({"outputs": {"income_history.csv": {"sha256": sha(body), "bytes": len(body)}}},
                                sort_keys=True).encode("utf-8")
    (world.oghist / "manifest.json").write_bytes(manifest_bytes)
    pins = _pins(world, oghist_manifest_sha256=sha(manifest_bytes))
    with pytest.raises(cw.SourceError, match="wb_code or country_name"):
        cw._load_oghist(world.oghist, pins)


def _write_oghist_bytes(rows, header_has_wb_code=True):
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["wb_code" if header_has_wb_code else "code", "code_system", "iso3", "country_name",
                     "effective_fy", "income_group"])
    for code, name in rows:
        writer.writerow([code, "WB", "", name, "2000", "LABEL-NOT-READ"])
    return buf.getvalue().encode("utf-8")


def test_a_presence_flag_reports_whether_the_resolved_code_is_in_oghist(tmp_path):
    out, _ = one(tmp_path, [ieg_rec("P-OGK", "Republic of Kenya"), ieg_rec("P-OGA", "Republic of Angola")])
    assert row_of(out, "P-OGK")["oghist_wb_code_present"] == "false"  # KEN is not in the OGHIST fixture
    assert row_of(out, "P-OGA")["oghist_wb_code_present"] == "true"


# --- projection and input shape ------------------------------------------------------------------


def test_asking_a_record_for_a_label_field_raises():
    raw = {"project_id": "P1", "country": "Angola", "outcome": "Satisfactory"}
    view = cw._Projection(raw, cw.IEG_FIELDS)
    assert view.get("country") == "Angola"
    with pytest.raises(cw.ProjectionError):
        view.get("outcome")


def test_the_pno_projection_does_not_reach_approval_dates():
    view = cw._Projection({"id": "P1", "countrycode": ["AO"], "boardapprovaldate": "2001-07-01"}, cw.PNO_FIELDS)
    with pytest.raises(cw.ProjectionError):
        view.get("boardapprovaldate")


def test_the_projection_helper_returns_only_its_fields():
    raw = {"project_id": "P1", "country": "Angola", "as_of_date": AS_OF_TEXT, "outcome": "x", "approval_fy": 2001}
    assert set(cw.project(raw, cw.IEG_FIELDS)) == set(cw.IEG_FIELDS)


def test_a_non_string_ieg_country_is_a_source_error(tmp_path):
    world = make_world(tmp_path / "w", [ieg_rec("P-INT", 7)])
    with pytest.raises(cw.SourceError, match="country string"):
        run(world, tmp_path / "out")


def test_a_pno_record_with_a_code_but_no_name_is_a_source_error(tmp_path):
    world = make_world(tmp_path / "w", [ieg_rec("P-HALF", "Republic of Angola")],
                       [pno_rec("P-HALF", ["AO"], None)])
    with pytest.raises(cw.SourceError, match="not present together"):
        run(world, tmp_path / "out")


def test_a_pno_record_whose_code_is_a_string_not_a_list_is_a_source_error(tmp_path):
    rec = pno_rec("P-STR", ["AO"], ["Republic of Angola"])
    rec["countrycode"] = "AO"
    world = make_world(tmp_path / "w", [ieg_rec("P-STR", "Republic of Angola")], [rec])
    with pytest.raises(cw.SourceError, match="not a list of strings"):
        run(world, tmp_path / "out")


def test_the_pno_dictionary_refuses_one_name_with_two_codes(tmp_path):
    world = make_world(tmp_path / "w", [ieg_rec("P-D1X", "Republic of Kenya")],
                       [pno_rec("P-D1X", ["ZZ"], ["Republic of Kenya"])])
    with pytest.raises(cw.SourceError, match="not 1:1"):
        run(world, tmp_path / "out")


def test_the_pno_dictionary_refuses_one_code_with_two_names(tmp_path):
    world = make_world(tmp_path / "w", [ieg_rec("P-D2X", "Kenya Land")],
                       [pno_rec("P-D2X", ["KE"], ["Kenya Land"])])
    with pytest.raises(cw.SourceError, match="more than one name"):
        run(world, tmp_path / "out")


def test_names_that_differ_only_in_spacing_are_refused_as_a_collision(tmp_path):
    world = make_world(tmp_path / "w", [ieg_rec("P-D3X", "Republic of Kenya")],
                       [pno_rec("P-D3X", ["KE"], ["Republic  of Kenya"])])
    with pytest.raises(cw.SourceError, match="not 1:1 under normalization"):
        run(world, tmp_path / "out")


# --- alias table refusals --------------------------------------------------------------------------


def _with_aliases(tmp_path, rows, ieg=None):
    world = make_world(tmp_path / "w", ieg or [ieg_rec("P-AR", "Republic of Angola")], aliases=rows)
    return world


def test_an_alias_naming_an_exact_pno_name_is_refused(tmp_path):
    rows = [alias_row("Republic of Angola", "Republic of Angola", "AO", "AGO", "Angola",
                      "registry_name_and_code", ext="yes")]
    with pytest.raises(cw.SourceError, match="exact P&O name"):
        run(_with_aliases(tmp_path, rows), tmp_path / "out")


def test_an_alias_whose_stated_method_does_not_hold_is_refused(tmp_path):
    rows = [alias_row("Socialist Republic of Viet Nam", "Socialist Republic of Vietnam", "VN", "VNM", "Viet Nam",
                      "truncation_40")]
    with pytest.raises(cw.SourceError, match="evidence method does not hold"):
        run(_with_aliases(tmp_path, rows), tmp_path / "out")


def test_a_dated_alias_is_refused_until_the_approval_date_is_projected(tmp_path):
    rows = [alias_row("Socialist Republic of Viet Nam", "Socialist Republic of Vietnam", "VN", "VNM", "Viet Nam",
                      "space_insensitive_equal", eff_start="2000-01-01")]
    with pytest.raises(cw.SourceError, match="dated aliases"):
        run(_with_aliases(tmp_path, rows), tmp_path / "out")


def test_an_alias_whose_target_is_not_a_registry_country_is_refused(tmp_path):
    rows = [alias_row("Yugoslavia, Socialist Federal", "Socialist Federal Republic of Yugoslavia", "YU", "YUG",
                      "Serbia and Montenegro", "registry_name_and_code", ext="yes")]
    with pytest.raises(cw.SourceError, match="not a registry country"):
        run(_with_aliases(tmp_path, rows), tmp_path / "out")


def test_an_alias_naming_a_pno_pair_that_is_not_in_the_corpus_is_refused(tmp_path):
    rows = [alias_row("Socialist Republic of Viet Nam", "Socialist Republic of Viet Nam", "VN", "VNM", "Viet Nam",
                      "space_insensitive_equal")]
    with pytest.raises(cw.SourceError, match="not one pair in the corpus"):
        run(_with_aliases(tmp_path, rows), tmp_path / "out")


def test_two_alias_rules_for_one_name_are_conflicting_and_refused(tmp_path):
    rows = [
        alias_row("Socialist Republic of Viet Nam", "Socialist Republic of Vietnam", "VN", "VNM", "Viet Nam",
                  "space_insensitive_equal", rule="ALIAS-A"),
        alias_row("Socialist Republic of Viet Nam", "Socialist Republic of Vietnam", "VN", "VNM", "Viet Nam",
                  "space_insensitive_equal", rule="ALIAS-B"),
    ]
    with pytest.raises(cw.SourceError, match="conflicting alias rules"):
        run(_with_aliases(tmp_path, rows), tmp_path / "out")


def test_an_alias_row_with_a_wrong_normalized_name_is_refused(tmp_path):
    row = alias_row("Socialist Republic of Viet Nam", "Socialist Republic of Vietnam", "VN", "VNM", "Viet Nam",
                    "space_insensitive_equal")
    row["normalized_name"] = "socialist republic of viet nam!"
    with pytest.raises(cw.SourceError, match="normalized_name"):
        run(_with_aliases(tmp_path, [row]), tmp_path / "out")


def test_an_alias_table_with_the_wrong_header_is_refused(tmp_path):
    world = make_world(tmp_path / "w", [ieg_rec("P-HDR", "Republic of Angola")])
    text = world.aliases.read_text(encoding="utf-8").replace("raw_ieg_name", "raw_name", 1)
    world.aliases.write_text(text, encoding="utf-8")
    with pytest.raises(cw.SourceError, match="header"):
        run(world, tmp_path / "out")


# --- regional table refusals --------------------------------------------------------------------------


def _with_regional(tmp_path, rows):
    return make_world(tmp_path / "w", [ieg_rec("P-RR", "Republic of Angola")], regional=rows)


def test_a_listed_code_that_is_a_registry_country_is_refused(tmp_path):
    rows = list(DEFAULT_REGIONAL) + [regional_row("pno_code", "ZA", "Republic of South Africa", "region", "REG-99")]
    with pytest.raises(cw.SourceError, match="a country is not a region"):
        run(_with_regional(tmp_path, rows), tmp_path / "out")


def test_a_region_listed_as_an_aggregate_when_the_registry_has_none_is_refused(tmp_path):
    rows = [regional_row("pno_code", "3A", "Africa", "registry_aggregate", "REG-01")]
    with pytest.raises(cw.SourceError, match="the registry has none"):
        run(_with_regional(tmp_path, rows), tmp_path / "out")


def test_a_registry_aggregate_listed_as_a_region_is_refused(tmp_path):
    rows = [regional_row("pno_code", "1W", "World", "region", "REG-01")]
    with pytest.raises(cw.SourceError, match="is a registry aggregate"):
        run(_with_regional(tmp_path, rows), tmp_path / "out")


def test_a_regional_name_inside_the_pno_dictionary_is_refused(tmp_path):
    rows = list(DEFAULT_REGIONAL) + [regional_row("name", "Republic of Kenya", "Republic of Kenya", "region", "REG-98")]
    with pytest.raises(cw.SourceError, match="in the P&O name dictionary"):
        run(_with_regional(tmp_path, rows), tmp_path / "out")


def test_a_regional_name_that_is_also_an_alias_is_refused(tmp_path):
    rows = list(DEFAULT_REGIONAL) + [
        regional_row("name", "Socialist Republic of Viet Nam", "Socialist Republic of Viet Nam", "region", "REG-98")]
    world = make_world(tmp_path / "w", [ieg_rec("P-RA", "Socialist Republic of Viet Nam")], regional=rows)
    with pytest.raises(cw.SourceError, match="conflicting rules"):
        run(world, tmp_path / "out")


def test_a_regional_name_absent_from_the_ieg_corpus_is_refused(tmp_path):
    rows = list(DEFAULT_REGIONAL) + [regional_row("name", "Nowhere Region", "Nowhere Region", "region", "REG-97")]
    with pytest.raises(cw.SourceError, match="does not occur in the IEG corpus"):
        run(_with_regional(tmp_path, rows), tmp_path / "out")


def test_a_regional_code_absent_from_the_pno_corpus_is_refused(tmp_path):
    rows = list(DEFAULT_REGIONAL) + [regional_row("pno_code", "9Z", "Nowhere", "region", "REG-96")]
    with pytest.raises(cw.SourceError, match="does not occur in the P&O corpus"):
        run(_with_regional(tmp_path, rows), tmp_path / "out")


def test_a_regional_code_displayed_under_another_name_is_refused(tmp_path):
    rows = [regional_row("pno_code", "3A", "Africa (renamed)", "region", "REG-01")]
    with pytest.raises(cw.SourceError, match="displayed under a name"):
        run(_with_regional(tmp_path, rows), tmp_path / "out")


def test_a_regional_entry_with_an_unknown_status_is_refused(tmp_path):
    rows = [regional_row("pno_code", "3A", "Africa", "region", "REG-01", status="approved-by-me")]
    with pytest.raises(cw.SourceError, match="unknown status"):
        run(_with_regional(tmp_path, rows), tmp_path / "out")


def test_a_repeated_regional_rule_id_is_refused(tmp_path):
    rows = [regional_row("pno_code", "3A", "Africa", "region", "REG-01"),
            regional_row("pno_code", "3W", "Western and Central Africa", "region", "REG-01")]
    with pytest.raises(cw.SourceError, match="appears more than once"):
        run(_with_regional(tmp_path, rows), tmp_path / "out")


def test_the_retired_legacy_codes_are_not_in_the_committed_regional_list():
    listed = {r["key"] for r in _read_path(cw.DEFAULT_REGIONAL) if r["kind"] == "pno_code"}
    for code in ("RY", "ZR", "YU", "YF", "TP", "GZ", "TW"):
        assert code not in listed, code


# --- destination, durability, exit codes -----------------------------------------------------------


def test_an_existing_nonempty_destination_is_refused_and_untouched(tmp_path):
    world = make_world(tmp_path / "w", [ieg_rec("P-DEST", "Republic of Angola")])
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("earlier", encoding="utf-8")
    with pytest.raises(cw.OutputRefused, match="not an empty directory"):
        run(world, out)
    assert (out / "keep.txt").read_text(encoding="utf-8") == "earlier"


def test_a_leftover_partial_is_refused_and_kept(tmp_path):
    world = make_world(tmp_path / "w", [ieg_rec("P-PART", "Republic of Angola")])
    partial = tmp_path / "out.partial"
    partial.mkdir()
    (partial / "left.txt").write_text("unfinished", encoding="utf-8")
    with pytest.raises(cw.OutputRefused, match="did not finish"):
        run(world, tmp_path / "out")
    assert (partial / "left.txt").read_text(encoding="utf-8") == "unfinished"


def test_a_missing_parent_is_refused_and_not_created(tmp_path):
    world = make_world(tmp_path / "w", [ieg_rec("P-PAR", "Republic of Angola")])
    with pytest.raises(cw.OutputRefused, match="does not exist"):
        run(world, tmp_path / "absent" / "out")
    assert not (tmp_path / "absent").exists()


def test_a_failure_before_the_rename_publishes_nothing_and_keeps_the_partial(tmp_path, monkeypatch):
    world = make_world(tmp_path / "w", [ieg_rec("P-REN", "Republic of Angola")])

    def boom(src, dst):
        raise OSError("rename refused")

    monkeypatch.setattr(cw.os, "rename", boom)
    with pytest.raises(OSError, match="rename refused"):
        run(world, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert (tmp_path / "out.partial" / cw.OUT_ROWS).exists()


def test_a_write_that_does_not_read_back_is_refused_before_the_rename(tmp_path, monkeypatch):
    world = make_world(tmp_path / "w", [ieg_rec("P-RB", "Republic of Angola")])
    real = cw._write_exclusive

    def corrupt(path, data):
        real(path, data + b"x")  # the bytes on disk are not the bytes built

    monkeypatch.setattr(cw, "_write_exclusive", corrupt)
    with pytest.raises(cw.OutputFailed, match="does not read back"):
        run(world, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_a_regional_name_with_a_regional_code_is_excluded_by_the_name(tmp_path):
    regional = DEFAULT_REGIONAL + (regional_row("pno_code", "6L", "Latin America", "region", "REG-07"),)
    out, _ = one(tmp_path, [ieg_rec("P-LAC", "Latin America and Caribbean")],
                 [pno_rec("P-LAC", ["6L"], ["Latin America"])], regional=regional)
    row = row_of(out, "P-LAC")
    assert row["status"] == "excluded_regional" and row["reason"] == "regional_name"


def test_a_failed_parent_fsync_reports_durability_unconfirmed_with_exit_three(tmp_path, monkeypatch, capsys):
    world = make_world(tmp_path / "w", [ieg_rec("P-FS", "Republic of Angola")])
    real = cw._fsync_dir

    def fail_on_parent(path):
        if Path(path) == tmp_path:
            raise OSError("parent fsync refused")
        real(path)

    monkeypatch.setattr(cw, "_fsync_dir", fail_on_parent)
    monkeypatch.setattr(cw, "DEFAULT_PINS", world.pins)
    code = cw.main(["--snapshot", str(world.snapshot), "--countries", str(world.countries),
                    "--oghist", str(world.oghist), "--aliases", str(world.aliases),
                    "--regional", str(world.regional), "--out", str(tmp_path / "out")])
    assert code == 3
    assert (tmp_path / "out" / cw.OUT_MANIFEST).exists()
    assert "durability is not confirmed" in capsys.readouterr().err


def test_directories_are_fsynced_bottom_up_before_the_rename(tmp_path, monkeypatch):
    world = make_world(tmp_path / "w", [ieg_rec("P-ORD", "Republic of Angola")])
    events = []
    real_fsync, real_rename = cw._fsync_dir, cw.os.rename

    def spy_fsync(path):
        events.append(("fsync", Path(path).name))
        real_fsync(path)

    def spy_rename(src, dst):
        events.append(("rename", Path(dst).name))
        real_rename(src, dst)

    monkeypatch.setattr(cw, "_fsync_dir", spy_fsync)
    monkeypatch.setattr(cw.os, "rename", spy_rename)
    run(world, tmp_path / "out")
    rename_at = events.index(("rename", "out"))
    before = events[:rename_at]
    assert ("fsync", "out.partial") in before
    assert events[-1] == ("fsync", tmp_path.name)


def test_main_publishes_with_exit_zero_and_prints_the_manifest_sha(tmp_path, monkeypatch, capsys):
    world = make_world(tmp_path / "w", [ieg_rec("P-MAIN", "Republic of Angola")])
    monkeypatch.setattr(cw, "DEFAULT_PINS", world.pins)
    out = tmp_path / "out"
    code = cw.main(["--snapshot", str(world.snapshot), "--countries", str(world.countries),
                    "--oghist", str(world.oghist), "--aliases", str(world.aliases),
                    "--regional", str(world.regional), "--out", str(out)])
    assert code == 0
    printed = capsys.readouterr().out.strip()
    assert printed == f"manifest sha256: {sha((out / cw.OUT_MANIFEST).read_bytes())}"


def test_main_refuses_with_exit_one_on_a_pin_mismatch(tmp_path, capsys):
    world = make_world(tmp_path / "w", [ieg_rec("P-MAIN2", "Republic of Angola")])
    code = cw.main(["--snapshot", str(world.snapshot), "--countries", str(world.countries),
                    "--oghist", str(world.oghist), "--aliases", str(world.aliases),
                    "--regional", str(world.regional), "--out", str(tmp_path / "out")])
    assert code == 1
    assert not (tmp_path / "out").exists()
    assert "refused" in capsys.readouterr().err


# --- committed inputs and program shape ----------------------------------------------------------


def _read_path(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_the_committed_alias_table_has_its_declared_shape_and_no_reviewed_status():
    rows = _read_path(REPO_ROOT / "scripts" / "ieg_country_aliases_v1.csv")
    with open(REPO_ROOT / "scripts" / "ieg_country_aliases_v1.csv", encoding="utf-8", newline="") as fh:
        assert tuple(next(csv.reader(fh))) == cw.ALIAS_COLUMNS
    assert len(rows) == 6
    assert {r["status"] for r in rows} == {"proposed"}
    assert len({r["rule_id"] for r in rows}) == len(rows)
    assert {r["evidence_method"] for r in rows} <= set(cw.ALIAS_METHODS)
    assert all(r["effective_start"] == "" and r["effective_end"] == "" for r in rows)


def test_the_committed_regional_list_has_its_declared_shape():
    rows = _read_path(REPO_ROOT / "scripts" / "ieg_regional_names_v1.csv")
    with open(REPO_ROOT / "scripts" / "ieg_regional_names_v1.csv", encoding="utf-8", newline="") as fh:
        assert tuple(next(csv.reader(fh))) == cw.REGIONAL_COLUMNS
    assert {r["status"] for r in rows} == {"proposed"}
    assert {r["kind"] for r in rows} <= {"pno_code", "name"}
    assert {r["class"] for r in rows} <= {"region", "registry_aggregate"}
    assert len({r["rule_id"] for r in rows}) == len(rows)


def test_the_script_imports_only_the_standard_library():
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    assert roots <= set(sys.stdlib_module_names), roots - set(sys.stdlib_module_names)
