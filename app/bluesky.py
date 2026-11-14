"""Read-only client for the public Bluesky AppView (no auth).

Verified 2026-11-14 against https://public.api.bsky.app/xrpc/:
  resolveHandle -> {did}; getPostThread -> thread (threadViewPost | notFoundPost | blockedPost)
  with nested replies; getAuthorFeed -> {feed[{post, reply?, reason?}], cursor};
  getProfile -> {..., createdAt?}; getPosts?uris=... (<=25) -> {posts}.
  searchPosts returns 403 without auth, so discovery uses public feeds (getFeed).
This module NEVER writes to Bluesky.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx

from .db import connect, init_db

log = logging.getLogger("polartrap.bluesky")

BASE = "https://public.api.bsky.app/xrpc/"
MAX_CONCURRENCY = 5
TIMEOUT = httpx.Timeout(15.0, connect=10.0)
URL_RE = re.compile(r"^https?://(?:www\.)?bsky\.app/profile/([^/?#]+)/post/([A-Za-z0-9]+)")
AT_RE = re.compile(r"^at://([^/]+)/app\.bsky\.feed\.post/([A-Za-z0-9]+)$")

TTL = {  # cache lifetimes (seconds)
    "app.bsky.feed.getPostThread": 600,
    "app.bsky.feed.getAuthorFeed": 6 * 3600,
    "app.bsky.actor.getProfile": 24 * 3600,
    "com.atproto.identity.resolveHandle": 24 * 3600,
    "app.bsky.feed.getPosts": 24 * 3600,
    "app.bsky.feed.getFeed": 600,
}


class BlueskyError(Exception):
    pass


def parse_post_url(url: str) -> tuple[str, str]:
    """Return (handle_or_did, rkey) for a bsky.app post URL or an at:// post URI."""
    url = (url or "").strip()
    m = URL_RE.match(url) or AT_RE.match(url)
    if not m:
        raise ValueError("Not a Bluesky post URL (expected https://bsky.app/profile/<handle>/post/<id>)")
    return m.group(1), m.group(2)


def did_from_at_uri(uri: str | None) -> str | None:
    if not uri or not uri.startswith("at://"):
        return None
    return uri[5:].split("/", 1)[0]


def is_english(record: dict) -> bool:
    langs = record.get("langs")
    if langs:
        return any(str(l).lower().startswith("en") for l in langs)
    text = record.get("text") or ""
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isascii() for c in letters) / len(letters) > 0.9


def _post_row(post: dict) -> dict | None:
    rec = post.get("record") or {}
    if rec.get("$type", "app.bsky.feed.post") != "app.bsky.feed.post":
        return None
    reply = rec.get("reply") or {}
    author = post.get("author") or {}
    return {
        "uri": post["uri"],
        "did": author.get("did") or did_from_at_uri(post["uri"]),
        "handle": author.get("handle"),
        "text": rec.get("text") or "",
        "created_at": rec.get("createdAt") or post.get("indexedAt"),
        "parent_uri": (reply.get("parent") or {}).get("uri"),
        "root_uri": (reply.get("root") or {}).get("uri"),
        "lang": ",".join(rec.get("langs") or []) or None,
        "english": is_english(rec),
    }


