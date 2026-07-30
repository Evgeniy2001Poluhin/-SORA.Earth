import os
import pathlib

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

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
def _frame_ancestors() -> str:
    return os.getenv("SORA_EMBED_FRAME_ANCESTORS", "*").strip() or "*"


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
