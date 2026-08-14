"""Upload carries content, not a path on the server.

#26. The existing route takes `file_path` -- a location in the server's
filesystem, named by a remote caller. PR #24 made that path safe to *open*:
admin-only, anchored on a directory descriptor, `O_NOFOLLOW`, size-checked,
atomic, locked, audited. What it could not make safe is the shape, because the
shape is "a caller names a file in a filesystem it cannot see", and that means
the bytes arrived by some channel outside this API and outside its audit.

Two properties are the point here, and each is tested by making it fail:

    the caller names nothing     the temp file is named by tempfile.mkstemp in
                                 a directory this module owns
    the limit is enforced early  per chunk while reading, not by fstat after
                                 the whole body has landed

The second is the one worth care. A test that posts an oversized body and
checks for 413 passes either way -- an after-the-fact check refuses it too,
having first written every byte to disk. So the assertion is on what is left on
disk at the moment of refusal.
"""
import io
import os

import pytest

CSV = ("budget,co2_reduction,social_impact,duration_months,success\n"
       "100000,150,7,24,1\n")

URL = "/api/v1/model/data/bulk-upload/content"


@pytest.fixture(autouse=True)
def as_admin(client):
    from app.auth import require_admin
    from app.main import app

    app.dependency_overrides[require_admin] = lambda: {"username": "t", "role": "admin"}
    yield
    app.dependency_overrides.pop(require_admin, None)


@pytest.fixture
def uploads_dir(tmp_path, monkeypatch):
    """A directory this test owns, so leftovers are visible."""
    import app.api.retrain as retrain

    monkeypatch.setattr(retrain, "UPLOADS_DIR", str(tmp_path))
    return tmp_path


def _post(client, body: bytes, name="data.csv", **params):
    return client.post(URL, files={"file": (name, io.BytesIO(body), "text/csv")},
                       params=params)


# --- the caller names nothing ------------------------------------------------


def test_the_request_carries_no_server_path(client):
    """The parameter list is the contract. `file_path` must not be reachable
    here under any name, or the class is back."""
    from app.main import app

    op = app.openapi()["paths"][URL]["post"]
    names = {q["name"] for q in op.get("parameters", [])}

    assert "file_path" not in names
    assert "auto_retrain" in names


def test_the_temporary_file_is_named_by_the_server(client, uploads_dir):
    """Whatever the client calls its file, nothing with that name is created.

    The filename travels in the multipart part and is attacker-controlled;
    using it to build a path is the same defect one directory further along.
    """
    response = _post(client, CSV.encode(), name="../../etc/passwd")

    assert response.status_code == 200, response.text
    assert not any("passwd" in p.name for p in uploads_dir.iterdir())


def test_nothing_is_left_behind_after_a_successful_upload(client, uploads_dir):
    assert _post(client, CSV.encode()).status_code == 200

    assert list(uploads_dir.iterdir()) == []


# --- the limit is enforced while reading -------------------------------------


def test_an_oversized_upload_is_refused(client, uploads_dir, monkeypatch):
    import app.api.retrain as retrain

    monkeypatch.setattr(retrain, "MAX_UPLOAD_BYTES", 1024)

    response = _post(client, b"x" * 200_000)

    assert response.status_code == 413


def test_the_refusal_happens_before_the_whole_body_is_written(
        client, uploads_dir, monkeypatch):
    """The distinction this endpoint exists for.

    `fstat` after the fact refuses the same request, having first written
    200,000 bytes to disk. Here at most one chunk beyond the limit reaches it,
    and the partial file is removed -- so the check is on the largest file that
    ever existed in the directory, captured as it is unlinked.
    """
    import app.api.retrain as retrain

    monkeypatch.setattr(retrain, "MAX_UPLOAD_BYTES", 1024)
    monkeypatch.setattr(retrain, "UPLOAD_CHUNK_BYTES", 256)

    high_water = []
    real_unlink = os.unlink

    def _watch(path):
        # Only this directory. `retrain.os` is the shared os module, so the
        # patch is process-wide for the duration of the test, and an unrelated
        # unlink of a large file elsewhere would fail the assertion below for a
        # reason that has nothing to do with uploads.
        if str(path).startswith(str(uploads_dir)):
            try:
                high_water.append(os.path.getsize(path))
            except OSError:
                pass
        return real_unlink(path)

    monkeypatch.setattr(retrain.os, "unlink", _watch)

    response = _post(client, b"x" * 200_000)

    assert response.status_code == 413
    assert high_water, "the partial upload was never unlinked"
    assert max(high_water) <= 1024 + 256, (
        f"{max(high_water)} bytes reached disk for a 1024-byte limit: the size "
        f"is being checked after the body has landed, not while reading it"
    )


def test_a_refused_upload_leaves_nothing_on_disk(client, uploads_dir, monkeypatch):
    """A prefix left behind rebuilds the shared mutable directory this replaces."""
    import app.api.retrain as retrain

    monkeypatch.setattr(retrain, "MAX_UPLOAD_BYTES", 1024)

    _post(client, b"x" * 200_000)

    assert list(uploads_dir.iterdir()) == []


def test_an_empty_upload_is_refused(client, uploads_dir):
    response = _post(client, b"")

    assert response.status_code == 400
    assert list(uploads_dir.iterdir()) == []


# --- the validation is the same validation -----------------------------------


def test_a_body_that_is_not_csv_is_refused(client, uploads_dir):
    response = _post(client, b"\x00\x01\x02 not a csv at all")

    assert response.status_code == 400
    assert list(uploads_dir.iterdir()) == []


def test_missing_columns_are_refused(client, uploads_dir):
    response = _post(client, b"budget,success\n1,1\n")

    assert response.status_code == 400
    assert "Missing columns" in response.json()["detail"]


def test_an_invalid_success_value_is_refused(client, uploads_dir):
    body = ("budget,co2_reduction,social_impact,duration_months,success\n"
            "100000,150,7,24,7\n")

    response = _post(client, body.encode())

    assert response.status_code == 400
    assert "invalid success values" in response.json()["detail"]


# --- auth, and the deprecated route ------------------------------------------


def test_it_is_admin_only(client, uploads_dir):
    from app.auth import require_admin
    from app.main import app

    app.dependency_overrides.pop(require_admin, None)
    try:
        response = _post(client, CSV.encode())
    finally:
        app.dependency_overrides[require_admin] = lambda: {"username": "t", "role": "admin"}

    assert response.status_code in (401, 403), response.status_code


def test_the_path_based_route_is_marked_deprecated(client):
    """Marked, not removed: the audit line it writes is the evidence for
    removing it, and evidence needs a release to accumulate (#26)."""
    from app.main import app

    op = app.openapi()["paths"]["/api/v1/model/data/bulk-upload"]["post"]

    assert op.get("deprecated") is True
