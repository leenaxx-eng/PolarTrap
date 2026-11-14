"""Builds datasets (live Bluesky thread / sandbox) and analyses antagonistic pairs.
Detector code: must never read `sandbox_labels`."""
from __future__ import annotations

import logging
import os
import sqlite3
from collections import Counter, defaultdict

from . import config, llm, stance
from .anonymize import anon_id
from .bluesky import Client, account_created_at
from .dataset import Dataset
from .decide import decide
from .reference import STATS_PATH, STYLE_PATH, Reference, pair_record
from .sandbox import ROOT_DID
from .signals import interaction, style

log = logging.getLogger("polartrap.pipeline")

LABEL_ORDER = {"strong_signals_needs_review": 0, "some_signals_needs_review": 1,
               "no_strong_signals_detected": 2, "insufficient_evidence": 3}
_models: dict = {}


def load_models() -> tuple[Reference, style.StyleModel | None]:
    """Reference stats + style model, reloaded when the files change."""
    key = tuple(os.path.getmtime(p) if p.exists() else 0 for p in (STATS_PATH, STYLE_PATH))
    if _models.get("key") != key:
        ref = Reference.load()
        model = style.StyleModel.load(STYLE_PATH) if STYLE_PATH.exists() else None
        _models.update(key=key, ref=ref, model=model)
    return _models["ref"], _models["model"]


# --- dataset builders ----------------------------------------------------------
def build_live(url: str, client: Client | None = None, progress=lambda msg: None) -> tuple[Dataset, dict]:
    c = client or Client()
    progress("Resolving thread")
    t = c.get_thread(c.post_uri_from_url(url))
    ds = Dataset("live")
    root = t["root"]["uri"]
    ds.focus_roots.add(root)
    handles = {}
    for row in t["posts"]:
        handles[row["did"]] = row.get("handle")
        ds.add_row(row)
    non_english = len(t["posts"]) - len(ds.posts)
    participants = {p.did for p in ds.posts.values()}
    ds.build_interactions(participants)
    progress(f"Classifying stance of {len(ds.interactions)} in-thread replies")
    ds.classify_stance(stance.classify)

    counts: Counter = Counter()
    for i in ds.interactions:
        counts[i.a] += 1
        counts[i.b] += 1
    chosen = [d for d, _ in counts.most_common(config.MAX_HISTORY_ACCOUNTS)]
    if len(counts) > len(chosen):
        ds.notes.append(f"Histories fetched for the {len(chosen)} most interactive of {len(counts)} "
                        "interacting accounts; others are evaluated on thread text only.")
    progress(f"Fetching post histories for {len(chosen)} accounts")
    feeds = dict(c.map(lambda d: c.get_author_feed(d, config.HISTORY_POSTS), chosen))
    profiles = dict(c.map(c.get_profile, chosen))
    for d in chosen:
        rows = feeds.get(d)
        if isinstance(rows, Exception) or rows is None:
            continue
        for r in rows:
            handles.setdefault(r["did"], r.get("handle"))
            ds.add_row(r)
        ds.history_fetched.add(d)
        prof = profiles.get(d)
        prof = None if isinstance(prof, Exception) else prof
        created, est = account_created_at(prof, rows)
        ds.accounts[d] = {"created_at": created, "created_at_estimated": est,
                          "handle": (prof or {}).get("handle") or handles.get(d)}
    ds.build_interactions(participants)  # adds cross-thread interactions from histories
    missing = ds.missing_parents()
    if missing:
        progress(f"Fetching {len(missing)} parent posts of cross-thread replies")
        try:
            for r in c.get_posts(missing):
                ds.add_row(r)
        except Exception as e:  # noqa: BLE001 - partial results with notice
            c.errors.append(f"getPosts: {e}")
    progress("Classifying stance of cross-thread replies")
    ds.classify_stance(stance.classify)
    if c.errors:
        ds.notes.append(f"{len(c.errors)} Bluesky requests failed; results may be partial.")
    meta = {"root_uri": root, "root_text": t["root"]["text"], "root_did": t["root"]["did"],
            "thread_posts": len(t["posts"]), "non_english_skipped": non_english,
            "hidden_replies": t["skipped"], "participants": sorted(participants), "handles": handles,
            "bluesky_requests": c.requests, "bluesky_errors": c.errors[:20]}
    _persist_live(ds, handles)
    return ds, meta


