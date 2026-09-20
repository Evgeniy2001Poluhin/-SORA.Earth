"""What the documents say about promotion, checked against what promotes.

`docs/DEVELOPMENT_ROADMAP.md` defines the project as ready when a specialist can
follow an output back along the whole chain -- and `promotion gate → active
champion → audit trail` are three of its links. A document that describes those
links wrongly is not a cosmetic problem: it is the map of the thing the
definition of done is about.

Three claims had gone stale in the same direction, saying a path was broken
after it was fixed:

    CLAUDE.md              POST /mlops/auto-retrain "applies only the last" of
                           the three refusals -- it calls gated_decision, which
                           applies all three and a fourth
    CLAUDE.md              "and never activates" -- it calls
                           activate_promoted_candidate
    app/model_source.py    under a heading reading "Still missing", the same two
                           claims plus one about the deployment manifest, which
                           records `active_model` and has for some time

Pessimistic staleness is the safer direction and still costs: someone fixes what
is fixed, or avoids an endpoint that works.

These tests bind the prose to the code in **both** directions. Naming the call
is the positive half -- a document that describes the path must name what the
path does -- and the negative half forbids the specific stale sentences. The
negative half ignores blockquoted lines, because CLAUDE.md teaches by quoting
what it used to say, and a check that forbade that would forbid the correction
along with the error.
"""
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def _without_quotations(text):
    """Markdown blockquotes removed, then whitespace collapsed.

    CLAUDE.md records its own past errors verbatim -- that is most of what makes
    it useful -- so a correction legitimately contains the sentence it corrects.
    Only unquoted prose is a claim the document is making now.

    The collapse is not cosmetic. Both files are hard-wrapped, and the first
    version of this module matched raw text: `not the \`run_id\`` falls across a
    line break in app/model_source.py, so the check found nothing and the test
    passed while the claim it looked for was sitting in the file. Quotations are
    stripped first, because that needs the line structure the collapse destroys.
    """
    unquoted = "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith(">")
    )
    return re.sub(r"\s+", " ", unquoted)


@pytest.fixture(scope="module")
def auto_retrain_activates():
    return "activate_promoted_candidate(" in _read("app", "api", "infra.py")


@pytest.fixture(scope="module")
def auto_retrain_uses_the_full_gate():
    return "gated_decision(" in _read("app", "api", "infra.py")


@pytest.mark.parametrize("path", [
    ("CLAUDE.md",),
    ("app", "model_source.py"),
])
def test_no_document_says_the_gated_path_skips_activation(
        path, auto_retrain_activates):
    """`POST /mlops/auto-retrain` activates, and two documents said it did not.

    The claim mattered: a candidate that is approved and never activated stays
    in `staged/` and serves nothing, which is a silent no-op. It was true once.
    """
    prose = _without_quotations(_read(*path))
    stale = [
        phrase for phrase in ("never activates", "never calls `activate()`")
        if phrase in prose
    ]

    if auto_retrain_activates:
        assert not stale, (
            f"{'/'.join(path)} still says the gated path {stale}, while "
            f"app/api/infra.py calls activate_promoted_candidate(). Someone "
            f"reading this either fixes what is fixed or avoids an endpoint "
            f"that works."
        )
    else:
        assert stale, (
            f"app/api/infra.py no longer calls activate_promoted_candidate(), "
            f"so {'/'.join(path)} should be saying the candidate is approved "
            f"and never activated -- a silent no-op nothing else would report"
        )


def test_claude_md_does_not_say_the_gated_path_applies_a_weaker_gate(
        auto_retrain_uses_the_full_gate):
    """`gated_decision` is the full gate plus one refusal of its own.

    It refuses when the serving baseline cannot be established (#329) and
    otherwise defers to `evaluate_promotion`, which applies the AUC lower bound,
    the registry check and non-degradation. "Only the last" describes the
    behaviour before that.
    """
    prose = _without_quotations(_read("CLAUDE.md"))

    if auto_retrain_uses_the_full_gate:
        assert "applies only the last" not in prose, (
            "CLAUDE.md says POST /mlops/auto-retrain applies only the "
            "non-degradation refusal, while app/api/infra.py calls "
            "gated_decision -- the baseline refusal plus all three of "
            "evaluate_promotion's"
        )


def test_the_manifest_claim_matches_what_the_deploy_script_writes():
    """The last link of the chain: which champion a deployment left serving.

    `app/model_source.py` listed this under "Still missing". The script writes
    `active_model` into every manifest, so the record exists -- and it is what
    ties a deployment to the model it left running, which the definition of done
    in docs/DEVELOPMENT_ROADMAP.md requires.
    """
    script = _read("scripts", "deploy_production.sh")
    records_active_model = re.search(r"active_model\s+\$", script) is not None

    prose = _without_quotations(_read("app", "model_source.py"))
    claims_absent = "not the `run_id`" in prose

    if records_active_model:
        assert not claims_absent, (
            "app/model_source.py says the deployment manifest does not record "
            "the active model, while scripts/deploy_production.sh writes an "
            "`active_model` line into every one"
        )
    else:
        assert claims_absent, (
            "scripts/deploy_production.sh no longer writes active_model, so "
            "nothing ties a deployment to the champion it left serving and "
            "app/model_source.py should say so"
        )


def test_still_missing_lists_only_things_that_are_missing():
    """The heading is a promise about its own contents.

    Both bullets under it had been done. A list of outstanding work that
    contains finished work is worse than no list: it is read as current, and
    each entry costs someone the time to discover it is not.
    """
    source = _read("app", "model_source.py")
    if "Still missing:" not in source:
        pytest.skip("the section was removed; nothing claims to be outstanding")

    section = re.sub(
        r"\s+", " ", source.split("Still missing:", 1)[1].split('"""', 1)[0])

    assert "never calls `activate()`" not in section, (
        "the Still missing list names activation on the gated path, which "
        "app/api/infra.py performs"
    )
    assert "not the `run_id`" not in section, (
        "the Still missing list names the manifest's missing active model, "
        "which scripts/deploy_production.sh writes"
    )
