"""A named volume mounted where the image has no directory is owned by root.

Docker initialises an empty named volume from the image path it is mounted over,
ownership included. If that path does not exist in the image there is nothing to
copy from and the volume's root stays `0:0` — while `backend` and `scheduler`
declare `user: "1000:1000"`.

`runtime_models` was mounted at `/app/runtime`, which the image never created.
Measured on the first deployment carrying #191 to a fresh server, 2026-09-02:

    PermissionError: [Errno 13] Permission denied: '/app/runtime/.model-source.lock'
      app/main.py:413            load_champion(recover_first=True)
      app/model_source.py:155    os.open(_lock_path(), os.O_CREAT | os.O_RDWR)

gunicorn reported "Worker failed to boot", nginx answered 502, and the
deployment refused at exit 76 and rolled back.

The window is narrow and that is the point: a volume is empty exactly once, so
every deployment to a server that already has one passes, and the failure waits
for the machine built from nothing — the restore path, during an incident.

**What this checks, and what it does not.** Like `tests/test_image_contents.py`,
it reads the Dockerfile rather than building it: CI does not build
`Dockerfile.prod` at all, because the runtime stage installs torch. So it can
see that a mount point is created and cannot see it removed again later, nor
prove the resulting ownership. Stated here rather than left to be assumed
stronger than it is.
"""
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "Dockerfile.prod"
COMPOSE = REPO / "docker-compose.prod.yml"


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dockerfile_text():
    return DOCKERFILE.read_text(encoding="utf-8")


def named_volumes(compose):
    return set((compose.get("volumes") or {}).keys())


def image_built_services(compose):
    """Services running the image this repository builds.

    Matched by `build:`, or by an image reference naming the local application
    image — a service pulling postgres or grafana is somebody else's filesystem
    and not this file's business.
    """
    out = {}
    for name, service in (compose.get("services") or {}).items():
        service = service or {}
        image = str(service.get("image") or "")
        if service.get("build") or "sora_earth_app" in image or "SORA_APP_IMAGE" in image:
            out[name] = service
    return out


def mount_points(service, volumes):
    """(source, target) for each *named volume* mounted into the service."""
    found = []
    for entry in service.get("volumes") or []:
        if isinstance(entry, str):
            parts = entry.split(":")
            if len(parts) >= 2 and parts[0] in volumes:
                found.append((parts[0], parts[1]))
        elif isinstance(entry, dict) and entry.get("type") == "volume":
            if entry.get("source") in volumes:
                found.append((entry.get("source"), entry.get("target")))
    return found


def paths_created_by_image(text):
    """Directories the Dockerfile brings into being, however it does it."""
    created = set()
    for match in re.finditer(r"^\s*RUN\s+(.+?)(?<!\\)$", text, re.M):
        for path in re.findall(r"mkdir\s+(?:-\w+\s+)*([^\s&|;]+)", match.group(1)):
            created.add(path.rstrip("/"))
    # COPY brings its destination into being too.
    for match in re.finditer(r"^\s*COPY\s+(.+)$", text, re.M):
        tokens = [t for t in match.group(1).split() if not t.startswith("--")]
        if tokens:
            created.add(tokens[-1].rstrip("/"))
    return created


def _resolves_to(target, created, workdir="/app"):
    """`./runtime`, `runtime` and `/app/runtime` name the same directory."""
    target = target.rstrip("/")
    candidates = {target}
    if target.startswith(workdir + "/"):
        rel = target[len(workdir) + 1:]
        candidates |= {rel, "./" + rel}
    return bool(candidates & {c.rstrip("/") for c in created})


def test_there_are_named_volumes_in_app_services_to_check(compose):
    """Guard: without this, the test below passes over an empty set and reports
    a safety it never established."""
    volumes = named_volumes(compose)
    pairs = [
        (name, mp)
        for name, service in image_built_services(compose).items()
        for mp in mount_points(service, volumes)
    ]
    assert pairs, (
        "no service built from this repository's image mounts a named volume; "
        "the check below would assert nothing"
    )


def test_every_named_volume_mount_point_exists_in_the_image(compose, dockerfile_text):
    created = paths_created_by_image(dockerfile_text)
    volumes = named_volumes(compose)

    for name, service in image_built_services(compose).items():
        user = str(service.get("user") or "")
        for source, target in mount_points(service, volumes):
            assert _resolves_to(target, created), (
                f"{name} mounts the named volume {source!r} at {target!r}, which "
                f"Dockerfile.prod never creates. Docker will initialise the empty "
                f"volume with no owner to copy, leaving it root-owned, and "
                f"{name} runs as {user or 'the image default'} — the first write "
                f"to it fails with EACCES. Create the directory in the image."
            )


def test_the_services_really_do_run_as_a_non_root_user(compose):
    """The rule has force only because these containers are not root. If that
    changed, this file's reasoning would need revisiting rather than quietly
    continuing to pass."""
    users = {
        name: str(service.get("user") or "")
        for name, service in image_built_services(compose).items()
    }
    assert users, "no service is built from this repository's image"
    non_root = {n: u for n, u in users.items() if u and not u.startswith("0")}
    assert non_root, (
        f"no image-built service declares a non-root user: {users}. Either the "
        f"deployment now runs as root — which is a bigger question than this "
        f"test — or the `user:` key moved and this check no longer sees it."
    )
