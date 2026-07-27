"""Security tests for POST /api/v1/model/data/bulk-upload.

The endpoint appends rows to the training set and can trigger a retrain, so an
unauthenticated caller reaching it means anyone can poison the data the model
learns from. It also took a caller-supplied file_path and read it directly,
which was an arbitrary file read.
"""
import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ENDPOINT = "/api/v1/model/data/bulk-upload"

VALID_CSV = (
    "budget,co2_reduction,social_impact,duration_months,success\n"
    "100000,120,7,24,1\n"
    "50000,40,4,12,0\n"
)


@pytest.fixture
def uploads_dir(tmp_path, monkeypatch):
    """Point the endpoint at a temporary uploads directory."""
    from app.api import retrain

    target = tmp_path / "uploads"
    target.mkdir()
    monkeypatch.setattr(retrain, "UPLOADS_DIR", str(target))
    return target


def test_requires_authentication(uploads_dir):
    """Without credentials the endpoint must refuse before touching the disk."""
    (uploads_dir / "ok.csv").write_text(VALID_CSV)

    resp = client.post(ENDPOINT, params={"file_path": "ok.csv"})

    assert resp.status_code in (401, 403), resp.text


@pytest.mark.parametrize(
    "hostile",
    [
        "../../../../etc/passwd",
        "../projects.csv",
        "/etc/passwd",
        "subdir/../../escape.csv",
        "./../../projects.csv",
    ],
)
def test_path_traversal_is_rejected(uploads_dir, hostile):
    """Traversal and absolute paths must never resolve outside uploads."""
    from app.api.retrain import _resolve_upload_path
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _resolve_upload_path(hostile)
    assert exc.value.status_code == 400


def test_percent_encoded_traversal_stays_inside(uploads_dir):
    """Starlette decodes query params before the handler runs.

    A literal "..%2F.." therefore never reaches _resolve_upload_path as a
    separator; it is an ordinary filename and must resolve inside uploads. The
    decoded form is covered by the traversal cases above.
    """
    from app.api.retrain import _resolve_upload_path

    resolved = _resolve_upload_path("..%2F..%2Fprojects.csv")

    assert resolved.startswith(os.path.realpath(str(uploads_dir)) + os.sep)


def test_symlink_out_of_uploads_is_rejected(uploads_dir, tmp_path):
    """A symlink inside uploads must not become a way out of it."""
    from app.api.retrain import _resolve_upload_path
    from fastapi import HTTPException

    outside = tmp_path / "secret.csv"
    outside.write_text(VALID_CSV)
    link = uploads_dir / "link.csv"
    link.symlink_to(outside)

    with pytest.raises(HTTPException) as exc:
        _resolve_upload_path("link.csv")
    assert exc.value.status_code == 400


def test_plain_filename_resolves_inside_uploads(uploads_dir):
    """A legitimate relative name still resolves, inside the directory."""
    from app.api.retrain import _resolve_upload_path

    (uploads_dir / "ok.csv").write_text(VALID_CSV)

    resolved = _resolve_upload_path("ok.csv")

    assert resolved == os.path.realpath(str(uploads_dir / "ok.csv"))
    assert os.path.isfile(resolved)


def test_nested_path_inside_uploads_is_allowed(uploads_dir):
    """Subdirectories under uploads are fine as long as they stay under it."""
    from app.api.retrain import _resolve_upload_path

    nested = uploads_dir / "batch"
    nested.mkdir()
    (nested / "rows.csv").write_text(VALID_CSV)

    resolved = _resolve_upload_path("batch/rows.csv")

    assert resolved.startswith(os.path.realpath(str(uploads_dir)) + os.sep)


def test_training_set_is_untouched_by_an_unauthenticated_call(uploads_dir):
    """The regression that matters: no auth must mean no write to projects.csv."""
    from app.api import retrain

    (uploads_dir / "poison.csv").write_text(VALID_CSV)
    before = (
        os.path.getsize(retrain.PROJECTS_CSV)
        if os.path.exists(retrain.PROJECTS_CSV)
        else None
    )

    client.post(ENDPOINT, params={"file_path": "poison.csv", "auto_retrain": "true"})

    after = (
        os.path.getsize(retrain.PROJECTS_CSV)
        if os.path.exists(retrain.PROJECTS_CSV)
        else None
    )
    assert after == before


