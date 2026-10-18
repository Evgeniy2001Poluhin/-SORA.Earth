"""The pinned map inputs are verified, aged at the date of use, and scale-checked. Offline.

Council decision C8, part 2. The table is data/map_inputs/2026-10-18, rebuilt from the
raw snapshot by scripts/build_map_inputs.py. Tests that load a table from disk go
through the production loader, including the manifest pin in app/map_inputs.py.
Synthetic tables are pinned to their own manifest with `_write_table`, so the table
checks they name are the ones reached.

Each guard has a mutation in the branch's notes: revert it and a test named for it
must fail.
"""
import copy
import hashlib
import json
import re
from datetime import date
from pathlib import Path

import pytest

from app import map_inputs
from app.api import map_data

SHIPPED_DIR: Path = map_inputs.TABLE_DIR
SHIPPED_TABLE_SHA256 = "3832f89f1f1dc4e32120aee189089b7b459278e1b89e273a7da4413ddcd1fead"
SHIPPED_MANIFEST_SHA256 = "5d133d3bf441092b9a1739a6ca2b1501327b493efd60609a52af2f10db027acf"
SHIPPED_DOC = json.loads((SHIPPED_DIR / map_inputs.TABLE_NAME).read_bytes())


@pytest.fixture(autouse=True)
def _fresh_table_state(monkeypatch):
    """Each test starts with no loaded table, and leaves none behind."""
    monkeypatch.setattr(map_inputs, "_STATE", {})


@pytest.fixture
def clock(monkeypatch):
    """Freeze the date ages are measured at. Starts at the day this was written."""
    box = {"today": date(2026, 10, 19)}
    monkeypatch.setattr(map_inputs, "use_date", lambda: box["today"])
    return box


def _manifest_for(body: bytes) -> bytes:
    manifest = {
        "schema": "map-inputs/v1",
        "outputs": {map_inputs.TABLE_NAME: {"bytes": len(body),
                                            "sha256": hashlib.sha256(body).hexdigest()}},
    }
    return json.dumps(manifest).encode("utf-8")


def _write_unpinned(directory: Path, doc) -> bytes:
    """Write a table and a manifest that records it correctly. Returns the manifest bytes."""
    directory.mkdir(parents=True, exist_ok=True)
    body = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
    manifest = _manifest_for(body)
    (directory / map_inputs.TABLE_NAME).write_bytes(body)
    (directory / map_inputs.MANIFEST_NAME).write_bytes(manifest)
    return manifest


def _pin_to(monkeypatch, manifest: bytes) -> None:
    monkeypatch.setattr(map_inputs, "PINNED_MANIFEST_SHA256", hashlib.sha256(manifest).hexdigest())


def _write_table(directory: Path, doc, monkeypatch) -> Path:
    """Write a table and its manifest, and pin the loader to that manifest, for synthetic tables."""
    _pin_to(monkeypatch, _write_unpinned(directory, doc))
    return directory


def _row(doc, iso3):
    return next(c for c in doc["countries"] if c["iso3"] == iso3)


def _state_for(doc):
    """An in-memory table for per-input tests. The disk checks are tested separately."""
    table = map_inputs.PinnedTable(
        rows={c["iso3"]: c for c in doc["countries"]},
        table_sha256="0" * 64, manifest_sha256="0" * 64, snapshot_date=doc["snapshot"]["date_utc"],
    )
    return map_inputs.TableState(table=table, reason=None)


# ---------- the shipped table, its manifest and its pin ----------

def test_the_shipped_table_is_the_part_one_build():
    assert hashlib.sha256((SHIPPED_DIR / map_inputs.TABLE_NAME).read_bytes()).hexdigest() == SHIPPED_TABLE_SHA256


def test_the_pinned_manifest_is_the_shipped_manifest():
    manifest_bytes = (SHIPPED_DIR / map_inputs.MANIFEST_NAME).read_bytes()

    assert hashlib.sha256(manifest_bytes).hexdigest() == SHIPPED_MANIFEST_SHA256
    assert map_inputs.PINNED_MANIFEST_SHA256 == SHIPPED_MANIFEST_SHA256


def test_the_shipped_table_loads_and_names_its_manifest():
    state = map_inputs.load_table(SHIPPED_DIR)

    assert state.reason is None
    assert state.table is not None
    assert state.table.table_sha256 == SHIPPED_TABLE_SHA256
    assert state.table.manifest_sha256 == SHIPPED_MANIFEST_SHA256
    assert state.table.snapshot_date == "2026-10-18"
    assert len(state.table.rows) == 33


