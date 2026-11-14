"""Key I - interaction pattern. All components together count as ONE key (decision 2):
two real people arguing actively can produce every one of them.

Components (higher = more coordinated-looking for every component):
  recurrence    distinct threads with an A<->B disagreement
  regularity    -std(log10 reply latency) over A<->B replies   (needs >= 3 latencies)
  alternation   share of author switches in A/B post sequences in shared threads (needs >= 3 transitions)
  simultaneity  (pairs of A/B posts within 60 s + 1) / (same under time-shift null + 1),
                direct replies between A and B excluded (they are covered by regularity)
"""
from __future__ import annotations

import bisect
import hashlib
import math
import random
from collections import defaultdict

from .. import config
from ..dataset import Dataset, Interaction

DAY = 86400.0
N_SHIFTS = 20


def _close_pairs(ta: list[float], tb_sorted: list[float], window: float) -> int:
    n = 0
    for t in ta:
        lo = bisect.bisect_left(tb_sorted, t - window)
        hi = bisect.bisect_right(tb_sorted, t + window)
        n += hi - lo
    return n


def simultaneity(ta: list[float], tb: list[float], seed_key: str,
                 window: float = config.SIMULTANEITY_WINDOW_S) -> dict:
    if len(ta) < 3 or len(tb) < 3:
        return {"value": None, "n": min(len(ta), len(tb)), "available": False,
                "reason": "fewer than 3 posts for one account"}
    tbs = sorted(tb)
    obs = _close_pairs(ta, tbs, window)
    seed = int(hashlib.sha256(f"{config.RANDOM_SEED}|{seed_key}".encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    nulls = []
    for _ in range(N_SHIFTS):
        shift = rng.uniform(1, 7) * DAY * rng.choice((-1, 1))
        nulls.append(_close_pairs(ta, sorted(t + shift for t in tbs), window))
    null_mean = sum(nulls) / len(nulls)
    return {"value": (obs + 1) / (null_mean + 1), "observed": obs, "null_mean": round(null_mean, 2),
            "n": len(ta) + len(tb), "available": True}


def compute(ds: Dataset, a: str, b: str, inter: list[Interaction]) -> dict:
    exchanges = len(inter)
    rel_counts = defaultdict(int)
    for i in inter:
        rel_counts[i.relation] += 1

    # recurrence
    disagree_roots = {i.root_uri or i.parent_uri for i in inter if i.relation == "disagree"}
    shared_roots = {i.root_uri or i.parent_uri for i in inter}
    recurrence = {"value": float(len(disagree_roots)), "n": len(shared_roots), "available": exchanges > 0}

    # regularity of latency
    lats = [max(1.0, i.t_reply - i.t_parent) for i in inter if i.t_parent is not None]
    if len(lats) >= 3:
        logs = [math.log10(x) for x in lats]
        mu = sum(logs) / len(logs)
        sd = math.sqrt(sum((x - mu) ** 2 for x in logs) / len(logs))
        med = sorted(lats)[len(lats) // 2]
        regularity = {"value": -sd, "std_log10_s": round(sd, 3), "median_latency_s": round(med, 1),
                      "n": len(lats), "available": True}
    else:
        regularity = {"value": None, "n": len(lats), "available": False,
                      "reason": "fewer than 3 timed replies"}

    # alternation in shared threads
    switches = transitions = 0
    for root in shared_roots:
        seq = sorted((p.t, p.did) for p in ds.by_did.get(a, []) + ds.by_did.get(b, [])
                     if (p.root_uri or p.uri) == root)
        for (_, d1), (_, d2) in zip(seq, seq[1:]):
            transitions += 1
            switches += d1 != d2
    if transitions >= 3:
        alternation = {"value": switches / transitions, "n": transitions, "available": True}
    else:
        alternation = {"value": None, "n": transitions, "available": False,
                       "reason": "fewer than 3 transitions in shared threads"}

    # simultaneity over full histories, excluding direct A<->B replies
    direct = {i.post_uri for i in inter}
    ta = [p.t for p in ds.by_did.get(a, []) if p.uri not in direct]
    tb = [p.t for p in ds.by_did.get(b, []) if p.uri not in direct]
    simul = simultaneity(ta, tb, "|".join(sorted((a, b))))

    sufficient = exchanges >= config.MIN_EXCHANGES
    return {
        "exchanges": exchanges,
        "relations": dict(rel_counts),
        "shared_threads": len(shared_roots),
        "insufficient_data": not sufficient,
        "reason": None if sufficient else f"{exchanges} A<->B exchanges; at least {config.MIN_EXCHANGES} needed",
        "components": {"recurrence": recurrence, "regularity": regularity,
                       "alternation": alternation, "simultaneity": simul},
    }
