"""Evaluation -> results.json (served on /metrics). Real numbers only.

  python -m eval.run_eval [--real-snapshot health1_eval] [--null-runs 20]

Sandbox: every pair is a test pair (thresholds were fixed in app/config.py before
analysis). Uncertainty = cluster bootstrap over PARTICIPANTS (pairs sharing a person
are dependent). This module is the only detector-side code allowed to read
sandbox_labels, and it does so only after all labels have been computed.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from app import config, llm
from app.db import connect, init_db
from app.pipeline import analyze_pairs, build_sandbox, load_models

TYPES = ("A", "B", "C", "D")
POSITIVE = {"A", "B"}


def git_commit() -> str:
    if config.GIT_COMMIT != "unknown":
        return config.GIT_COMMIT
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=config.ROOT,
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


# --- statistics --------------------------------------------------------------------
def cluster_bootstrap(rows: list[dict], field: str, reps: int, seed: int) -> dict[str, dict]:
    """rows: [{'type', 'participants': [...], field: bool}]. Resample participants with
    replacement; a pair's weight = product of its distinct participants' draw counts."""
    participants = sorted({p for r in rows for p in r["participants"]})
    rng = random.Random(seed)
    samples: dict[str, list[float]] = defaultdict(list)
    for _ in range(reps):
        draw = Counter(rng.choice(participants) for _ in participants)
        num, den = defaultdict(float), defaultdict(float)
        for r in rows:
            w = 1.0
            for p in set(r["participants"]):
                w *= draw.get(p, 0)
            if w:
                num[r["type"]] += w * bool(r[field])
                den[r["type"]] += w
        for t in den:
            samples[t].append(num[t] / den[t])
    out = {}
    for t, vals in samples.items():
        vals.sort()
        out[t] = {"lo": vals[int(0.025 * (len(vals) - 1))], "hi": vals[int(0.975 * (len(vals) - 1))],
                  "reps_with_data": len(vals)}
    return out


def summarize(rows: list[dict], field: str, reps: int, seed: int) -> dict:
    ci = cluster_bootstrap(rows, field, reps, seed) if rows else {}
    out = {}
    for t in TYPES:
        sub = [r for r in rows if r["type"] == t]
        k = sum(bool(r[field]) for r in sub)
        out[t] = {"pairs": len(sub), "participants": len({p for r in sub for p in r["participants"]}),
                  "flagged": k, "rate": (k / len(sub)) if sub else None,
                  "ci95": ci.get(t) if sub else None}
    return out


def cohen_kappa(x: list[str], y: list[str]) -> float | None:
    n = len(x)
    if n == 0:
        return None
    po = sum(a == b for a, b in zip(x, y)) / n
    cx, cy = Counter(x), Counter(y)
    pe = sum(cx[k] * cy[k] for k in set(cx) | set(cy)) / (n * n)
    return None if pe == 1 else (po - pe) / (1 - pe)


# --- sandbox ----------------------------------------------------------------------------
def eval_sandbox(reps: int) -> dict:
    con = connect()
    try:
        ds, names = build_sandbox(con)
        roots = {r[0] for r in con.execute("SELECT root_uri FROM sandbox_threads")}
        # labels are read only here, after the detector inputs were built
        lab = [dict(r) for r in con.execute(
            "SELECT l.pair_id, l.pair_type, l.participant_id, p.did FROM sandbox_labels l "
            "JOIN sandbox_personas p ON p.id=l.persona_id")]
    finally:
        con.close()
    pairs = defaultdict(list)
    for r in lab:
        pairs[(r["pair_id"], r["pair_type"])].append(r)
    keys = {}
    for (pid, ptype), members in pairs.items():
        if len(members) == 2:
            keys[(pid, ptype)] = (tuple(sorted(m["did"] for m in members)), [m["participant_id"] for m in members])
    ref, model = load_models()
    antag = set(ds.antagonistic_pairs())
    results = analyze_pairs(ds, [k for k, _ in keys.values()], ref, model, topic_roots=roots)
    by_pair = {tuple(sorted((r["a"], r["b"]))): r for r in results}
    rows = []
    for (pid, ptype), (dids, parts) in sorted(keys.items()):
        r = by_pair[dids]
        is_antag = dids in antag
        i_ext = bool(r["I"].get("extreme")) and not r["I"].get("insufficient_data")
        s_ext = bool(r["S"].get("extreme")) and not r["S"].get("insufficient_data")
        rows.append({
            "pair_id": pid, "type": ptype, "participants": parts, "antagonistic": is_antag,
            "label": r["label"] if is_antag else "not_antagonistic",
            "flagged": is_antag and r["flagged"],
            "I_only": is_antag and i_ext, "S_only": is_antag and s_ext,
            "I_percentile": r["I"].get("percentile"), "S_percentile": r["S"].get("percentile"),
            "S_volume": r["S"].get("volume"), "exchanges": r["I"].get("exchanges"),
            "words": [r["S"].get("words_a"), r["S"].get("words_b")],
        })
    n_part = len({p for r in rows for p in r["participants"]})
    seed = config.RANDOM_SEED
    conf = Counter()
    for r in rows:
        conf[("positive" if r["type"] in POSITIVE else "negative", "flagged" if r["flagged"] else "not_flagged")] += 1
    rel = Counter(i.relation for i in ds.interactions)
    return {
        "header": f"Exploratory pilot — {n_part} participants, {len(rows)} pairs",
        "participants": n_part, "pairs": len(rows),
        "per_type": summarize(rows, "flagged", reps, seed),
        "label_distribution": {t: dict(Counter(r["label"] for r in rows if r["type"] == t)) for t in TYPES},
        "confusion": {f"{a}/{b}": v for (a, b), v in sorted(conf.items())},
        "ablation": {"I_only": summarize(rows, "I_only", reps, seed + 1),
                     "S_only": summarize(rows, "S_only", reps, seed + 2),
                     "I_plus_S_rule": summarize(rows, "flagged", reps, seed)},
        "stance": {"interactions": len(ds.interactions), "relations": dict(rel)},
        "pairs_detail": rows,
        "notes": ["All sandbox pairs are test pairs; thresholds were committed before analysis.",
                  "Sandbox false-flag rates (C, D) apply to the sandbox setting only (domain shift vs Bluesky).",
                  "Intervals: 95% cluster bootstrap over participants."],
    }


# --- review agreement ---------------------------------------------------------------------
def review_agreement() -> dict:
    con = connect()
    try:
        rows = con.execute("SELECT analysis_id, pair_key, reviewer, rating FROM feedback ORDER BY id").fetchall()
    finally:
        con.close()
    items: dict = defaultdict(dict)
    for r in rows:
        items[(r["analysis_id"], r["pair_key"])][r["reviewer"]] = r["rating"]  # latest wins
    multi = {k: v for k, v in items.items() if len(v) >= 2}
    reviewers = Counter(rv for v in multi.values() for rv in v)
    out = {"ratings": len(rows), "items": len(items), "items_with_2plus_reviewers": len(multi),
           "rating_distribution": dict(Counter(r["rating"] for r in rows))}
    if len(reviewers) >= 2:
        r1, r2 = [r for r, _ in reviewers.most_common(2)]
        shared = [v for v in multi.values() if r1 in v and r2 in v]
        x, y = [v[r1] for v in shared], [v[r2] for v in shared]
        out.update(pair_compared="two most active reviewers", shared_items=len(shared),
                   percent_agreement=(sum(a == b for a, b in zip(x, y)) / len(shared)) if shared else None,
                   cohen_kappa=cohen_kappa(x, y))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--real-snapshot", help="held-out Bluesky snapshot (not the reference corpus)")
    ap.add_argument("--null-runs", type=int, default=20)
    ap.add_argument("--reps", type=int, default=config.BOOTSTRAP_REPS)
    ap.add_argument("--out", default=config.RESULTS_PATH)
    a = ap.parse_args(argv)
    init_db()
    if not llm.available():
        print("WARNING: AI Router not configured - every stance is unknown, so no pair is antagonistic.")
    ref, model = load_models()
    results = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": git_commit(),
        "config": {"extreme_percentile": config.EXTREME_PERCENTILE, "min_controls": config.MIN_CONTROLS,
                   "min_exchanges": config.MIN_EXCHANGES, "style_full_words": config.STYLE_FULL_WORDS,
                   "style_reduced_words": config.STYLE_REDUCED_WORDS, "flag_labels": list(config.FLAG_LABELS),
                   "seed": config.RANDOM_SEED, "bootstrap_reps": a.reps, "stance_model": config.AI_MODEL_CHEAP},
        "reference": {"available": ref.available, "pairs": len(ref.pairs),
                      "snapshot": (ref.stats or {}).get("snapshot"), "summary": (ref.stats or {}).get("summary"),
                      "overlap": (ref.stats or {}).get("overlap")},
        "sandbox": eval_sandbox(a.reps),
        "review": review_agreement(),
        "signals_tested": ["Key I (interaction pattern, 4 components combined into one key)",
                           "Key S (writing style, 5 feature blocks)"],
        "disclaimer": "Percentiles are positions among comparable real arguments in the reference corpus, "
                      "not probabilities of coordination. Labels are signals for human review, not proof.",
    }
    if a.real_snapshot:
        from eval.null_runs import real_vs_null
        results["real"] = real_vs_null(a.real_snapshot, a.null_runs)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print(results["sandbox"]["header"])
    print(json.dumps(results["sandbox"]["per_type"], indent=1))
    print(f"written {a.out}")


if __name__ == "__main__":
    main()