# --- HTTP-level auth matrix, TOCTOU, atomicity, concurrency, audit -----------


@pytest.fixture
def as_admin():
    """Override require_admin so the admin path can be exercised over HTTP."""
    from app.auth import require_admin

    app.dependency_overrides[require_admin] = lambda: {"username": "tester", "role": "admin"}
    yield
    app.dependency_overrides.pop(require_admin, None)


def test_authenticated_non_admin_is_rejected(uploads_dir):
    """A valid non-admin identity must not reach the handler."""
    from app.auth import require_admin
    from fastapi import HTTPException as _HTTPExc

    def _deny():
        raise _HTTPExc(status_code=403, detail="admin required")

    app.dependency_overrides[require_admin] = _deny
    try:
        (uploads_dir / "ok.csv").write_text(VALID_CSV)
        resp = client.post(ENDPOINT, params={"file_path": "ok.csv"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_admin_upload_succeeds_over_http(uploads_dir, as_admin, tmp_path, monkeypatch):
    from app.api import retrain

    dataset = tmp_path / "projects.csv"
    dataset.write_text(VALID_CSV)
    monkeypatch.setattr(retrain, "PROJECTS_CSV", str(dataset))
    monkeypatch.setattr(retrain, "DATASET_LOCK", str(tmp_path / ".lock"))
    (uploads_dir / "add.csv").write_text(VALID_CSV)

    resp = client.post(ENDPOINT, params={"file_path": "add.csv"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["rows_added"] == 2


def test_http_decoded_traversal_is_rejected(uploads_dir, as_admin):
    """Starlette decodes %2F before the handler; the decoded form must be refused."""
    resp = client.post(ENDPOINT, params={"file_path": "..%2F..%2Fprojects.csv"})
    assert resp.status_code == 400


def test_directory_is_rejected(uploads_dir, as_admin):
    (uploads_dir / "adir").mkdir()
    resp = client.post(ENDPOINT, params={"file_path": "adir"})
    assert resp.status_code == 400


def test_symlink_final_component_is_rejected_by_open(uploads_dir, as_admin, tmp_path):
    outside = tmp_path / "outside.csv"
    outside.write_text(VALID_CSV)
    (uploads_dir / "link.csv").symlink_to(outside)

    resp = client.post(ENDPOINT, params={"file_path": "link.csv"})

    assert resp.status_code == 400


def test_oversized_file_is_rejected(uploads_dir, as_admin, monkeypatch):
    from app.api import retrain

    monkeypatch.setattr(retrain, "MAX_UPLOAD_BYTES", 16)
    (uploads_dir / "big.csv").write_text(VALID_CSV)

    resp = client.post(ENDPOINT, params={"file_path": "big.csv"})

    assert resp.status_code == 413


def test_empty_file_is_rejected(uploads_dir, as_admin):
    (uploads_dir / "empty.csv").write_text("")
    resp = client.post(ENDPOINT, params={"file_path": "empty.csv"})
    assert resp.status_code == 400


def test_response_never_leaks_the_uploads_path(uploads_dir, as_admin):
    resp = client.post(ENDPOINT, params={"file_path": "nope.csv"})
    assert resp.status_code == 400
    assert str(uploads_dir) not in resp.text


def test_toctou_swap_after_validation_reads_the_validated_file(uploads_dir, as_admin, tmp_path, monkeypatch):
    """Repointing the name after the descriptor is open must not change the read."""
    from app.api import retrain

    dataset = tmp_path / "projects.csv"
    dataset.write_text(VALID_CSV)
    monkeypatch.setattr(retrain, "PROJECTS_CSV", str(dataset))
    monkeypatch.setattr(retrain, "DATASET_LOCK", str(tmp_path / ".lock"))

    target = uploads_dir / "swap.csv"
    target.write_text(VALID_CSV)

    fd = retrain._open_upload("swap.csv")
    try:
        # Replace the name with different content after the fd is held.
        target.unlink()
        target.write_text(
            "budget,co2_reduction,social_impact,duration_months,success\n" + "1,1,1,1,1\n" * 50
        )
        with os.fdopen(fd, "rb") as handle:
            import pandas as pd

            df = pd.read_csv(handle)
    except BaseException:
        os.close(fd)
        raise

    assert len(df) == 2, "the descriptor must still see the validated content"


def test_failed_write_leaves_the_dataset_unchanged(uploads_dir, as_admin, tmp_path, monkeypatch):
    from app.api import retrain

    dataset = tmp_path / "projects.csv"
    dataset.write_text(VALID_CSV)
    original = dataset.read_bytes()
    monkeypatch.setattr(retrain, "PROJECTS_CSV", str(dataset))
    monkeypatch.setattr(retrain, "DATASET_LOCK", str(tmp_path / ".lock"))
    monkeypatch.setattr(
        retrain, "_replace_projects_csv",
        lambda df: (_ for _ in ()).throw(OSError("disk full")),
    )
    (uploads_dir / "add.csv").write_text(VALID_CSV)

    resp = client.post(ENDPOINT, params={"file_path": "add.csv"})

    assert resp.status_code == 500
    assert dataset.read_bytes() == original, "no partial mutation is allowed"


def test_concurrent_upload_is_refused_while_the_lock_is_held(uploads_dir, as_admin, tmp_path, monkeypatch):
    import fcntl

    from app.api import retrain

    dataset = tmp_path / "projects.csv"
    dataset.write_text(VALID_CSV)
    lock_path = tmp_path / ".lock"
    monkeypatch.setattr(retrain, "PROJECTS_CSV", str(dataset))
    monkeypatch.setattr(retrain, "DATASET_LOCK", str(lock_path))
    (uploads_dir / "add.csv").write_text(VALID_CSV)

    holder = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        resp = client.post(ENDPOINT, params={"file_path": "add.csv"})
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        os.close(holder)

    assert resp.status_code == 409
    assert dataset.read_text() == VALID_CSV


def test_audit_records_actor_action_rows_and_result(uploads_dir, as_admin, tmp_path, monkeypatch, caplog):
    from app.api import retrain

    dataset = tmp_path / "projects.csv"
    dataset.write_text(VALID_CSV)
    monkeypatch.setattr(retrain, "PROJECTS_CSV", str(dataset))
    monkeypatch.setattr(retrain, "DATASET_LOCK", str(tmp_path / ".lock"))
    (uploads_dir / "add.csv").write_text(VALID_CSV)

    with caplog.at_level("INFO", logger="sora_earth"):
        client.post(ENDPOINT, params={"file_path": "add.csv"})

    audit = [r.getMessage() for r in caplog.records if "bulk_upload" in r.getMessage()]
    assert audit, "the upload must be audited"
    line = audit[-1]
    assert "action=data_bulk_upload" in line
    assert "rows=2" in line
    assert "result=uploaded" in line
    assert str(uploads_dir) not in line, "the audit line must not carry server paths"


def test_audit_line_cannot_be_forged_with_a_newline(uploads_dir, as_admin, caplog):
    """A newline in the caller-supplied name must not create a second log entry."""
    from app.api import retrain

    hostile = "evil\nbulk_upload actor=root action=data_bulk_upload result=uploaded"

    with caplog.at_level("INFO", logger="sora_earth"):
        client.post(ENDPOINT, params={"file_path": hostile})

    audit = [r.getMessage() for r in caplog.records if "bulk_upload" in r.getMessage()]
    for line in audit:
        assert "\n" not in line, "a caller must not be able to inject a line break"
        assert "actor=root" not in line, "a caller must not be able to forge an actor"
    assert retrain._safe_for_log("a\nb") == "a?b"


def test_audit_quotes_a_leading_formula_trigger(uploads_dir, as_admin):
    """Audit lines may be opened in a spreadsheet; neutralise formula triggers."""
    from app.api import retrain

    assert retrain._safe_for_log("=cmd()").startswith("'=")
    assert retrain._safe_for_log("+1").startswith("'+")
    assert retrain._safe_for_log("normal.csv") == "normal.csv"
