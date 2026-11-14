"""Neutral in-memory representation used by live threads, the reference corpus and the
sandbox, so every signal is computed by exactly the same code on all three."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .bluesky import did_from_at_uri, ts


@dataclass
class Post:
    uri: str
    did: str
    text: str
    t: float                      # unix seconds
    parent_uri: str | None = None
    root_uri: str | None = None   # None for thread roots / standalone posts
    links: tuple[str, ...] = ()


@dataclass
class Interaction:
    post_uri: str
    a: str                        # reply author
    b: str                        # parent author
    parent_uri: str
    root_uri: str | None
    t_reply: float
    t_parent: float | None
    relation: str = "unknown"
    confidence: float | None = None


@dataclass
class Dataset:
    namespace: str
    posts: dict[str, Post] = field(default_factory=dict)          # uri -> post
    by_did: dict[str, list[Post]] = field(default_factory=lambda: defaultdict(list))
    interactions: list[Interaction] = field(default_factory=list)
    accounts: dict[str, dict] = field(default_factory=dict)       # did -> meta (created_at, estimate)
    history_fetched: set[str] = field(default_factory=set)
    focus_roots: set[str] = field(default_factory=set)            # threads under analysis
    notes: list[str] = field(default_factory=list)

    def add_post(self, p: Post) -> None:
        if p.uri in self.posts:
            return
        self.posts[p.uri] = p
        self.by_did[p.did].append(p)

    def add_row(self, row: dict) -> Post | None:
        """Add a row as produced by bluesky._post_row / the DB. Non-English posts are skipped."""
        if row.get("english") is False or not row.get("created_at") or not row.get("did"):
            return None
        p = Post(uri=row["uri"], did=row["did"], text=row.get("text") or "", t=ts(row["created_at"]),
                 parent_uri=row.get("parent_uri"), root_uri=row.get("root_uri"),
                 links=tuple(row.get("links") or ()))
        self.add_post(p)
        return self.posts[p.uri]

    def parent_did(self, p: Post) -> str | None:
        if not p.parent_uri:
            return None
        parent = self.posts.get(p.parent_uri)
        return parent.did if parent else did_from_at_uri(p.parent_uri)

    def build_interactions(self, dids: set[str]) -> None:
        """Every reply from one account in `dids` to a different account in `dids`."""
        existing = {i.post_uri for i in self.interactions}
        for p in self.posts.values():
            if p.uri in existing or p.did not in dids or not p.parent_uri:
                continue
            b = self.parent_did(p)
            if not b or b == p.did or b not in dids:
                continue
            parent = self.posts.get(p.parent_uri)
            self.interactions.append(Interaction(
                post_uri=p.uri, a=p.did, b=b, parent_uri=p.parent_uri, root_uri=p.root_uri,
                t_reply=p.t, t_parent=parent.t if parent else None))

    def missing_parents(self) -> list[str]:
        return sorted({i.parent_uri for i in self.interactions if i.parent_uri not in self.posts})

    def classify_stance(self, classify_fn) -> None:
        """classify_fn([(parent_text, reply_text)]) -> [{'relation','confidence'}].
        Interactions whose parent text is unavailable stay `unknown`."""
        todo = [i for i in self.interactions if i.relation == "unknown" and i.parent_uri in self.posts]
        if not todo:
            return
        res = classify_fn([(self.posts[i.parent_uri].text, self.posts[i.post_uri].text) for i in todo])
        for i, r in zip(todo, res):
            i.relation, i.confidence = r["relation"], r.get("confidence")
            if i.t_parent is None:
                i.t_parent = self.posts[i.parent_uri].t

    def pair_interactions(self) -> dict[tuple[str, str], list[Interaction]]:
        out: dict[tuple[str, str], list[Interaction]] = defaultdict(list)
        for i in self.interactions:
            out[tuple(sorted((i.a, i.b)))].append(i)
        return out

    def antagonistic_pairs(self) -> list[tuple[str, str]]:
        return sorted(k for k, v in self.pair_interactions().items()
                      if any(i.relation == "disagree" for i in v))
