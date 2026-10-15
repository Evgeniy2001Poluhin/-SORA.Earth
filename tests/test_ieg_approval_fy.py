"""scripts/ieg_approval_fy.py against small synthetic snapshots. Nothing here reads the real snapshot.

The script is loaded from its file rather than through sys.path, as
tests/test_snapshot_worldbank_outcomes.py does. Each test builds its own
snapshot in tmp_path, with the pins computed here by an implementation written
separately from the script's, so a pin error in either shows up. Any socket use
fails the test.
"""
import ast
import csv
import hashlib
import importlib.util
import json
import os
import socket
import time
import calendar
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = REPO_ROOT / "scripts" / "ieg_approval_fy.py"
_spec = importlib.util.spec_from_file_location("ieg_approval_fy", _SCRIPT)
fy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fy)

ABSENT = object()  # the key is omitted from the record
AS_OF = date(2026, 10, 13)
DATA_FILES = ("project_approval_no_y.csv", "approval_conflicts.csv", "approval_qa.json", "fy_rules.md")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a unit test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.fixture
def host_tz():
    saved = os.environ.get("TZ")

    def set_tz(name):
        os.environ["TZ"] = name
        time.tzset()

    yield set_tz
    if saved is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = saved
    time.tzset()


# --- synthetic snapshot ---------------------------------------------------------


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def ieg_rec(pid, approval_fy=2001, **over):
    """An IEG record. The label and rating fields are present, and are never meant to matter."""
    rec = {
        "project_id": pid,
        "approval_fy": approval_fy,
        "evaluation_fy": "2008",
        "as_of_date": "13-Oct-2026",
        "outcome": "Satisfactory",
        "rating_ppp": "Moderately Satisfactory",
        "country": "Atlantis",
    }
    rec.update(over)
    return {k: v for k, v in rec.items() if v is not ABSENT}


def pno_rec(pid, boardapprovaldate=ABSENT, approvalfy=ABSENT, **over):
    rec = {"id": pid, "countrycode": "AT", "totalamt": 1000}
    if boardapprovaldate is not ABSENT:
        rec["boardapprovaldate"] = boardapprovaldate
    if approvalfy is not ABSENT:
        rec["approvalfy"] = approvalfy
    rec.update(over)
    return {k: v for k, v in rec.items() if v is not ABSENT}


class Snap:
    def __init__(self, path, pins):
        self.dir = path
        self.pins = pins


def make_snapshot(root, ieg, pno, *, name="snap", as_of="13-Oct-2026", ieg_per_page=2, pno_per_page=2,
                  manifest_ieg_sum=None):
    """Write pages and a manifest shaped like the real snapshot, and return its directory and pins."""
    base = Path(root) / name
    (base / "ieg").mkdir(parents=True)
    (base / "projects").mkdir()
    ieg_pages, pno_pages = [], []
    for n, start in enumerate(range(0, len(ieg), ieg_per_page)):
        chunk = ieg[start:start + ieg_per_page]
        rel = f"ieg/page-{n:05d}.json"
        body = json.dumps({"count": len(ieg), "data": chunk}, ensure_ascii=False, indent=1).encode("utf-8")
        (base / rel).write_bytes(body)
        ieg_pages.append({"file": rel, "sha256": _sha(body), "bytes": len(body), "records": len(chunk),
                          "api_total": len(ieg), "http_status": 200, "params": {}})
    for n, start in enumerate(range(0, len(pno), pno_per_page)):
        chunk = pno[start:start + pno_per_page]
        rel = f"projects/page-{n:05d}.json"
        mapping = {}
        for rec in chunk:
            key = rec.get("_key", rec["id"])
            mapping[key] = {k: v for k, v in rec.items() if k != "_key"}
        body = json.dumps({"total": str(len(pno)), "rows": pno_per_page, "projects": mapping},
                          ensure_ascii=False, indent=1).encode("utf-8")
        (base / rel).write_bytes(body)
        pno_pages.append({"file": rel, "sha256": _sha(body), "bytes": len(body), "records": len(chunk),
                          "api_total": len(pno), "http_status": 200, "params": {}})
    # The mapping key is the id unless a record carries _key, which is a test-only override.
    manifest = {
        "datasets": {
            "ieg": {"api_total": len(ieg),
                    "sum_records": len(ieg) if manifest_ieg_sum is None else manifest_ieg_sum,
                    "as_of_dates": [as_of], "pages": ieg_pages},
            "projects": {"api_total": len(pno), "sum_records": len(pno), "pages": pno_pages},
        },
        "snapshot_digest_method": "test fixture",
    }
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    (base / "manifest.json").write_bytes(manifest_bytes)
    lines = [f"{p['sha256']}  {p['file']}" for p in ieg_pages + pno_pages]
    digest = _sha("".join(line + "\n" for line in sorted(lines)).encode("utf-8"))
    return Snap(base, fy.Pins(_sha(manifest_bytes), digest))


def build(snap, out):
    return fy.build(snap.dir, out, pins=snap.pins)


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def rows_of(out):
    return read_csv(out / "project_approval_no_y.csv")


def row_for(out, pid):
    found = [r for r in rows_of(out) if r["project_id"] == pid]
    assert len(found) == 1, f"expected one row for {pid}, found {len(found)}"
    return found[0]


def rows_for(out, pid):
    return [r for r in rows_of(out) if r["project_id"] == pid]


