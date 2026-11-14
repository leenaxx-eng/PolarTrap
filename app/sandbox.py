"""Sandbox: one-time login codes, HttpOnly session cookies, threads, feed.

Ground-truth pair labels go into `sandbox_labels`, which only admin endpoints
and eval/ may read. Detector code must never import from here.
"""
import hashlib
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from . import config
from .db import db_dep

router = APIRouter(prefix="/api/sandbox")

COOKIE = "pt_session"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)*")
ID_RE = r"^[A-Za-z0-9_-]{1,32}$"
ROOT_DID = "sbx:root"  # author of thread root posts; not a persona, never analysed

_ADJ = ["amber", "brisk", "calm", "dusty", "eager", "fuzzy", "grand", "hazy", "icy", "jolly",
        "keen", "lucky", "misty", "noble", "olive", "plain", "quiet", "rusty", "sunny", "tidy",
        "urban", "vivid", "witty", "young", "zesty"]
_NOUN = ["otter", "heron", "badger", "falcon", "lynx", "marten", "osprey", "puffin", "raven",
         "salmon", "tern", "vole", "walrus", "wren", "yak", "bison", "crane", "dingo", "egret",
         "ferret", "gecko", "ibis", "koala", "lemur", "moose"]


# --- helpers ---------------------------------------------------------------
def now_iso(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def word_count(text: str) -> int:
    return len(WORD_RE.findall(text or ""))


def normalize_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", code.upper())


def new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def _issue_code(con: sqlite3.Connection, persona_id: str) -> str:
    # Invalidate any previous unused code for this persona.
    con.execute("UPDATE sandbox_codes SET used_at=? WHERE persona_id=? AND used_at IS NULL",
                (now_iso(), persona_id))
    code = new_code()
    con.execute("INSERT INTO sandbox_codes(code_hash, persona_id, created_at) VALUES (?,?,?)",
                (sha(normalize_code(code)), persona_id, now_iso()))
    return code


def _unique_display_name(con: sqlite3.Connection) -> str:
    for _ in range(200):
        name = f"{secrets.choice(_ADJ)}_{secrets.choice(_NOUN)}{secrets.randbelow(90) + 10}"
        if not con.execute("SELECT 1 FROM sandbox_personas WHERE display_name=?", (name,)).fetchone():
            return name
    raise HTTPException(500, "Could not allocate a display name")


def progress(con: sqlite3.Connection, did: str) -> dict:
    rows = con.execute(
        "SELECT root_uri, text FROM posts WHERE did=? AND namespace='sandbox'", (did,)).fetchall()
    replies = [r for r in rows if r["root_uri"]]
    feed = [r for r in rows if not r["root_uri"]]
    p = {
        "replies": len(replies),
        "threads": len({r["root_uri"] for r in replies}),
        "feed_posts": len(feed),
        "words": sum(word_count(r["text"]) for r in rows),
    }
    p["targets_met"] = all(p[k] >= v for k, v in config.SANDBOX_TARGETS.items())
    return p


# --- rate limit (single uvicorn worker -> in-memory is fine) ---------------
_lock = threading.Lock()
_hist: dict[str, deque] = defaultdict(deque)


def check_rate(persona_id: str) -> None:
    now = time.monotonic()
    with _lock:
        d = _hist[persona_id]
        while d and now - d[0] > 600:
            d.popleft()
        if d and now - d[-1] < config.MIN_SECONDS_BETWEEN_POSTS:
            raise HTTPException(429, "Slow down a little - wait a few seconds between posts.")
        if len(d) >= config.MAX_POSTS_PER_10_MIN:
            raise HTTPException(429, "Post limit reached for the last 10 minutes.")
        d.append(now)


# --- auth dependencies -----------------------------------------------------
_basic = HTTPBasic(auto_error=False)


def require_admin(creds: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    if not config.ADMIN_PASSWORD:
        raise HTTPException(503, "ADMIN_PASSWORD is not configured")
    if creds is None or not secrets.compare_digest(
            creds.password.encode(), config.ADMIN_PASSWORD.encode()):
        raise HTTPException(401, "Admin login required", headers={"WWW-Authenticate": "Basic"})


def current_persona(request: Request, con: sqlite3.Connection = Depends(db_dep)) -> sqlite3.Row:
    tok = request.cookies.get(COOKIE)
    if not tok:
        raise HTTPException(401, "Not logged in")
    row = con.execute(
        "SELECT p.* FROM sandbox_sessions s JOIN sandbox_personas p ON p.id=s.persona_id "
        "WHERE s.token_hash=? AND s.expires_at>? AND p.active=1",
        (sha(tok), now_iso())).fetchone()
    if not row:
        raise HTTPException(401, "Session expired - log in again with a new code")
    return row


# --- participant endpoints -------------------------------------------------
class LoginIn(BaseModel):
    code: str = Field(min_length=4, max_length=64)
    consent: bool


@router.post("/login")
def login(body: LoginIn, response: Response, con: sqlite3.Connection = Depends(db_dep)):
    if not body.consent:
        raise HTTPException(400, "Please read and tick the consent box first.")
    ch = sha(normalize_code(body.code))
    t = now_iso()
    # Atomic single-use: only one request can flip used_at from NULL.
    cur = con.execute("UPDATE sandbox_codes SET used_at=? WHERE code_hash=? AND used_at IS NULL",
                      (t, ch))
    if cur.rowcount != 1:
        raise HTTPException(401, "Invalid or already used code.")
    pid = con.execute("SELECT persona_id FROM sandbox_codes WHERE code_hash=?", (ch,)).fetchone()[0]
    con.execute("UPDATE sandbox_personas SET consented_at=COALESCE(consented_at, ?) WHERE id=?",
                (t, pid))
    tok = secrets.token_urlsafe(32)
    exp = datetime.now(timezone.utc) + timedelta(hours=config.SESSION_TTL_HOURS)
    con.execute("INSERT INTO sandbox_sessions(token_hash, persona_id, created_at, expires_at) "
                "VALUES (?,?,?,?)", (sha(tok), pid, t, now_iso(exp)))
    response.set_cookie(COOKIE, tok, max_age=config.SESSION_TTL_HOURS * 3600, path="/",
                        httponly=True, secure=config.COOKIE_SECURE, samesite="strict")
    name = con.execute("SELECT display_name FROM sandbox_personas WHERE id=?", (pid,)).fetchone()[0]
    return {"ok": True, "display_name": name}


@router.post("/logout")
def logout(request: Request, response: Response, con: sqlite3.Connection = Depends(db_dep)):
    tok = request.cookies.get(COOKIE)
    if tok:
        con.execute("DELETE FROM sandbox_sessions WHERE token_hash=?", (sha(tok),))
    response.delete_cookie(COOKIE, path="/", httponly=True, secure=config.COOKIE_SECURE,
                           samesite="strict")
    return {"ok": True}


@router.get("/me")
def me(persona=Depends(current_persona), con: sqlite3.Connection = Depends(db_dep)):
    return {"display_name": persona["display_name"], "progress": progress(con, persona["did"]),
            "targets": config.SANDBOX_TARGETS}


@router.get("/threads")
def list_threads(persona=Depends(current_persona), con: sqlite3.Connection = Depends(db_dep)):
    rows = con.execute(
        "SELECT t.id, t.title, t.topic, t.created_at, p.text AS root_text, "
        " (SELECT COUNT(*) FROM posts r WHERE r.root_uri=t.root_uri) AS replies, "
        " (SELECT MAX(created_at) FROM posts r WHERE r.root_uri=t.root_uri) AS last_reply_at "
        "FROM sandbox_threads t JOIN posts p ON p.uri=t.root_uri ORDER BY t.id").fetchall()
    return {"threads": [dict(r) for r in rows]}


@router.get("/threads/{thread_id}")
def get_thread(thread_id: int, persona=Depends(current_persona),
               con: sqlite3.Connection = Depends(db_dep)):
    t = con.execute("SELECT * FROM sandbox_threads WHERE id=?", (thread_id,)).fetchone()
    if not t:
        raise HTTPException(404, "Thread not found")
    root = con.execute("SELECT uri, text, created_at FROM posts WHERE uri=?",
                       (t["root_uri"],)).fetchone()
    replies = con.execute(
        "SELECT p.uri, p.parent_uri, p.text, p.created_at, sp.display_name AS author "
        "FROM posts p LEFT JOIN sandbox_personas sp ON sp.did=p.did "
        "WHERE p.root_uri=? ORDER BY p.created_at, p.rowid", (t["root_uri"],)).fetchall()
    return {"thread": {"id": t["id"], "title": t["title"], "topic": t["topic"]},
            "root": dict(root), "replies": [dict(r) for r in replies],
            "me": persona["display_name"]}


@router.get("/feed")
def get_feed(persona=Depends(current_persona), con: sqlite3.Connection = Depends(db_dep)):
    rows = con.execute(
        "SELECT p.uri, p.text, p.created_at, sp.display_name AS author "
        "FROM posts p LEFT JOIN sandbox_personas sp ON sp.did=p.did "
        "WHERE p.namespace='sandbox' AND p.root_uri IS NULL AND p.uri LIKE 'sandbox://post/%' "
        "ORDER BY p.created_at DESC, p.rowid DESC LIMIT 200").fetchall()
    return {"posts": [dict(r) for r in rows], "me": persona["display_name"]}


class PostIn(BaseModel):
    text: str = Field(min_length=1, max_length=config.POST_MAX_CHARS)
    thread_id: int | None = None
    parent_uri: str | None = Field(default=None, max_length=200)


@router.post("/post")
def create_post(body: PostIn, persona=Depends(current_persona),
                con: sqlite3.Connection = Depends(db_dep)):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Empty post")
    root_uri = parent_uri = None
    if body.thread_id is not None:
        t = con.execute("SELECT root_uri FROM sandbox_threads WHERE id=?",
                        (body.thread_id,)).fetchone()
        if not t:
            raise HTTPException(404, "Thread not found")
        root_uri = t["root_uri"]
        parent_uri = body.parent_uri or root_uri
        if parent_uri != root_uri and not con.execute(
                "SELECT 1 FROM posts WHERE uri=? AND root_uri=?", (parent_uri, root_uri)).fetchone():
            raise HTTPException(400, "Parent post is not in this thread")
    elif body.parent_uri:
        raise HTTPException(400, "parent_uri requires thread_id")
    check_rate(persona["id"])
    uri = f"sandbox://post/{secrets.token_hex(8)}"
    created = now_iso()  # server-side timestamp only
    con.execute("INSERT INTO posts(uri, did, root_uri, parent_uri, text, created_at, lang, namespace) "
                "VALUES (?,?,?,?,?,?, 'en', 'sandbox')",
                (uri, persona["did"], root_uri, parent_uri, text, created))
    return {"uri": uri, "created_at": created, "progress": progress(con, persona["did"])}


# --- admin endpoints -------------------------------------------------------
class PairIn(BaseModel):
    pair_type: Literal["A", "B", "C", "D"]
    participant_ids: list[str] = Field(min_length=2, max_length=2)
    pair_id: str | None = Field(default=None, pattern=ID_RE)


@router.post("/admin/personas", dependencies=[Depends(require_admin)])
def admin_create_pair(body: PairIn, con: sqlite3.Connection = Depends(db_dep)):
    """Create the two personas of one pair and return one single-use code each.
    Type A = one participant with two personas; B/C/D = two different participants."""
    p1, p2 = (p.strip() for p in body.participant_ids)
    for p in (p1, p2):
        if not re.match(ID_RE, p):
            raise HTTPException(400, "participant_ids must be pseudonymous IDs like P07 (no names)")
    if body.pair_type == "A" and p1 != p2:
        raise HTTPException(400, "Type A pairs are one person: both participant_ids must match")
    if body.pair_type != "A" and p1 == p2:
        raise HTTPException(400, "Types B/C/D need two different participants")
    pair_id = body.pair_id or f"pair_{secrets.token_hex(3)}"
    if con.execute("SELECT 1 FROM sandbox_labels WHERE pair_id=?", (pair_id,)).fetchone():
        raise HTTPException(409, "pair_id already exists")
    out = []
    t = now_iso()
    con.execute("BEGIN")
    try:
        for p in (p1, p2):
            pid = secrets.token_hex(6)
            name = _unique_display_name(con)
            con.execute("INSERT INTO sandbox_personas(id, did, display_name, created_at) "
                        "VALUES (?,?,?,?)", (pid, f"sbx:{pid}", name, t))
            con.execute("INSERT INTO sandbox_labels(persona_id, participant_id, pair_id, pair_type, "
                        "created_at) VALUES (?,?,?,?,?)", (pid, p, pair_id, body.pair_type, t))
            out.append({"persona_id": pid, "participant_id": p, "display_name": name,
                        "code": _issue_code(con, pid)})
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"pair_id": pair_id, "pair_type": body.pair_type, "personas": out,
            "note": "Codes are shown once. Hand each to its participant privately."}


@router.post("/admin/personas/{persona_id}/code", dependencies=[Depends(require_admin)])
def admin_reissue_code(persona_id: str, con: sqlite3.Connection = Depends(db_dep)):
    row = con.execute("SELECT display_name FROM sandbox_personas WHERE id=?", (persona_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Persona not found")
    return {"persona_id": persona_id, "display_name": row[0], "code": _issue_code(con, persona_id)}


class ThreadIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    root_text: str = Field(min_length=1, max_length=3000)
    topic: str | None = Field(default=None, max_length=100)


@router.post("/admin/threads", dependencies=[Depends(require_admin)])
def admin_create_thread(body: ThreadIn, con: sqlite3.Connection = Depends(db_dep)):
    """Root text is copied from a real Bluesky thread - strip handles before pasting."""
    t = now_iso()
    con.execute("BEGIN")
    try:
        cur = con.execute("INSERT INTO sandbox_threads(title, topic, created_at) VALUES (?,?,?)",
                          (body.title.strip(), body.topic, t))
        tid = cur.lastrowid
        root_uri = f"sandbox://thread/{tid}"
        con.execute("UPDATE sandbox_threads SET root_uri=? WHERE id=?", (root_uri, tid))
        con.execute("INSERT INTO posts(uri, did, root_uri, parent_uri, text, created_at, lang, "
                    "namespace) VALUES (?,?,NULL,NULL,?,?, 'en', 'sandbox')",
                    (root_uri, ROOT_DID, body.root_text.strip(), t))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"id": tid, "root_uri": root_uri}


@router.delete("/admin/pairs/{pair_id}", dependencies=[Depends(require_admin)])
def admin_delete_pair(pair_id: str, con: sqlite3.Connection = Depends(db_dep)):
    """Remove a pair completely (e.g. test pairs created before real collection):
    its personas, labels, codes, sessions and the personas' own posts."""
    pids = [r[0] for r in con.execute("SELECT persona_id FROM sandbox_labels WHERE pair_id=?", (pair_id,))]
    if not pids:
        raise HTTPException(404, "Pair not found")
    marks = ",".join("?" * len(pids))
    dids = [f"sbx:{p}" for p in pids]
    con.execute("BEGIN")
    try:
        n_posts = con.execute(f"DELETE FROM posts WHERE namespace='sandbox' AND did IN ({marks})", dids).rowcount
        for table in ("sandbox_sessions", "sandbox_codes", "sandbox_labels"):
            con.execute(f"DELETE FROM {table} WHERE persona_id IN ({marks})", pids)
        con.execute(f"DELETE FROM sandbox_personas WHERE id IN ({marks})", pids)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"deleted_pair": pair_id, "personas": len(pids), "posts": n_posts}


def strip_identifiers(text: str) -> str:
    """Remove @handles and links so copied root text carries no account identifiers."""
    text = re.sub(r"@[\w.-]+", "", text)
    text = re.sub(r"https?://\S+|\b[\w-]+(\.[\w-]+)+/\S*", "", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


class ThreadFromUrlIn(BaseModel):
    url: str = Field(max_length=300)
    title: str = Field(min_length=1, max_length=200)
    topic: str | None = Field(default=None, max_length=100)


@router.post("/admin/threads_from_url", dependencies=[Depends(require_admin)])
def admin_thread_from_url(body: ThreadFromUrlIn, con: sqlite3.Connection = Depends(db_dep)):
    from .bluesky import BlueskyError, Client
    try:
        c = Client()
        root = c.get_thread(c.post_uri_from_url(body.url), depth=0)["root"]
    except (ValueError, BlueskyError) as e:
        raise HTTPException(400, str(e))
    text = strip_identifiers(root["text"])
    if not text:
        raise HTTPException(400, "Root post has no usable text after removing handles and links")
    return admin_create_thread(ThreadIn(title=body.title, root_text=text, topic=body.topic), con)


@router.get("/admin/progress", dependencies=[Depends(require_admin)])
def admin_progress(con: sqlite3.Connection = Depends(db_dep)):
    rows = con.execute(
        "SELECT p.id, p.did, p.display_name, p.consented_at, l.participant_id, l.pair_id, "
        " l.pair_type, (SELECT MIN(created_at) FROM posts WHERE did=p.did) AS first_post, "
        " (SELECT MAX(created_at) FROM posts WHERE did=p.did) AS last_post "
        "FROM sandbox_personas p LEFT JOIN sandbox_labels l ON l.persona_id=p.id "
        "ORDER BY l.pair_type, l.pair_id").fetchall()
    personas = [{**dict(r), "progress": progress(con, r["did"])} for r in rows]
    by_type: dict[str, dict] = {}
    for p in personas:
        bt = by_type.setdefault(p["pair_type"] or "?", {"personas": 0, "targets_met": 0,
                                                        "words": 0, "posts": 0})
        bt["personas"] += 1
        bt["targets_met"] += int(p["progress"]["targets_met"])
        bt["words"] += p["progress"]["words"]
        bt["posts"] += p["progress"]["replies"] + p["progress"]["feed_posts"]
    return {
        "participants": len({p["participant_id"] for p in personas if p["participant_id"]}),
        "pairs": len({p["pair_id"] for p in personas if p["pair_id"]}),
        "by_type": by_type,
        "personas": personas,
        "threads": [dict(r) for r in
                    con.execute("SELECT id, title, topic FROM sandbox_threads ORDER BY id")],
        "targets": config.SANDBOX_TARGETS,
    }
