from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
import pathlib

router = APIRouter(prefix="/embed", tags=["embed"])
_HTML = (pathlib.Path(__file__).parent / "widget.html").read_text(encoding="utf-8")

@router.get("", response_class=HTMLResponse)
def embed_widget():
    return HTMLResponse(_HTML, headers={"X-Frame-Options": "ALLOWALL"})

@router.get("/snippet", response_class=PlainTextResponse)
def embed_snippet(request: Request):
    base = str(request.base_url).rstrip("/")
    return f'<iframe src="{base}/api/v1/embed" width="412" height="640" style="border:0" title="SORA.Earth ESG"></iframe>'
