"""The seed artefacts must be real files at deploy time, not LFS pointers (PR C).

`scripts/check_seed_materialized.sh` refuses a checkout where `git lfs` did not
smudge, so a container is never built from ~130-byte pointer stubs that would
load as models and fail at the first prediction. The guard reads only tracked
files under `models/`, so its behaviour can be exercised on throwaway repos.
"""
import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(REPO, "scripts", "check_seed_materialized.sh")

POINTER = (
    "version https://git-lfs.github.com/spec/v1\n"
    "oid sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
    "size 793976\n"
)


def _run(checkout):
    return subprocess.run(
        ["bash", GUARD], env={**os.environ, "SEED_CHECKOUT": str(checkout)},
        capture_output=True, text=True,
    )


def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    return tmp_path


def _commit(repo, relpath, content, *, binary=False):
    full = repo / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    if binary:
        full.write_bytes(content)
    else:
        full.write_text(content)
    subprocess.run(["git", "add", str(relpath)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add"], cwd=repo, check=True)
    return full


def test_the_repository_seed_is_materialized():
    """The tree under test ships real artefacts; the guard must pass on it."""
    result = _run(REPO)
    assert result.returncode == 0, result.stderr
    assert "materialized" in result.stdout


def test_a_pointer_is_refused(tmp_path):
    repo = _git_repo(tmp_path)
    _commit(repo, "models/model.pkl", POINTER)

    result = _run(repo)
    assert result.returncode == 1
    assert "LFS POINTER" in result.stderr
    assert "models/model.pkl" in result.stderr


def test_a_materialized_pth_passes(tmp_path):
    repo = _git_repo(tmp_path)
    _commit(repo, "models/pytorch_mlp.pth", b"\x80\x04binary-not-a-pointer", binary=True)

    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_a_tracked_but_absent_artefact_is_refused(tmp_path):
    repo = _git_repo(tmp_path)
    full = _commit(repo, "models/model.pkl", b"\x80\x04real", binary=True)
    full.unlink()  # tracked by git, gone from the working tree

    result = _run(repo)
    assert result.returncode == 1
    assert "MISSING" in result.stderr


def test_a_tree_without_model_artefacts_passes(tmp_path):
    """The deploy guard's own sandbox has no models/; the check must not trip there."""
    repo = _git_repo(tmp_path)
    _commit(repo, "README.md", "no models here")

    result = _run(repo)
    assert result.returncode == 0
    assert "no tracked model artefacts" in result.stdout


def test_only_pkl_and_pth_are_treated_as_artefacts(tmp_path):
    """A pointer-looking file that is not a model artefact is not this guard's concern."""
    repo = _git_repo(tmp_path)
    _commit(repo, "models/model.pkl", b"\x80\x04real", binary=True)
    _commit(repo, "models/notes.txt", POINTER)  # same text, wrong extension

    result = _run(repo)
    assert result.returncode == 0, result.stderr
