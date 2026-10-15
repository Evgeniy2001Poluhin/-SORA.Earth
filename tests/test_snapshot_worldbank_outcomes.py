"""scripts/snapshot_worldbank_outcomes.py against mocked HTTP. Nothing here reaches the network.

The script is loaded from its file rather than through sys.path, so no import
path is changed for the rest of the suite. Each test replaces requests.get with
a fake that serves pages shaped like the two APIs, and replaces the script's
clock and sleep, so the pacing and backoff rules are asserted without waiting.
Any request that reaches requests.get without a fake installed fails the test.
"""
import importlib.util
import json
import re
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = REPO_ROOT / "scripts" / "snapshot_worldbank_outcomes.py"
_spec = importlib.util.spec_from_file_location("snapshot_worldbank_outcomes", _SCRIPT)
snap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(snap)

SnapshotError = snap.SnapshotError
IEG_PAGE = snap.DATASETS["ieg"]["page_size"]
PROJECTS_PAGE = snap.DATASETS["projects"]["page_size"]


class FakeResponse:
    def __init__(self, status_code, content):
        self.status_code = status_code
        self.content = content


def ieg_record(i, outcome="Satisfactory", project_id=None):
    """One IEG record as text. Index i gives the default id P(i+1) and an alternating as_of_date."""
    date = "13-Oct-2026" if i % 2 == 0 else "12-Oct-2026"
    pid = project_id or "P%06d" % (i + 1)
    return (
        '{"project_id":"%s", "as_of_date":"%s", "project_name":"Caf\\u00e9 %d",'
        ' "outcome":"%s"}' % (pid, date, i, outcome)
    )


def ieg_body(total, records):
    """Bytes for an IEG page holding the given record texts, with the odd spacing kept."""
    return ('{ "count" : %d,\n "data" : [%s] }' % (total, ", ".join(records))).encode("utf-8")


def body_for(name, total, offset, n=None):
    """Bytes shaped like the real endpoint, deliberately NOT canonical JSON.

    The spacing, key order and the \\u escape would all change under a
    json.dumps round trip, so a snapshot that re-serialises would not match.
    """
    if n is None:
        n = max(0, min(snap.DATASETS[name]["page_size"], total - offset))
    if name == "ieg":
        return ieg_body(total, [ieg_record(i) for i in range(offset, offset + n)])
    else:
        items = []
        for i in range(offset, offset + n):
            pid = "P%06d" % (i + 1)
            items.append('"%s":{"id":"%s","status":"Active","totalamt":"1,000,000"}' % (pid, pid))
        # The real P&O total is a string.
        text = '{"total":"%d","rows":%d,"projects":{%s}}' % (total, PROJECTS_PAGE, ", ".join(items))
    return text.encode("utf-8")


def _name_for(url):
    for name, spec in snap.DATASETS.items():
        if url == spec["base_url"]:
            return name
    raise AssertionError(f"unexpected URL in a unit test: {url}")


class FakeWorldBank:
    """Stands in for requests.get.

    Serves each dataset's pages from `totals`. `hook(name, offset, nth)` may
    return a FakeResponse, or raise, to replace the answer for one request; it
    returns None to take the default. `nth` counts requests for that offset.
    """

    def __init__(self, totals, hook=None, clock=None):
        self.totals = totals
        self.hook = hook
        self.clock = clock
        self.calls = []
        self.served = {}

    def __call__(self, url, params=None, timeout=None):
        name = _name_for(url)
        params = dict(params or {})
        spec = snap.DATASETS[name]
        offset = int(params[spec["offset_param"]])
        nth = 1 + sum(1 for c in self.calls if c["name"] == name and c["offset"] == offset)
        self.calls.append({
            "name": name,
            "url": url,
            "params": params,
            "timeout": timeout,
            "offset": offset,
            "at": self.clock() if self.clock else None,
        })
        answer = self.hook(name, offset, nth) if self.hook else None
        if answer is None:
            answer = FakeResponse(200, body_for(name, self.totals[name], offset))
        if answer.status_code == 200:
            self.served[(name, offset)] = answer.content
        return answer


def _no_network(*args, **kwargs):
    raise AssertionError("a unit test reached requests.get without a fake installed")


