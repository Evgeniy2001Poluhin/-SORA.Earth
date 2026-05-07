#!/usr/bin/env python3
from pathlib import Path

p = Path("app/main.py")
src = p.read_text()

if "# --- UI ROUTES ---" in src:
    print("SKIP: main.py already patched")
else:
    inject = '''
# --- UI ROUTES ---
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path as _Path

_STATIC = _Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC), name="static")

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def _landing():
    return FileResponse(_STATIC / "pages/landing.html")

@app.get("/auth/login", response_class=HTMLResponse, include_in_schema=False)
async def _login_page():
    return FileResponse(_STATIC / "pages/login.html")

@app.get("/app/{path:path}", response_class=HTMLResponse, include_in_schema=False)
async def _app_shell(path: str = ""):
    return FileResponse(_STATIC / "pages/app.html")

@app.get("/admin/{path:path}", response_class=HTMLResponse, include_in_schema=False)
async def _admin_shell(path: str = ""):
    return FileResponse(_STATIC / "pages/admin.html")
# --- /UI ROUTES ---
'''
    p.write_text(src + inject)
    print("OK: main.py patched (+" + str(len(inject)) + " chars)")
