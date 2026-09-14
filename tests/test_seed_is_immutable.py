"""The seed is read-only, and that is enforced rather than agreed (#191).

`models/` is the seed: the bootstrap champion that ships in Git and is never
written at runtime. `app/paths.py` says so and `app/model_source.py` honours it,
but until this file the invariant rested on everyone continuing to honour it.

It did not hold. `app/training.py` created `models/` and wrote six pickles into
it, and nothing failed — the function had no callers, so the write never ran and
no test could notice it was there. That is the whole shape of #191: the
directory is both a Git working tree and a container mount, so a single write
reaching it makes the next deployment refuse to start.

Two independent guards, because they fail in different places:

* the AST scan below refuses a **write in the source**, which is where the bug
  is introduced;
* the compose check refuses a **writable mount**, which is what would let such a
  write reach the host checkout even if the scan were fooled.

Each has a negative control. A guard that reports absence is worth nothing until
it has been shown to fire on a present fault — `test_the_scan_fires_on_a_planted_write`
and `test_the_mount_check_fires_without_ro` are that demonstration, and they run
the same functions the real checks run.

What this does not cover: a write reaching the seed through a path this file
cannot resolve statically — a name imported from another module, a path read
from configuration, or `subprocess`. The compose guard is the backstop for those.
"""
import ast
import os
import textwrap

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "app")
COMPOSE = os.path.join(REPO_ROOT, "docker-compose.prod.yml")

#: Functions that resolve the seed root. `staged_dir` and `active_dir` are
#: deliberately absent: they resolve the runtime volume, which exists to be
#: written to.
SEED_CALLS = frozenset({"models_dir", "seed_dir"})

#: A literal that names the seed directly, however the process reached it.
SEED_LITERALS = ("models", "models/", "/app/models", "/app/models/")

#: `name -> index of the argument that is the destination`. Everything else in
#: WRITE_CALLS is flagged when *any* argument is seed-rooted; these four take a
#: source as well, and reading the seed to copy it elsewhere is not a write.
DESTINATION_ARG = {
    "copy": 1, "copy2": 1, "copyfile": 1, "copytree": 1, "move": 1,
    "replace": 1, "rename": 1,
}

WRITE_CALLS = frozenset({
    "dump", "save", "savez", "to_csv", "to_json", "write_text", "write_bytes",
    "makedirs", "mkdir", "remove", "unlink", "rmtree", "rmdir",
    *DESTINATION_ARG,
})

WRITING_MODES = frozenset("wax+")


