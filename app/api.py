import json
import logging
import secrets
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import config, llm, pipeline
from .bluesky import parse_post_url
from .db import connect, db_dep
from .sandbox import now_iso, require_admin

log = logging.getLogger("polartrap.api")
router = APIRouter(prefix="/api")
_executor = ThreadPoolExecutor(max_workers=2)


@router.get("/health")
def health(con: sqlite3.Connection = Depends(db_dep)):
    con.execute("SELECT 1").fetchone()
    ref, model = pipeline.load_models()
    return {
        "status": "ok",
        "db": "ok",
        "commit": config.GIT_COMMIT,
        "anon_key_set": bool(config.ANON_HMAC_KEY),
        "admin_password_set": bool(config.ADMIN_PASSWORD),
        "ai_router_configured": llm.available(),
        "reference_pairs": len(ref.pairs),
        "style_model": model is not None,
    }


# --- background jobs ------------------------------------------------------------
def _set(job_id: str, **fields) -> None:
    con = connect()
    try:
        sets = ", ".join(f"{k}=?" for k in fields)
        con.execute(f"UPDATE analyses SET {sets} WHERE id=?", (*fields.values(), job_id))
    finally:
        con.close()


def _run_job(job_id: str, kind: str, arg) -> None:
    _set(job_id, status="running", progress="Starting")
    try:
        progress = lambda msg: _set(job_id, progress=msg)  # noqa: E731
        if kind == "live":
            report, admin = pipeline.run_live(arg, progress=progress)
        else:
            con = connect()
            try:
                report, admin = pipeline.run_sandbox(con, int(arg), progress=progress)
            finally:
                con.close()
        report["analysis_id"] = job_id
        _set(job_id, status="done", progress="Done", finished_at=now_iso(),
             result_json=json.dumps(report), admin_json=json.dumps(admin))
    except Exception as e:  # noqa: BLE001 - surfaced to the user
        log.exception("analysis %s failed", job_id)
        _set(job_id, status="error", error=str(e)[:500], finished_at=now_iso())


def _start(kind: str, arg: str) -> str:
    job_id = secrets.token_hex(8)
    con = connect()
    try:
        con.execute("INSERT INTO analyses(id, kind, input, status, created_at, progress) VALUES (?,?,?,?,?,?)",
                    (job_id, kind, arg, "queued", now_iso(), "Queued"))
    finally:
        con.close()
    _executor.submit(_run_job, job_id, kind, arg)
    return job_id


class AnalyzeIn(BaseModel):
    thread_url: str = Field(max_length=300)


@router.post("/analyze")
def analyze(body: AnalyzeIn):
    try:
        parse_post_url(body.thread_url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"job_id": _start("live", body.thread_url.strip())}


class AnalyzeSandboxIn(BaseModel):
    sandbox_thread_id: int


@router.post("/analyze_sandbox")
def analyze_sandbox(body: AnalyzeSandboxIn, con: sqlite3.Connection = Depends(db_dep)):
    if not con.execute("SELECT 1 FROM sandbox_threads WHERE id=?", (body.sandbox_thread_id,)).fetchone():
        raise HTTPException(404, "Sandbox thread not found")
    return {"job_id": _start("sandbox", str(body.sandbox_thread_id))}


@router.get("/analysis/{job_id}")
def get_analysis(job_id: str, con: sqlite3.Connection = Depends(db_dep)):
    row = con.execute("SELECT id, kind, input, status, progress, error, created_at, finished_at, result_json "
                      "FROM analyses WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Analysis not found")
    out = {k: row[k] for k in ("id", "kind", "status", "progress", "error", "created_at", "finished_at")}
    # thread URLs contain handles: only show input for sandbox jobs
    out["input"] = row["input"] if row["kind"] == "sandbox" else None
    out["result"] = json.loads(row["result_json"]) if row["result_json"] else None
    return out


@router.get("/analyses/sandbox_threads")
def sandbox_threads_public(con: sqlite3.Connection = Depends(db_dep)):
    """Thread titles for the demo picker (no post content, no persona data)."""
    return {"threads": [dict(r) for r in con.execute("SELECT id, title, topic FROM sandbox_threads ORDER BY id")]}


@router.get("/admin/analysis/{job_id}", dependencies=[Depends(require_admin)])
def admin_analysis(job_id: str, con: sqlite3.Connection = Depends(db_dep)):
    row = con.execute("SELECT input, admin_json FROM analyses WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Analysis not found")
    return {"input": row["input"], **(json.loads(row["admin_json"]) if row["admin_json"] else {})}


class FeedbackIn(BaseModel):
    analysis_id: str = Field(max_length=64)
    pair_key: str = Field(max_length=200)
    reviewer: str = Field(min_length=1, max_length=40)
    rating: str = Field(pattern="^(clear|weak|misleading)$")
    note: str | None = Field(default=None, max_length=1000)


@router.post("/feedback")
def feedback(body: FeedbackIn, con: sqlite3.Connection = Depends(db_dep)):
    if not con.execute("SELECT 1 FROM analyses WHERE id=?", (body.analysis_id,)).fetchone():
        raise HTTPException(404, "Analysis not found")
    con.execute("INSERT INTO feedback(analysis_id, pair_key, reviewer, rating, note, created_at) "
                "VALUES (?,?,?,?,?,?)", (body.analysis_id, body.pair_key, body.reviewer.strip(), body.rating,
                                        body.note, now_iso()))
    return {"ok": True}


@router.get("/metrics")
def metrics():
    from pathlib import Path
    path = Path(config.RESULTS_PATH)
    if not path.exists():
        path = config.ROOT / "eval" / "results.json"  # committed copy
    if not path.exists():
        return {"available": False}
    return {"available": True, **json.loads(path.read_text(encoding="utf-8"))}