def test_the_code_ages_match_the_tables_own_policy():
    assert SHIPPED_DOC["policy"]["max_age_years"] == map_inputs.MAX_AGE_YEARS


# ---------- the manifest pin ----------

def test_an_edited_table_with_its_manifest_is_refused_by_the_pin(tmp_path):
    """Pin guard, the reproduced hole. Table and manifest edited together are internally consistent.

    Editing Korea's renewables to 38.7 scores Korea 64 instead of 56. Refused here: the
    manifest is not the one pinned in code, so no input from this table is used.
    """
    doc = copy.deepcopy(SHIPPED_DOC)
    _row(doc, "KOR")["inputs"]["renewable_share"]["value"] = 38.7
    directory = tmp_path / "edited"
    _write_unpinned(directory, doc)

    state = map_inputs.load_table(directory)

    assert state.table is None
    assert "not the manifest pinned" in state.reason
    result = map_data.score_country("KOR", state=state, as_of=date(2026, 10, 19))
    assert result["esg"] is None
    assert result["inputs"] == {}
    assert result["inputs_manifest_sha256"] is None


def test_the_same_edited_pair_would_score_korea_64_without_the_pin(tmp_path, monkeypatch):
    """The hole itself. Pin the loader to the edited manifest, which is what the pin forbids, and it scores."""
    doc = copy.deepcopy(SHIPPED_DOC)
    _row(doc, "KOR")["inputs"]["renewable_share"]["value"] = 38.7
    directory = tmp_path / "edited_unpinned"
    manifest = _write_unpinned(directory, doc)
    _pin_to(monkeypatch, manifest)

    state = map_inputs.load_table(directory)

    assert state.table is not None
    assert map_data.score_country("KOR", state=state, as_of=date(2026, 10, 19))["esg"] == 64


def test_a_changed_byte_in_the_table_means_no_inputs(tmp_path, monkeypatch):
    """Hash verification guard: a table that no longer matches its manifest gives no inputs."""
    directory = _write_table(tmp_path / "ok", SHIPPED_DOC, monkeypatch)
    body = (directory / map_inputs.TABLE_NAME).read_bytes()
    tampered = body.replace(b'"value": ', b'"value": 9', 1)
    assert tampered != body
    (directory / map_inputs.TABLE_NAME).write_bytes(tampered)

    state = map_inputs.load_table(directory)

    assert state.table is None
    assert "does not match" in state.reason


def test_a_same_length_edit_is_refused_by_the_hash_alone(tmp_path, monkeypatch):
    """Hash guard, on its own. The size is unchanged, so only the sha256 can see this edit."""
    directory = _write_table(tmp_path / "same_length", SHIPPED_DOC, monkeypatch)
    body = (directory / map_inputs.TABLE_NAME).read_bytes()
    match = re.search(rb'"value": (\d)', body)
    assert match is not None
    digit = int(match.group(1))
    tampered = body[:match.start(1)] + str((digit + 1) % 10).encode() + body[match.end(1):]
    assert len(tampered) == len(body) and tampered != body
    (directory / map_inputs.TABLE_NAME).write_bytes(tampered)

    state = map_inputs.load_table(directory)

    assert state.table is None
    assert "does not match" in state.reason


def test_a_manifest_that_does_not_record_the_table_means_no_inputs(tmp_path, monkeypatch):
    directory = tmp_path / "bad_manifest"
    _write_unpinned(directory, SHIPPED_DOC)
    manifest = json.dumps({"outputs": {}}).encode("utf-8")
    (directory / map_inputs.MANIFEST_NAME).write_bytes(manifest)
    _pin_to(monkeypatch, manifest)

    state = map_inputs.load_table(directory)

    assert state.table is None
    assert "does not record" in state.reason


def test_a_missing_table_directory_means_no_inputs(tmp_path):
    state = map_inputs.load_table(tmp_path / "does-not-exist")

    assert state.table is None
    assert "not readable" in state.reason


def test_a_bad_hash_at_startup_means_every_country_is_incomplete(tmp_path, monkeypatch):
    """End to end through the process's own table: the scores carry no inputs and no digest."""
    directory = _write_table(tmp_path / "served", SHIPPED_DOC, monkeypatch)
    body = (directory / map_inputs.TABLE_NAME).read_bytes()
    (directory / map_inputs.TABLE_NAME).write_bytes(body.replace(b'"value": ', b'"value": 9', 1))
    monkeypatch.setattr(map_inputs, "TABLE_DIR", directory)

    for c in map_data.COUNTRIES:
        result = map_data.score_country(c["code"])
        assert result["esg"] is None, c["code"]
        assert result["esg_status"] == "incomplete"
        assert result["inputs"] == {}
        assert result["inputs_table_sha256"] is None
        assert result["inputs_manifest_sha256"] is None
        assert set(result["missing_inputs"]) == set(map_inputs.INPUT_KEYS)
        assert all(r["code"] == "table_unavailable" for r in result["input_reasons"].values())


