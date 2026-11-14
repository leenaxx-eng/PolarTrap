"""Frozen reference corpus.

  python -m app.corpus discover --keywords "ai,chatgpt,llm" [--feed URI ...] [--actor HANDLE ...]
  python -m app.corpus fetch --name ai1 --threads eval/corpus_threads_ai1.txt
  python -m app.corpus overlap --name ai1
  python -m app.corpus stance --name ai1           # needs AI Router
  python -m app.corpus build --name ai1            # writes eval/reference/*

The snapshot (raw public posts, DIDs) is stored in data/corpus/<name>.json.gz, which is
gitignored. Only text-free per-pair statistics and the style model are committed.
Reference pairs are a comparison distribution, NOT confirmed negatives.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone

from . import config
from .bluesky import Client, account_created_at
from .dataset import Dataset
from .reference import REF_DIR, STATS_PATH, STYLE_PATH, pair_record
from .signals import interaction, style

CORPUS_DIR = Path(os.getenv("CORPUS_DIR", str(config.ROOT / "data" / "corpus")))
WHATS_HOT = "at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.generator/whats-hot"
HISTORY_PER_THREAD = 40


def snapshot_path(name: str):
    return CORPUS_DIR / f"{name}.json.gz"


def content_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# --- discover ---------------------------------------------------------------------
def discover(keywords: list[str], feeds: list[str], actors: list[str], min_replies: int, pages: int) -> list[dict]:
    c = Client()
    posts = []
    for f in feeds or [WHATS_HOT]:
        posts += c.get_feed(f, pages=pages)
    for a in actors:
        try:
            did = c.resolve_handle(a)
        except Exception as e:  # noqa: BLE001
            print(f"skip actor {a}: {e}", file=sys.stderr)
            continue
        cursor = None
        for _ in range(pages):
            params = {"actor": did, "limit": 100, "filter": "posts_no_replies"}
            if cursor:
                params["cursor"] = cursor
            d = c._get("app.bsky.feed.getAuthorFeed", params)
            posts += [i["post"] for i in d.get("feed", []) if not i.get("reason")]
            cursor = d.get("cursor")
            if not cursor:
                break
    kws = [k.strip().lower() for k in keywords if k.strip()]
    seen, out = set(), []
    for p in posts:
        text = (p.get("record") or {}).get("text", "")
        if p["uri"] in seen or (p.get("replyCount") or 0) < min_replies:
            continue
        if kws and not any(k in text.lower() for k in kws):
            continue
        seen.add(p["uri"])
        out.append({"uri": p["uri"], "replies": p.get("replyCount"), "text": text[:140].replace("\n", " ")})
    return sorted(out, key=lambda x: -x["replies"])


# --- fetch ------------------------------------------------------------------------
def fetch(name: str, thread_uris: list[str]) -> dict:
    c = Client()
    threads, histories, profiles = [], {}, {}
    wanted: list[str] = []
    for u in thread_uris:
        u = u.strip()
        if not u or u.startswith("#"):
            continue
        uri = c.post_uri_from_url(u) if u.startswith("http") else u
        try:
            t = c.get_thread(uri)
        except Exception as e:  # noqa: BLE001
            print(f"skip {u}: {e}", file=sys.stderr)
            continue
        threads.append({"root_uri": t["root"]["uri"], "posts": t["posts"]})
        counts: Counter = Counter()
        by_uri = {p["uri"]: p for p in t["posts"]}
        for p in t["posts"]:
            par = by_uri.get(p.get("parent_uri"))
            if par and par["did"] != p["did"]:
                counts[p["did"]] += 1
                counts[par["did"]] += 1
        wanted += [d for d, _ in counts.most_common(HISTORY_PER_THREAD) if d not in wanted]
        print(f"thread {t['root']['uri']}: {len(t['posts'])} posts, {len(counts)} interacting accounts")
    print(f"fetching histories for {len(wanted)} accounts ...")
    for d, rows in c.map(lambda d: c.get_author_feed(d, config.HISTORY_POSTS), wanted):
        if not isinstance(rows, Exception):
            histories[d] = rows
    for d, prof in c.map(c.get_profile, list(histories)):
        if not isinstance(prof, Exception):
            created, est = account_created_at(prof, histories[d])
            profiles[d] = {"created_at": created, "created_at_estimated": est}
    # parents of cross-thread replies between corpus accounts
    accounts = set(histories) | {p["did"] for t in threads for p in t["posts"]}
    known = {p["uri"] for t in threads for p in t["posts"]} | {p["uri"] for h in histories.values() for p in h}
    need = sorted({p["parent_uri"] for h in histories.values() for p in h
                   if p.get("parent_uri") and p["parent_uri"] not in known
                   and p["parent_uri"][5:].split("/", 1)[0] in accounts})
    print(f"fetching {len(need)} parent posts ...")
    parents = []
    for i in range(0, len(need), 25):
        try:
            parents += c.get_posts(need[i:i + 25])
        except Exception as e:  # noqa: BLE001
            print(f"getPosts failed: {e}", file=sys.stderr)
    content = {"threads": threads, "histories": histories, "profiles": profiles, "parents": parents}
    snap = {"name": name, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "content_hash": content_hash(content), "requests": c.requests, "errors": c.errors[:50], **content}
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(snapshot_path(name), "wt", encoding="utf-8") as f:
        json.dump(snap, f)
    from .db import connect
    con = connect()
    try:
        con.execute("INSERT INTO corpus_snapshots(name, created_at, content_hash, meta_json) VALUES (?,?,?,?)",
                    (name, snap["created_at"], snap["content_hash"],
                     json.dumps({"threads": len(threads), "histories": len(histories), "parents": len(parents)})))
    finally:
        con.close()
    print(f"snapshot {snapshot_path(name)}  hash {snap['content_hash'][:16]}  threads={len(threads)} "
          f"histories={len(histories)} requests={c.requests} errors={len(c.errors)}")
    return snap


def load_snapshot(name: str) -> dict:
    with gzip.open(snapshot_path(name), "rt", encoding="utf-8") as f:
        snap = json.load(f)
    content = {k: snap[k] for k in ("threads", "histories", "profiles", "parents")}
    if content_hash(content) != snap["content_hash"]:
        raise SystemExit("snapshot content hash mismatch - file was modified")
    return snap


def dataset_from_snapshot(snap: dict) -> tuple[Dataset, set[str]]:
    ds = Dataset("corpus")
    participants = set()
    for t in snap["threads"]:
        ds.focus_roots.add(t["root_uri"])
        for row in t["posts"]:
            if ds.add_row(row):
                participants.add(row["did"])
    for d, rows in snap["histories"].items():
        for r in rows:
            ds.add_row(r)
        ds.history_fetched.add(d)
    for r in snap["parents"]:
        ds.add_row(r)
    ds.accounts = snap["profiles"]
    accounts = participants | set(snap["histories"])
    ds.build_interactions(accounts)
    return ds, accounts


# --- overlap report ------------------------------------------------------------------------
def overlap(ds: Dataset) -> dict:
    thread_accounts = defaultdict(set)
    for p in ds.posts.values():
        r = p.root_uri or p.uri
        if r in ds.focus_roots:
            thread_accounts[p.did].add(r)
    pair_threads, antag_threads = defaultdict(set), defaultdict(set)
    for i in ds.interactions:
        k = tuple(sorted((i.a, i.b)))
        root = i.root_uri or i.parent_uri
        pair_threads[k].add(root)
        if i.relation == "disagree":
            antag_threads[k].add(root)
    pi = ds.pair_interactions()
    antag = ds.antagonistic_pairs()
    rel = Counter(i.relation for i in ds.interactions)
    return {
        "threads": len(ds.focus_roots),
        "accounts_in_threads": len(thread_accounts),
        "accounts_in_2plus_threads": sum(len(v) >= 2 for v in thread_accounts.values()),
        "interacting_pairs": len(pair_threads),
        "interacting_pairs_2plus_threads": sum(len(v) >= 2 for v in pair_threads.values()),
        "interactions": len(ds.interactions),
        "relations": dict(rel),
        "antagonistic_pairs": len(antag),
        "antagonistic_pairs_2plus_threads": sum(len(v) >= 2 for v in antag_threads.values()),
        "antagonistic_pairs_4plus_exchanges": sum(len(pi[k]) >= config.MIN_EXCHANGES for k in antag),
    }


# --- build reference -------------------------------------------------------------------------
def build(name: str, snap: dict, ds: Dataset) -> dict:
    from .pipeline import thread_texts
    REF_DIR.mkdir(parents=True, exist_ok=True)
    texts = thread_texts(ds, ds.focus_roots)
    acct_posts = {d: [p.text for p in ds.by_did[d]] for d in ds.history_fetched if ds.by_did.get(d)}
    model = style.StyleModel.fit(acct_posts, texts)
    model.save(STYLE_PATH)
    topic = model.topic_words(texts)
    pi = ds.pair_interactions()
    pairs = []
    for a, b in ds.antagonistic_pairs():
        inter = pi[(a, b)]
        i_res = interaction.compute(ds, a, b, inter)
        pa, pb = [p.text for p in ds.by_did[a]], [p.text for p in ds.by_did[b]]
        s_res = style.compute(model, pa, pb, topic)
        pairs.append(pair_record(i_res, s_res, len(pa), len(pb)))
    stats = {
        "snapshot": {"name": name, "content_hash": snap["content_hash"], "created_at": snap["created_at"]},
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {"extreme_percentile": config.EXTREME_PERCENTILE, "min_controls": config.MIN_CONTROLS,
                   "min_exchanges": config.MIN_EXCHANGES, "seed": config.RANDOM_SEED,
                   "stance_model": config.AI_MODEL_CHEAP},
        "overlap": overlap(ds),
        "summary": {"pairs": len(pairs), "I_evaluable": sum(p["I_ok"] for p in pairs),
                    "S_evaluable": sum(p["S_ok"] for p in pairs),
                    "S_full": sum(p["S_volume"] == "full" for p in pairs)},
        "pairs": pairs,
    }
    STATS_PATH.write_text(json.dumps(stats, indent=1), encoding="utf-8")
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m app.corpus")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover")
    d.add_argument("--keywords", default="")
    d.add_argument("--feed", action="append", default=[])
    d.add_argument("--actor", action="append", default=[])
    d.add_argument("--min-replies", type=int, default=20)
    d.add_argument("--pages", type=int, default=5)
    for cmd in ("fetch", "overlap", "stance", "build"):
        p = sub.add_parser(cmd)
        p.add_argument("--name", required=True)
        if cmd == "fetch":
            p.add_argument("--threads", required=True, help="file with one thread URL / at:// URI per line")
    a = ap.parse_args(argv)
    from .db import init_db
    init_db()
    if a.cmd == "discover":
        for r in discover(a.keywords.split(","), a.feed, a.actor, a.min_replies, a.pages):
            print(f"{r['replies']:>4}  {r['uri']}  {r['text']}")
        return
    if a.cmd == "fetch":
        with open(a.threads, encoding="utf-8") as f:
            fetch(a.name, f.read().splitlines())
        return
    snap = load_snapshot(a.name)
    ds, _ = dataset_from_snapshot(snap)
    if a.cmd in ("stance", "build"):
        from . import llm, stance
        if not llm.available():
            raise SystemExit("AI Router not configured (AI_ROUTER_API_KEY, AI_MODEL_CHEAP)")
        print(f"classifying {len(ds.interactions)} interactions (cached ones are free) ...")
        ds.classify_stance(stance.classify)
    if a.cmd == "build":
        stats = build(a.name, snap, ds)
        print(json.dumps({k: stats[k] for k in ("snapshot", "overlap", "summary")}, indent=1))
        return
    print(json.dumps(overlap(ds), indent=1))


if __name__ == "__main__":
    main()

