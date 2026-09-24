"""Under `pipefail`, a found match must not read as "not found".

`printf '%s\\n' "$X" | grep -q needle` exits grep at the first match and closes
the pipe. If printf is still writing, it takes SIGPIPE, and under
`set -o pipefail` the pipeline fails -- although the match was found. Measured
2026-09-24 on macOS, bash 3.2, match on the first line, 200 tries per size:

    337 B    0/200 false "not found"
    4 KB     0/200
    16 KB    0/200
    70 KB  199/200
    300 KB 200/200

Above the pipe buffer it is deterministic; below it, a rare race under load --
it failed `tests/test_backup_lock.sh` once in seven full runs on 4.5 KB.

Where it mattered: `scripts/backup_retention.sh` decided with it whether a
backup is in the keep list -- and a false "not found" is followed by
`store_delete_backup`. Its `NEWEST="$(printf ... | head -n 1)"` would, on a
large list, fail the assignment and end the script under `set -e`. The
mutation tools search the output of one pytest node or one shell suite for
"FAIL" -- a few kilobytes, so exposed only to the rare race; a false "not
found" there would report a killed mutant as a survivor.

The fix everywhere is a here-string: `grep -q needle <<<"$X"` has no pipe, so
there is no SIGPIPE to lose the answer to.
"""
import pathlib
import re
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

# 150 KB -- above the pipe buffer, where the old form fails deterministically --
# built with one printf rather than a loop (a bash 3.2 append loop took ~30 s,
# the whole of pytest.ini's per-test budget).
_SHELL = r'''
set -uo pipefail
data="NEEDLE"$'\n'"$(printf '20260924T030000Z-backup-id-padding-%05d\n' $(seq 1 3500))"
old=0; new=0
for i in $(seq 1 10); do
  printf '%s\n' "$data" | grep -qxF NEEDLE || old=$((old+1))
  grep -qxF -- NEEDLE <<<"$data" || new=$((new+1))
done
echo "$old $new"
'''


def test_a_here_string_finds_what_the_pipe_loses():
    out = subprocess.run(["bash", "-c", _SHELL], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    old_misses, new_misses = map(int, out.stdout.split())
    if old_misses == 0:
        pytest.skip("this platform's pipe buffer holds 150 KB; the hazard cannot be shown here")
    assert new_misses == 0, (
        f"the here-string form missed a present line {new_misses} times out of 10"
    )


# `printf ... | grep -q` / `echo ... | grep -q` and `... | head`: a reader that
# can stop before the writer is done.
_EARLY_EXIT = re.compile(r"\b(printf|echo)\b[^|]*\|\s*(grep\s+-[a-zA-Z]*q|head\b)")


def _pipefail_scripts():
    tracked = subprocess.run(["git", "ls-files", "*.sh"], cwd=REPO,
                             capture_output=True, text=True, check=True).stdout.split()
    return [REPO / p for p in tracked
            if "pipefail" in (REPO / p).read_text(encoding="utf-8", errors="replace")]


def test_the_scan_sees_what_it_claims_to():
    assert _EARLY_EXIT.search("""    printf '%s\\n' "$KEEP" | grep -qxF "$1" 2>/dev/null && return 0""")
    assert _EARLY_EXIT.search("""NEWEST="$(printf '%s\\n' "$LIST" | head -n 1)\"""")
    assert _EARLY_EXIT.search("""    if echo "$out" | grep -q "FAIL.*$expect"; then""")
    assert not _EARLY_EXIT.search("""    grep -qxF -- "$1" <<<"$KEEP" && return 0""")
    assert len(_pipefail_scripts()) >= 10, "too few pipefail scripts found; the scan is not looking where it should"


def test_no_pipefail_script_feeds_an_early_exit_reader_through_a_pipe():
    found = []
    for path in _pipefail_scripts():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if _EARLY_EXIT.search(line):
                found.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert not found, (
        "under pipefail these can read a found match as 'not found' -- use a "
        "here-string (grep -q needle <<<\"$X\", head -n 1 <<<\"$X\"):\n  " + "\n  ".join(found)
    )
