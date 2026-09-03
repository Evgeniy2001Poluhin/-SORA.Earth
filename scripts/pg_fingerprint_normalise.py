"""Normalise a schema fingerprint so a correct restore compares equal.

Reads a fingerprint on stdin, writes it back on stdout with **CHECK constraint
definitions only** rewritten to a canonical form. Every other line — columns,
indexes, row counts, the data hash, the alembic revision — passes through byte
for byte.

## Why this exists

`pg_get_constraintdef` renders the stored parse tree. `pg_dump` writes that
rendering as SQL, and the restore re-parses it, producing an equivalent tree
that renders differently. Measured on a drill against PostgreSQL 16,
2026-09-03 — one constraint, identical in meaning, before and after a restore:

    -  CHECK (((temporal_kind)::text = ANY ((ARRAY['observed'::character varying,
       'period'::character varying, ...])::text[])))
    +  CHECK (((temporal_kind)::text = ANY (ARRAY[('observed'::character varying)::text,
       ('period'::character varying)::text, ...])))

The array cast moved from outside the constructor to each element. Nothing was
lost; PostgreSQL simply prints the second tree that way.

A comparison that fails on this fails on **every** correct restore, and a check
that is always red is a check that gets deleted. Hence a canonical form.

## What this is blind to, stated rather than discovered later

Casts and parentheses are removed, so within a CHECK expression **operator
precedence is invisible**:

    CHECK (((a = 1) AND (b = 2)) OR (c = 3))
    CHECK ((a = 1) AND ((b = 2) OR (c = 3)))

normalise to the same string. `tests/test_fingerprint_normalisation.py` asserts
that this is so, so the limit is recorded as behaviour rather than left for
someone to find.

The blindness is bounded on purpose. A dump/restore cycle cannot regroup an
expression — PostgreSQL re-renders the tree it parsed — so the case this cannot
see is not one the drill is guarding against. And it applies to CHECK
expressions alone: a changed column, a changed value list, a dropped index, a
missing table, a different row count or a different data hash are all still
compared exactly.
"""
import re
import sys

#: `::text`, `::character varying`, `::"MySchema".mytype`, `::text[]`.
CAST = re.compile(r"::\s*(?:\"[^\"]+\"|[a-zA-Z_][\w ]*)(?:\s*\[\s*\])?")

#: Only these lines are touched. The prefix is written by pg_fingerprint.
CONSTRAINT_LINE = re.compile(r"^constraint\|(.*?)(CHECK .*)$", re.S)


def canonical_check(definition):
    """A CHECK expression stripped of the parts PostgreSQL re-renders freely."""
    without_casts = CAST.sub("", definition)
    return re.sub(r"[()\s]+", "", without_casts)


def normalise_line(line):
    match = CONSTRAINT_LINE.match(line)
    if not match:
        return line
    prefix, definition = match.groups()
    return f"constraint|{prefix}{canonical_check(definition)}"


def main():
    for line in sys.stdin:
        sys.stdout.write(normalise_line(line.rstrip("\n")) + "\n")


if __name__ == "__main__":
    main()