def conflicts_for(out, pid):
    return [r for r in read_csv(out / "approval_conflicts.csv") if r["project_id"] == pid]


def qa_of(out):
    return json.loads((out / "approval_qa.json").read_text(encoding="utf-8"))


def one_project(tmp_path, ieg_record, pno_record=None, **kwargs):
    """Build a snapshot with one IEG record and an optional P&O record. Return the output row."""
    pno = [pno_record] if pno_record is not None else []
    if not pno:
        pno = [pno_rec("PDUMMY")]  # keeps the P&O side non-empty; not an IEG project
    snap = make_snapshot(tmp_path, [ieg_record], pno, **kwargs)
    out = tmp_path / "out"
    build(snap, out)
    return row_for(out, ieg_record["project_id"])


# --- the spec's control example and the boundary cases ---------------------------


def test_p050342_resolves_to_2001_with_an_auxiliary_warning(tmp_path):
    row = one_project(tmp_path, ieg_rec("P050342", 2001), pno_rec("P050342", "2000-07-27T00:00:00Z", "2000"))

    assert row["board_date_parsed"] == "2000-07-27"
    assert row["computed_wb_fy"] == "2001"
    assert row["ieg_fy_value"] == "2001"
    assert row["aux_state"] == "disagrees"
    assert row["status"] == "accepted_with_aux_warning"
    assert row["resolved_wb_fy"] == "2001"
    assert row["rule_id"] == "FY-02"
    assert row["availability_bound"] == "2000-07-27"
    assert row["availability_bound_kind"] == "approval_date"


def test_p050342_with_ieg_2000_is_quarantined_as_a_date_conflict(tmp_path):
    row = one_project(tmp_path, ieg_rec("P050342", 2000), pno_rec("P050342", "2000-07-27T00:00:00Z", "2000"))

    assert row["status"] == "quarantine"
    assert row["reason"] == "date_ieg_fy_conflict"
    assert row["rule_id"] == "FY-04"
    assert row["resolved_wb_fy"] == ""
    assert row["availability_bound"] == ""


def test_the_p050342_aux_warning_is_in_the_conflicts_file(tmp_path):
    snap = make_snapshot(tmp_path, [ieg_rec("P050342", 2001)],
                         [pno_rec("P050342", "2000-07-27T00:00:00Z", "2000")])
    out = tmp_path / "out"
    build(snap, out)

    found = conflicts_for(out, "P050342")
    assert len(found) == 1
    assert found[0]["conflict_categories"] == "aux_pno_approvalfy_disagrees"
    assert found[0]["disposition"] == "unreviewed"


def test_30_june_and_1_july_fall_in_different_fiscal_years(tmp_path):
    ieg = [
        ieg_rec("PJUN", 2000),
        ieg_rec("PJUL", 2001),
        ieg_rec("PJUN_WRONG", 2001),
        ieg_rec("PJUL_WRONG", 2000),
    ]
    pno = [
        pno_rec("PJUN", "2000-06-30T00:00:00Z"),
        pno_rec("PJUL", "2000-07-01T00:00:00Z"),
        pno_rec("PJUN_WRONG", "2000-06-30T00:00:00Z"),
        pno_rec("PJUL_WRONG", "2000-07-01T00:00:00Z"),
    ]
    snap = make_snapshot(tmp_path, ieg, pno)
    out = tmp_path / "out"
    build(snap, out)

    assert row_for(out, "PJUN")["resolved_wb_fy"] == "2000"
    assert row_for(out, "PJUL")["resolved_wb_fy"] == "2001"
    assert row_for(out, "PJUN_WRONG")["reason"] == "date_ieg_fy_conflict"
    assert row_for(out, "PJUL_WRONG")["reason"] == "date_ieg_fy_conflict"


def test_leap_day_is_valid_in_leap_years_and_invalid_otherwise(tmp_path):
    ieg = [ieg_rec("PLEAP", 2000), ieg_rec("PLEAP24", 2024), ieg_rec("PNOLEAP", 2001)]
    pno = [pno_rec("PLEAP", "2000-02-29T00:00:00Z"), pno_rec("PLEAP24", "2024-02-29T00:00:00Z"),
           pno_rec("PNOLEAP", "2001-02-29T00:00:00Z")]
    snap = make_snapshot(tmp_path, ieg, pno)
    out = tmp_path / "out"
    build(snap, out)

    assert row_for(out, "PLEAP")["status"] == "accepted"
    assert row_for(out, "PLEAP24")["status"] == "accepted"
    bad = row_for(out, "PNOLEAP")
    assert bad["status"] == "quarantine"
    assert bad["reason"] == "board_date_invalid"
    assert bad["board_date_parsed"] == ""
    assert bad["rule_id"] == "FY-06"


