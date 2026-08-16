"""Model artefacts must be LFS-tracked wherever they sit under models/.

`models/*.pkl` matches one level. A file in `models/seed/` would fall outside
it, enter git as an ordinary binary, and a fresh clone would fail on a missing
LFS object — which is what closed PR #196. The tracking *is* the delivery
mechanism.

Asserted with `git check-attr`, which reports what git would actually apply,
rather than by reading `.gitattributes` and believing it. A pattern can be
present and still not match.
"""
import os
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def attr_for(path, name="filter"):
    result = subprocess.run(
        ["git", "check-attr", name, "--", path],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"git check-attr unavailable: {result.stderr.strip()}")
    # "path: filter: lfs"
    return result.stdout.strip().rsplit(": ", 1)[-1]


@pytest.mark.parametrize("path", [
    "models/model.pkl",
    "models/seed/model.pkl",
    "models/seed/scaler.pkl",
    "models/seed/nested/deeper/model.pkl",
])
def test_a_pickle_anywhere_under_models_is_lfs_tracked(path):
    assert attr_for(path) == "lfs", (
        f"{path} would enter git as an ordinary binary; a fresh clone fails on "
        "the missing LFS object"
    )


def test_the_torch_checkpoint_is_covered_too():
    """`pytorch_mlp.pth` is loaded by app/main.py and is not a .pkl."""
    assert attr_for("models/seed/pytorch_mlp.pth") == "lfs"


def test_something_outside_models_is_not_swept_in():
    """The pattern must not quietly capture unrelated files."""
    assert attr_for("data/projects.csv") != "lfs"
    assert attr_for("app/main.py") != "lfs"
