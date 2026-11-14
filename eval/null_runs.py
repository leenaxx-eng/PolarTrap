"""Real Bluesky data vs chance level. No precision/FPR claim is made on real data.

Runs the full pipeline on a held-out Bluesky snapshot (fetched with
`python -m app.corpus fetch --name <eval_name> ...`, NOT used to build the reference),
then on null versions of the same data:
  time-shift  each account's timestamps shifted by an independent random offset of 1-7 days
              (destroys timing coordination, keeps text and reply structure)
  re-pairing  each antagonistic pair's first account paired with a random account from a
              different thread (no shared interactions; tests how often style alone flags)
"""
from __future__ import annotations

import random
from collections import Counter

from app import config, stance
from app.corpus import dataset_from_snapshot, load_snapshot
from app.dataset import Dataset, Post
from app.pipeline import analyze_pairs, load_models

DAY = 86400.0


def shifted_copy(ds: Dataset, rng: random.Random) -> Dataset:
    offsets = {d: rng.uniform(1, 7) * DAY * rng.choice((-1, 1)) for d in ds.by_did}
    new = Dataset(ds.namespace)
    new.focus_roots = set(ds.focus_roots)
    new.history_fetched = set(ds.history_fetched)
    for p in ds.posts.values():
        new.add_post(Post(p.uri, p.did, p.text, p.t + offsets[p.did], p.parent_uri, p.root_uri))
    rel = {i.post_uri: (i.relation, i.confidence) for i in ds.interactions}
    new.build_interactions({i.a for i in ds.interactions} | {i.b for i in ds.interactions})
    for i in new.interactions:
        i.relation, i.confidence = rel.get(i.post_uri, ("unknown", None))
        if i.t_parent is not None:  # latency magnitude under the null
            i.t_reply = i.t_parent + abs(i.t_reply - i.t_parent)
    return new


def real_vs_null(snapshot: str, runs: int) -> dict:
    snap = load_snapshot(snapshot)
    ref, model = load_models()
    if ref.stats and ref.stats["snapshot"]["content_hash"] == snap["content_hash"]:
        raise SystemExit("held-out snapshot must differ from the reference corpus")
    ds, _ = dataset_from_snapshot(snap)
    ds.classify_stance(stance.classify)
    pairs = ds.antagonistic_pairs()
    real = analyze_pairs(ds, pairs, ref, model)
    rng = random.Random(config.RANDOM_SEED)

    shift_flags = []
    for _ in range(runs):
        nds = shifted_copy(ds, rng)
        shift_flags.append(sum(r["flagged"] for r in analyze_pairs(nds, pairs, ref, model)))

    thread_of = {}
    for p in ds.posts.values():
        r = p.root_uri or p.uri
        if r in ds.focus_roots:
            thread_of.setdefault(p.did, r)
    pool = [d for d in ds.history_fetched if d in thread_of]
    repair_flags = []
    for _ in range(runs):
        fake = []
        for a, b in pairs:
            cands = [d for d in pool if thread_of.get(d) != thread_of.get(a) and d not in (a, b)]
            if cands:
                fake.append(tuple(sorted((a, rng.choice(cands)))))
        repair_flags.append(sum(r["flagged"] for r in analyze_pairs(ds, fake, ref, model)))

    def stats(xs):
        xs = sorted(xs)
        return {"mean": sum(xs) / len(xs) if xs else None, "min": xs[0] if xs else None,
                "max": xs[-1] if xs else None, "runs": len(xs)}
    return {
        "snapshot": {"name": snapshot, "content_hash": snap["content_hash"], "threads": len(snap["threads"])},
        "pairs_tested": len(pairs),
        "label_distribution": dict(Counter(r["label"] for r in real)),
        "flags_real": sum(r["flagged"] for r in real),
        "flags_null_time_shift": stats(shift_flags),
        "flags_null_repairing": stats(repair_flags),
        "note": "No precision or false-positive rate is claimed on real data: we have no ground truth there.",
    }
