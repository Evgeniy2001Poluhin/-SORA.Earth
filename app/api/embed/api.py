import logging
import os
import re
import pathlib

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/embed", tags=["embed"])
_HTML = (pathlib.Path(__file__).parent / "widget.html").read_text(encoding="utf-8")

# This endpoint sent `X-Frame-Options: ALLOWALL`, which is not a value the header
# has. The specification defines DENY and SAMEORIGIN (and the obsolete
# ALLOW-FROM); browsers ignore anything else. So the widget had no frame policy
# at all, while carrying a header that read as a decision about framing.
#
# Framing is the point here -- /snippet hands out the <iframe> tag for it -- so
# the fix is to say so in the mechanism that browsers actually implement, and to
# make it restrictable without a code change.
#
# Default `*`. It preserves what the endpoint already did in practice and does
# not silently break embeds that exist today; the difference is that the
# permission is now declared rather than accidental. Set
# SORA_EMBED_FRAME_ANCESTORS to a space-separated origin list to narrow it.
#
# The clickjacking exposure this leaves, assessed rather than assumed: the widget
# reads five numbers and POSTs them to /api/v1/copilot/explain. No state change,
# no credentials, nothing destructive -- a user tricked into clicking it gets an
# explanation of some numbers. That is why an open default is defensible here and
# would not be on a page that acts on the user's behalf.
# Validated before it reaches the header, and a bad value is refused.
#
# This whole change exists because `X-Frame-Options: ALLOWALL` was a value
# browsers do not implement, so the header read as a decision and enforced
# nothing. An unvalidated SORA_EMBED_FRAME_ANCESTORS rebuilds exactly that by
# another route: a typo, a comma, a stray quote, and the directive is malformed,
# browsers ignore it, and the widget is framable by anyone while the
# configuration claims otherwise.
#
# An earlier version of this fell back to "*" with a warning. That is fail-open
# on a security control, and the argument for it -- that silently tightening
# would break embedding confusingly -- had the case backwards. Nobody sets this
# variable except to *restrict*; serving a wider policy than the one asked for
# is the failure that matters, and a warning in a log nobody reads is not
# consent. It refuses now, and validate_frame_ancestors_config() is called at
# startup so a misconfigured deployment does not boot rather than discovering it
# on the first embed request.
#
# Unset is different from wrong: absent means "no restriction requested", which
# is the documented default for a widget whose purpose is to be embedded.
_ANCESTOR = re.compile(
    r"""^(?:
          \*                                   # any origin
        | 'self' | 'none'                      # the two keywords CSP defines here
        | (?:https?://)?                       # optional scheme
          (?:\*\.)?                            # optional leading wildcard label
          [A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*     # host
          (?::\d{1,5})?                        # optional port
    )$""",
    re.VERBOSE,
)


class FrameAncestorsError(ValueError):
    """SORA_EMBED_FRAME_ANCESTORS was set to something browsers would ignore."""


def _frame_ancestors() -> str:
    raw = os.getenv("SORA_EMBED_FRAME_ANCESTORS", "").strip()
    if not raw:
        return "*"

    sources = raw.split()
    bad = [src for src in sources if not _ANCESTOR.match(src)]
    if bad:
        raise FrameAncestorsError(
            "SORA_EMBED_FRAME_ANCESTORS contains unusable source(s) %r. "
            "A malformed frame-ancestors directive is ignored by browsers, "
            "which would leave the widget framable by anyone while this "
            "setting claims to restrict it. Fix the value or unset it." % bad
        )

    # '*' and 'none' are absolute: combined with anything else the result is
    # either meaningless or misleading about what it permits.
    absolutes = [src for src in sources if src in ("*", "'none'")]
    if len(sources) > 1 and absolutes:
        raise FrameAncestorsError(
            "SORA_EMBED_FRAME_ANCESTORS mixes %r with other sources, which "
            "does not mean what it appears to. Use one or the other." % absolutes
        )

    return " ".join(sources)


def validate_frame_ancestors_config() -> str:
    """Called at startup, so a bad value stops the process rather than the page.

    Discovering it on the first embed request would mean the deployment looked
    healthy -- and every other route would work -- while the one control this
    module exists to provide was unusable.
    """
    value = _frame_ancestors()
    log.info("embed frame-ancestors policy: %s", value)
    return value


@router.get("", response_class=HTMLResponse)
def embed_widget():
    return HTMLResponse(
        _HTML,
        headers={"Content-Security-Policy": f"frame-ancestors {_frame_ancestors()}"},
    )

@router.get("/snippet", response_class=PlainTextResponse)
def embed_snippet(request: Request):
    base = str(request.base_url).rstrip("/")
    return f'<iframe src="{base}/api/v1/embed" width="412" height="640" style="border:0" title="SORA.Earth ESG"></iframe>'
