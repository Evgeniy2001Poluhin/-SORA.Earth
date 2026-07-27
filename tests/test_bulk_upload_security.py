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
