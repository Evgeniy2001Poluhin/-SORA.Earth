"""Files retraining rewrites are not source, and are not tracked.

#191. Tracked, they made retraining and deploying mutually exclusive:

    a successful retrain      left the working tree dirty
    deploy_production.sh      refused: "working tree is not clean"
    git checkout -- models/   restored them owned by whoever ran it
    the next retrain          PermissionError, container runs as uid 1000

Observed three times in one hour on 2026-08-15, and it is the most likely
explanation for the month before: `models/` became root-owned on 17 July and
every retrain died writing its artefact until 15 August (#188).

The split is exact rather than by directory. Twelve files in `models/` are
never written at run time and still arrive with the clone; untracking those
would have removed delivery for artefacts that never caused the conflict.
"""
import os
import re
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Read from the source, not listed twice. If a seventh file starts being
#: written at run time, this test fails until it is untracked too.
def _written_at_runtime():
    with open(os.path.join(REPO_ROOT, "app", "api", "retrain.py"),
              encoding="utf-8") as fh:
        body = fh.read()
    block = body[body.index("def _do_retrain"):]
    return set(re.findall(r'MODELS_DIR, "([a-z_]+\.(?:pkl|json))"', block))


def _tracked():
    out = subprocess.run(["git", "ls-files", "models/"], cwd=REPO_ROOT,
                         capture_output=True, text=True, timeout=30)
    return {os.path.basename(p) for p in out.stdout.split()}


def test_nothing_written_at_runtime_is_tracked():
    """The property the whole issue turns on."""
    overlap = _written_at_runtime() & _tracked()

    assert overlap == set(), (
        f"{sorted(overlap)} are rewritten by _do_retrain and tracked by git. A "
        f"successful retrain will dirty the tree and block the next deploy."
    )


def test_the_written_set_is_not_empty():
    """Otherwise the assertion above passes because the parser found nothing --
    the shape of failure this file exists to catch elsewhere."""
    assert len(_written_at_runtime()) >= 5


def test_the_artefacts_nobody_writes_still_ship():
    """Untracking the directory would have been simpler and wrong: these come
    with the clone and nothing at run time touches them."""
    tracked = _tracked()

    for name in ("xgb_model.pkl", "ensemble_model_v2_cal.pkl",
                 "cat_encodings.json", "country_gdp.json"):
        assert name in tracked, name


def test_a_missing_artefact_refuses_at_startup_by_name():
    """An untracked file is one a fresh clone will not have, so the failure has
    to be legible. entrypoint.sh checks two of them and names the mount (#50).
    """
    with open(os.path.join(REPO_ROOT, "entrypoint.sh"), encoding="utf-8") as fh:
        body = fh.read()

    assert "scaler.pkl" in body and "model.pkl" in body
    assert "./models:/app/models" in body


@pytest.mark.parametrize("name", sorted(_written_at_runtime()))
def test_each_written_file_is_ignored(name):
    """`.gitignore`, so that writing one does not re-dirty the tree."""
    with open(os.path.join(REPO_ROOT, ".gitignore"), encoding="utf-8") as fh:
        assert f"models/{name}" in fh.read(), name