@pytest.mark.parametrize("zone", ["America/Los_Angeles", "Pacific/Kiritimati", "UTC"])
def test_dates_near_midnight_do_not_depend_on_the_host_timezone(tmp_path, host_tz, zone):
    host_tz(zone)
    ieg = [ieg_rec("PLATE", 2001), ieg_rec("PEARLY", 2000), ieg_rec("PSHIFT", 2001)]
    pno = [
        pno_rec("PLATE", "2000-07-01T00:00:00Z"),
        pno_rec("PEARLY", "2000-06-30T23:59:59Z"),
        pno_rec("PSHIFT", "2000-06-30T23:59:59.999Z"),
    ]
    snap = make_snapshot(tmp_path, ieg, pno)
    out = tmp_path / "out"
    build(snap, out)

    assert row_for(out, "PLATE")["status"] == "accepted"
    assert row_for(out, "PLATE")["board_date_parsed"] == "2000-07-01"
    assert row_for(out, "PEARLY")["status"] == "accepted"
    assert row_for(out, "PEARLY")["board_date_parsed"] == "2000-06-30"
    assert row_for(out, "PSHIFT")["board_date_parsed"] == "2000-06-30"


def test_the_host_timezone_really_differs_in_the_trap_case(tmp_path, host_tz):
    """Guards the test above. If the host TZ did not shift this instant, that test would prove nothing."""
    host_tz("Pacific/Kiritimati")
    utc_instant = calendar.timegm((2000, 6, 30, 23, 59, 59))
    assert time.localtime(utc_instant).tm_mday == 1
    host_tz("America/Los_Angeles")
    utc_instant = calendar.timegm((2000, 7, 1, 0, 0, 0))
    assert time.localtime(utc_instant).tm_mday == 30


def test_a_zone_other_than_utc_or_no_zone_is_quarantined_not_converted(tmp_path):
    ieg = [ieg_rec("POFF", 2001), ieg_rec("PNOZONE", 2001), ieg_rec("PPLUS", 2001)]
    pno = [pno_rec("POFF", "2000-06-30T23:30:00-05:00"),
           pno_rec("PNOZONE", "2000-07-01T00:00:00"),
           pno_rec("PPLUS", "2000-07-01T00:00:00+05:00")]
    snap = make_snapshot(tmp_path, ieg, pno)
    out = tmp_path / "out"
    build(snap, out)

    for pid in ("POFF", "PNOZONE", "PPLUS"):
        row = row_for(out, pid)
        assert row["reason"] == "board_date_timezone_ambiguous", pid
        assert row["rule_id"] == "FY-07"
        assert row["board_date_parsed"] == ""


def test_a_utc_offset_written_as_plus_zero_is_accepted(tmp_path):
    row = one_project(tmp_path, ieg_rec("PZERO", 2001), pno_rec("PZERO", "2000-07-01T00:00:00+00:00"))
    assert row["status"] == "accepted"
    assert row["board_date_parsed"] == "2000-07-01"


def test_fy_only_when_there_is_no_approval_date(tmp_path):
    ieg = [ieg_rec("PNOREC", 2001), ieg_rec("PNODATE", 2001), ieg_rec("PNULLDATE", 2001)]
    pno = [pno_rec("PNODATE", ABSENT, "2001"), pno_rec("PNULLDATE", None)]
    snap = make_snapshot(tmp_path, ieg, pno)
    out = tmp_path / "out"
    build(snap, out)

    no_record = row_for(out, "PNOREC")
    no_date = row_for(out, "PNODATE")
    null_date = row_for(out, "PNULLDATE")
    for row in (no_record, no_date, null_date):
        assert row["status"] == "fy_only"
        assert row["rule_id"] == "FY-03"
        assert row["resolved_wb_fy"] == "2001"
        assert row["availability_bound"] == "2000-07-01"
        assert row["availability_bound_kind"] == "fy_start_NOT_an_approval_date"
    assert no_record["pno_record_found"] == "false"
    assert no_date["aux_state"] == "unused"
    assert null_date["board_date_state"] == "absent"


def test_both_sources_missing_is_quarantined(tmp_path):
    ieg = [ieg_rec("PBOTHABS", ABSENT), ieg_rec("PBOTHNULL", None), ieg_rec("PBOTHBAD", "FY2001")]
    pno = [pno_rec("PBOTHABS"), pno_rec("PBOTHNULL", None), pno_rec("PBOTHBAD")]
    snap = make_snapshot(tmp_path, ieg, pno)
    out = tmp_path / "out"
    build(snap, out)

    for pid in ("PBOTHABS", "PBOTHNULL", "PBOTHBAD"):
        row = row_for(out, pid)
        assert row["status"] == "quarantine", pid
        assert row["reason"] == "neither_usable_source"
        assert row["rule_id"] == "FY-05"


@pytest.mark.parametrize("bad_date", [
    "not-a-date",
    "",
    20000727,
    True,
    "2000-07-27 00:00:00",
    "2000-13-01T00:00:00Z",
    "2000-07-27T24:00:00Z",
])
def test_an_invalid_present_date_is_quarantined_and_never_treated_as_missing(tmp_path, bad_date):
    """A present but invalid date with a valid IEG FY must not fall back to FY-only."""
    row = one_project(tmp_path, ieg_rec("PBAD", 2001), pno_rec("PBAD", bad_date))

    assert row["status"] == "quarantine", repr(bad_date)
    assert row["reason"] == "board_date_invalid"
    assert row["rule_id"] == "FY-06"
    assert row["board_date_state"] == "invalid"


def test_a_date_after_the_snapshot_or_before_1944_is_out_of_range(tmp_path):
    ieg = [ieg_rec("PFUT", 2027), ieg_rec("POLD", 1944)]
    pno = [pno_rec("PFUT", "2026-10-14T00:00:00Z"), pno_rec("POLD", "1943-12-31T00:00:00Z")]
    snap = make_snapshot(tmp_path, ieg, pno)
    out = tmp_path / "out"
    build(snap, out)

    assert row_for(out, "PFUT")["reason"] == "board_date_after_snapshot"
    assert row_for(out, "POLD")["reason"] == "board_date_out_of_range"
    assert row_for(out, "POLD")["rule_id"] == "FY-08"


