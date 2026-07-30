"""The scanner's own workflow must not become the way secrets leave.

A job that reads a pull request's own configuration is running untrusted input.
That is fine while it has nothing worth taking; these assertions are what keeps
it that way, because the dangerous version of this file looks almost identical
to the safe one.
"""
import pathlib
import re

import pytest
import yaml

WORKFLOW = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/secret-scan.yml"


@pytest.fixture(scope="module")
def raw():
    return WORKFLOW.read_text()


@pytest.fixture(scope="module")
def parsed(raw):
    return yaml.safe_load(raw)


def test_it_never_uses_pull_request_target(raw):
    """pull_request_target runs with the base repository's privileges and its
    secrets, against code the pull request author wrote."""
    assert "pull_request_target" not in raw


def test_it_requests_no_write_permission(parsed):
    assert parsed.get("permissions") == {"contents": "read"}


def test_it_references_no_repository_secret(raw):
    """There is nothing for this job to authenticate to, so a secret reference
    could only be an accident -- and an accident with a scanner's output next
    to it."""
    assert "secrets." not in raw


def test_the_checkout_does_not_keep_the_token(raw):
    assert "persist-credentials: false" in raw


def test_the_scanner_image_is_pinned_by_digest(raw):
    """A tag can be moved. The meaning of a green check should not change
    without a commit."""
    references = len(re.findall(r"zricethezav/gitleaks", raw))
    digests = len(re.findall(r"gitleaks@sha256:[0-9a-f]{64}", raw))
    assert references == digests, f"{references - digests} reference(s) not pinned"


def test_redaction_is_enabled_on_every_invocation(raw):
    assert raw.count("--redact") == len(re.findall(r"gitleaks@sha256:", raw))


def test_the_report_is_never_published(raw):
    """Uploading the findings moves the secret rather than containing it."""
    assert "upload-artifact" not in raw
    assert "actions/upload" not in raw


def test_the_history_job_fetches_the_history(raw):
    """Without full depth it would scan one commit and pass."""
    assert "fetch-depth: 0" in raw


def test_a_finding_fails_the_job(parsed):
    """continue-on-error would turn the gate into a notification."""
    for job in parsed["jobs"].values():
        for step in job.get("steps", []):
            assert step.get("continue-on-error") is not True
