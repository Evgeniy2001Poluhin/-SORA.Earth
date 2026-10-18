"""The map's country inputs come from a pinned, verified snapshot, offline.

scripts/snapshot_map_sources.py fetches the sources; scripts/build_map_inputs.py
builds the per-country table from the snapshot directory and nothing else. These
tests run both against a synthetic world served by a fake, so no test touches a
socket. Each refusal the snapshot makes has a test that triggers it, and the
build's own re-checks have tests too.
"""
import hashlib
import json
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_map_inputs as build  # noqa: E402
import snapshot_map_sources as snap  # noqa: E402

from app.api.map_data import COUNTRIES  # noqa: E402

DATE = "2026-10-18"
PREVIOUS = "2026-10-10"
CSV_URL = "https://hdr.undp.org/sites/default/files/2025_HDR/HDR25_Composite_indices_complete_time_series.csv"
OLD_CSV_URL = "https://hdr.undp.org/sites/default/files/2023-24_HDR/HDR23-24_Composite_indices_complete_time_series.csv"
GINI, RENEW, CO2, GOV = snap.GINI_CODE, snap.RENEWABLE_CODES["final"], snap.CO2_CODE, snap.GOV_WGI_CODE
NAMES = {
    GINI: "Gini index",
    RENEW: "Renewable energy consumption (% of total final energy consumption)",
    CO2: "Carbon dioxide (CO2) emissions excluding LULUCF per capita (t CO2e/capita)",
    GOV: "Government Effectiveness - Governance estimate (approx. -2.5 to +2.5)",
}
SOURCES = {GINI: "World Development Indicators", RENEW: "World Development Indicators",
           CO2: "World Development Indicators", GOV: "Worldwide Governance Indicators"}