def test_a_table_with_another_schema_is_refused(tmp_path, monkeypatch):
    doc = copy.deepcopy(SHIPPED_DOC)
    doc["schema"] = "map-inputs/v0"

    assert map_inputs.load_table(_write_table(tmp_path / "schema", doc, monkeypatch)).table is None


def test_a_table_whose_policy_differs_from_the_code_is_refused(tmp_path, monkeypatch):
    doc = copy.deepcopy(SHIPPED_DOC)
    doc["policy"]["max_age_years"]["gini_index"] = 5

    state = map_inputs.load_table(_write_table(tmp_path / "policy", doc, monkeypatch))

    assert state.table is None
    assert "maximum ages" in state.reason


def test_a_duplicate_country_is_refused(tmp_path, monkeypatch):
    doc = copy.deepcopy(SHIPPED_DOC)
    doc["countries"].append(copy.deepcopy(_row(doc, "DEU")))

    assert map_inputs.load_table(_write_table(tmp_path / "dup", doc, monkeypatch)).table is None


# ---------- table-level scale checks ----------

def test_governance_on_a_percentile_scale_refuses_the_table(tmp_path, monkeypatch):
    """Scale check guard. A column with no negative value is not the WGI estimate."""
    doc = copy.deepcopy(SHIPPED_DOC)
    for row in doc["countries"]:
        entry = row["inputs"]["gov_effectiveness"]
        if entry.get("value") is not None:
            entry["value"] = abs(entry["value"]) * 40 + 10  # a 0-100 percentile look-alike

    state = map_inputs.load_table(_write_table(tmp_path / "pct", doc, monkeypatch))

    assert state.table is None
    assert "does not span zero" in state.reason


def test_renewables_as_a_fraction_refuses_the_table(tmp_path, monkeypatch):
    doc = copy.deepcopy(SHIPPED_DOC)
    for row in doc["countries"]:
        entry = row["inputs"]["renewable_share"]
        if entry.get("value") is not None:
            entry["value"] = entry["value"] / 100.0

    state = map_inputs.load_table(_write_table(tmp_path / "frac", doc, monkeypatch))

    assert state.table is None
    assert "never exceeds 1" in state.reason


# ---------- per-value scale ----------

@pytest.mark.parametrize("key,bad", [
    ("gov_effectiveness", 50.0),   # a percentile in a WGI column
    ("hdi", 94.0),                 # HDI written on 0-100
    ("gini_index", 250.0),
    ("renewable_share", 101.0),
    ("co2_per_capita", -1.0),
])
def test_a_value_outside_its_scale_is_missing_not_clipped(clock, key, bad):
    """Scale check guard: an out-of-scale value makes the country incomplete. It is never rescaled."""
    doc = copy.deepcopy(SHIPPED_DOC)
    _row(doc, "DEU")["inputs"][key]["value"] = bad
    state = _state_for(doc)

    result = map_data.score_country("DEU", state=state)

    assert result["esg"] is None
    assert key in result["missing_inputs"]
    assert result["input_reasons"][key]["code"] == "outside_scale"
    assert "outside the input's scale" in result["input_notes"][key]


def test_a_measured_zero_stays_zero(clock):
    doc = copy.deepcopy(SHIPPED_DOC)
    _row(doc, "DEU")["inputs"]["renewable_share"]["value"] = 0.0
    state = _state_for(doc)

    result = map_data.score_country("DEU", state=state)

    assert "renewable_share" not in result["missing_inputs"]
    assert result["inputs"]["renewable_share"]["value"] == 0.0
    assert result["renewable_share_pct"] == 0.0


# ---------- age at the date of use ----------

def test_the_shipped_table_scores_32_countries_at_the_day_it_was_written(clock):
    results = [map_data.score_country(c["code"]) for c in map_data.COUNTRIES]

    assert sum(1 for r in results if r["esg_status"] == "ok") == 32
    assert [c["code"] for c, r in zip(map_data.COUNTRIES, results) if r["esg"] is None] == ["NZL"]


