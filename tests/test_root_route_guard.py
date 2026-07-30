"""GET / must not 500 when the SPA has not been built.

app/static/index.html is a symlink to spa/index.html, which is build output and
is not tracked once the SPA is built in the image. On any checkout where the
frontend has not been built the link dangles, and FileResponse raises
RuntimeError at request time — a 500 for a condition whose honest answer is 404.
"""
import os

from app.main import BASE_DIR


def test_root_returns_a_status_when_the_index_is_missing(client, tmp_path, monkeypatch):
    """Point BASE_DIR's index at nothing and confirm the route still answers."""
    import app.main as m

    missing = str(tmp_path / "no-such-root")
    monkeypatch.setattr(m, "BASE_DIR", missing)

    response = client.get("/")

    assert response.status_code in (404, 200), response.text
    if response.status_code == 404:
        assert "not built" in response.text.lower()


def test_root_serves_the_index_when_it_resolves(client):
    """The normal path is unaffected: a resolvable index is still served."""
    index = os.path.join(BASE_DIR, "static", "index.html")
    response = client.get("/")

    if os.path.exists(index):
        assert response.status_code == 200
    else:
        assert response.status_code == 404


def test_a_dangling_symlink_is_treated_as_missing(tmp_path):
    """os.path.exists resolves the link rather than reporting the link itself."""
    link = tmp_path / "index.html"
    link.symlink_to(tmp_path / "spa" / "index.html")

    assert link.is_symlink()
    assert not os.path.exists(link), "a dangling symlink must read as missing"
