#!/usr/bin/env bash
# shellcheck disable=SC2016
#   The mutation programs are literal text handed to a Python replacement, not
#   shell to be evaluated. Single quotes are required so $VARIABLES and %s inside
#   them reach the script's source unexpanded. File-wide, because every mutation
#   is such a string.
# Mutation testing for scripts/backfill_indicator_periods.py.
#
# The write-path tests were rewritten because the previous ones could not fail:
# they re-typed the corrected SQL and asserted the database obeyed. "Eight tests
# pass" says nothing about whether they discriminate, and saying so in a review
# comment is a claim about a run nobody else made. This is that run, in CI.
#
# Each mutation below restores one defect review actually found, on a temporary
# copy; the named test must fail. If it still passes, that test is decorative
# and this run says so.
#
# The tracked script is never modified. Mutants live in a temp directory and are
# deleted; none is ever committed.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ORIGINAL="$ROOT/scripts/backfill_indicator_periods.py"
TESTS="$ROOT/tests/test_backfill_integration_postgres.py"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

KILLED=0; SURVIVED=0; INVALID=0

case "${DATABASE_URL:-}" in
    postgresql*) ;;
    *) echo "SKIP: needs a PostgreSQL DATABASE_URL; the tests skip without one"; exit 0 ;;
esac

# Runs one test node against a given copy of the script. Prints pytest's own
# summary line so a failure here is readable without re-running by hand.
run_case() {
    local script="$1" node="$2"
    SCRIPT_UNDER_TEST="$script" python3 -m pytest "$TESTS::$node" \
        -q --timeout=600 -p no:cacheprovider 2>&1
}

# name | test node that must fail | replacement, as "old||=>||new"
#
# Not a sed program, whatever the shape suggests: the third argument is split on
# ||=>|| and handed to Python's str.replace, so both halves are literal text.
run_mutation() {
    local name="$1" node="$2" program="$3"
    local mutant="$WORK/mutant.py"
    cp "$ORIGINAL" "$mutant"
    if ! python3 - "$mutant" "$program" <<'PY'
import sys
path, prog = sys.argv[1], sys.argv[2]
old, new = prog.split("||=>||")
s = open(path).read()
if old not in s:
    sys.exit("MUTATION ANCHOR NOT FOUND")
open(path, "w").write(s.replace(old, new, 1))
import ast
ast.parse(open(path).read())
PY
    then
        printf '  invalid  %-34s anchor not found or mutant does not parse\n' "$name"
        INVALID=$((INVALID+1)); return
    fi

    # Captured, not piped. `set -o pipefail` reports the pipeline's rightmost
    # non-zero status, and a caught mutation is exactly the case where pytest
    # exits 1 -- so `run_case | grep -q "1 failed"` returned failure precisely
    # when the mutation *was* caught. The first version of this tool reported
    # all four as MISSED while every one of them was being caught: a harness
    # whose verdict had nothing to do with what it measured, which is the defect
    # class this whole tool exists to catch.
    # Status and output together, because neither alone is enough.
    #
    # Output only: a run that ends "1 passed, 1 error" contains no "1 failed",
    # so a mutant that broke collection is scored as surviving. Status only: a
    # mutant that fails to import also exits non-zero, and would be scored as
    # caught while testing nothing at all.
    #
    # So: exit zero is survival, and a non-zero exit counts only when the named
    # test is the one that failed. Anything else is invalid -- the harness did
    # not measure what it claims, and saying so is the point of the third
    # category.
    local out status
    out="$(run_case "$mutant" "$node")"
    status=$?
    if [ "$status" -eq 0 ]; then
        printf '  survived %-34s → %s still passed\n' "$name" "$node"
        SURVIVED=$((SURVIVED+1))
    elif printf '%s' "$out" | grep -q "FAILED.*$node"; then
        printf '  killed   %-34s → %s failed\n' "$name" "$node"
        KILLED=$((KILLED+1))
    else
        printf '  invalid  %-34s → non-zero exit, but %s did not fail\n' "$name" "$node"
        printf '%s\n' "$out" | tail -3
        INVALID=$((INVALID+1))
    fi
}

# Baseline first. Without it, "caught" cannot be told from a test that fails
# against everything -- including the unmodified script -- which would make every
# line below meaningless while looking like proof.
echo "== the unmodified script passes =="
for node in test_a_matching_value_on_a_later_page_makes_it_ambiguous \
            test_a_newer_rule_withdraws_an_older_verdict_and_the_date_goes_with_it \
            test_the_request_narrows_the_series_in_no_way; do
    # The status, not the wording. "1 passed, 1 error" contains "1 passed" and
    # is not a green baseline; pytest's exit code says so unambiguously.
    if baseline="$(run_case "$ORIGINAL" "$node")"; then
        printf '  ok       %s\n' "$node"
    else
        printf '  FAIL     %s does not pass unmutated; nothing below means anything\n' "$node"
        printf '%s\n' "$baseline" | tail -3
        INVALID=$((INVALID+1))
    fi
done

echo
echo "== each defect review found, restored =="

# Read only the first page. The truncation flag existed and could not save it:
# classify consulted it after counting candidates, so a unique match on page one
# was recorded as recovered while a second match sat unread on page two.
run_mutation "reads only the first page" \
    "test_a_matching_value_on_a_later_page_makes_it_ambiguous" \
    '        if page >= pages:||=>||        if True:'

# A recovered row carries a date by definition, so this excluded exactly the
# rows a recheck exists for: a corrected rule could not reach what the old one
# got wrong.
run_mutation "recheck cannot reach a decided row" \
    "test_a_newer_rule_withdraws_an_older_verdict_and_the_date_goes_with_it" \
    '              AND (period_status IS NOT NULL OR as_of_date IS NULL)||=>||              AND as_of_date IS NULL'

# COALESCE kept the old date when the new verdict had none, so a row demoted to
# ambiguous carried the date its discredited verdict produced.
run_mutation "withdrawn verdict keeps its date" \
    "test_a_newer_rule_withdraws_an_older_verdict_and_the_date_goes_with_it" \
    '                   SET as_of_date = %s,||=>||                   SET as_of_date = COALESCE(%s, as_of_date),'

# A bounded search that reports absence as revision. outside_query_window is
# unreachable for a fully-read bounded response, so a value from outside the
# window became no_match_current_vintage.
run_mutation "narrows the series with a date bound" \
    "test_the_request_narrows_the_series_in_no_way" \
    '+ f"?format=json&per_page={PER_PAGE}&page={page}")||=>||+ f"?format=json&per_page={PER_PAGE}&date=1960:2030&page={page}")'

echo
echo "  killed: $KILLED   survived: $SURVIVED   invalid: $INVALID"
[ "$SURVIVED" -eq 0 ] && [ "$INVALID" -eq 0 ]
