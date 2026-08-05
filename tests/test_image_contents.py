"""What the production image must contain.

Issue #99. `scripts/refresh_indicator_history.py` is documented in its own
module as *the* way to perform the first history refresh, and it was in the
repository and not in the image. Nothing else references `scripts/` at
runtime, so nothing failed until someone tried to run it on production:

    python3: can't open file '/app/scripts/refresh_indicator_history.py'

The workaround was `docker cp`, which does not survive the next container
recreate — so the next operator meets the same wall.

**What this checks, and what it does not.** It reads the COPY instructions in the
stage the deployment actually builds -- read from `target:` in
docker-compose.prod.yml, not hardcoded -- and asserts that everything the
running system needs lands where it is expected. It does not build the image, so it cannot catch a path that is copied
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
COMPOSE = REPO / "docker-compose.prod.yml"

# Everything the container needs at runtime: where it must land, and why -- so
# that a future removal has to argue with a reason rather than with a list.
REQUIRED = {
    "app/": ("/app/app/", "the application itself"),
    "alembic/": ("/app/alembic/", "migrations, run by entrypoint.sh at start"),
    "alembic.ini": ("/app/alembic.ini", "alembic cannot find its config without it"),
    "run_scheduler.py": ("/app/run_scheduler.py", "the scheduler's command"),
    "entrypoint.sh": ("/app/entrypoint.sh", "the image's entry point"),
    "scripts/": ("/app/scripts/", "operational entry points, run in the container (#99)"),
    "data/": ("/app/data/", "reference data read at startup"),
}


def _runtime_stage():
    """The stage the deployment actually builds, read from compose.

    Derived rather than hardcoded: a service switching `target:` would
    otherwise leave these tests checking a stage nobody ships.
    """
    targets = set(re.findall(r"^\s*target:\s*(\S+)\s*$", COMPOSE.read_text(),
                             re.MULTILINE))
    assert len(targets) == 1, (
        f"expected one build target across services, found {sorted(targets)}"
    )
    return targets.pop()


def _copies_into(stage):
    """{source: absolute destination} for COPYs from the build context.

    Stage-aware and destination-aware, because neither on its own is enough:
    a COPY in the *builder* stage satisfies "scripts/ is copied" while the
    runtime image has nothing, and a runtime COPY to some other directory
    leaves /app/scripts absent just as surely. Both were possible with the
    first version of this test, which looked only at source paths across every
    stage.

    `--from=` copies are skipped: they take from another stage, not from the
    repository, so they say nothing about what the repository ships.

    Line continuations are not handled -- this Dockerfile uses none, and a
    parser that quietly mis-reads them would be worse than one that does not
    pretend to.
    """
    current = None
    workdir = "/"
    found = {}

    for raw in DOCKERFILE.read_text().splitlines():
        line = raw.strip()
        upper = line.upper()

        if upper.startswith("FROM "):
            match = re.search(r"\bAS\s+(\S+)", line, re.IGNORECASE)
            current = match.group(1) if match else None
            workdir = "/"
            continue

        if current != stage:
            continue

        if upper.startswith("WORKDIR "):
            workdir = line.split(None, 1)[1].strip()
            continue

        if not upper.startswith("COPY ") or "--from=" in line:
            continue

        parts = [p for p in line.split()[1:] if not p.startswith("--")]
        if len(parts) < 2:
            continue
        *sources, dest = parts
        for source in sources:
            if dest.endswith("/") and not dest.endswith(Path(source).name + "/"):
                # `COPY x.py ./` keeps the name; `COPY dir/ ./dir/` states it.
                resolved = f"{workdir.rstrip('/')}/{dest.strip('./').rstrip('/')}"
                resolved = f"{resolved.rstrip('/')}/{Path(source.rstrip('/')).name}"
                if source.endswith("/"):
                    resolved += "/"
            else:
                resolved = f"{workdir.rstrip('/')}/{dest.lstrip('./')}"
            found[source] = re.sub(r"/+", "/", resolved)

    return found


@pytest.mark.parametrize("path,expected,reason",
                         [(p, d, r) for p, (d, r) in sorted(REQUIRED.items())])
def test_the_image_receives(path, expected, reason):
    """Each runtime-required path is copied, in the shipped stage, to /app."""
    stage = _runtime_stage()
    copied = _copies_into(stage)

    assert path in copied, (
        f"Dockerfile.prod stage {stage!r} does not copy {path!r} ({reason}). "
        f"It copies: {sorted(copied)}"
    )
    assert copied[path] == expected, (
        f"{path!r} lands at {copied[path]!r}, not {expected!r} ({reason})"
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

    stage = _runtime_stage()
    copied = _copies_into(stage)
    assert copied.get("scripts/") == "/app/scripts/", (
        f"{len(entry_points)} runnable scripts exist and the directory carrying "
        f"them does not reach /app/scripts in stage {stage!r} "
        f"(got {copied.get('scripts/')!r}); e.g. {entry_points[0].name}"
    )
