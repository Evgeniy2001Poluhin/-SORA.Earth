"""The production image must not ship a frontend that invents its numbers.

`web/src/api/mock.ts` decides at build time:

    export const isMock =
        import.meta.env.VITE_USE_MOCK === "1" || !import.meta.env.VITE_API_BASE;

Vite substitutes an unset variable with `undefined`, so an image built without
`VITE_API_BASE` ships `isMock === true`, and eight endpoint modules — evaluate,
explain, calibration, driftBaseline, history, mlops, admin, report — answer from
canned data instead of the API.

Observed on production 2026-09-03, before the fix: the SHAP panel drew a beige
rectangle captioned "SHAP Waterfall (mock)"; an ESG score of 79.5 and a success
probability of 87.8% appeared without the model being asked; and the drift page
showed "OBSERVATIONS 0 / NO BASELINE" beside "BASELINE 734 samples / 7 feats"
and 100% drift on four features — the real API and the mock answering in the
same view.

Nothing failed. That is the whole difficulty: a mock is a working page. It
returns 200, renders, and looks convincing, so every check that asks whether the
site is up says yes.

## Why the variable name is read rather than written down

The name is extracted from `mock.ts`. Hardcoding it here would leave the test
green after a rename — while the deployment went back to shipping mocks, which
is exactly the failure. The test follows the definition.

## What this checks, and what it does not

Like `tests/test_image_contents.py`, it reads the Dockerfile: CI does not build
`Dockerfile.prod`, whose runtime stage installs torch. So it can see the
variable set before the build and cannot prove what Vite did with it. The build
itself carries the second half — a `RUN` step greps the emitted bundle for a
mock payload and fails the image — and that is the check that cannot be fooled
by reasoning about the Dockerfile.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "Dockerfile.prod"
MOCK_TS = REPO / "web" / "src" / "api" / "mock.ts"


@pytest.fixture(scope="module")
def mock_flag_source():
    if not MOCK_TS.exists():
        pytest.skip(f"{MOCK_TS.relative_to(REPO)} is gone; this file's premise with it")
    return MOCK_TS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dockerfile():
    return DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def is_mock_expression(mock_flag_source):
    """The right-hand side of `export const isMock = ...`."""
    match = re.search(r"export\s+const\s+isMock\s*=\s*([^;]+);", mock_flag_source)
    if not match:
        pytest.skip("isMock is no longer defined in mock.ts")
    return match.group(1)


def _instructions_only(text):
    """The Dockerfile without its comments.

    Everything here splits on instruction text, and a comment is not an
    instruction. The first version of this file did not strip them, and the
    explanation written above the ARG -- which mentions "the step after
    `npm run build`" -- became the first match, so the parse cut the stage in
    half and the test failed on a Dockerfile that was entirely correct.

    The same mistake, in the same session, as a frontend test that found the
    word `noWrap` in a comment saying why `noWrap` must not be used.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#"))


def spa_stage(dockerfile):
    """The build stage that compiles the frontend, found by its FROM line."""
    stages = re.split(r"^FROM ", _instructions_only(dockerfile), flags=re.M)
    for stage in stages:
        if "npm run build" in stage:
            return stage
    return None


def test_the_frontend_is_built_in_the_image(dockerfile):
    """Guard for everything below: if the SPA stage were gone, the assertions
    would have nothing to inspect and would pass over an absence."""
    assert spa_stage(dockerfile) is not None, (
        "no build stage in Dockerfile.prod runs `npm run build`; either the "
        "frontend is no longer built here — in which case this file needs "
        "rewriting rather than deleting — or the build command changed name"
    )


def test_the_variable_that_disables_mocks_is_set_before_the_build(is_mock_expression, dockerfile):
    """Read the required variable out of the expression that uses it.

    `!import.meta.env.X` means an unset X turns mocks **on**, so X must be set.
    """
    negated = re.findall(r"!\s*import\.meta\.env\.([A-Z0-9_]+)", is_mock_expression)
    assert negated, (
        f"no negated env variable in the isMock expression ({is_mock_expression!r}); "
        "the mock switch works differently now and this test must be rewritten"
    )

    stage = spa_stage(dockerfile)
    before_build = stage.split("npm run build")[0]

    for name in negated:
        assert re.search(rf"^\s*(ENV|ARG)\s+{name}\s*=", before_build, re.M), (
            f"Dockerfile.prod does not set {name} before `npm run build`. Vite "
            f"substitutes it with `undefined`, `isMock` becomes true, and the "
            f"image ships a frontend that answers from canned data — which looks "
            f"like a working site and returns 200 to every health check."
        )