@pytest.fixture
def offline(monkeypatch):
    """No network, no real sleeping, and no pacing unless a test asks for it."""
    sleeps = []
    monkeypatch.setattr(snap.requests, "get", _no_network)
    monkeypatch.setattr(snap, "_sleep", sleeps.append)
    monkeypatch.setattr(snap, "MIN_INTERVAL_S", 0.0)

    def serve(fake):
        monkeypatch.setattr(snap.requests, "get", fake)
        return fake

    return SimpleNamespace(serve=serve, sleeps=sleeps, monkeypatch=monkeypatch)


def _partial(out):
    return out.with_name(out.name + ".partial")


def test_ieg_paging_stops_exactly_at_count(tmp_path, offline):
    fake = offline.serve(FakeWorldBank({"ieg": 2500}))
    manifest = snap.take_snapshot(tmp_path / "snap", ["ieg"])

    offsets = [c["offset"] for c in fake.calls]
    assert offsets == [0, IEG_PAGE, 2 * IEG_PAGE], "a request beyond the count was made"
    assert manifest["datasets"]["ieg"]["sum_records"] == 2500
    assert [c["params"] for c in fake.calls][0] == {
        "datasetId": "DS00053", "resourceId": "RS00055", "type": "json",
        "top": 1000, "skip": 0,
    }


@pytest.mark.parametrize("total, expected_offsets", [
    (1000, [0, PROJECTS_PAGE]),
    (1001, [0, PROJECTS_PAGE, 2 * PROJECTS_PAGE]),
])
def test_projects_paging_stops_exactly_at_total(tmp_path, offline, total, expected_offsets):
    fake = offline.serve(FakeWorldBank({"projects": total}))
    snap.take_snapshot(tmp_path / "snap", ["projects"])

    assert [c["offset"] for c in fake.calls] == expected_offsets
    assert all(c["params"]["format"] == "json" for c in fake.calls)
    assert all("fl" not in c["params"] for c in fake.calls)


def test_empty_page_before_total_is_refused(tmp_path, offline):
    def hook(name, offset, nth):
        if offset == IEG_PAGE:
            return FakeResponse(200, body_for("ieg", 2500, offset, n=0))
        return None

    fake = offline.serve(FakeWorldBank({"ieg": 2500}, hook=hook))
    with pytest.raises(SnapshotError, match="empty page before the reported total of 2500"):
        snap.take_snapshot(tmp_path / "snap", ["ieg"])

    assert [c["offset"] for c in fake.calls] == [0, IEG_PAGE], "paging went on past the empty page"


def test_changed_total_is_refused(tmp_path, offline):
    def hook(name, offset, nth):
        if offset == IEG_PAGE:
            return FakeResponse(200, body_for("ieg", 2501, offset))
        return None

    offline.serve(FakeWorldBank({"ieg": 2500}, hook=hook))
    with pytest.raises(SnapshotError, match="total changed from 2500 to 2501"):
        snap.take_snapshot(tmp_path / "snap", ["ieg"])
    assert not (tmp_path / "snap").exists()


def test_pages_that_do_not_add_up_are_refused(tmp_path, offline):
    def hook(name, offset, nth):
        if offset == 0:
            return FakeResponse(200, body_for("ieg", 2500, 0, n=900))
        return None

    offline.serve(FakeWorldBank({"ieg": 2500}, hook=hook))
    with pytest.raises(SnapshotError, match="hold 2400 records but the API reports a total of 2500"):
        snap.take_snapshot(tmp_path / "snap", ["ieg"])
    assert not (tmp_path / "snap").exists()


@pytest.mark.parametrize("body, reason", [
    (b"<html>maintenance</html>", "not JSON"),
    (b'{"data": []}', "no 'count' field"),
    (b'{"count": true, "data": []}', "boolean"),
    (b'{"count": 0, "data": []}', "nothing to snapshot"),
    (b'{"count": 5, "data": {}}', "is not a list"),
])
def test_misshapen_responses_are_refused(tmp_path, offline, body, reason):
    def hook(name, offset, nth):
        return FakeResponse(200, body) if offset == 0 else None

    offline.serve(FakeWorldBank({"ieg": 2500}, hook=hook))
    with pytest.raises(SnapshotError, match=re.escape(reason)):
        snap.take_snapshot(tmp_path / "snap", ["ieg"])