def _literal(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


class SeedWriteScan(ast.NodeVisitor):
    """Collects writes whose destination resolves to the seed directory.

    Two passes in one: assignments bind local names to seed-rooted expressions
    (`MODEL_DIR = os.path.join(ROOT_DIR, "models")`), and calls are then checked
    against those names as well as against the resolver functions directly.
    """

    def __init__(self, filename):
        self.filename = filename
        self.seed_names = set()
        self.findings = []

    # -- resolution -------------------------------------------------------

    def _is_seed(self, node):
        if node is None:
            return False
        text = _literal(node)
        if text is not None:
            return text in SEED_LITERALS or text.startswith(("models/", "/app/models/"))
        if isinstance(node, ast.Name):
            return node.id in self.seed_names
        if isinstance(node, ast.Call):
            name = self._callee(node)
            if name in SEED_CALLS:
                return True
            if name == "join":
                return any(self._is_seed(arg) for arg in node.args)
            return False
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self._is_seed(node.left) or self._is_seed(node.right)
        if isinstance(node, ast.JoinedStr):
            return any(self._is_seed(v) for v in node.values)
        if isinstance(node, ast.FormattedValue):
            return self._is_seed(node.value)
        return False

    @staticmethod
    def _callee(node):
        func = node.func
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
        return None

    # -- collection -------------------------------------------------------

    def visit_Assign(self, node):
        if self._is_seed(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.seed_names.add(target.id)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if isinstance(node.target, ast.Name) and self._is_seed(node.value):
            self.seed_names.add(node.target.id)
        self.generic_visit(node)

    def visit_Call(self, node):
        name = self._callee(node)
        if name == "open":
            self._check_open(node)
        elif name in WRITE_CALLS:
            self._check_write(node, name)
        self.generic_visit(node)

    # -- decisions --------------------------------------------------------

    def _check_open(self, node):
        if not node.args or not self._is_seed(node.args[0]):
            return
        mode = node.args[1] if len(node.args) > 1 else None
        for keyword in node.keywords:
            if keyword.arg == "mode":
                mode = keyword.value
        text = _literal(mode) if mode is not None else "r"
        if text is None:
            # A mode this file cannot read is not evidence of a read.
            self._record(node, "open() on the seed with a mode that is not a literal")
        elif set(text) & WRITING_MODES:
            self._record(node, "open(..., %r) on the seed" % text)

    def _check_write(self, node, name):
        index = DESTINATION_ARG.get(name)
        if index is not None:
            targets = node.args[index:index + 1]
            targets += [k.value for k in node.keywords if k.arg in ("dst", "target")]
        else:
            targets = list(node.args) + [k.value for k in node.keywords]
        if any(self._is_seed(target) for target in targets):
            self._record(node, "%s() writes into the seed" % name)

    def _record(self, node, why):
        self.findings.append("%s:%d %s" % (self.filename, node.lineno, why))


def seed_writes(source, filename):
    """Every write into the seed that `source` declares. The subject of both
    the real scan and its negative control, so neither can drift from the other."""
    scan = SeedWriteScan(filename)
    scan.visit(ast.parse(source))
    return scan.findings


def _app_modules():
    for base, _dirs, files in os.walk(APP_DIR):
        for name in sorted(files):
            if name.endswith(".py"):
                path = os.path.join(base, name)
                yield os.path.relpath(path, REPO_ROOT), path


# ---------------------------------------------------------------- the source


def test_no_module_under_app_writes_into_the_seed():
    scanned, findings = 0, []
    for relative, path in _app_modules():
        with open(path, encoding="utf-8") as handle:
            findings += seed_writes(handle.read(), relative)
        scanned += 1
    assert scanned > 50, "scanned only %d modules — the walk found nothing to check" % scanned
    assert not findings, (
        "the seed is written by application code:\n  "
        + "\n  ".join(findings)
        + "\n\nRetraining writes to runtime/staged/<run_id>/ via staged_dir(); "
          "models/ is the immutable bootstrap and only a commit may change it."
    )


def test_the_scan_fires_on_a_planted_write():
    """The negative control. Without this the scan above proves nothing."""
    planted = textwrap.dedent(
        """
        import os, pickle
        from app.paths import models_dir

        MODEL_DIR = os.path.join(models_dir(), "sub")

        def ship_it(model):
            os.makedirs(MODEL_DIR, exist_ok=True)
            with open(os.path.join(MODEL_DIR, "model.pkl"), "wb") as handle:
                pickle.dump(model, handle)
        """
    )
    findings = seed_writes(planted, "planted.py")
    assert len(findings) == 2, "expected the makedirs and the open, got: %r" % findings
    assert any("makedirs" in f for f in findings)
    assert any("open(" in f for f in findings)


def test_the_scan_stays_quiet_on_a_read():
    """The other half of the control: it must discriminate, not just fire."""
    benign = textwrap.dedent(
        """
        import os, pickle
        from app.paths import models_dir, staged_dir

        def load():
            with open(os.path.join(models_dir(), "model.pkl"), "rb") as handle:
                return pickle.load(handle)

        def stage(run_id, model):
            with open(os.path.join(staged_dir(run_id), "model.pkl"), "wb") as handle:
                pickle.dump(model, handle)
        """
    )
    assert seed_writes(benign, "benign.py") == []


# ----------------------------------------------------------------- the mount

yaml = pytest.importorskip("yaml")

SERVICES = ("backend", "scheduler")
MOUNT_POINT = "/app/models"


def writable_seed_mounts(compose):
    """`service -> mount` for every mount landing on the seed without `:ro`.

    Both compose spellings are accepted, because either is valid and this check
    must not dictate which one the file uses.
    """
    offenders = []
    for service, definition in (compose.get("services") or {}).items():
        for entry in definition.get("volumes") or []:
            if isinstance(entry, str):
                parts = entry.split(":")
                if len(parts) >= 2 and parts[1] == MOUNT_POINT and "ro" not in parts[2:]:
                    offenders.append((service, entry))
            elif isinstance(entry, dict) and entry.get("target") == MOUNT_POINT:
                if not entry.get("read_only"):
                    offenders.append((service, entry))
    return offenders


@pytest.fixture(scope="module")
def compose():
    with open(COMPOSE, encoding="utf-8") as handle:
        parsed = yaml.safe_load(handle)
    assert parsed and "services" in parsed, "docker-compose.prod.yml declares no services"
    return parsed


@pytest.mark.parametrize("service", SERVICES)
def test_each_service_mounts_the_seed(compose, service):
    """Guarding the mount is worthless if the mount stopped existing."""
    mounted = [
        entry for entry in compose["services"][service].get("volumes") or []
        if (isinstance(entry, str) and entry.split(":")[1:2] == [MOUNT_POINT])
        or (isinstance(entry, dict) and entry.get("target") == MOUNT_POINT)
    ]
    assert mounted, "%s no longer mounts anything at %s" % (service, MOUNT_POINT)


def test_both_services_mount_the_seed_read_only(compose):
    offenders = writable_seed_mounts(compose)
    assert not offenders, (
        "the seed is mounted writable:\n  "
        + "\n  ".join("%s: %r" % pair for pair in offenders)
        + "\n\nA container that can write the checkout can dirty Git, and "
          "scripts/deploy_production.sh then refuses the next deployment."
    )


def test_the_mount_check_fires_without_ro():
    """The negative control for the mount guard."""
    writable = {
        "services": {
            "backend": {"volumes": ["./models:/app/models", "runtime_models:/app/runtime"]},
            "scheduler": {"volumes": [{"type": "bind", "source": "./models", "target": "/app/models"}]},
        }
    }
    offenders = writable_seed_mounts(writable)
    assert {service for service, _ in offenders} == {"backend", "scheduler"}

    read_only = {
        "services": {
            "backend": {"volumes": ["./models:/app/models:ro"]},
            "scheduler": {"volumes": [
                {"type": "bind", "source": "./models", "target": "/app/models", "read_only": True}
            ]},
        }
    }
    assert writable_seed_mounts(read_only) == []
