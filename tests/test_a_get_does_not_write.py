"""Reading a chart does not rewrite a file.

`GET /model/reliability-diagram` and `GET /model/ab-comparison/plot` rendered
their PNG with `plt.savefig(os.path.join(data_dir(), ...))` and returned it with
`FileResponse`. Both files are tracked in git, so every request dirtied the
working tree in development and wrote into the data directory in production --
and an overnight sweep that called every GET "because GETs only read" rewrote
both files in the repository (2026-09-24, restored from the index).

A GET is the route's promise not to change state; these two kept it in the
response and broke it in the handler. They now render into memory and return
the same bytes, the same media type, the same attachment filename and the same
`X-Metrics` / `X-Samples` headers.

The check records the data directory -- every file's size and modification
time -- before and after each request. It first asserts the route answered 200
with a PNG, because a route that failed before rendering would also leave the
directory untouched, and would pass for the wrong reason.
"""
import os

import pytest

from app.paths import data_dir

ROUTES = [
    ("/api/v1/model/reliability-diagram", "reliability_diagram.png"),
    ("/api/v1/model/ab-comparison/plot", "ab_comparison.png"),
]


def _snapshot(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            st = os.stat(p)
            out[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns)
    return out


@pytest.mark.parametrize("path,filename", ROUTES)
def test_the_chart_route_answers_without_writing(client, path, filename):
    root = data_dir()
    before = _snapshot(root)

    r = client.get(path)

    assert r.status_code == 200, f"{path} answered {r.status_code}; the write check below would mean nothing"
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG\r\n\x1a\n"), "the body is not a PNG"
    assert f'filename="{filename}"' in r.headers.get("content-disposition", ""), r.headers.get("content-disposition")

    after = _snapshot(root)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    assert not changed, f"GET {path} changed files in the data directory: {changed}"