def test_raw_bytes_are_saved_unchanged(tmp_path, offline):
    fake = offline.serve(FakeWorldBank({"ieg": 2500, "projects": 1001}))
    snap.take_snapshot(tmp_path / "snap", None)

    assert fake.served, "the fake served nothing"
    for (name, offset), body in fake.served.items():
        # Sanity: these bodies would not survive a re-serialisation unchanged.
        assert json.dumps(json.loads(body)).encode("utf-8") != body
        page = offset // snap.DATASETS[name]["page_size"]
        saved = tmp_path / "snap" / name / f"page-{page:05d}.json"
        assert saved.read_bytes() == body, f"{name} offset {offset} was not saved byte for byte"


def test_manifest_matches_the_files(tmp_path, offline):
    offline.serve(FakeWorldBank({"ieg": 2500, "projects": 1001}))
    out = tmp_path / "snap"
    snap.take_snapshot(out, None)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["tool"]["name"] == "scripts/snapshot_worldbank_outcomes.py"
    sha = manifest["tool"]["git_sha"]
    assert sha is None or re.fullmatch(r"[0-9a-f]{40}", sha)
    for key in ("started_utc", "finished_utc"):
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", manifest[key])

    lines = []
    for name, dataset in manifest["datasets"].items():
        assert dataset["sum_records"] == dataset["api_total"]
        records_seen = 0
        for page in dataset["pages"]:
            body = (out / page["file"]).read_bytes()
            assert page["bytes"] == len(body)
            assert page["sha256"] == sha256(body).hexdigest()
            assert page["http_status"] == 200
            payload = json.loads(body)
            held = payload["data"] if name == "ieg" else payload["projects"]
            assert page["records"] == len(held)
            records_seen += len(held)
            lines.append(f"{page['sha256']}  {page['file']}")
        assert records_seen == dataset["sum_records"]

    ieg = manifest["datasets"]["ieg"]
    assert ieg["api_total"] == 2500
    assert ieg["unique_ids"] == 2500
    assert ieg["records_without_id"] == 0
    assert ieg["duplicate_records"] == 0
    assert manifest["datasets"]["projects"]["duplicate_records"] == 0
    assert ieg["as_of_dates"] == ["12-Oct-2026", "13-Oct-2026"]
    assert "as_of_dates" not in manifest["datasets"]["projects"]
    assert manifest["datasets"]["projects"]["unique_ids"] == 1001

    expected = sha256("".join(line + "\n" for line in sorted(lines)).encode("utf-8")).hexdigest()
    assert manifest["snapshot_digest"] == expected


def test_existing_non_empty_out_is_refused(tmp_path, offline):
    out = tmp_path / "snap"
    out.mkdir()
    (out / "keep.txt").write_text("already here", encoding="utf-8")
    fake = offline.serve(FakeWorldBank({"ieg": 2500}))

    with pytest.raises(SnapshotError, match="already exists and is not an empty directory"):
        snap.take_snapshot(out, ["ieg"])

    assert fake.calls == [], "requests were made for a destination that was refused"
    assert (out / "keep.txt").read_text(encoding="utf-8") == "already here"
    assert not _partial(out).exists()


def test_leftover_partial_directory_is_refused(tmp_path, offline):
    out = tmp_path / "snap"
    _partial(out).mkdir()
    fake = offline.serve(FakeWorldBank({"ieg": 2500}))

    with pytest.raises(SnapshotError, match="left by an earlier run that did not finish"):
        snap.take_snapshot(out, ["ieg"])
    assert fake.calls == []


def test_an_empty_existing_out_is_allowed(tmp_path, offline):
    out = tmp_path / "snap"
    out.mkdir()
    offline.serve(FakeWorldBank({"ieg": 2500}))
    snap.take_snapshot(out, ["ieg"])
    assert (out / "manifest.json").is_file()