def test_the_snapshot_as_of_day_itself_is_inside_the_range(tmp_path):
    row = one_project(tmp_path, ieg_rec("PASOF", 2027), pno_rec("PASOF", "2026-10-13T00:00:00Z"))
    assert row["status"] == "accepted"
    assert row["resolved_wb_fy"] == "2027"


def test_valid_date_with_no_ieg_fy_is_quarantined_as_an_interpretation(tmp_path):
    row = one_project(tmp_path, ieg_rec("PNOIEG", ABSENT), pno_rec("PNOIEG", "2000-07-27T00:00:00Z", "2001"))
    assert row["status"] == "quarantine"
    assert row["reason"] == "ieg_fy_absent_with_board_date"
    assert row["rule_id"] == "FY-10"


def test_ieg_disagreeing_with_a_valid_date_is_never_silently_corrected(tmp_path):
    row = one_project(tmp_path, ieg_rec("PDIS", 2000), pno_rec("PDIS", "2000-07-27T00:00:00Z"))
    assert row["status"] == "quarantine"
    assert row["resolved_wb_fy"] == ""
    assert row["computed_wb_fy"] == "2001"
    assert row["ieg_fy_value"] == "2000"


@pytest.mark.parametrize("aux, expected_state, expected_status", [
    ("2001", "agrees", "accepted"),
    (2001, "agrees", "accepted"),
    ("2000", "disagrees", "accepted_with_aux_warning"),
    ("FY2001", "invalid", "accepted_with_aux_warning"),
    ("2001.0", "invalid", "accepted_with_aux_warning"),
    (ABSENT, "absent", "accepted"),
])
def test_the_pno_approvalfy_is_auxiliary_and_never_decides(tmp_path, aux, expected_state, expected_status):
    row = one_project(tmp_path, ieg_rec("PAUX", 2001), pno_rec("PAUX", "2000-07-27T00:00:00Z", aux))
    assert row["aux_state"] == expected_state
    assert row["status"] == expected_status
    assert row["resolved_wb_fy"] == "2001"


@pytest.mark.parametrize("fy_value, state, reason", [
    (True, "invalid", "ieg_fy_invalid_with_board_date"),
    (False, "invalid", "ieg_fy_invalid_with_board_date"),
    (2001.0, "invalid", "ieg_fy_invalid_with_board_date"),
    (" 2001", "invalid", "ieg_fy_invalid_with_board_date"),
    ("FY2001", "invalid", "ieg_fy_invalid_with_board_date"),
    ("2001 ", "invalid", "ieg_fy_invalid_with_board_date"),
    ("０２００１", "invalid", "ieg_fy_invalid_with_board_date"),
    (99999, "invalid", "ieg_fy_invalid_with_board_date"),
    (2028, "invalid", "ieg_fy_invalid_with_board_date"),
    (1943, "invalid", "ieg_fy_invalid_with_board_date"),
])
def test_a_bool_or_non_integer_ieg_fy_is_refused(tmp_path, fy_value, state, reason):
    row = one_project(tmp_path, ieg_rec("PFYBAD", fy_value), pno_rec("PFYBAD", "2000-07-27T00:00:00Z"))
    assert row["ieg_fy_state"] == state
    assert row["status"] == "quarantine"
    assert row["reason"] == reason
    assert row["rule_id"] == "FY-09"


def test_a_bool_is_refused_even_though_it_is_an_int_in_python(tmp_path):
    row = one_project(tmp_path, ieg_rec("PTRUE", True), pno_rec("PTRUE"))
    assert row["ieg_fy_state"] == "invalid"
    assert row["status"] == "quarantine"
    assert row["reason"] == "neither_usable_source"
    assert "fy_bool" in row["detail"]


def test_a_four_digit_string_fy_is_accepted_and_documented(tmp_path):
    row = one_project(tmp_path, ieg_rec("PSTR", "2001"), pno_rec("PSTR", "2000-07-27T00:00:00Z"))
    assert row["ieg_fy_state"] == "valid"
    assert row["ieg_fy_value"] == "2001"
    assert row["aux_state"] == "absent"
    assert row["status"] == "accepted"


def test_the_snapshot_as_of_day_fy_is_the_upper_bound_for_an_ieg_fy(tmp_path):
    row = one_project(tmp_path, ieg_rec("PMAX", 2027), pno_rec("PMAX", "2026-10-13T00:00:00Z"))
    assert row["ieg_fy_state"] == "valid"
    assert row["resolved_wb_fy"] == "2027"


