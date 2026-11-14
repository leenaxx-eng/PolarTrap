import sqlite3

from fastapi import APIRouter, Depends

from . import config
from .db import db_dep

router = APIRouter(prefix="/api")


@router.get("/health")
def health(con: sqlite3.Connection = Depends(db_dep)):
    con.execute("SELECT 1").fetchone()
    return {
        "status": "ok",
        "db": "ok",
        "commit": config.GIT_COMMIT,
        "anon_key_set": bool(config.ANON_HMAC_KEY),
        "admin_password_set": bool(config.ADMIN_PASSWORD),
        "ai_router_configured": bool(config.AI_ROUTER_API_KEY and config.AI_ROUTER_BASE_URL),
    }