def test_partial_is_renamed_only_after_success(tmp_path, offline):
    out = tmp_path / "snap"

    def hook(name, offset, nth):
        if name == "projects" and offset == PROJECTS_PAGE:
            return FakeResponse(404, b"gone")
        return None

    offline.serve(FakeWorldBank({"ieg": 1500, "projects": 1001}, hook=hook))
    with pytest.raises(SnapshotError, match="HTTP 404"):
        snap.take_snapshot(out, None)

    assert not out.exists(), "a failed run created the final directory"
    assert _partial(out).is_dir(), "the partial directory should stay for inspection"
    assert (_partial(out) / "ieg" / "page-00000.json").is_file()
    assert not (_partial(out) / "manifest.json").exists()

    out2 = tmp_path / "snap2"
    offline.serve(FakeWorldBank({"ieg": 1500}))
    snap.take_snapshot(out2, ["ieg"])
    assert out2.is_dir()
    assert (out2 / "manifest.json").is_file()
    assert not _partial(out2).exists(), "a finished run left its partial directory behind"


def test_429_is_retried_then_succeeds(tmp_path, offline):
    def hook(name, offset, nth):
        if name == "ieg" and offset == 0 and nth == 1:
            return FakeResponse(429, b"slow down")
        return None

    fake = offline.serve(FakeWorldBank({"ieg": 1500}, hook=hook))
    manifest = snap.take_snapshot(tmp_path / "snap", ["ieg"])

    assert sum(1 for c in fake.calls if c["offset"] == 0) == 2, "the 429 was not retried"
    assert offline.sleeps == [60], "the first retry should wait 60 s"
    assert manifest["datasets"]["ieg"]["sum_records"] == 1500


def test_retries_stop_after_three_attempts_and_name_the_page(tmp_path, offline):
    def hook(name, offset, nth):
        return FakeResponse(503, b"busy") if (name == "ieg" and offset == 0) else None

    fake = offline.serve(FakeWorldBank({"ieg": 1500}, hook=hook))
    with pytest.raises(SnapshotError, match=r"ieg page 0 \(skip=0\).*all 3 attempts"):
        snap.take_snapshot(tmp_path / "snap", ["ieg"])

    assert sum(1 for c in fake.calls if c["offset"] == 0) == 3
    assert offline.sleeps == [60, 120], "backoff should double from 60 s, with no wait after the last attempt"


def test_connection_errors_are_retried(tmp_path, offline):
    def hook(name, offset, nth):
        if name == "ieg" and offset == 0 and nth == 1:
            raise requests.ConnectionError("reset by peer")
        return None

    offline.serve(FakeWorldBank({"ieg": 1500}, hook=hook))
    snap.take_snapshot(tmp_path / "snap", ["ieg"])
    assert offline.sleeps == [60]


def test_other_client_errors_are_not_retried(tmp_path, offline):
    def hook(name, offset, nth):
        return FakeResponse(404, b"no such dataset") if name == "ieg" else None

    fake = offline.serve(FakeWorldBank({"ieg": 1500}, hook=hook))
    with pytest.raises(SnapshotError, match=r"ieg page 0 \(skip=0\): HTTP 404; not retried"):
        snap.take_snapshot(tmp_path / "snap", ["ieg"])
    assert len(fake.calls) == 1
    assert offline.sleeps == []


def test_requests_are_at_least_one_second_apart(tmp_path, offline):
    now = [0.0]
    offline.monkeypatch.setattr(snap, "MIN_INTERVAL_S", 1.0)
    offline.monkeypatch.setattr(snap, "_clock", lambda: now[0])
    offline.monkeypatch.setattr(snap, "_sleep", lambda s: now.__setitem__(0, now[0] + s))
    fake = offline.serve(FakeWorldBank({"ieg": 2500}, clock=lambda: now[0]))

    snap.take_snapshot(tmp_path / "snap", ["ieg"])

    starts = [c["at"] for c in fake.calls]
    assert len(starts) == 3
    assert all(later - earlier >= 1.0 for earlier, later in zip(starts, starts[1:])), starts


def test_dry_run_writes_nothing_and_fetches_page_zero_only(tmp_path, offline, capsys):
    out = tmp_path / "snap"
    fake = offline.serve(FakeWorldBank({"ieg": 2500, "projects": 1001}))

    assert snap.main(["--out", str(out), "--dry-run"]) == 0

    assert [(c["name"], c["offset"]) for c in fake.calls] == [("ieg", 0), ("projects", 0)]
    assert not out.exists() and not _partial(out).exists()
    text = capsys.readouterr().out
    assert "ieg: API total 2500; page 0 holds 1000 records" in text
    assert "ieg: first record keys: project_id, as_of_date, project_name, outcome" in text
    assert "projects: API total 1001; page 0 holds 500 records" in text
    assert "dry run: nothing written" in text


