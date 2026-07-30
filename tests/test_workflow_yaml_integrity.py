"""Workflow files must be valid to GitHub Actions, not merely to a YAML parser.

These are different bars, and the gap between them cost two integration
attempts. A duplicate mapping key parses cleanly under PyYAML -- it keeps the
last value and says nothing -- while GitHub rejects the workflow outright and
runs no jobs at all. The failure surfaces as "this run likely failed because of
a workflow file issue", with no indication of which key or which line.

Both times it arrived the same way: resolving a conflict where two branches each
appended a job, by union. The union is right for added lines and wrong for a
repeated key, and nothing in the ordinary toolchain distinguishes the two.
"""
import pathlib

import pytest
import yaml

WORKFLOWS = sorted((pathlib.Path(__file__).resolve().parents[1] / ".github/workflows").glob("*.yml"))


class DuplicateKeyLoader(yaml.SafeLoader):
    """A loader that refuses what PyYAML accepts silently."""


def _no_duplicates(loader, node, deep=False):
    seen, out = {}, {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None,
                f"duplicate key {key!r} at line {key_node.start_mark.line + 1} "
                f"(first seen at line {seen[key] + 1})",
                key_node.start_mark,
            )
        seen[key] = key_node.start_mark.line
        out[key] = loader.construct_object(value_node, deep=deep)
    return out


DuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates
)


def test_there_are_workflows_to_check():
    """Guard against the glob silently matching nothing."""
    assert WORKFLOWS, "no workflow files found"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_duplicate_keys_anywhere(path):
    try:
        yaml.load(path.read_text(), Loader=DuplicateKeyLoader)
    except yaml.constructor.ConstructorError as exc:
        pytest.fail(f"{path.name}: {exc.problem}")


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_job_declares_runs_on_and_steps(path):
    """A job missing either is rejected by Actions and accepted by the parser."""
    doc = yaml.safe_load(path.read_text())
    for name, job in (doc.get("jobs") or {}).items():
        if "uses" in job:
            continue  # a reusable-workflow call has neither
        assert job.get("runs-on"), f"{path.name}: job {name} has no runs-on"
        assert job.get("steps"), f"{path.name}: job {name} has no steps"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_step_has_exactly_one_of_uses_or_run(path):
    doc = yaml.safe_load(path.read_text())
    for name, job in (doc.get("jobs") or {}).items():
        for i, step in enumerate(job.get("steps") or []):
            has_uses, has_run = "uses" in step, "run" in step
            label = step.get("name") or step.get("uses") or f"step {i}"
            assert has_uses != has_run, \
                f"{path.name}: job {name}, {label} has {'both' if has_uses else 'neither'} uses and run"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_needs_names_a_job_that_exists(path):
    """A typo here makes the job never run, silently."""
    doc = yaml.safe_load(path.read_text())
    jobs = doc.get("jobs") or {}
    for name, job in jobs.items():
        needs = job.get("needs")
        if not needs:
            continue
        for dependency in ([needs] if isinstance(needs, str) else needs):
            assert dependency in jobs, \
                f"{path.name}: job {name} needs {dependency!r}, which does not exist"