def test_repeated_ieg_project_ids_that_agree_are_both_kept(tmp_path):
    ieg = [ieg_rec("PDUP", 2001), ieg_rec("PDUP", 2001, country="Other"), ieg_rec("POTHER", 2001)]
    pno = [pno_rec("PDUP", "2000-07-27T00:00:00Z", "2000"), pno_rec("POTHER", "2000-07-27T00:00:00Z", "2001")]
    snap = make_snapshot(tmp_path, ieg, pno)
    out = tmp_path / "out"
    build(snap, out)

    both = rows_for(out, "PDUP")
    assert len(both) == 2
    assert len({(r["ieg_page_file"], r["ieg_index"]) for r in both}) == 2
    assert all(r["ieg_duplicate_count"] == "2" for r in both)
    assert all(r["status"] == "accepted_with_aux_warning" for r in both)
    listed = conflicts_for(out, "PDUP")
    assert len(listed) == 2
    assert all("ieg_duplicate_project_id" in r["conflict_categories"] for r in listed)
    assert all("ieg_duplicate_fy_disagreement" not in r["conflict_categories"] for r in listed)


def test_repeated_ieg_project_ids_that_disagree_are_both_quarantined(tmp_path):
    ieg = [ieg_rec("PDIS2", 2001), ieg_rec("PDIS2", 2000)]
    pno = [pno_rec("PDIS2", "2000-07-27T00:00:00Z", "2000")]
    snap = make_snapshot(tmp_path, ieg, pno)
    out = tmp_path / "out"
    build(snap, out)

    first, second = sorted(rows_for(out, "PDIS2"), key=lambda r: r["ieg_index"])
    assert first["status"] == "quarantine"
    assert first["reason"] == "ieg_duplicate_fy_disagreement"
    assert first["rule_id"] == "FY-11"
    assert second["status"] == "quarantine"
    assert second["reason"] == "date_ieg_fy_conflict"
    assert second["resolved_wb_fy"] == ""
    cats = conflicts_for(out, "PDIS2")
    assert all("ieg_duplicate_fy_disagreement" in c["conflict_categories"] for c in cats)


# --- projection and no-y --------------------------------------------------------


def test_the_projection_is_exactly_the_documented_fields():
    assert fy.IEG_FIELDS == ("project_id", "approval_fy", "evaluation_fy", "as_of_date")
    assert fy.PNO_FIELDS == ("id", "boardapprovaldate", "approvalfy")


def test_a_record_refuses_access_to_any_field_outside_the_projection():
    view = fy._Projection({"project_id": "P1", "outcome": "x"}, fy.IEG_FIELDS)
    assert view.get("project_id") == "P1"
    with pytest.raises(fy.ProjectionError):
        view.get("outcome")


def test_project_returns_only_the_allowed_fields():
    got = fy.project({"id": "P1", "boardapprovaldate": "2000-07-27T00:00:00Z", "outcome": "x", "rating": "y"},
                     fy.PNO_FIELDS)
    assert set(got) == set(fy.PNO_FIELDS)
    assert got["approvalfy"] is fy._ABSENT


def test_changing_synthetic_outcome_and_ratings_gives_byte_identical_outputs(tmp_path):
    def records(outcome, rating):
        ieg = [
            ieg_rec("P050342", 2001, outcome=outcome, rating_ppp=rating),
            ieg_rec("PNOMATCH", 2001, outcome=outcome, rating_ppp=rating),
            ieg_rec("PQ", 2000, outcome=outcome, rating_ppp=rating),
            ieg_rec("PX", 2001, outcome=outcome, rating_ppp=rating),
            ieg_rec("PX", 2001, outcome=outcome, rating_ppp=rating, country="Other"),
        ]
        pno = [
            pno_rec("P050342", "2000-07-27T00:00:00Z", "2000", outcome=outcome, rating_ppp=rating),
            pno_rec("PQ", "2000-07-27T00:00:00Z", "2000", outcome=outcome),
            pno_rec("PX", "2000-07-27T00:00:00Z", "2000"),
        ]
        return ieg, pno

    ieg_a, pno_a = records("Satisfactory", "High")
    ieg_b, pno_b = records("Unsatisfactory", "Negligible")
    ieg_c = [{k: v for k, v in rec.items() if k not in ("outcome", "rating_ppp")} for rec in ieg_a]
    pno_c = [{k: v for k, v in rec.items() if k not in ("outcome", "rating_ppp")} for rec in pno_a]

    snap_a = make_snapshot(tmp_path / "a", ieg_a, pno_a)
    snap_b = make_snapshot(tmp_path / "b", ieg_b, pno_b)
    snap_c = make_snapshot(tmp_path / "c", ieg_c, pno_c)
    out_a, out_b, out_c = tmp_path / "oa", tmp_path / "ob", tmp_path / "oc"
    build(snap_a, out_a)
    build(snap_b, out_b)
    build(snap_c, out_c)

    for name in DATA_FILES:
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes(), name
        assert (out_a / name).read_bytes() == (out_c / name).read_bytes(), name
    man_a = json.loads((out_a / "manifest.json").read_text(encoding="utf-8"))
    man_b = json.loads((out_b / "manifest.json").read_text(encoding="utf-8"))
    assert man_a["outputs"] == man_b["outputs"]
    assert man_a["inputs"]["manifest_sha256"] != man_b["inputs"]["manifest_sha256"]


def test_the_script_names_no_outcome_or_rating_field_and_imports_nothing_from_the_app():
    source = _SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                doc_nodes.add(id(body[0].value))

    banned_roots = {"app", "requests", "urllib", "http", "socket", "ssl", "httpx", "aiohttp", "urllib3"}
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for name in names:
            assert name.split(".")[0] not in banned_roots, f"import of {name}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in doc_nodes:
            text = node.value
            if " " not in text:
                lowered = text.lower()
                assert "outcome" not in lowered, f"field-like string {text!r}"
                assert "rating" not in lowered, f"field-like string {text!r}"