def test_age_is_measured_at_the_date_of_use_not_the_snapshot_year(clock):
    """Age guard. The same row is fresh today and too old in 2031. A fixed snapshot year would not change."""
    state = _state_for(copy.deepcopy(SHIPPED_DOC))

    today = map_data.score_country("DEU", state=state)
    assert today["esg_status"] == "ok"
    assert today["inputs"]["hdi"]["age_years"] == 3

    clock["today"] = date(2031, 6, 1)
    later = map_data.score_country("DEU", state=state)

    assert later["esg_status"] == "incomplete"
    assert later["age_reference_date"] == "2031-06-01"
    assert later["input_notes"]["hdi"] == "too old: 8 years at 2031-06-01, the limit is 3"
    assert later["input_notes"]["renewable_share"] == "too old: 10 years at 2031-06-01, the limit is 5"


def test_a_value_too_old_at_the_date_of_use_is_missing_with_its_reason(clock):
    clock["today"] = date(2029, 1, 1)
    state = _state_for(copy.deepcopy(SHIPPED_DOC))

    result = map_data.score_country("DEU", state=state)

    assert "gov_effectiveness" in result["missing_inputs"]
    assert result["input_notes"]["gov_effectiveness"] == "too old: 4 years at 2029-01-01, the limit is 3"


def test_gini_window_edge_is_inclusive_at_ten_years(clock):
    """A Gini aged exactly 10 years qualifies; one year older does not. Policy edge, provisional."""
    doc = copy.deepcopy(SHIPPED_DOC)
    _row(doc, "DEU")["inputs"]["gini_index"]["year"] = "2016"
    _row(doc, "FRA")["inputs"]["gini_index"]["year"] = "2015"
    state = _state_for(doc)

    deu = map_data.score_country("DEU", state=state)
    fra = map_data.score_country("FRA", state=state)

    assert "gini_index" not in deu["missing_inputs"]
    assert deu["inputs"]["gini_index"]["age_years"] == 10
    assert "gini_index" in fra["missing_inputs"]
    assert "too old: 11 years" in fra["input_notes"]["gini_index"]


def test_a_year_after_the_date_of_use_is_missing(clock):
    clock["today"] = date(2024, 1, 1)
    state = _state_for(copy.deepcopy(SHIPPED_DOC))

    result = map_data.score_country("DEU", state=state)

    assert "after the date of use" in result["input_notes"]["gov_effectiveness"]
    assert result["input_reasons"]["gov_effectiveness"]["code"] == "after_use_date"


def test_a_value_with_no_stated_source_is_missing(clock):
    doc = copy.deepcopy(SHIPPED_DOC)
    _row(doc, "DEU")["inputs"]["hdi"]["source"] = ""
    state = _state_for(doc)

    result = map_data.score_country("DEU", state=state)

    assert "hdi" in result["missing_inputs"]
    assert "no source or release" in result["input_notes"]["hdi"]


# ---------- ISO3 and no fallback ----------

def test_korea_and_czechia_resolve_by_iso3_to_their_pinned_rows(clock):
    for code in ("KOR", "CZE"):
        result = map_data.score_country(code)
        row = map_inputs.current_table_state().table.rows[code]

        assert result["esg_status"] == "ok", code
        for key in map_inputs.INPUT_KEYS:
            assert result["inputs"][key]["value"] == row["inputs"][key]["value"], (code, key)


def test_nzl_is_incomplete_because_of_gini_and_nothing_else(clock):
    result = map_data.score_country("NZL")

    assert result["esg"] is None
    assert result["missing_inputs"] == ["gini_index"]
    assert result["input_notes"]["gini_index"] == "no observation in the pinned table"


def test_no_table_means_no_score_and_no_benchmark_value(clock):
    """Fallback guard. BENCHMARKS has rows for these countries; with no table none of them may be scored from it."""
    state = map_inputs.TableState(table=None, reason="test: no table")

    for c in map_data.COUNTRIES:
        result = map_data.score_country(c["code"], state=state)
        assert result["esg"] is None, c["code"]
        assert result["inputs"] == {}
        assert all(note.startswith("pinned input table unavailable") for note in result["input_notes"].values())


def test_the_score_path_imports_no_benchmark_or_live_source():
    """Fallback guard, static. The scoring module reads the pinned table only."""
    import ast
    source = Path(map_data.__file__).read_text()
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert "app.country_benchmarks" not in imported
    assert "app.external_data" not in imported


def test_the_pinned_table_is_the_only_source_of_score_inputs(clock):
    """Every input value a score reports is the table's own value, to the last digit."""
    rows = map_inputs.current_table_state().table.rows
    for c in map_data.COUNTRIES:
        result = map_data.score_country(c["code"])
        for key, provenance in result["inputs"].items():
            assert provenance["value"] == rows[c["code"]]["inputs"][key]["value"], (c["code"], key)
            assert provenance["source"] and provenance["release"]
            assert provenance["unit"] and provenance["year"]
            assert provenance["fetched_at"]
