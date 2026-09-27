"""Region names must reach the production image.

The defect (measured on production 2026-09-27): GET /api/v1/map/russia/RU-MOW
returned `"region": {"code": "RU-MOW", "name": "RU-MOW"}`, and list items
carried only `code`. app/routes/map_russia.py builds _REGIONS_META at import by
parsing web/src/data/russia_regions.ts; when the file is missing, _load_regions()
returns {} and every name falls back to the code.

The production image copies app/, data/, alembic/, scripts/ and more, but not
web/src/data/russia_regions.ts. It exists in the repository, so every local run
and CI sees names; only the image does not.

This test ensures:
1. The region-loading code works when the file is present
2. The Dockerfile.prod runtime stage copies the file to the right location
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "Dockerfile.prod"
COMPOSE = REPO / "docker-compose.prod.yml"


def test_load_regions_gives_names_for_all_declared():
    """Every declared region has a non-empty name different from its code."""
    from app.services.esg_aggregator import DECLARED_REGIONS
    from app.routes.map_russia import _load_regions

    regions = _load_regions()

    # Denominator: 85 declared regions
    assert len(DECLARED_REGIONS) == 85, (
        f"expected 85 declared regions, found {len(DECLARED_REGIONS)}"
    )

    # Every declared region is present
    missing = DECLARED_REGIONS - set(regions.keys())
    assert not missing, (
        f"{len(missing)} declared regions missing from _load_regions(): "
        f"{sorted(missing)}"
    )

    # Each has a non-empty name different from its code
    for code in sorted(DECLARED_REGIONS):
        name = regions[code].get("name")
        assert name, f"{code!r} has empty or missing name in _load_regions()"
        assert name != code, (
            f"{code!r} name is the code itself ({name!r}), not a region name"
        )


def _runtime_stage():
    """The stage the deployment builds, read from docker-compose.prod.yml.

    Derived, not hardcoded: if a service switches `target:` the tests check the
    stage that ships, not the old one.
    """
    targets = set(re.findall(r"^\s*target:\s*(\S+)\s*$", COMPOSE.read_text(),
                             re.MULTILINE))
    assert len(targets) == 1, (
        f"expected one build target across services, found {sorted(targets)}"
    )
    return targets.pop()


def _copies_into(stage):
    """{source: absolute destination} for COPYs from the build context.

    Stage-aware and destination-aware: a COPY in the builder stage or to the
    wrong directory leaves the runtime image missing the file. `--from=` copies
    are skipped -- they take from another stage, not from the repository.
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
            # Resolve relative destination against WORKDIR
            if dest.startswith("/"):
                resolved = dest
            else:
                resolved = f"{workdir.rstrip('/')}/{dest.lstrip('./')}"

            # If source is a file and dest is a directory or ends with /,
            # the file keeps its name
            if not source.endswith("/") and dest.endswith("/"):
                resolved = f"{resolved.rstrip('/')}/{Path(source).name}"

            # Normalize multiple slashes
            resolved = re.sub(r"/+", "/", resolved)
            found[source] = resolved

    return found


def test_the_image_receives_russia_regions_ts():
    """Dockerfile.prod runtime stage copies web/src/data/russia_regions.ts.

    app/routes/map_russia.py reads it as:
        _TS_PATH = Path(__file__).resolve().parent.parent.parent / "web/src/data/russia_regions.ts"

    Since __file__ is /app/app/routes/map_russia.py, that resolves to
    /app/web/src/data/russia_regions.ts, which is where the COPY must land it.

    Derived from _TS_PATH rather than typed, so moving the file cannot leave the
    test checking the old name.
    """
    from app.routes.map_russia import _TS_PATH

    # Compute the path _TS_PATH expects, relative to the repository root
    # _TS_PATH is: <repo>/web/src/data/russia_regions.ts
    # Relative to repo: web/src/data/russia_regions.ts
    relative_path = _TS_PATH.relative_to(REPO)
    source = str(relative_path)

    # Inside the image: /app/web/src/data/russia_regions.ts
    # (because WORKDIR is /app and map_russia.py is at /app/app/routes/)
    expected_dest = f"/app/{source}"

    stage = _runtime_stage()
    copied = _copies_into(stage)

    assert source in copied, (
        f"Dockerfile.prod stage {stage!r} does not copy {source!r}. "
        f"map_russia.py reads it at {_TS_PATH} inside the container. "
        f"It copies: {sorted(copied.keys())}"
    )
    assert copied[source] == expected_dest, (
        f"{source!r} lands at {copied[source]!r}, not {expected_dest!r}. "
        f"map_russia.py will not find it at {_TS_PATH}"
    )