def test_no_lost_rows_across_every_category(tmp_path):
    ieg = [
        ieg_rec("PACC", 2001),                      # accepted
        ieg_rec("PAUX", 2001),                      # accepted_with_aux_warning
        ieg_rec("PFYO", 2001),                      # fy_only (no P&O record)
        ieg_rec("PCONF", 2000),                     # quarantine: date_ieg_fy_conflict
        ieg_rec("PBAD", 2001),                      # quarantine: board_date_invalid
        ieg_rec("PNEI", ABSENT),                    # quarantine: neither
        ieg_rec("PDUPA", 2001), ieg_rec("PDUPA", 2001),   # agreeing duplicates
    ]
    pno = [
        pno_rec("PACC", "2000-07-27T00:00:00Z", "2001"),
        pno_rec("PAUX", "2000-07-27T00:00:00Z", "2000"),
        pno_rec("PCONF", "2000-07-27T00:00:00Z"),
        pno_rec("PBAD", "2000-02-30T00:00:00Z"),
        pno_rec("PNEI"),
        pno_rec("PDUPA", "2000-07-27T00:00:00Z", "2001"),
        pno_rec("PONLY1", "2010-01-01T00:00:00Z", "2010"),
        pno_rec("PONLY2", "2011-01-01T00:00:00Z", "2011"),
    ]
    snap = make_snapshot(tmp_path, ieg, pno)
    out = tmp_path / "out"
    build(snap, out)

    rows = rows_of(out)
    assert len(rows) == len(ieg) == 8
    assert len({(r["ieg_page_file"], r["ieg_index"]) for r in rows}) == 8

    qa = qa_of(out)
    assert qa["conservation"] == {"ieg_records_in": 8, "rows_out": 8, "status_total": 8,
                                  "distinct_locators": 8, "ok": True}
    assert sum(qa["status_counts"].values()) == 8
    assert qa["status_counts"]["accepted"] == 3       # PACC and the two PDUPA rows
    assert qa["status_counts"]["accepted_with_aux_warning"] == 1
    assert qa["status_counts"]["fy_only"] == 1
    assert qa["status_counts"]["quarantine"] == 3
    assert qa["counts"]["pno_records_in"] == 8
    assert qa["counts"]["pno_records_not_in_ieg"] == 2
    assert qa["counts"]["pno_records_matched_to_ieg"] == 6

    conflict_rows = read_csv(out / "approval_conflicts.csv")
    assert len(conflict_rows) == qa["counts"]["conflict_rows"]
    assert {r["project_id"] for r in conflict_rows} == {"PAUX", "PCONF", "PBAD", "PNEI", "PDUPA"}


def test_rows_are_sorted_by_project_id(tmp_path):
    ieg = [ieg_rec("PZ", 2001), ieg_rec("PA", 2001), ieg_rec("PM", 2001)]
    pno = [pno_rec("PZ", "2000-07-27T00:00:00Z", "2001"), pno_rec("PA", "2000-07-27T00:00:00Z", "2001"),
           pno_rec("PM", "2000-07-27T00:00:00Z", "2001")]
    snap = make_snapshot(tmp_path, ieg, pno)
    out = tmp_path / "out"
    build(snap, out)

    assert [r["project_id"] for r in rows_of(out)] == ["PA", "PM", "PZ"]


def test_the_same_source_gives_the_same_output_bytes_on_every_run(tmp_path):
    ieg = [ieg_rec("PQ1", 2001), ieg_rec("PQ2", 2000), ieg_rec("PQ3", 2001)]
    pno = [pno_rec("PQ1", "2000-07-27T00:00:00Z", "2000"), pno_rec("PQ2", "2000-07-27T00:00:00Z"),
           pno_rec("PQ3", "2000-07-01T00:00:00Z", "2001")]
    snap = make_snapshot(tmp_path, ieg, pno)
    out1, out2 = tmp_path / "o1", tmp_path / "o2"
    build(snap, out1)
    build(snap, out2)
    for name in DATA_FILES + ("manifest.json",):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes(), name


def test_outputs_are_utf8_lf_and_json_has_sorted_keys(tmp_path):
    # A non-ASCII project_id is projected, so it reaches the output bytes.
    snap = make_snapshot(tmp_path, [ieg_rec("PÜ1", 2001)], [pno_rec("PÜ1", "2000-07-27T00:00:00Z", "2001")])
    out = tmp_path / "out"
    build(snap, out)
    for name in DATA_FILES + ("manifest.json",):
        data = (out / name).read_bytes()
        data.decode("utf-8")
        assert b"\r" not in data, name
    qa_text = (out / "approval_qa.json").read_text(encoding="utf-8")
    keys = list(json.loads(qa_text).keys())
    assert keys == sorted(keys)


# --- pins, the snapshot, and refusals -------------------------------------------


def test_default_pins_refuse_a_synthetic_snapshot(tmp_path):
    snap = make_snapshot(tmp_path, [ieg_rec("PP", 2001)], [pno_rec("PP")])
    out = tmp_path / "out"
    with pytest.raises(fy.PinMismatch):
        fy.build(snap.dir, out)
    assert not out.exists()


