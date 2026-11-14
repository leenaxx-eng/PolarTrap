"""Short plain-language explanations of a pair's evidence. The LLM receives ONLY the
evidence JSON (pseudonymous ids, numbers, labels) - never post text or handles.
Failure -> no explanation (the evidence card still shows every number)."""
from __future__ import annotations

import hashlib
import json
import re

from . import config, llm
from .db import connect

SYSTEM = ("You explain evidence; you never add facts. In 3-4 plain sentences, say why this pair got its label, "
          "name the strongest evidence and at least one limitation or counter-evidence. A percentile is a position "
          "among comparable real arguments, not a probability. Never say the accounts are run by the same person. "
          "End with: 'This is a signal for human review, not proof.'")
ENDING = "This is a signal for human review, not proof."
FORBIDDEN_RE = re.compile(r"\b(same (person|operator|individual)|sock ?puppets?|trolls?|bots?|fake accounts?)\b",
                          re.I)


def evidence_json(p: dict) -> dict:
    """Compact evidence passed to the model (public, pseudonymous fields only)."""
    def key(k):
        return {f: k.get(f) for f in ("percentile", "extreme", "insufficient_data", "reason", "controls", "matching",
                                      "low_reference_quality", "exchanges", "relations", "component_percentiles",
                                      "volume", "words_a", "words_b", "value") if f in k}
    return {"label": p["label_text"], "accounts": [p["a"], p["b"]], "key_I_interaction": key(p["I"]),
            "key_S_writing_style": key(p["S"]), "key_L": p["L"], "notes": p.get("notes"),
            "missing": p.get("missing"), "supporting_context": p.get("supporting"),
            "extreme_threshold_percentile": config.EXTREME_PERCENTILE}


def acceptable(text: str | None) -> bool:
    if not text:
        return False
    return not FORBIDDEN_RE.search(text)


def explain(p: dict) -> str | None:
    ev = json.dumps(evidence_json(p), sort_keys=True, default=str)
    model = config.AI_MODEL_STRONG or config.AI_MODEL_CHEAP
    k = hashlib.sha256(f"{model}|{ev}".encode()).hexdigest()
    con = connect()
    try:
        row = con.execute("SELECT text FROM explain_cache WHERE key=?", (k,)).fetchone()
        if row:
            return row[0]
        text = llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": ev}],
                        model=model, max_tokens=300, temperature=0.2)
        text = (text or "").strip()
        if not acceptable(text):
            return None
        if not text.endswith(ENDING):
            text = text.rstrip() + " " + ENDING
        con.execute("INSERT OR REPLACE INTO explain_cache(key, text, model, created_at) "
                    "VALUES (?,?,?,datetime('now'))", (k, text, model))
        return text
    finally:
        con.close()
