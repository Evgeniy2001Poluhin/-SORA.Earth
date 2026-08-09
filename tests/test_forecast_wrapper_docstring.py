"""`_query_time_series` must not carry a second copy of the loader's contract.

Its docstring claimed the series was built "with interpolation and synthetic
extension" for months after `e555560` deleted the synthetic extension (#134).
The loader's own docstring was corrected in #122; this one was two frames up
the call stack and nothing connected them, so it survived untouched.

The fix is not a better description -- it is less description. A wrapper that
restates what it delegates to has two texts that must be kept in agreement by
hand, and this is the second time in this codebase that arrangement drifted.

So these tests assert absence, which needs care: a test that only bans words
passes trivially against an empty docstring. The last check here requires the
text to point at `load_time_series`, so "say nothing at all" is not a way to
go green.
"""
import re

from app.api.forecast import _query_time_series


DOC = _query_time_series.__doc__ or ""


def test_it_does_not_promise_the_removed_synthetic_extension():
    """The claim that started this: behaviour deleted in `e555560`."""
    lowered = DOC.lower()

    for gone in ("synthetic", "backfill", "extending backward", "extension"):
        assert gone not in lowered, (
            f"the wrapper still describes removed behaviour: {gone!r}"
        )


def test_it_does_not_restate_the_loader_mechanics():
    """Whatever the loader does is the loader's to document.

    Named individually rather than as one blob so a failure says which piece
    crept back in.
    """
    lowered = DOC.lower()

    for mechanic in ("interpolat", "smooth", "rolling", "threshold",
                     "provenance", "daily mean"):
        assert mechanic not in lowered, (
            f"the wrapper restates loader mechanics: {mechanic!r} -- this is "
            f"the duplication that went stale, not merely a wording choice"
        )


def test_it_still_points_at_the_function_it_delegates_to():
    """Without this the two tests above are satisfied by an empty docstring.

    Absence is the property under test, so something has to make absence
    distinguishable from silence.
    """
    assert re.search(r"\bload_time_series\b", DOC), (
        "the docstring names no destination, so a reader cannot find where "
        "the contract actually lives"
    )