MAP_CODES = list(snap.MAP_ISO3)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Every socket fails. A test that reaches the network fails here, not in a flaky way later."""
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(snap, "MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(snap, "_sleep", lambda seconds: None)


def _default_series():
    """The synthetic world: every map country has values, the latest well inside each window."""
    series = {GINI: [], RENEW: [], CO2: [], GOV: []}
    for index, iso3 in enumerate(MAP_CODES):
        for year in range(2015, 2025):
            series[GINI].append((iso3, str(year), 30.0 + index % 7 + (year - 2015) * 0.1))
            series[CO2].append((iso3, str(year), 5.0 + index % 4))
        for year in range(2015, 2022):
            series[RENEW].append((iso3, str(year), 10.0 + index))
        for year in range(2015, 2026):
            series[GOV].append((iso3, str(year), 0.5 + index * 0.01))
    return series


def _hdi_csv():
    years = list(range(2015, 2024))
    header = ["iso3", "country", "hdicode", "region", "hdi_rank_2025"] + [f"hdi_{y}" for y in years] \
        + ["le_2023"]
    lines = [",".join(header)]
    lines.append(",World,,,,"  + ",".join("0.7" for _ in years) + ",72")  # aggregate: no ISO3
    for index, iso3 in enumerate(MAP_CODES):
        values = []
        for year in years:
            if iso3 == "NZL" and year == 2015:
                values.append("")  # a blank year is a missing value, not a zero
            else:
                values.append(f"{0.80 + index / 1000 + (year - 2015) / 100:.3f}")
        lines.append(",".join([iso3, f"Country {iso3}", "very high", "Region", str(index + 1)] + values + ["70"]))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _html_docs(*links):
    anchors = "".join(f'<a href="{link}">download</a>\n' for link in links)
    return f"<!DOCTYPE html><html><body>{anchors}</body></html>".encode("utf-8")


class FakeWorld:
    """Serves the World Bank API, the UNDP page and the UNDP CSV from memory.

    `transform` and `raw` let a test break one series: transform rewrites the rows
    that are served while the reported total stays what the full series holds;
    raw replaces the body of every page of a series with given bytes.
    """

    def __init__(self, series=None):
        self.series = series or _default_series()
        self.transform = {}
        self.raw = {}
        self.csv = _hdi_csv()
        self.csv_override = None
        self.docs = _html_docs(OLD_CSV_URL, CSV_URL)
        self.names = dict(NAMES)
        self.sources = dict(SOURCES)
        self.per_page_override = None
        self.stamps = {}  # code -> function(page_number) -> lastupdated stamp, when a test needs one
        self.calls = []

    def get(self, label, url, params=None):
        self.calls.append(url)
        if url == snap.UNDP_DOCS_URL:
            return self.docs, url
        if url == CSV_URL:
            return (self.csv_override if self.csv_override is not None else self.csv), url
        prefix = f"{snap.WB_BASE}/indicator/"
        if url.startswith(prefix):
            code = url[len(prefix):]
            payload = [{"page": 1, "pages": 1, "per_page": 50, "total": 1}, [
                {"id": code, "name": self.names.get(code, "x"),
                 "source": {"id": "x", "value": self.sources.get(code, "World Development Indicators")}}
            ]]
            if code not in self.series and code not in self.names:
                payload = [{"message": [{"id": "120", "key": "Invalid value", "value": "not valid"}]}]
            return json.dumps(payload).encode("utf-8"), url
        marker = "/indicator/"
        assert marker in url, url
        code = url.rsplit(marker, 1)[1]
        if code in self.raw:
            return self.raw[code], url
        rows = self.series[code]
        served = self.transform[code](list(rows)) if code in self.transform else list(rows)
        per_page = int(params["per_page"])
        page = int(params["page"])
        total = len(rows)
        pages = max(1, -(-total // per_page))
        chunk = served[(page - 1) * per_page: page * per_page]
        body_rows = []
        for iso3, year, value in chunk:
            body_rows.append({
                "indicator": {"id": code, "value": self.names[code]},
                "country": {"id": iso3[:2], "value": iso3},
                "countryiso3code": iso3,
                "date": year,
                "value": value,
                "unit": "",
                "obs_status": "",
                "decimal": 1,
            })
        stamp = self.stamps[code](page) if code in self.stamps else "2026-10-08"
        meta = {"page": page, "pages": pages, "per_page": per_page, "total": total,
                "sourceid": "2", "lastupdated": stamp}
        return json.dumps([meta, body_rows or None]).encode("utf-8"), url


@pytest.fixture
def world(monkeypatch):
    fake = FakeWorld()
    monkeypatch.setattr(snap, "_get", fake.get)
    monkeypatch.setattr(snap, "WB_PER_PAGE", 100)
    return fake


def _take(root, date=DATE, **kwargs):
    return snap.take_snapshot(root, date, **kwargs)


def _snapshot_dir(root, date=DATE):
    return Path(root) / date


def _listing(directory):
    return {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(directory).rglob("*")) if p.is_file()
    }


# ---------------------------------------------------------------------------
# Snapshot: the refusals
# ---------------------------------------------------------------------------

def test_a_clean_world_snapshots_and_verifies(tmp_path, world):
    manifest = _take(tmp_path)
    assert manifest["renewables_choice"] == "final"
    assert manifest["renewables_code"] == RENEW
    assert set(manifest["indicators"]) == {"gini", "renewables", "co2_per_capita", "gov_effectiveness", "hdi"}
    assert manifest["countries_iso3"] == MAP_CODES
    built, _sha = build.verify_snapshot(_snapshot_dir(tmp_path))
    assert built["snapshot_digest"] == manifest["snapshot_digest"]


def test_an_incomplete_series_is_refused(tmp_path, world):
    """The server reports the full total, and serves fewer rows: the pages do not add up."""
    world.transform[RENEW] = lambda rows: rows[:-5]
    with pytest.raises(snap.SnapshotError, match="incomplete"):
        _take(tmp_path)
    assert not _snapshot_dir(tmp_path).exists()


def test_a_page_out_of_sequence_is_refused(tmp_path, world, monkeypatch):
    """Asking for page 2 and receiving page 1 again is a refusal, not a silent repeat."""
    real = world.get

    def repeat_first_page(label, url, params=None):
        body, u = real(label, url, params)
        if url.endswith(f"/indicator/{CO2}") and params and params.get("page") == 2:
            return real(label, url, {**params, "page": 1})
        return body, u
    monkeypatch.setattr(snap, "_get", repeat_first_page)
    with pytest.raises(snap.SnapshotError, match="meta.page"):
        _take(tmp_path)


def test_a_duplicate_country_year_is_refused(tmp_path, world):
    world.series[CO2].append(("DEU", "2024", 6.9))  # DEU 2024 already exists
    with pytest.raises(snap.SnapshotError, match="duplicate|appears twice"):
        _take(tmp_path)


def test_a_duplicate_hdi_country_is_refused(tmp_path, world):
    world.csv = world.csv + b"DEU,Germany again,very high,Europe,9,0.9,0.9,0.9,0.9,0.9,0.9,0.9,0.9,0.9,80\n"
    with pytest.raises(snap.SnapshotError, match="appears twice"):
        _take(tmp_path)


@pytest.mark.parametrize("code, bad", [
    (GINI, 150.0),          # a Gini above 100
    (RENEW, -1.0),          # a share below zero
    (RENEW, 120.0),         # a share above 100
    (CO2, 500.0),           # tonnes per capita far outside any country
    (GOV, 4.0),             # a WGI estimate outside -3..3
])
def test_an_out_of_range_value_is_refused(tmp_path, world, code, bad):
    world.series[code][3] = (world.series[code][3][0], world.series[code][3][1], bad)
    with pytest.raises(snap.SnapshotError, match="outside"):
        _take(tmp_path)


def test_an_hdi_outside_zero_to_one_is_refused(tmp_path, world):
    world.csv = _hdi_csv().replace(b"0.800", b"1.800", 1)
    with pytest.raises(snap.SnapshotError, match="outside"):
        _take(tmp_path)


def test_an_html_body_where_a_series_belongs_is_refused(tmp_path, world):
    world.raw[GINI] = b"<!DOCTYPE html><html><body>Access denied</body></html>"
    with pytest.raises(snap.SnapshotError, match="HTML"):
        _take(tmp_path)


def test_an_html_body_where_the_csv_belongs_is_refused(tmp_path, world):
    world.csv_override = b"<html><head></head><body>Please enable JavaScript</body></html>"
    with pytest.raises(snap.SnapshotError, match="HTML"):
        _take(tmp_path)


def test_a_world_bank_error_body_is_refused(tmp_path, world):
    """The API answers 200 with a message for a code it does not have."""
    world.raw[CO2] = json.dumps([{"message": [{"id": "120", "key": "Invalid value"}]}]).encode("utf-8")
    with pytest.raises(snap.SnapshotError, match="error"):
        _take(tmp_path)


def test_a_series_whose_name_does_not_match_its_purpose_is_refused(tmp_path, world):
    """A code moved to another series is a different number. Its name must still say what was chosen."""
    world.names[GOV] = "Regulatory Quality - Governance estimate"
    with pytest.raises(snap.SnapshotError, match="does not contain"):
        _take(tmp_path)


def test_a_series_published_under_another_source_is_refused(tmp_path, world):
    world.sources[GOV] = "WDI Database Archives"
    with pytest.raises(snap.SnapshotError, match="published under"):
        _take(tmp_path)


def test_a_row_for_a_country_that_was_not_asked_for_is_refused(tmp_path, world):
    world.series[GINI].append(("WLD", "2020", 40.0))
    with pytest.raises(snap.SnapshotError, match="not requested"):
        _take(tmp_path)


def test_a_series_with_no_value_at_all_is_refused(tmp_path, world):
    world.series[CO2] = [(iso3, year, None) for iso3, year, _ in world.series[CO2]]
    with pytest.raises(snap.SnapshotError, match="no data"):
        _take(tmp_path)


def test_an_existing_snapshot_directory_is_never_overwritten(tmp_path, world):
    _take(tmp_path)
    with pytest.raises(snap.SnapshotError, match="already exists"):
        _take(tmp_path)


def test_a_leftover_partial_directory_stops_the_run(tmp_path, world):
    (tmp_path / f"{DATE}.partial").mkdir()
    with pytest.raises(snap.SnapshotError, match="earlier run"):
        _take(tmp_path)


def test_the_undp_page_without_a_csv_is_refused(tmp_path, world):
    world.docs = _html_docs("https://hdr.undp.org/some-other-page")
    with pytest.raises(snap.SnapshotError, match="no composite-indices"):
        _take(tmp_path)


def test_the_newest_hdi_release_is_chosen_by_year(world):
    chosen, year, candidates = snap.choose_undp_csv(world.docs, "hdi")
    assert chosen == CSV_URL
    assert year == 2025
    assert OLD_CSV_URL in candidates


# ---------------------------------------------------------------------------
# A failed or refused run leaves the previous verified snapshot as the one to use
# ---------------------------------------------------------------------------

def test_a_refused_run_does_not_touch_the_previous_snapshot(tmp_path, world):
    _take(tmp_path, PREVIOUS)
    before = _listing(_snapshot_dir(tmp_path, PREVIOUS))
    world.transform[CO2] = lambda rows: rows[:-3]
    with pytest.raises(snap.SnapshotError):
        _take(tmp_path, DATE)
    assert _listing(_snapshot_dir(tmp_path, PREVIOUS)) == before
    assert not _snapshot_dir(tmp_path, DATE).exists()
    assert (tmp_path / f"{DATE}.partial").is_dir()
    assert build.latest_verified_snapshot(tmp_path) == _snapshot_dir(tmp_path, PREVIOUS)


def test_the_build_ignores_a_partial_and_uses_the_last_verified_snapshot(tmp_path, world):
    _take(tmp_path, PREVIOUS)
    world.transform[GINI] = lambda rows: rows[:-1]
    with pytest.raises(snap.SnapshotError):
        _take(tmp_path, DATE)
    table, manifest = build.write_build(build.latest_verified_snapshot(tmp_path), tmp_path / "inputs")
    assert table["snapshot"]["date_utc"] == PREVIOUS


def test_a_tampered_newest_snapshot_is_skipped_for_the_one_before(tmp_path, world):
    _take(tmp_path, PREVIOUS)
    _take(tmp_path, DATE)
    newest = _snapshot_dir(tmp_path, DATE) / "raw" / "gini" / "page-00001.json"
    newest.write_bytes(newest.read_bytes().replace(b"30.0", b"31.0", 1))
    assert build.latest_verified_snapshot(tmp_path) == _snapshot_dir(tmp_path, PREVIOUS)


# ---------------------------------------------------------------------------
# Build: selection, age, no substitution, determinism, verification
# ---------------------------------------------------------------------------

def _built(tmp_path, world, date=DATE):
    _take(tmp_path, date)
    table, _manifest = build.write_build(_snapshot_dir(tmp_path, date), tmp_path / f"inputs-{date}-{id(world)}")
    return {c["iso3"]: c for c in table["countries"]}


def test_the_latest_year_is_chosen(tmp_path, world):
    countries = _built(tmp_path, world)
    gini = countries["DEU"]["inputs"]["gini_index"]
    expected = [v for i, y, v in world.series[GINI] if i == "DEU" and y == "2024"]
    assert gini["year"] == "2024"
    assert gini["value"] == pytest.approx(expected[0])


def test_the_latest_year_for_renewables_is_the_snapshot_source_year(tmp_path, world):
    countries = _built(tmp_path, world)
    assert countries["FIN"]["inputs"]["renewable_share"]["year"] == "2021"
    assert countries["FIN"]["inputs"]["renewable_share"]["age_years"] == 5


def test_a_value_older_than_its_window_is_missing_with_its_reason(tmp_path, world):
    """Governance is three years. Latest 2019 against a 2026 snapshot is seven years: missing, not used."""
    world.series[GOV] = [(iso3, year, value) for iso3, year, value in world.series[GOV] if year <= "2019" or iso3 != "NOR"]
    countries = _built(tmp_path, world)
    nor = countries["NOR"]["inputs"]["gov_effectiveness"]
    assert nor["value"] is None
    assert nor["reason"] == "too_old"
    assert nor["latest_year"] == "2019"
    assert nor["max_age_years"] == 3
    assert "gov_effectiveness" in countries["NOR"]["missing"]
    assert countries["NOR"]["complete"] is False


def test_a_country_with_no_row_is_missing_not_defaulted(tmp_path, world):
    """Gini for New Zealand is absent from the series: no value, no world average, no benchmark."""
    world.series[GINI] = [(i, y, v) for i, y, v in world.series[GINI] if i != "NZL"]
    countries = _built(tmp_path, world)
    gini = countries["NZL"]["inputs"]["gini_index"]
    assert gini == {"value": None, "reason": "no_observation"}


def test_a_blank_latest_year_is_not_replaced_by_an_earlier_one_in_window(tmp_path, world):
    """NZL's 2023 HDI is blank, so the latest value is 2022: four years old, past the
    three-year window. The build reports it missing as too_old. It does not quietly
    move to 2021, which would be inside the window and is not the latest figure."""
    lines = world.csv.decode().splitlines()
    col = lines[0].split(",").index("hdi_2023")
    for i, line in enumerate(lines):
        if line.startswith("NZL,"):
            parts = line.split(",")
            parts[col] = ""
            lines[i] = ",".join(parts)
    world.csv = ("\n".join(lines) + "\n").encode()
    countries = _built(tmp_path, world)
    hdi = countries["NZL"]["inputs"]["hdi"]
    assert hdi["value"] is None
    assert hdi["reason"] == "too_old"
    assert hdi["latest_year"] == "2022"


def test_no_input_is_taken_from_the_static_benchmarks(tmp_path, world):
    """The build reads the snapshot only. A value that is only a BENCHMARKS figure cannot appear."""
    from app.country_benchmarks import BENCHMARKS
    countries = _built(tmp_path, world)
    for code, country in countries.items():
        for name, input_ in country["inputs"].items():
            if input_["value"] is not None:
                assert input_["source"].startswith(("World Bank", "UNDP")), (code, name, input_["source"])
                assert input_["fetched_at"], (code, name)
    assert BENCHMARKS  # the comparison is meaningful: the table exists and was not used


def test_the_output_is_byte_identical_for_the_same_snapshot(tmp_path, world, monkeypatch):
    """The clock moves between the two builds. A wall-clock stamp at one-second resolution
    would otherwise be identical in both and this test could not fail."""
    ticks = iter(range(10_000))

    def ticking_clock():
        return f"2026-10-18T00:00:{next(ticks) % 60:02d}Z"
    monkeypatch.setattr(snap, "_utc_now", ticking_clock)
    _take(tmp_path)
    snapshot = _snapshot_dir(tmp_path)
    build.write_build(snapshot, tmp_path / "first")
    build.write_build(snapshot, tmp_path / "second")
    for name in (build.OUTPUT_NAME, build.MANIFEST_NAME):
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()


def test_a_build_never_overwrites_an_output(tmp_path, world):
    _take(tmp_path)
    build.write_build(_snapshot_dir(tmp_path), tmp_path / "out")
    with pytest.raises(build.BuildError, match="already exists"):
        build.write_build(_snapshot_dir(tmp_path), tmp_path / "out")


def test_a_raw_file_changed_after_the_snapshot_is_refused(tmp_path, world):
    _take(tmp_path)
    page = _snapshot_dir(tmp_path) / "raw" / "co2_per_capita" / "page-00001.json"
    page.write_bytes(page.read_bytes().replace(b"5.0", b"6.0", 1))
    with pytest.raises(build.BuildError, match="does not match the manifest"):
        build.verify_snapshot(_snapshot_dir(tmp_path))


def test_an_unlisted_raw_file_is_refused(tmp_path, world):
    _take(tmp_path)
    (_snapshot_dir(tmp_path) / "raw" / "extra.json").write_text("{}")
    with pytest.raises(build.BuildError, match="not in the manifest"):
        build.verify_snapshot(_snapshot_dir(tmp_path))


def test_a_manifest_that_disagrees_with_the_raw_bytes_is_refused(tmp_path, world):
    """The manifest's latest-year figures are checked against the bytes, not believed."""
    _take(tmp_path)
    manifest_path = _snapshot_dir(tmp_path) / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["indicators"]["co2_per_capita"]["latest_year_per_country"]["DEU"] = "2019"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(build.BuildError, match="the raw bytes say"):
        build.write_build(_snapshot_dir(tmp_path), tmp_path / "out")