def test_a_wrong_manifest_pin_is_refused(tmp_path):
    snap = make_snapshot(tmp_path, [ieg_rec("PP", 2001)], [pno_rec("PP")])
    bad = fy.Pins("0" * 64, snap.pins.page_digest)
    out = tmp_path / "out"
    with pytest.raises(fy.PinMismatch, match="manifest"):
        fy.build(snap.dir, out, pins=bad)
    assert not out.exists() and not (tmp_path / "out.partial").exists()


def test_a_wrong_page_digest_pin_is_refused(tmp_path):
    snap = make_snapshot(tmp_path, [ieg_rec("PP", 2001)], [pno_rec("PP")])
    bad = fy.Pins(snap.pins.manifest_sha256, "0" * 64)
    out = tmp_path / "out"
    with pytest.raises(fy.PinMismatch, match="page digest"):
        fy.build(snap.dir, out, pins=bad)
    assert not out.exists()


def test_a_page_changed_after_the_manifest_is_refused(tmp_path):
    snap = make_snapshot(tmp_path, [ieg_rec("PP", 2001)], [pno_rec("PP")])
    page = snap.dir / "ieg" / "page-00000.json"
    page.write_bytes(page.read_bytes() + b"\n")
    out = tmp_path / "out"
    with pytest.raises(fy.SourceError, match="bytes or sha256 differ"):
        build(snap, out)
    assert not out.exists()


def test_a_duplicate_key_in_a_source_object_is_refused(tmp_path):
    snap = make_snapshot(tmp_path, [ieg_rec("PP", 2001)], [pno_rec("PP")])
    page = snap.dir / "ieg" / "page-00000.json"
    text = page.read_text(encoding="utf-8").replace('"country": "Atlantis"', '"country": "Atlantis", "country": "X"', 1)
    page.write_text(text, encoding="utf-8")
    # The manifest must then match the altered page, so only the duplicate key is at fault.
    manifest_path = snap.dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest["datasets"]["ieg"]["pages"][0]
    body = page.read_bytes()
    entry["sha256"], entry["bytes"] = _sha(body), len(body)
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    pages_lines = [f"{p['sha256']}  {p['file']}" for p in manifest["datasets"]["ieg"]["pages"]
                   + manifest["datasets"]["projects"]["pages"]]
    digest = _sha("".join(line + "\n" for line in sorted(pages_lines)).encode("utf-8"))
    pins = fy.Pins(_sha(manifest_bytes), digest)
    with pytest.raises(fy.SourceError, match="duplicate key"):
        fy.build(snap.dir, tmp_path / "out", pins=pins)


def test_the_manifest_record_count_must_match_the_records(tmp_path):
    snap = make_snapshot(tmp_path, [ieg_rec("PA", 2001), ieg_rec("PB", 2001)], [pno_rec("PA"), pno_rec("PB")],
                         manifest_ieg_sum=3)
    with pytest.raises(fy.SourceError, match="IEG record count"):
        build(snap, tmp_path / "out")


def test_a_mixed_as_of_date_is_refused(tmp_path):
    ieg = [ieg_rec("PA", 2001), ieg_rec("PB", 2001, as_of_date="12-Oct-2026")]
    snap = make_snapshot(tmp_path, ieg, [pno_rec("PA"), pno_rec("PB")])
    with pytest.raises(fy.SourceError, match="as_of_date"):
        build(snap, tmp_path / "out")


def test_an_ieg_record_without_a_project_id_is_refused(tmp_path):
    ieg = [ieg_rec("PA", 2001), ieg_rec(ABSENT, 2001)]
    snap = make_snapshot(tmp_path, ieg, [pno_rec("PA")])
    with pytest.raises(fy.SourceError, match="project_id"):
        build(snap, tmp_path / "out")


def test_a_pno_record_whose_id_differs_from_its_key_is_refused(tmp_path):
    snap = make_snapshot(tmp_path, [ieg_rec("PA", 2001)], [pno_rec("PA", _key="PX")])
    with pytest.raises(fy.SourceError, match="differs from its key"):
        build(snap, tmp_path / "out")


def test_the_snapshot_is_not_written_to(tmp_path):
    snap = make_snapshot(tmp_path, [ieg_rec("PA", 2001)], [pno_rec("PA", "2000-07-27T00:00:00Z", "2000")])

    def listing_and_hashes():
        return {str(p.relative_to(snap.dir)): _sha(p.read_bytes())
                for p in sorted(snap.dir.rglob("*")) if p.is_file()}

    before = listing_and_hashes()
    build(snap, tmp_path / "out")
    assert listing_and_hashes() == before


# --- output destination --------------------------------------------------------


def test_a_non_empty_existing_output_is_refused_and_left_alone(tmp_path):
    snap = make_snapshot(tmp_path, [ieg_rec("PA", 2001)], [pno_rec("PA")])
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("already here", encoding="utf-8")
    with pytest.raises(fy.OutputRefused, match="not an empty directory"):
        build(snap, out)
    assert (out / "keep.txt").read_text(encoding="utf-8") == "already here"
    assert sorted(p.name for p in out.iterdir()) == ["keep.txt"]
    assert not (tmp_path / "out.partial").exists()


