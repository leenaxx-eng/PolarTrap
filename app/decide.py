"""Label rules (section 5.4). Pure function of the evidence; returns label + reasons."""
from __future__ import annotations

from . import config

LABEL_TEXT = {
    "insufficient_evidence": "Insufficient evidence",
    "strong_signals_needs_review": "Strong coordinated-looking pattern — needs human review",
    "some_signals_needs_review": "Some coordinated-looking signals — needs human review",
    "no_strong_signals_detected": "No strong signals detected",
}
I_ALONE_NOTE = "Interaction pattern alone can't distinguish a staged argument from an intense genuine one."


def decide(key_i: dict, key_s: dict, key_l: dict | None = None) -> dict:
    """key_i: {'insufficient_data', 'extreme', 'low_reference_quality', ...}
    key_s: {'insufficient_data', 'volume', 'extreme', 'low_reference_quality', 'reason', ...}
    key_l: optional, same shape as key_s (P3, absent in MVP)."""
    notes: list[str] = []
    externals = {"S": key_s}
    if key_l is not None:
        externals["L"] = key_l

    def evaluable(k: dict) -> bool:
        return not k.get("insufficient_data") and not k.get("low_reference_quality")

    if not any(evaluable(k) for k in externals.values()):
        missing = []
        for name, k in externals.items():
            if k.get("insufficient_data"):
                missing.append(f"{name}: {k.get('reason') or 'not enough data'}")
            elif k.get("low_reference_quality"):
                missing.append(f"{name}: fewer than {config.MIN_CONTROLS} comparable reference pairs")
        if key_l is None:
            missing.append("L (long-term schedule): not available in this version")
        return {"label": "insufficient_evidence", "label_text": LABEL_TEXT["insufficient_evidence"],
                "flagged": False, "missing": missing, "notes": notes}

    i_ext = (not key_i.get("insufficient_data") and not key_i.get("low_reference_quality")
             and bool(key_i.get("extreme")))
    ext_full = any(evaluable(k) and k.get("extreme") and k.get("volume", "full") == "full"
                   for k in externals.values())
    s_reduced = evaluable(key_s) and key_s.get("extreme") and key_s.get("volume") == "reduced"

    if i_ext and ext_full:
        label = "strong_signals_needs_review"
    elif (i_ext and s_reduced) or (ext_full and not i_ext):
        label = "some_signals_needs_review"
    else:
        label = "no_strong_signals_detected"
        if i_ext:
            notes.append(I_ALONE_NOTE)
    if key_i.get("insufficient_data"):
        notes.append(f"Interaction key not evaluated: {key_i.get('reason')}")
    elif key_i.get("low_reference_quality"):
        notes.append("Interaction key: low reference quality (too few comparable reference pairs).")
    if evaluable(key_s) and key_s.get("volume") == "reduced":
        notes.append("Writing style evaluated with reduced data volume (500–1,500 words per account).")
    return {"label": label, "label_text": LABEL_TEXT[label], "flagged": label in config.FLAG_LABELS,
            "missing": [], "notes": notes}