def test_a_snapshot_directory_with_the_wrong_name_is_refused(tmp_path, world):
    _take(tmp_path)
    renamed = tmp_path / "2026-01-01"
    _snapshot_dir(tmp_path).rename(renamed)
    with pytest.raises(build.BuildError, match="not named for the manifest"):
        build.verify_snapshot(renamed)


def test_the_map_countries_are_the_snapshot_countries():
    """The 33 countries the map offers are the 33 the snapshot asks for, by code."""
    assert {c["code"] for c in COUNTRIES} == set(snap.MAP_ISO3)
    assert len(snap.MAP_ISO3) == len(set(snap.MAP_ISO3)) == 33


def test_the_snapshot_renewables_choice_is_the_final_energy_series():
    assert snap.DEFAULT_RENEWABLES == "final"
    assert snap.RENEWABLE_CODES["final"] == "EG.FEC.RNEW.ZS"


def test_a_manifest_digest_that_does_not_recompute_is_refused(tmp_path, world):
    """Every listed file matches, but the manifest claims a different snapshot digest."""
    _take(tmp_path)
    manifest_path = _snapshot_dir(tmp_path) / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["snapshot_digest"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(build.BuildError, match="digest does not recompute"):
        build.verify_snapshot(_snapshot_dir(tmp_path))


# ---------------------------------------------------------------------------
# One release per series: pages that disagree on lastupdated are refused
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", [GINI, RENEW, CO2, GOV])
def test_pages_of_one_series_from_two_releases_are_refused(tmp_path, world, code):
    """Two pages of one indicator with different lastupdated stamps mix two releases."""
    world.stamps[code] = lambda page: "2026-09-01" if page == 2 else "2026-10-08"
    with pytest.raises(snap.SnapshotError, match="release stamps"):
        _take(tmp_path)
    assert not _snapshot_dir(tmp_path).exists()


def test_a_page_with_no_release_stamp_is_refused(tmp_path, world):
    world.stamps[CO2] = lambda page: None
    with pytest.raises(snap.SnapshotError, match="no lastupdated"):
        _take(tmp_path)


def test_one_release_across_every_page_is_accepted(tmp_path, world):
    world.stamps[GOV] = lambda page: "2026-09-25"
    manifest = _take(tmp_path)
    assert manifest["indicators"]["gov_effectiveness"]["lastupdated"] == "2026-09-25"


def _resign(snapshot_dir, rel):
    """Re-sign one raw file: update its manifest entry and the digest, as someone who
    controls the manifest could. The build must still refuse what the bytes say."""
    manifest_path = snapshot_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    body = (snapshot_dir / rel).read_bytes()
    for entry in manifest["files"]:
        if entry["path"] == rel:
            entry["sha256"] = hashlib.sha256(body).hexdigest()
            entry["bytes"] = len(body)
    manifest["snapshot_digest"] = snap._snapshot_digest(manifest["files"])
    manifest_path.write_text(json.dumps(manifest))


def test_the_build_refuses_a_re_signed_mixed_release(tmp_path, world):
    """The snapshot is refused at creation. Here the raw page is changed and re-signed, so
    only the build's own re-check of the bytes can catch it."""
    _take(tmp_path)
    snapshot = _snapshot_dir(tmp_path)
    rel = "raw/gov_effectiveness/page-00002.json"
    page = snapshot / rel
    payload = json.loads(page.read_text())
    payload[0]["lastupdated"] = "2026-09-01"
    page.write_text(json.dumps(payload))
    _resign(snapshot, rel)
    with pytest.raises((build.BuildError, snap.SnapshotError), match="release stamps"):
        build.write_build(snapshot, tmp_path / "out")
