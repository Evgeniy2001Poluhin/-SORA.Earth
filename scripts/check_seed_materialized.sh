#!/usr/bin/env bash
# Refuse a checkout whose model artefacts are LFS pointers rather than the real
# files. A checkout where `git lfs` did not smudge leaves `models/*.pkl` and
# `models/*.pth` as ~130-byte pointer stubs; a container built from it would
# load a pointer as a model and fail at the first prediction, with every health
# check green until then (the same shape as #196). This fails early and loudly,
# before anything is built or deployed.
#
# It reads only tracked files under `models/`, and classifies each as
# materialized, an LFS pointer, or missing. A tree with no tracked artefacts --
# the deploy guard's own test sandbox, say -- passes: there is nothing to ship
# wrongly.
set -euo pipefail

repo="${SEED_CHECKOUT:-$(git rev-parse --show-toplevel)}"
cd "$repo"

pointers=0
missing=0
checked=0
while IFS= read -r path; do
    case "$path" in
        *.pkl | *.pth) ;;
        *) continue ;;
    esac
    checked=$((checked + 1))
    if [ ! -f "$path" ]; then
        echo "MISSING       $path" >&2
        missing=$((missing + 1))
        continue
    fi
    # An LFS pointer is a tiny text file beginning with the spec URL. The real
    # artefacts are binary pickles/torch files that do not contain it.
    if head -c 100 "$path" | grep -q 'https://git-lfs.github.com/spec'; then
        echo "LFS POINTER   $path" >&2
        pointers=$((pointers + 1))
    fi
done < <(git ls-files -- models)

if [ "$checked" -eq 0 ]; then
    echo "no tracked model artefacts under models/ to check"
    exit 0
fi

if [ "$pointers" -ne 0 ] || [ "$missing" -ne 0 ]; then
    echo "FAIL: of $checked seed artefact(s), $pointers is/are LFS pointer(s) and $missing missing." >&2
    echo "      Run 'git lfs pull' before deploying; a pointer is not a model." >&2
    exit 1
fi

echo "OK: $checked seed artefact(s) under models/ are materialized"
