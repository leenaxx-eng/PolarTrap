import sqlite3
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
  did TEXT PRIMARY KEY, handle TEXT, created_at TEXT,
  created_at_estimated INTEGER NOT NULL DEFAULT 0,
  profile_json TEXT, fetched_at TEXT, namespace TEXT NOT NULL DEFAULT 'live');

CREATE TABLE IF NOT EXISTS posts(
  uri TEXT PRIMARY KEY, did TEXT NOT NULL, root_uri TEXT, parent_uri TEXT,
  text TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, lang TEXT,
  namespace TEXT NOT NULL CHECK(namespace IN ('live','corpus','sandbox')));
CREATE INDEX IF NOT EXISTS ix_posts_did ON posts(did);
CREATE INDEX IF NOT EXISTS ix_posts_root ON posts(root_uri);
CREATE INDEX IF NOT EXISTS ix_posts_parent ON posts(parent_uri);
CREATE INDEX IF NOT EXISTS ix_posts_ns ON posts(namespace);

CREATE TABLE IF NOT EXISTS interactions(
  post_uri TEXT PRIMARY KEY, a_did TEXT NOT NULL, b_did TEXT NOT NULL,
  parent_uri TEXT, root_uri TEXT, relation TEXT, confidence REAL, model TEXT);
CREATE INDEX IF NOT EXISTS ix_inter_pair ON interactions(a_did, b_did);

CREATE TABLE IF NOT EXISTS corpus_snapshots(
  id INTEGER PRIMARY KEY, name TEXT, created_at TEXT, content_hash TEXT, meta_json TEXT);

CREATE TABLE IF NOT EXISTS analyses(
  id TEXT PRIMARY KEY, kind TEXT, input TEXT, status TEXT, created_at TEXT,
  finished_at TEXT, result_json TEXT, error TEXT);

CREATE TABLE IF NOT EXISTS feedback(
  id INTEGER PRIMARY KEY, analysis_id TEXT, pair_key TEXT, reviewer TEXT,
  rating TEXT CHECK(rating IN ('clear','weak','misleading')), note TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS sandbox_threads(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, topic TEXT, root_uri TEXT UNIQUE,
  created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS sandbox_personas(
  id TEXT PRIMARY KEY, did TEXT UNIQUE NOT NULL, display_name TEXT UNIQUE NOT NULL,
  consented_at TEXT, created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS sandbox_codes(
  code_hash TEXT PRIMARY KEY, persona_id TEXT NOT NULL REFERENCES sandbox_personas(id),
  created_at TEXT NOT NULL, used_at TEXT);

CREATE TABLE IF NOT EXISTS sandbox_sessions(
  token_hash TEXT PRIMARY KEY, persona_id TEXT NOT NULL REFERENCES sandbox_personas(id),
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL);

-- Ground-truth labels. NEVER read by detector code (only eval/ and admin).
CREATE TABLE IF NOT EXISTS sandbox_labels(
  persona_id TEXT PRIMARY KEY REFERENCES sandbox_personas(id),
  participant_id TEXT NOT NULL, pair_id TEXT NOT NULL,
  pair_type TEXT NOT NULL CHECK(pair_type IN ('A','B','C','D')), created_at TEXT NOT NULL);
"""


def connect() -> sqlite3.Connection:
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None,
                          check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def db_dep():
    """FastAPI dependency: one connection per request (autocommit mode)."""
    con = connect()
    try:
        yield con
    finally:
        con.close()


def init_db() -> None:
    con = connect()
    try:
        con.executescript(SCHEMA)
    finally:
        con.close()
