"""What the production image must contain.

Issue #99. `scripts/refresh_indicator_history.py` is documented in its own
module as *the* way to perform the first history refresh, and it was in the
repository and not in the image. Nothing else references `scripts/` at
runtime, so nothing failed until someone tried to run it on production:

    python3: can't open file '/app/scripts/refresh_indicator_history.py'

The workaround was `docker cp`, which does not survive the next container
recreate — so the next operator meets the same wall.

**What this checks, and what it does not.** It reads the COPY instructions in
Dockerfile.prod and asserts that everything the running system needs is among
them. It does not build the image, so it cannot catch a path that is copied
and then removed, or a .dockerignore rule that empties it. CI does not build
Dockerfile.prod at all — the runtime stage installs torch, which is minutes
per run — so a build-and-inspect test would be new and slow infrastructure for
this one question. Stated here rather than left for someone to assume this is
stronger than it is.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "Dockerfile.prod"

# Everything the container needs at runtime, and why -- so that a future
# removal has to argue with a reason rather than with a list.
REQUIRED = {
    "app/": "the application itself",
    "alembic/": "migrations, run by entrypoint.sh before the app starts",
    "alembic.ini": "alembic cannot find its config without it",
    "run_scheduler.py": "the scheduler container's command",
    "entrypoint.sh": "the image's entry point",
    "scripts/": "operational entry points, run inside the container by hand (#99)",
    "data/": "reference data read at startup",
}


def _copied_paths():
    """Source paths of every COPY in the file, ignoring --from= stages.

    A COPY with --from= brings something out of a build stage rather than from
    the repository, so it says nothing about what the repository ships.
    """
    text = DOCKERFILE.read_text()
    paths = set()
    for line in text.splitlines():
        line = line.strip()
        if not line.upper().startswith("COPY "):
            continue
        if "--from=" in line:
            continue
        parts = [p for p in line.split()[1:] if not p.startswith("--")]
        if len(parts) >= 2:
            paths.update(parts[:-1])
    return paths


@pytest.mark.parametrize("path,reason", sorted(REQUIRED.items()))
def test_the_image_receives(path, reason):
    """Each runtime-required path is copied into the image."""
    copied = _copied_paths()
    assert path in copied, (
        f"Dockerfile.prod does not copy {path!r} ({reason}). "
        f"It copies: {sorted(copied)}"
    )


def test_every_script_entry_point_is_shipped():
    """The rule behind the list: a runnable script must reach the image.

    Derived from the filesystem rather than restated, so a new operational
    script is covered the moment it is added -- which is the case that went
    wrong. `scripts/` is copied as a directory, so this passes as a whole or
    fails as a whole; the point is that the directory cannot quietly stop
    being copied while entry points keep being added to it.
    """
    entry_points = [
        p for p in sorted((REPO / "scripts").glob("*.py"))
        if "__main__" in p.read_text()
    ]
    assert entry_points, "no runnable scripts found -- has the layout changed?"

    copied = _copied_paths()
    assert "scripts/" in copied, (
        f"{len(entry_points)} runnable scripts exist and none reach the image; "
        f"e.g. {entry_points[0].name}"
    )
