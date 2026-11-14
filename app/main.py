import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import api, config, sandbox
from .db import init_db

log = logging.getLogger("polartrap")

PAGES = {
    "/": "index.html",
    "/analysis": "analysis.html",
    "/metrics": "metrics.html",
    "/about": "about.html",
    "/sandbox": "sandbox/index.html",
    "/sandbox/login": "sandbox/login.html",
    "/sandbox/thread": "sandbox/thread.html",
    "/sandbox/feed": "sandbox/feed.html",
    "/sandbox/admin": "sandbox/admin.html",
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    for name in ("ANON_HMAC_KEY", "ADMIN_PASSWORD"):
        if not getattr(config, name):
            log.warning("%s is not set - related features are disabled", name)
    yield


app = FastAPI(title="PolarTrap", lifespan=lifespan)
app.include_router(api.router)
app.include_router(sandbox.router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
    return resp


def _page(filename: str):
    path = config.FRONTEND_DIR / filename

    def handler():
        return FileResponse(path, headers={"Cache-Control": "no-cache"})
    return handler


for route, fname in PAGES.items():
    app.add_api_route(route, _page(fname), methods=["GET"], include_in_schema=False)

app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")