def test_the_explicit_mock_switch_is_not_turned_on(is_mock_expression, dockerfile):
    """The other half of the expression: `X === "1"` forces mocks on."""
    forced = re.findall(
        r"import\.meta\.env\.([A-Z0-9_]+)\s*===\s*[\"']1[\"']", is_mock_expression)
    stage = spa_stage(dockerfile)
    for name in forced:
        assert not re.search(rf"^\s*(ENV|ARG)\s+{name}\s*=\s*[\"']?1[\"']?\s*$", stage, re.M), (
            f"Dockerfile.prod sets {name}=1, which turns the mock frontend on "
            f"deliberately")


def test_the_build_refuses_to_emit_a_mock_payload(dockerfile):
    """The Dockerfile must check its own output, not just declare intent.

    Setting an ENV is a statement about what should happen. Grepping the emitted
    bundle is the observation, and it is the half that survives a rename, a
    changed vite config, or a variable that reaches the shell but not the build.
    """
    stage = spa_stage(dockerfile)
    after_build = stage.split("npm run build", 1)[1]

    assert "static/spa" in after_build and "grep" in after_build, (
        "nothing inspects the emitted bundle after `npm run build`. Without it "
        "this file's other assertions are about the Dockerfile's wording rather "
        "than about what the image contains."
    )
    assert "exit 1" in after_build, (
        "the post-build inspection does not fail the build; a warning in a "
        "build log is not a gate"
    )
    # Restricted to executable output, and this is not a detail.
    #
    # `vite.config.ts` sets `sourcemap: true`, and a source map carries the
    # original text of the branch Vite removed. Measured on a correct build with
    # VITE_API_BASE set: the mock string appears in one `.js.map` and in no
    # `.js`. An unrestricted grep therefore rejects a build that is entirely
    # right — the first version of this check did exactly that.
    # More than one marker. A grep for a single string checks one example: the
    # first version looked only for the SHAP placeholder and would have passed a
    # bundle still carrying the fabricated PDF report, which reads
    # "Score: 72.3 / 100" over a real timestamp and is handed to the user as a
    # file that outlives the page it came from.
    markers = re.findall(r"'\(mock\)'|'mockEvaluate'", after_build)
    assert len(set(markers)) >= 2, (
        f"the post-build guard looks for {sorted(set(markers)) or 'nothing'}. "
        "One marker is a check against one example; the placeholders follow a "
        "convention and the generator has a name, and both are cheap to look for."
    )
    assert "--include='*.js'" in after_build or '--include="*.js"' in after_build, (
        "the post-build grep is not restricted to *.js. Source maps contain the "
        "removed branch verbatim, so it will fail correct builds — and a check "
        "that fails when nothing is wrong gets deleted rather than fixed."
    )


def test_the_api_base_is_not_load_bearing_for_routing(is_mock_expression):
    """A note kept executable rather than left in a comment.

    `web/src/api/client.ts` hardcodes the API path, so the variable set in the
    Dockerfile is a switch and not a destination. If that ever changes, the
    value in Dockerfile.prod stops being arbitrary and this test should be the
    thing that says so.
    """
    client = REPO / "web" / "src" / "api" / "client.ts"
    if not client.exists():
        pytest.skip("client.ts moved; the claim below needs relocating")

    body = client.read_text(encoding="utf-8")
    assert re.search(r'const\s+BASE\s*=\s*["\']/api/v1["\']', body), (
        "client.ts no longer hardcodes /api/v1. The value of the build-time "
        "variable may now affect where requests go, so Dockerfile.prod's "
        "setting has to be chosen deliberately rather than for symmetry."
    )