def test_only_flag_fetches_one_dataset(tmp_path, offline):
    fake = offline.serve(FakeWorldBank({"ieg": 2500, "projects": 1001}))
    out = tmp_path / "snap"

    assert snap.main(["--out", str(out), "--only", "projects"]) == 0

    assert {c["name"] for c in fake.calls} == {"projects"}
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert list(manifest["datasets"]) == ["projects"]


def test_main_reports_a_refusal_with_exit_code_one(tmp_path, offline, capsys):
    out = tmp_path / "snap"
    out.mkdir()
    (out / "keep.txt").write_text("x", encoding="utf-8")
    fake = offline.serve(FakeWorldBank({"ieg": 2500}))

    assert snap.main(["--out", str(out), "--only", "ieg"]) == 1
    assert fake.calls == []
    assert "already exists" in capsys.readouterr().err


def test_projects_requests_are_sorted_by_id(tmp_path, offline):
    fake = offline.serve(FakeWorldBank({"projects": 1001}))
    snap.take_snapshot(tmp_path / "snap", ["projects"])

    assert len(fake.calls) == 3
    for call in fake.calls:
        assert call["params"]["srt"] == "id"
        assert call["params"]["order"] == "asc"


def test_projects_repeated_ids_are_refused(tmp_path, offline):
    """Page 1 repeats page 0 exactly. The total and the record sum both still match."""

    def hook(name, offset, nth):
        if name == "projects" and offset == PROJECTS_PAGE:
            return FakeResponse(200, body_for("projects", 1000, 0))
        return None

    offline.serve(FakeWorldBank({"projects": 1000}, hook=hook))
    with pytest.raises(
        SnapshotError,
        match=r"hold 1000 records but 500 distinct ids .*the paging was not stable",
    ):
        snap.take_snapshot(tmp_path / "snap", ["projects"])
    assert not (tmp_path / "snap").exists()


def test_ieg_identical_record_on_two_pages_is_refused(tmp_path, offline):
    """Page 1 starts with a copy of page 0's last record. Count and sum still match."""

    def hook(name, offset, nth):
        if name == "ieg" and offset == IEG_PAGE:
            records = [ieg_record(IEG_PAGE - 1)]
            records += [ieg_record(i) for i in range(IEG_PAGE, 2 * IEG_PAGE - 1)]
            return FakeResponse(200, ieg_body(2500, records))
        return None

    offline.serve(FakeWorldBank({"ieg": 2500}, hook=hook))
    with pytest.raises(SnapshotError, match="identical to an earlier record"):
        snap.take_snapshot(tmp_path / "snap", ["ieg"])
    assert not (tmp_path / "snap").exists()


def test_ieg_same_project_id_with_a_different_record_passes(tmp_path, offline):
    """IEG repeats a project_id legitimately when the records differ."""

    def hook(name, offset, nth):
        if name == "ieg" and offset == IEG_PAGE:
            records = [ieg_record(IEG_PAGE, outcome="Unsatisfactory", project_id="P%06d" % IEG_PAGE)]
            records += [ieg_record(i) for i in range(IEG_PAGE + 1, 2 * IEG_PAGE)]
            return FakeResponse(200, ieg_body(2500, records))
        return None

    offline.serve(FakeWorldBank({"ieg": 2500}, hook=hook))
    manifest = snap.take_snapshot(tmp_path / "snap", ["ieg"])

    ieg = manifest["datasets"]["ieg"]
    assert ieg["duplicate_records"] == 0
    assert ieg["sum_records"] == 2500
    assert ieg["unique_ids"] == 2499, "one project_id appears on two pages"


def test_help_runs_as_a_subprocess():
    proc = subprocess.run(
        [sys.executable, "scripts/snapshot_worldbank_outcomes.py", "--help"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    for flag in ("--out", "--only", "--dry-run"):
        assert flag in proc.stdout
