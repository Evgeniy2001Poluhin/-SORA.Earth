"""One Node version, stated in four places, all agreeing.

On 2026-09-03 a frontend test passed locally and failed in CI: undici's
`Response` rejects jsdom's `Blob` on Node 20 and accepts it on Node 24. The
machine it was written on had 24; the runner has 20. Nothing in the repository
said which was intended, so nothing could disagree.

`.nvmrc` and `engines` say it now, `engine-strict=true` makes `npm ci` refuse
rather than warn, and this asserts the four statements have not drifted apart --
a pin that disagrees with CI is worse than none, because it is believed.
"""
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "web"


def major(text):
    m = re.search(r"(\d+)", text)
    return m.group(1) if m else None


@pytest.fixture(scope="module")
def declared():
    nvmrc = WEB / ".nvmrc"
    if not nvmrc.exists():
        pytest.fail("web/.nvmrc is gone; the version is unstated again")
    return major(nvmrc.read_text(encoding="utf-8").strip())


def test_the_nvmrc_names_a_major_version(declared):
    assert declared and declared.isdigit(), "web/.nvmrc does not name a version"


def test_package_json_agrees(declared):
    pkg = json.loads((WEB / "package.json").read_text(encoding="utf-8"))
    engines = pkg.get("engines") or {}
    node = engines.get("node")
    assert node, "web/package.json declares no engines.node"
    assert declared in node, (
        f"engines.node is {node!r} but .nvmrc says {declared}; a developer "
        f"following one gets a different runtime from the other"
    )


def test_npm_actually_enforces_it():
    """Without engine-strict, `engines` is a warning and installs proceed."""
    npmrc = WEB / ".npmrc"
    assert npmrc.exists(), "web/.npmrc is gone; engines is advisory again"
    body = npmrc.read_text(encoding="utf-8")
    assert re.search(r"^\s*engine-strict\s*=\s*true", body, re.M), (
        "engine-strict is not true, so npm warns about a wrong Node and "
        "installs anyway -- which is how the mismatch went unnoticed"
    )


def test_ci_uses_the_same_version(declared):
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    versions = set(re.findall(r'node-version:\s*"?(\d+)"?', ci))
    assert versions, "no node-version in ci.yml; this test cannot compare"
    assert versions == {declared}, (
        f"ci.yml uses Node {sorted(versions)} and .nvmrc says {declared}. The "
        f"pin exists to stop exactly this divergence, so it must track it."
    )


def test_the_image_builds_on_the_same_version(declared):
    """The SPA stage compiles the bundle that ships; a different Node there is a
    third answer to the same question."""
    dockerfile = (REPO / "Dockerfile.prod").read_text(encoding="utf-8")
    code = "\n".join(l for l in dockerfile.splitlines()
                     if not l.lstrip().startswith("#"))
    versions = set(re.findall(r"FROM\s+node:(\d+)", code))
    assert versions, "Dockerfile.prod has no node: stage; this test cannot compare"
    assert versions == {declared}, (
        f"Dockerfile.prod builds on Node {sorted(versions)} and .nvmrc says "
        f"{declared}"
    )
