"""CLAUDE.md must give one answer to "how is production deployed".

It gave two. `CLAUDE.md:105` carried, under the comment `# Production
deployment`, a `docker-compose -f docker-compose.yml -f docker-compose.prod.yml
up -d`, while the "Production Server" section states that
`./scripts/deploy_production.sh` is the only supported way and explains why.

The two are not the same command with different spelling. Measured on the
production host with `--dry-run` on 2026-09-07, same project name, only the
`-f` list differing:

    -f docker-compose.prod.yml                    recreate prometheus, nothing else
    -f docker-compose.yml -f docker-compose.prod.yml
                                                  recreate postgres, redis,
                                                  prometheus, and CREATE app-1

`docker-compose.yml` declares a service `app` that production does not run --
production runs `backend`. `depends_on` is merged across files, so adding the
base file adds dependencies the production file never declared. Following the
documented line would have started a second application container beside the
running one, which is the 2026-08-09 incident (#129): a duplicate backend took
the address, nginx kept the old one, and the public site returned 502 for four
and a half minutes with every container reporting healthy.

Filed as #278. These tests exist because prose is not enforceable and this
particular prose was wrong for months without anything noticing.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "CLAUDE.md"
DEPLOY_SCRIPT = REPO / "scripts" / "deploy_production.sh"

# `docker compose` and the older `docker-compose`, since the file uses both.
_INVOCATION = re.compile(r"\bdocker[- ]compose\b")

# Compose's own subcommands. Needed because `-f` means two different things
# depending on where it sits: a compose file before the subcommand, and
# `--follow` after it in `logs -f backend`. Collecting every `-f` on the line
# read that `backend` as a compose file -- found by this file's own tests
# failing on the documented log commands, which is what they are for.
_SUBCOMMANDS = frozenset(
    """attach build config cp create down events exec images kill logs ls pause
    port ps pull push restart rm run scale start stats stop top unpause up
    version wait watch""".split()
)
_MUTATING = frozenset({"up", "down"})


def deployment_compose_file() -> str:
    """The compose file the deployment actually uses, read from the script.

    Read rather than written down here. Hardcoding it would let the script and
    this test drift apart in the one direction that matters -- the script
    changing -- and the test would keep passing while describing the old world.
    """
    text = DEPLOY_SCRIPT.read_text()
    match = re.search(r'^COMPOSE="\$\{COMPOSE_FILE:-\$REPO/([^}"]+)\}"', text, re.M)
    assert match, (
        "could not find the COMPOSE default in scripts/deploy_production.sh; "
        "the assignment moved or changed shape, and every assertion below is "
        "about a file this test can no longer identify"
    )
    return match.group(1)


def compose_command_lines() -> list[tuple[int, str]]:
    """Every line in CLAUDE.md that invokes compose, with its 1-based number."""
    return [
        (n, line.strip())
        for n, line in enumerate(DOC.read_text().splitlines(), start=1)
        if _INVOCATION.search(line) and not line.lstrip().startswith(("#", ">", "|"))
    ]


def parse_invocation(line: str) -> tuple[list[str], str | None]:
    """The `-f` files and the subcommand, split at the subcommand.

    Everything before the subcommand belongs to compose; everything after it
    belongs to the subcommand, and `-f` on that side is not a file.
    """
    tokens = [t.strip("`") for t in line.replace("`", " ").split()]
    start = next(
        (i for i, t in enumerate(tokens) if t in ("docker-compose", "compose")), None
    )
    if start is None:
        return [], None
    files: list[str] = []
    subcommand = None
    i = start + 1
    while i < len(tokens):
        token = tokens[i]
        if token in ("-f", "--file") and i + 1 < len(tokens):
            files.append(tokens[i + 1])
            i += 2
            continue
        if token.startswith("-"):
            i += 1
            continue
        if token in _SUBCOMMANDS:
            subcommand = token
        break
    return files, subcommand


def test_the_parser_finds_the_commands_it_judges():
    """Negative control, first: every assertion below is over this list.

    An empty list passes all of them. The file carries a documented compose
    command per operation -- logs, ps, restart, exec, pg_dump -- so a parser
    returning nothing has stopped reading the file rather than found it clean.
    """
    lines = compose_command_lines()
    assert len(lines) >= 6, (
        f"only {len(lines)} compose invocations parsed out of CLAUDE.md; "
        "the fences or the spelling changed and these tests are now vacuous"
    )
    prod = deployment_compose_file()
    named = [line for _, line in lines if prod in parse_invocation(line)[0]]
    assert len(named) >= 4, (
        f"only {len(named)} of them name {prod} as a compose file; "
        "the production operations section is not being read"
    )
    # And that the split works at all: the documented log commands carry `-f`
    # on both sides of the subcommand, and reading the second one as a file is
    # the mistake this parser was rewritten to stop making.
    files, subcommand = parse_invocation(
        "docker compose -f docker-compose.prod.yml logs -f backend"
    )
    assert (files, subcommand) == ([prod], "logs"), (files, subcommand)


def test_no_documented_command_starts_or_stops_production():
    """Deployment has one supported form, and it is not a compose command.

    `up` against the production compose file is a deployment by another name --
    it recreates containers, skips every check the script runs, and leaves no
    manifest. `down` removes them. Neither belongs in a document that also
    says the script is the only supported way.
    """
    prod = deployment_compose_file()
    offenders = []
    for n, line in compose_command_lines():
        files, subcommand = parse_invocation(line)
        if prod in files and subcommand in _MUTATING:
            offenders.append((n, line))
    assert not offenders, (
        "CLAUDE.md documents a compose command that starts or stops production "
        "containers; deployment is ./scripts/deploy_production.sh:\n"
        + "\n".join(f"  line {n}: {line}" for n, line in offenders)
    )


def test_production_commands_use_the_file_the_deployment_uses():
    """One file, the same one the script composes with.

    The two-file form addresses the same project by name, so it looks
    equivalent and is not: it declares services production does not run. A
    command that composes differently from the deployment is describing a
    different system.
    """
    prod = deployment_compose_file()
    wrong = []
    for n, line in compose_command_lines():
        files, _ = parse_invocation(line)
        if prod in files and files != [prod]:
            wrong.append((n, line, files))
    assert not wrong, (
        f"CLAUDE.md composes production commands from a different file set than "
        f"scripts/deploy_production.sh, which uses exactly [{prod}]:\n"
        + "\n".join(f"  line {n}: {files} -- {line}" for n, line, files in wrong)
    )


def test_the_document_still_names_the_supported_deployment():
    """The half that has to remain true after removing the wrong answer.

    Deleting a bad command and leaving no command is a different failure, and
    one this file would otherwise pass silently.
    """
    text = DOC.read_text()
    assert "./scripts/deploy_production.sh" in text, (
        "CLAUDE.md no longer names scripts/deploy_production.sh; removing the "
        "second answer must not remove the first one"
    )