def _persist_live(ds: Dataset, handles: dict) -> None:
    from .db import connect
    from .sandbox import now_iso
    con = connect()
    try:
        con.execute("BEGIN")
        for d, h in handles.items():
            acc = ds.accounts.get(d, {})
            con.execute("INSERT INTO accounts(did, handle, created_at, created_at_estimated, fetched_at, namespace) "
                        "VALUES (?,?,?,?,?,'live') ON CONFLICT(did) DO UPDATE SET handle=excluded.handle, "
                        "created_at=COALESCE(excluded.created_at, accounts.created_at), fetched_at=excluded.fetched_at",
                        (d, h, acc.get("created_at"), int(bool(acc.get("created_at_estimated", True))), now_iso()))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def build_sandbox(con: sqlite3.Connection) -> tuple[Dataset, dict[str, str]]:
    """All sandbox posts. Returns dataset and did -> persona display name."""
    ds = Dataset("sandbox")
    for r in con.execute("SELECT uri, did, root_uri, parent_uri, text, created_at FROM posts "
                         "WHERE namespace='sandbox' ORDER BY created_at"):
        ds.add_row({**dict(r), "english": True})
    names = {r["did"]: r["display_name"] for r in con.execute("SELECT did, display_name FROM sandbox_personas")}
    personas = set(names)
    ds.history_fetched |= personas
    # replies to thread roots are classified too (used only for the display-only Pro/Con position)
    ds.build_interactions(personas | {ROOT_DID})
    ds.classify_stance(stance.classify)
    return ds, names


# --- analysis ------------------------------------------------------------------------
def thread_texts(ds: Dataset, roots: set[str]) -> dict[str, str]:
    docs: dict[str, list[str]] = defaultdict(list)
    for p in ds.posts.values():
        r = p.root_uri or p.uri
        if r in roots:
            docs[r].append(p.text)
    return {r: "\n".join(v) for r, v in docs.items()}


def analyze_pair(ds: Dataset, a: str, b: str, inter, ref: Reference, model, topic: set[str]) -> dict:
    i_res = interaction.compute(ds, a, b, inter)
    posts_a = [p.text for p in ds.by_did.get(a, [])]
    posts_b = [p.text for p in ds.by_did.get(b, [])]
    s_res = style.compute(model, posts_a, posts_b, topic)
    if s_res["insufficient_data"] and ds.namespace == "live":
        unfetched = [x for x in (a, b) if x not in ds.history_fetched]
        if unfetched:
            s_res["reason"] = (s_res["reason"] or "") + " - post history not fetched for one or both accounts"
    rec = pair_record(i_res, s_res, len(posts_a), len(posts_b))
    none = {"percentile": None, "controls": 0, "matching": None, "low_reference_quality": False, "extreme": False}
    key_i = {**i_res, **(ref.score_I(rec) if not i_res["insufficient_data"] else none)}
    key_s = {**s_res, **(ref.score_S(rec) if s_res["value"] is not None else none)}
    if not ref.available:
        key_i["reference_missing"] = key_s["reference_missing"] = True
    dec = decide(key_i, key_s)
    return {"a": a, "b": b, "record": rec, "I": key_i, "S": key_s,
            "L": {"available": False, "reason": "Long-term schedule (Key L) is not part of this version."},
            **dec}


def analyze_pairs(ds: Dataset, pairs: list[tuple[str, str]], ref: Reference | None = None,
                  model=None, topic_roots: set[str] | None = None) -> list[dict]:
    if ref is None:
        ref, model = load_models()
    pi = ds.pair_interactions()
    roots = set(topic_roots or ds.focus_roots)
    for a, b in pairs:
        roots |= {i.root_uri or i.parent_uri for i in pi.get((a, b), [])}
    topic = model.topic_words(thread_texts(ds, roots)) if model else set()
    out = [analyze_pair(ds, a, b, pi.get((a, b), []), ref, model, topic) for a, b in pairs]
    out.sort(key=lambda r: (LABEL_ORDER[r["label"]], -(r["I"].get("exchanges") or 0)))
    return out


def positions(ds: Dataset, root_uri: str, root_did: str | None) -> dict[str, str]:
    """Display-only position relative to the root claim, propagated along in-thread replies."""
    pos: dict[str, str] = {}
    if root_did:
        pos[root_did] = "pro"
    inter = sorted((i for i in ds.interactions if (i.root_uri or i.parent_uri) == root_uri
                    and i.relation in ("agree", "disagree")), key=lambda i: i.t_reply)
    for _ in range(4):
        votes: dict[str, Counter] = defaultdict(Counter)
        for i in inter:
            pb = pos.get(i.b)
            if pb in ("pro", "con"):
                same = i.relation == "agree"
                votes[i.a][pb if same else ("con" if pb == "pro" else "pro")] += 1
        new = dict(pos)
        for d, v in votes.items():
            if d == root_did:
                continue
            new[d] = "mixed" if v["pro"] == v["con"] else ("pro" if v["pro"] > v["con"] else "con")
        if new == pos:
            break
        pos = new
    return pos


