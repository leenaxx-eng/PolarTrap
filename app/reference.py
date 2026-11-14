"""Matched reference controls and percentiles.

Every pair is compared with antagonistic pairs from the frozen reference corpus,
matched on activity bins (min words, min posts of the two accounts) and number of
A<->B exchanges. If fewer than MIN_CONTROLS controls exist even after widening, the
key is marked low reference quality and is never treated as extreme.

A percentile says where a pair sits among comparable real arguments. It is NOT a
probability of coordination.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config

REF_DIR = config.ROOT / "eval" / "reference"
STATS_PATH = REF_DIR / "reference_stats.json"
STYLE_PATH = REF_DIR / "style_model.joblib"

EXCHANGE_EDGES = [4, 6, 10, 20]
WORD_EDGES = [500, 1500, 5000]
POST_EDGES = [20, 100, 300]
I_COMPONENTS = ("recurrence", "regularity", "alternation", "simultaneity")

# matching levels from strictest to widest: which bins must be equal
LEVELS = [("exact", ("ex", "w", "p")), ("widened: posts bin dropped", ("ex", "w")),
          ("widened: words bin only", ("w",)), ("widened: exchanges bin only", ("ex",)),
          ("widened: all reference pairs", ())]


def _bin(x: float, edges: list[float]) -> int:
    return sum(x >= e for e in edges)


def bins(exchanges: int, words_min: int, posts_min: int) -> dict:
    return {"ex": _bin(exchanges, EXCHANGE_EDGES), "w": _bin(words_min, WORD_EDGES), "p": _bin(posts_min, POST_EDGES)}


def percentile(x: float, arr: list[float]) -> float:
    """Mid-rank percentile of x within arr (0-100)."""
    if not arr:
        return float("nan")
    below = sum(v < x for v in arr)
    equal = sum(v == x for v in arr)
    return 100.0 * (below + 0.5 * equal) / len(arr)


def pair_record(i_res: dict, s_res: dict, posts_a: int, posts_b: int) -> dict:
    """Compact, text-free per-pair record (stored in the reference file and used for matching)."""
    comps = i_res["components"]
    return {
        "exchanges": i_res["exchanges"],
        "words_min": min(s_res["words_a"], s_res["words_b"]),
        "posts_min": min(posts_a, posts_b),
        "I_ok": not i_res["insufficient_data"],
        "I": {k: (comps[k]["value"] if comps[k]["available"] else None) for k in I_COMPONENTS},
        "S_ok": s_res["value"] is not None,
        "S": s_res["value"],
        "S_volume": s_res["volume"],
    }


class Reference:
    def __init__(self, stats: dict | None):
        self.stats = stats
        self.pairs = (stats or {}).get("pairs", [])
        for p in self.pairs:
            p["_bins"] = bins(p["exchanges"], p["words_min"], p["posts_min"])

    @classmethod
    def load(cls, path: Path = STATS_PATH) -> "Reference":
        if not path.exists():
            return cls(None)
        return cls(json.loads(path.read_text(encoding="utf-8")))

    @property
    def available(self) -> bool:
        return bool(self.pairs)

    def controls(self, rec: dict, key: str) -> tuple[list[dict], str]:
        ok = "I_ok" if key == "I" else "S_ok"
        pool = [p for p in self.pairs if p[ok]]
        b = bins(rec["exchanges"], rec["words_min"], rec["posts_min"])
        for name, fields in LEVELS:
            ctl = [p for p in pool if all(p["_bins"][f] == b[f] for f in fields)]
            if len(ctl) >= config.MIN_CONTROLS:
                return ctl, name
        return pool, LEVELS[-1][0]

    def score_I(self, rec: dict) -> dict:
        """Combined Key I score = mean of component percentiles among the controls; the reported
        percentile is the pair's combined score ranked among the controls' own combined scores
        (computed the same way), so it is a proper percentile, not an average of percentiles."""
        if not self.available:
            return {"percentile": None, "controls": 0, "matching": None, "low_reference_quality": True}
        ctl, level = self.controls(rec, "I")
        comps = [k for k in I_COMPONENTS if rec["I"].get(k) is not None]
        dist = {k: [c["I"][k] for c in ctl if c["I"].get(k) is not None] for k in comps}
        comp_pct = {k: percentile(rec["I"][k], dist[k]) for k in comps if dist[k]}

        def combined(r):
            vals = [percentile(r["I"][k], dist[k]) for k in comp_pct if r["I"].get(k) is not None]
            return sum(vals) / len(vals) if vals else None
        own = combined(rec)
        ctl_scores = [s for s in (combined(c) for c in ctl) if s is not None]
        pct = percentile(own, ctl_scores) if own is not None and ctl_scores else None
        low = len(ctl_scores) < config.MIN_CONTROLS
        return {"percentile": pct, "component_percentiles": comp_pct, "combined_score": own,
                "controls": len(ctl_scores), "matching": level, "low_reference_quality": low,
                "extreme": bool(pct is not None and not low and pct >= config.EXTREME_PERCENTILE)}

    def score_S(self, rec: dict) -> dict:
        if not self.available:
            return {"percentile": None, "controls": 0, "matching": None, "low_reference_quality": True}
        ctl, level = self.controls(rec, "S")
        dist = [c["S"] for c in ctl]
        pct = percentile(rec["S"], dist) if dist else None
        low = len(dist) < config.MIN_CONTROLS
        return {"percentile": pct, "controls": len(dist), "matching": level, "low_reference_quality": low,
                "extreme": bool(pct is not None and not low and pct >= config.EXTREME_PERCENTILE)}
