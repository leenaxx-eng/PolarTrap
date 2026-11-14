"""Supporting signal (context only, can never create a flag): shared rare links.

Links both accounts posted, weighted by inverse frequency among all accounts in the
dataset. The thread's own link(s) and very common domains are listed but not counted."""
from __future__ import annotations

import math
import re
from collections import defaultdict
from urllib.parse import urlsplit

URL_RE = re.compile(r"https?://[^\s<>\"]+")
COMMON_DOMAINS = {
    "bsky.app", "youtube.com", "youtu.be", "x.com", "twitter.com", "google.com", "wikipedia.org", "en.wikipedia.org",
    "facebook.com", "instagram.com", "tiktok.com", "reddit.com", "nytimes.com", "washingtonpost.com",
    "theguardian.com", "cnn.com", "bbc.co.uk", "bbc.com", "apnews.com", "reuters.com", "reut.rs", "npr.org",
    "giphy.com", "media.tenor.com", "tenor.com", "spotify.com", "open.spotify.com", "imgur.com", "gofundme.com",
}


def normalize(url: str) -> str | None:
    try:
        u = urlsplit(url.strip().rstrip(".,);"))
    except ValueError:
        return None
    if not u.netloc:
        return None
    host = u.netloc.lower().removeprefix("www.")
    path = u.path.rstrip("/")
    return f"{host}{path}"


def domain(norm: str) -> str:
    return norm.split("/", 1)[0]


def post_links(post) -> set[str]:
    raw = list(post.links) or URL_RE.findall(post.text or "")
    return {n for n in (normalize(x) for x in raw) if n}


def link_index(ds) -> dict[str, set[str]]:
    """normalized link -> set of accounts that posted it."""
    idx: dict[str, set[str]] = defaultdict(set)
    for p in ds.posts.values():
        for link in post_links(p):
            idx[link].add(p.did)
    return idx


def compute(ds, a: str, b: str, idx: dict[str, set[str]], thread_links: set[str]) -> dict:
    n_acc = max(2, len(ds.by_did))
    shared = sorted(l for l, accs in idx.items() if a in accs and b in accs)
    counted, excluded = [], []
    for l in shared:
        if l in thread_links:
            excluded.append({"link": l, "why": "shared link is the discussed article — not counted"})
        elif domain(l) in COMMON_DOMAINS or len(l.split("/", 1)) == 1:
            excluded.append({"link": l, "why": "common domain or bare homepage — not counted"})
        else:
            counted.append({"link": l, "accounts_posting": len(idx[l]),
                            "weight": round(math.log(n_acc / len(idx[l])), 2)})
    return {"available": True, "score": round(sum(c["weight"] for c in counted), 2),
            "shared_rare_links": counted[:10], "not_counted": excluded[:10]}