def public_report(ds: Dataset, pair_results: list[dict], meta: dict, names: dict[str, str] | None = None) -> dict:
    """Anonymised result for the public UI. Sandbox personas keep their (already pseudonymous) names."""
    def pid(did: str) -> str:
        if did == ROOT_DID:
            return "original post"
        return names[did] if names and did in names else anon_id(did)

    root = meta["root_uri"]
    pos = positions(ds, root, meta.get("root_did"))
    thread_posts = [p for p in ds.posts.values() if (p.root_uri or p.uri) == root]
    in_thread = Counter(p.did for p in thread_posts)
    thread_inter = [i for i in ds.interactions if (i.root_uri or i.parent_uri) == root]
    rel = Counter(i.relation for i in ds.interactions)
    pairs = []
    for r in pair_results:
        pr = {k: v for k, v in r.items() if k not in ("a", "b", "record")}
        pr["a"], pr["b"] = pid(r["a"]), pid(r["b"])
        pr["pair_key"] = "|".join(sorted((pr["a"], pr["b"])))
        pr["positions"] = {pr["a"]: pos.get(r["a"], "unknown"), pr["b"]: pos.get(r["b"], "unknown")}
        pairs.append(pr)
    ref, _ = load_models()
    banners = []
    if not llm.available():
        banners.append("AI Router is not configured: every stance is 'unknown', so no antagonistic pairs can be found.")
    elif rel.get("unknown"):
        banners.append(f"{rel['unknown']} replies could not be classified (AI Router failure, timeout or missing "
                       "parent text). They are excluded, not treated as neutral.")
    if not ref.available:
        banners.append("Reference corpus not built yet: no key can be compared with real arguments, so every pair "
                       "is 'Insufficient evidence'.")
    return {
        "namespace": ds.namespace,
        "root": {"text": meta.get("root_text"), "author": pid(meta["root_did"]) if meta.get("root_did") else None},
        "summary": {
            "comments": max(0, len(thread_posts) - 1),
            "accounts": len(in_thread),
            "interactions": len(ds.interactions),
            "in_thread_interactions": len(thread_inter),
            "relations": dict(rel),
            "unknown": rel.get("unknown", 0),
            "antagonistic_pairs": len(pair_results),
            "flagged_pairs": sum(r["flagged"] for r in pair_results),
            "labels": dict(Counter(r["label"] for r in pair_results)),
            "non_english_skipped": meta.get("non_english_skipped", 0),
            "hidden_replies": meta.get("hidden_replies", 0),
            "history_accounts": len(ds.history_fetched),
            "reference": {"available": ref.available, "pairs": len(ref.pairs),
                          "snapshot": (ref.stats or {}).get("snapshot")},
        },
        "participants": sorted(({"id": pid(d), "position": pos.get(d, "unknown"), "posts_in_thread": n}
                                for d, n in in_thread.items()), key=lambda x: -x["posts_in_thread"]),
        "pairs": pairs,
        "banners": banners,
        "notes": ds.notes,
        "thresholds": {"extreme_percentile": config.EXTREME_PERCENTILE, "min_controls": config.MIN_CONTROLS,
                       "min_exchanges": config.MIN_EXCHANGES, "style_full_words": config.STYLE_FULL_WORDS,
                       "style_reduced_words": config.STYLE_REDUCED_WORDS},
        "commit": config.GIT_COMMIT,
    }


def run_live(url: str, progress=lambda m: None) -> tuple[dict, dict]:
    ds, meta = build_live(url, progress=progress)
    participants = set(meta["participants"])
    pairs = [p for p in ds.antagonistic_pairs() if p[0] in participants and p[1] in participants]
    progress(f"Scoring {len(pairs)} antagonistic pairs")
    results = analyze_pairs(ds, pairs)
    report = public_report(ds, results, meta)
    admin = {"handles": {anon_id(d): h for d, h in meta["handles"].items() if h},
             "bluesky_errors": meta["bluesky_errors"], "bluesky_requests": meta["bluesky_requests"]}
    return report, admin


def run_sandbox(con: sqlite3.Connection, thread_id: int, progress=lambda m: None) -> tuple[dict, dict]:
    t = con.execute("SELECT * FROM sandbox_threads WHERE id=?", (thread_id,)).fetchone()
    if not t:
        raise ValueError("Sandbox thread not found")
    progress("Loading sandbox posts and classifying stance")
    ds, names = build_sandbox(con)
    root = t["root_uri"]
    ds.focus_roots.add(root)
    in_thread = {p.did for p in ds.posts.values() if p.root_uri == root and p.did in names}
    pairs = [p for p in ds.antagonistic_pairs() if p[0] in in_thread and p[1] in in_thread]
    progress(f"Scoring {len(pairs)} antagonistic pairs")
    results = analyze_pairs(ds, pairs)
    meta = {"root_uri": root, "root_text": ds.posts[root].text if root in ds.posts else "", "root_did": ROOT_DID}
    return public_report(ds, results, meta, names), {}
