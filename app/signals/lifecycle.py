"""Supporting signal (context only, can never create a flag): account lifecycle."""
from __future__ import annotations

import time

from ..bluesky import ts

YOUNG_DAYS = 90


def compute(meta_a: dict | None, meta_b: dict | None, now: float | None = None) -> dict:
    now = now or time.time()
    if not meta_a or not meta_b or not meta_a.get("created_at") or not meta_b.get("created_at"):
        return {"available": False, "reason": "account creation dates not available"}
    ca, cb = ts(meta_a["created_at"]), ts(meta_b["created_at"])
    age_a, age_b = (now - ca) / 86400, (now - cb) / 86400
    return {
        "available": True,
        "creation_gap_days": round(abs(ca - cb) / 86400, 1),
        "age_days": [round(age_a), round(age_b)],
        "both_young": age_a < YOUNG_DAYS and age_b < YOUNG_DAYS,
        "estimated": bool(meta_a.get("created_at_estimated") or meta_b.get("created_at_estimated")),
        "note": ("creation date estimated from oldest observed post for at least one account"
                 if meta_a.get("created_at_estimated") or meta_b.get("created_at_estimated") else None),
    }
