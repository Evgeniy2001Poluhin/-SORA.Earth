"""Private local stand paths, not a sandbox against a hostile same-user process.

No Docker calls here. Only an explicitly initialized directory below
~/.sora-gstand may receive generated files. Existing outputs are never deleted.
"""
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys


def fail(message):
    raise ValueError(message)


def private_entry(path, *, directory=False):
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        fail("stand entry must be owned, non-symlink, and not group/world writable")
    if not directory and info.st_nlink != 1:
        fail("stand file must not be hardlinked")


def root(create=False):
    home = Path.home().resolve(strict=True)
    base = home / ".sora-gstand"
    work = Path(os.environ.get("GSTAND_WORK", str(base / "isolated")))
    if not work.is_absolute() or ".." in work.parts or base not in work.parents:
        fail("GSTAND_WORK must be a dedicated child of ~/.sora-gstand")
    current = base
    for part in work.relative_to(base).parts:
        if create and not current.exists() and not current.is_symlink():
            current.mkdir(mode=0o700)
        if current.exists() or current.is_symlink():
            private_entry(current, directory=True)
        else:
            fail("run stand.sh init first")
        current = current / part
    if create and not work.exists() and not work.is_symlink():
        work.mkdir(mode=0o700)
    if work.exists() or work.is_symlink():
        private_entry(work, directory=True)
    else:
        fail("run stand.sh init first")
    return work


def write_new(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def session(initialize=False):
    work = root(create=initialize)
    marker = work / "owner.json"
    if initialize and not marker.exists() and not marker.is_symlink():
        if any(work.iterdir()):
            fail("refusing to adopt a non-empty unrecognized stand directory")
        write_new(marker, json.dumps({"version": 1, "run_id": secrets.token_hex(8)}) + "\n")
    private_entry(marker)
    with marker.open() as stream:
        doc = json.loads(stream.read(1024))
    if not isinstance(doc, dict) or set(doc) != {"version", "run_id"} or doc["version"] != 1:
        fail("invalid stand marker")
    if not isinstance(doc["run_id"], str) or not re.fullmatch(r"[0-9a-f]{16}", doc["run_id"]):
        fail("invalid stand run id")
    return work, doc["run_id"]


def child_directory(work, name):
    path = work / name
    if not path.exists() and not path.is_symlink():
        path.mkdir(mode=0o700)
    private_entry(path, directory=True)
    return path


def new_output(work, name):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name):
        fail("output must be a simple stand-local name")
    path = child_directory(work, "outputs") / name
    # mkdir without exist_ok refuses existing directories and symlinks alike.
    path.mkdir(mode=0o700)
    return path


def provisioning(work, argument):
    repo = Path(__file__).resolve().parents[2]
    path = Path(argument)
    canonical = repo / "grafana" / "provisioning"
    outputs = work / "outputs"
    if not path.is_absolute() or ".." in path.parts:
        fail("provisioning must be an absolute stand output or repository provisioning")
    if path != canonical and outputs not in path.parents:
        fail("foreign provisioning directory refused")
    anchor = repo if path == canonical else work
    current = anchor
    private_entry(current, directory=True)
    for part in path.relative_to(anchor).parts:
        current = current / part
        private_entry(current, directory=True)
    for entry in path.rglob("*"):
        if entry.is_symlink():
            fail("symlink in provisioning refused")
    for name in ("alerting", "datasources", "dashboards"):
        private_entry(path / name, directory=True)
    return path


def password(work, *, create=False):
    path = work / "admin-password"
    if create and not path.exists() and not path.is_symlink():
        write_new(path, secrets.token_hex(32) + "\n")
    private_entry(path)
    value = path.read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        fail("invalid disposable stand password")
    return value


def targets(work, run_id, mode):
    if mode not in {"vanish", "restore"}:
        fail("targets expects vanish or restore")
    directory = child_directory(work, "targets")
    target = directory / "sora-app.json"
    if target.exists() or target.is_symlink():
        private_entry(target)
    rows = [] if mode == "vanish" else [{"targets": [f"sora-gstand-{run_id}-metrics:8000"]}]
    temp = directory / (".targets-" + secrets.token_hex(8))
    write_new(temp, json.dumps(rows) + "\n")
    os.replace(temp, target)


def main():
    command, *args = sys.argv[1:]
    work, run_id = session(initialize=command == "init")
    if command in {"init", "session"} and not args:
        print(run_id)
    elif command == "root" and not args:
        print(work)
    elif command == "new-output" and len(args) == 1:
        print(new_output(work, args[0]))
    elif command == "provisioning" and len(args) == 1:
        print(provisioning(work, args[0]))
    elif command == "password" and not args:
        print(password(work, create=True))
    elif command == "targets" and len(args) == 1:
        targets(work, run_id, args[0])
    else:
        fail("invalid path guard command")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(f"stand refused: {type(error).__name__}: "
              + (str(error) if isinstance(error, ValueError) else "unsafe or unavailable path"),
              file=sys.stderr)
        sys.exit(2)