def test_a_leftover_partial_is_refused_and_not_removed(tmp_path):
    snap = make_snapshot(tmp_path, [ieg_rec("PA", 2001)], [pno_rec("PA")])
    partial = tmp_path / "out.partial"
    partial.mkdir()
    (partial / "stale.txt").write_text("from an earlier run", encoding="utf-8")
    with pytest.raises(fy.OutputRefused, match="did not finish"):
        build(snap, tmp_path / "out")
    assert (partial / "stale.txt").read_text(encoding="utf-8") == "from an earlier run"
    assert not (tmp_path / "out").exists()


def test_a_missing_parent_is_refused_and_not_created(tmp_path):
    snap = make_snapshot(tmp_path, [ieg_rec("PA", 2001)], [pno_rec("PA")])
    with pytest.raises(fy.OutputRefused, match="parent directory"):
        build(snap, tmp_path / "missing-parent" / "out")
    assert not (tmp_path / "missing-parent").exists()


def test_an_empty_existing_output_directory_is_allowed(tmp_path):
    snap = make_snapshot(tmp_path, [ieg_rec("PA", 2001)], [pno_rec("PA")])
    out = tmp_path / "out"
    out.mkdir()
    build(snap, out)
    assert (out / "manifest.json").is_file()


def test_the_manifest_hashes_match_the_files(tmp_path):
    snap = make_snapshot(tmp_path, [ieg_rec("PA", 2001)], [pno_rec("PA", "2000-07-27T00:00:00Z", "2000")])
    out = tmp_path / "out"
    sha = build(snap, out)

    manifest_bytes = (out / "manifest.json").read_bytes()
    assert sha == _sha(manifest_bytes)
    manifest = json.loads(manifest_bytes)
    assert manifest["schema_version"] == "1"
    for name in DATA_FILES:
        data = (out / name).read_bytes()
        assert manifest["outputs"][name] == {"bytes": len(data), "sha256": _sha(data)}
    assert set(manifest["outputs"]) == set(DATA_FILES)
    assert manifest["inputs"]["manifest_sha256"] == snap.pins.manifest_sha256
    assert manifest["inputs"]["page_digest"] == snap.pins.page_digest
    assert not (tmp_path / "out.partial").exists()
    assert sorted(p.name for p in out.iterdir()) == sorted(DATA_FILES + ("manifest.json",))


def test_main_prints_the_manifest_sha256_on_success(tmp_path, monkeypatch, capsys):
    snap = make_snapshot(tmp_path, [ieg_rec("PA", 2001)], [pno_rec("PA")])
    out = tmp_path / "out"
    real_build = fy.build
    # The real pins are not the synthetic snapshot's; pass this snapshot's own pins.
    monkeypatch.setattr(fy, "build", lambda s, o, pins=None: real_build(s, o, pins=snap.pins))

    assert fy.main(["--snapshot", str(snap.dir), "--out", str(out)]) == 0

    expected = _sha((out / "manifest.json").read_bytes())
    assert f"manifest sha256: {expected}" in capsys.readouterr().out.splitlines()


def test_main_refuses_a_synthetic_snapshot_with_exit_one(tmp_path, capsys):
    snap = make_snapshot(tmp_path, [ieg_rec("PA", 2001)], [pno_rec("PA")])
    code = fy.main(["--snapshot", str(snap.dir), "--out", str(tmp_path / "out")])
    assert code == 1
    assert "pinned" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


# --- write protocol -----------------------------------------------------------------


def test_directories_are_fsynced_before_and_after_the_rename(tmp_path, monkeypatch):
    snap = make_snapshot(tmp_path, [ieg_rec("PA", 2001)], [pno_rec("PA")])
    out = tmp_path / "out"
    partial = tmp_path / "out.partial"
    real_fsync = os.fsync
    dir_syncs = []

    def recording(fd):
        if _is_dir_fd(fd):
            dir_syncs.append({"partial_exists": partial.exists(), "out_exists": out.exists()})
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording)
    build(snap, out)

    assert [s["out_exists"] for s in dir_syncs] == [False, True]
    assert dir_syncs[0]["partial_exists"] is True
    assert dir_syncs[1]["partial_exists"] is False


def _is_dir_fd(fd):
    import stat

    return stat.S_ISDIR(os.fstat(fd).st_mode)


def test_an_error_after_the_rename_reports_durability_unconfirmed(tmp_path, monkeypatch, capsys):
    snap = make_snapshot(tmp_path, [ieg_rec("PA", 2001)], [pno_rec("PA")])
    out = tmp_path / "out"
    real = fy._fsync_dir

    def fail_on_parent(path):
        if Path(path) == out.parent:
            raise OSError(5, "simulated I/O error")
        real(path)

    monkeypatch.setattr(fy, "_fsync_dir", fail_on_parent)
    with pytest.raises(fy.DurabilityUnconfirmed):
        build(snap, out)
    assert out.is_dir(), "the final directory exists after the rename"
    assert (out / "manifest.json").is_file()


def test_an_error_before_the_rename_publishes_no_final_directory(tmp_path, monkeypatch):
    snap = make_snapshot(tmp_path, [ieg_rec("PA", 2001)], [pno_rec("PA")])
    out = tmp_path / "out"

    def fail(_root):
        raise OSError(5, "simulated failure before the rename")

    monkeypatch.setattr(fy, "_fsync_tree_bottom_up", fail)
    with pytest.raises(OSError):
        build(snap, out)
    assert not out.exists(), "a final directory was published before the rename"
    assert (tmp_path / "out.partial").is_dir(), "the partial was deleted; nothing is removed automatically"
