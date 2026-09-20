"""Regenerate tests/closed_issue_reference_cache.json.

Queries GitHub's GraphQL `issueOrPullRequest` for every `#N` the corpus in
test_a_closed_issue_does_not_track_open_work.py cites, batched (GraphQL
aliases, ~40 numbers per request) rather than one `gh` call per number.

`issueOrPullRequest`, not `issue`: `gh issue view <N>` returns exit 0 and a
CLOSED-shaped JSON body for a number that is actually a pull request -- see
the guard's module docstring, Trap 1. The typed union is what lets the cache
say which one it got instead of repeating that mistake.

Run this after adding a new #N reference the guard's cache-completeness test
reports missing, or periodically to catch an issue that closed since the last
run -- nothing here does that on a schedule (a documented, deliberate gap;
see the guard's module docstring).
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_a_closed_issue_does_not_track_open_work import (  # noqa: E402
    CACHE_PATH, referenced_numbers,
)


def _repo_owner_name() -> tuple[str, str]:
    out = subprocess.run(
        ["gh", "repo", "view", "--json", "owner,name"],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    return data["owner"]["login"], data["name"]


def _batched(items, n):
    items = sorted(items)
    for i in range(0, len(items), n):
        yield items[i:i + n]


def main() -> int:
    owner, name = _repo_owner_name()
    nums = referenced_numbers()
    results = {}

    for chunk in _batched(nums, 40):
        fields = "\n".join(
            f'q{n}: issueOrPullRequest(number: {n}) {{ '
            f'__typename ... on Issue {{ number state title }} '
            f'... on PullRequest {{ number state title }} }}'
            for n in chunk
        )
        query = f'query {{ repository(owner: "{owner}", name: "{name}") {{ {fields} }} }}'
        out = subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={query}"],
            capture_output=True, text=True,
        )
        if out.returncode != 0:
            print(f"ERROR resolving {chunk[:3]}...: {out.stderr[:500]}", file=sys.stderr)
            return 1
        repo = json.loads(out.stdout)["data"]["repository"]
        for n in chunk:
            node = repo.get(f"q{n}")
            if node is None:
                print(f"WARNING: #{n} resolves to neither an issue nor a pull "
                      f"request (deleted, or a cross-repo/typo\'d number)")
                results[str(n)] = {"type": "MISSING", "state": None, "title": None}
            else:
                results[str(n)] = {
                    "type": node["__typename"],
                    "state": node["state"],
                    "title": node["title"],
                }

    CACHE_PATH.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")

    issues = {n: r for n, r in results.items() if r["type"] == "Issue"}
    prs = {n: r for n, r in results.items() if r["type"] == "PullRequest"}
    closed_issues = {n: r for n, r in issues.items() if r["state"] == "CLOSED"}
    print(f"wrote {CACHE_PATH} -- {len(results)} numbers "
          f"({len(issues)} issues, {len(closed_issues)} closed; {len(prs)} pull requests)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
