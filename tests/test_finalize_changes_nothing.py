"""`--finalize` reconciles the deployment ledger and touches nothing else.

#160. A run that built, migrated, recreated and verified was interrupted before
it wrote its manifest, leaving production correct and unrecorded. `--finalize`
writes the missing record -- and the one thing it must never do is any part of
the deployment again, under the name of a bookkeeping fix.

Read from the script rather than executed: the behavioural half runs under
tests/test_deploy_production.sh with a stubbed docker, and this states the
property that no stub can demonstrate -- that the branch contains no mutation at
all, whatever a daemon would have answered.

The first version of this check extracted the wrong range. It ran from the
finalize branch to the closing `fi` of the whole conditional, so it read the
`else` -- the deployment itself -- and reported four forbidden operations that
were exactly where they belong.
"""
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "deploy_production.sh")

MUTATIONS = ("up -d", "run --rm", " build ", "git checkout", "alembic upgrade",
             "docker rm", "docker stop")


def _finalize_branch():
    source = open(SCRIPT).read()
    start = source.index('if [ "$MODE" = finalize ]; then\n    # Nothing below this point')
    end = source.index("\nelse\nPHASE=mutating", start)
    return source[start:end]


def _executable_lines(block):
    return [
        line for line in block.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_the_branch_is_found_at_all():
    """A rename upstream would make every assertion below vacuous."""
    block = _finalize_branch()

    assert len(_executable_lines(block)) > 10, (
        "the finalize branch is suspiciously short; the markers this test slices "
        "on have probably moved"
    )


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_the_finalize_branch_performs_no_deployment(mutation):
    offending = [
        line.strip() for line in _executable_lines(_finalize_branch())
        if mutation in line
    ]

    assert offending == [], (
        f"finalize would run {mutation!r}: {offending}. It records a deployment "
        f"that already happened; repeating any part of it is a second "
        f"deployment wearing the name of a fix."
    )


def test_the_deployment_branch_still_does_all_of_it():
    """The converse, so the test above cannot be satisfied by a script that
    stopped deploying altogether."""
    source = open(SCRIPT).read()
    start = source.index("\nelse\nPHASE=mutating")
    end = source.index("fi   # end of the mutating section")
    deployment = source[start:end]

    for required in ("build backend", "run --rm --no-deps migrate",
                     "up -d --no-build --remove-orphans"):
        assert required in deployment, required


def test_the_journal_is_cleared_after_the_manifest_is_published():
    """The ordering that made the interruption survivable.

    `journal_clear` ran before the manifest was written, so a run interrupted
    between the two left neither: no record of what is deployed, and no sign
    that anything was unfinished. Behaviourally invisible -- an uninterrupted
    run ends the same either way -- so it is asserted on the source.
    """
    lines = open(SCRIPT).read().splitlines()

    clear_at = [i for i, l in enumerate(lines) if l.strip() == "journal_clear"]
    publish_at = [i for i, l in enumerate(lines) if "mv -Tf" in l and "LATEST" in l]

    assert clear_at and publish_at
    assert max(clear_at) > publish_at[0], (
        "the journal is removed before the manifest is published; an "
        "interruption between them leaves neither"
    )


def test_the_manifest_is_synced_before_and_after_the_rename():
    """A rename makes the name durable, not the bytes behind it."""
    source = open(SCRIPT).read()

    assert re.search(r'sync "\$TMP_MANIFEST"', source), (
        "the manifest's contents are never synced, so a crash can leave an "
        "empty file that reads as a deployment which recorded nothing"
    )
    assert source.count('sync "$MANIFEST_DIR"') >= 2, (
        "the directory is synced fewer than twice: once for the rename and "
        "once for the journal's removal, which is itself a directory entry"
    )