class Client:
    def __init__(self, use_cache: bool = True, offline: bool = False):
        self.http = httpx.Client(base_url=BASE, timeout=TIMEOUT,
                                 headers={"User-Agent": "PolarTrap-research/0.1 (read-only)"})
        self.sem = threading.Semaphore(MAX_CONCURRENCY)
        self.use_cache = use_cache
        self.offline = offline  # serve only from cache (frozen corpus rebuilds)
        self.requests = 0
        self.errors: list[str] = []
        init_db()

    # --- transport ------------------------------------------------------
    def _get(self, method: str, params: dict) -> dict:
        key = method + "?" + json.dumps(params, sort_keys=True)
        if self.use_cache or self.offline:
            con = connect()
            try:
                row = con.execute("SELECT body, fetched_at FROM http_cache WHERE key=?", (key,)).fetchone()
            finally:
                con.close()
            if row:
                age = time.time() - row["fetched_at"]
                if self.offline or age < TTL.get(method, 3600):
                    return json.loads(row["body"])
        if self.offline:
            raise BlueskyError(f"offline mode: {method} not cached")
        delay = 1.0
        last = None
        for _attempt in range(5):
            with self.sem:
                try:
                    self.requests += 1
                    r = self.http.get(method, params=params)
                except httpx.HTTPError as e:
                    last = str(e)
                    r = None
            if r is not None:
                if r.status_code == 200:
                    body = r.text
                    con = connect()
                    try:
                        con.execute("INSERT OR REPLACE INTO http_cache(key, body, fetched_at) VALUES (?,?,?)",
                                    (key, body, time.time()))
                    finally:
                        con.close()
                    return json.loads(body)
                if r.status_code in (400, 404):
                    try:
                        err = r.json()
                    except ValueError:
                        err = {"error": r.text[:200]}
                    raise BlueskyError(f"{method}: {err.get('error')} {err.get('message', '')}".strip())
                last = f"HTTP {r.status_code}"
                if r.status_code == 429:
                    reset = r.headers.get("ratelimit-reset")
                    if reset and reset.isdigit():
                        delay = max(delay, min(60.0, int(reset) - time.time()))
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
        raise BlueskyError(f"{method}: gave up after retries ({last})")

    # --- endpoints ------------------------------------------------------
    def resolve_handle(self, handle: str) -> str:
        if handle.startswith("did:"):
            return handle
        return self._get("com.atproto.identity.resolveHandle", {"handle": handle})["did"]

    def post_uri_from_url(self, url: str) -> str:
        actor, rkey = parse_post_url(url)
        return f"at://{self.resolve_handle(actor)}/app.bsky.feed.post/{rkey}"

    def get_thread(self, uri: str, depth: int = 1000) -> dict:
        """Return {'root': row, 'posts': [rows incl. root], 'skipped': n} for the whole reply tree.
        If uri is a reply, the tree of its root post is fetched."""
        d = self._get("app.bsky.feed.getPostThread", {"uri": uri, "depth": depth, "parentHeight": 1000})
        node = d.get("thread") or {}
        if node.get("$type") != "app.bsky.feed.defs#threadViewPost":
            raise BlueskyError("Thread not found or not visible (deleted, blocked or restricted).")
        while (node.get("parent") or {}).get("$type") == "app.bsky.feed.defs#threadViewPost":
            node = node["parent"]
        root_row = _post_row(node["post"])
        posts, skipped = [], 0
        stack = [node]
        while stack:
            n = stack.pop()
            row = _post_row(n["post"])
            if row:
                if row["uri"] != root_row["uri"]:
                    row["root_uri"] = root_row["uri"]
                posts.append(row)
            for child in n.get("replies") or []:
                if child.get("$type") == "app.bsky.feed.defs#threadViewPost":
                    stack.append(child)
                else:
                    skipped += 1
        if root_row["root_uri"]:
            # requested post's ancestors were not all visible
            root_row["root_uri"] = None
        return {"root": root_row, "posts": posts, "skipped": skipped}

    def get_profile(self, did: str) -> dict:
        return self._get("app.bsky.actor.getProfile", {"actor": did})

    def get_author_feed(self, did: str, max_posts: int = 300) -> list[dict]:
        """Own posts and replies (reposts and pinned duplicates dropped), newest first."""
        out, seen, cursor = [], set(), None
        while len(out) < max_posts:
            params = {"actor": did, "limit": 100, "filter": "posts_with_replies"}
            if cursor:
                params["cursor"] = cursor
            d = self._get("app.bsky.feed.getAuthorFeed", params)
            for item in d.get("feed", []):
                if item.get("reason"):  # repost or pin
                    continue
                p = item.get("post") or {}
                if (p.get("author") or {}).get("did") != did or p.get("uri") in seen:
                    continue
                row = _post_row(p)
                if row:
                    seen.add(row["uri"])
                    out.append(row)
            cursor = d.get("cursor")
            if not cursor or not d.get("feed"):
                break
        return out[:max_posts]

    def get_posts(self, uris: list[str]) -> list[dict]:
        rows = []
        for i in range(0, len(uris), 25):
            d = self._get("app.bsky.feed.getPosts", {"uris": uris[i:i + 25]})
            rows.extend(r for r in (_post_row(p) for p in d.get("posts", [])) if r)
        return rows

    def get_feed(self, feed_uri: str, pages: int = 3) -> list[dict]:
        out, cursor = [], None
        for _ in range(pages):
            params = {"feed": feed_uri, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            d = self._get("app.bsky.feed.getFeed", params)
            out.extend(item["post"] for item in d.get("feed", []) if "post" in item)
            cursor = d.get("cursor")
            if not cursor:
                break
        return out

    def map(self, fn, items):
        """Run fn over items with bounded concurrency; returns list of (item, result|Exception)."""
        def safe(x):
            try:
                return x, fn(x)
            except Exception as e:  # noqa: BLE001 - partial results are fine, recorded below
                self.errors.append(f"{getattr(fn, '__name__', 'call')}({x}): {e}")
                return x, e
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as ex:
            return list(ex.map(safe, items))


def account_created_at(profile: dict | None, posts: list[dict]) -> tuple[str | None, bool]:
    """(created_at, is_estimate). Falls back to oldest observed post, flagged as estimate."""
    if profile and profile.get("createdAt"):
        return profile["createdAt"], False
    times = sorted(p["created_at"] for p in posts if p.get("created_at"))
    return (times[0], True) if times else (None, True)


def parse_time(s: str) -> datetime:
    s = s.strip().replace("Z", "+00:00")
    # tolerate >6 fractional digits
    m = re.match(r"^(.*\.\d{6})\d*(.*)$", s)
    if m:
        s = m.group(1) + m.group(2)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def ts(s: str) -> float:
    return parse_time(s).timestamp()

