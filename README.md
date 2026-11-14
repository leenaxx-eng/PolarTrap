# PolarTrap

> Bot detectors find accounts that agree too much. PolarTrap looks for accounts that disagree
> suspiciously well, and tells you when it can't tell.

**Claim:** PolarTrap detects *unusual patterns of coordinated-looking antagonistic interaction* and
shows the evidence for human review. It never states that accounts are run by the same person.

Junction 2026 · Hostinger challenge "Decode the Noise".

## Status

| Milestone | State |
|---|---|
| M1 skeleton, health, minimal sandbox, Docker + Caddy | done |
| M2 Bluesky thread import, stance, Pro/Con view | done (stance needs AI Router key) |
| M3 frozen reference corpus + overlap report | snapshot `health1` fetched (14 threads); **stance + build pending AI Router key** |
| M4 Key I, Key S, percentiles, labels, evidence cards | done |
| M5 evaluation scripts + Metrics page | done; numbers appear once sandbox data + reference exist |
| P2 explanations, supporting signals, admin view | done |
| P3 Jetstream, Key L, IRA, graph | not started |

## Pipeline

1. **Import** (read-only AppView): reply tree, then the 300 most recent posts of the 60 most interactive
   participants, then the parent posts of their replies to each other in *other* threads (cross-thread interactions).
2. **Stance** per reply A→B: `agree | disagree | neutral` from the cheap AI Router model, batched 8 per call and cached
   by content hash. Any failure gives `unknown`, which is excluded and counted. It is never treated as neutral.
3. **Antagonistic pair** = A disagreed with B at least once, anywhere.
4. **Key I: interaction pattern (one key).** Components: threads with disagreement, reply-latency regularity, turn
   alternation, and near-simultaneous posting vs. a time-shift null. Combined score = mean of the component percentiles;
   the reported percentile ranks that score among the controls' own combined scores. Needs ≥ 4 A↔B exchanges.
5. **Key S: writing style** with topic words removed. URLs, @mentions, #tags and numbers are masked. Topic words
   (top TF-IDF terms per thread vs. the corpus) and capitalised entity-like words are replaced by `T`. Five blocks:
   char 2–4-gram TF-IDF (0.4), function words (0.3), punctuation (0.1), surface stats (0.1), idiosyncrasies (0.1).
   Score = weighted mean of per-block cosines. Volume: ≥ 1,500 words per account = full, 500–1,500 = reduced, < 500 = insufficient.
6. **Reference percentiles.** Antagonistic pairs from the frozen corpus are matched on words/posts/exchanges bins and
   widened stepwise. Below 50 controls, the key is "low reference quality" and can never count as extreme.
   Extreme = ≥ 95th percentile (`app/config.py`).
7. **Labels** (`app/decide.py`): `insufficient_evidence` (no external key evaluable), `strong_signals_needs_review`
   (I extreme + S extreme with full volume), `some_signals_needs_review` (I extreme + S extreme with reduced volume,
   or S extreme with full volume while I is not extreme), `no_strong_signals_detected` (everything else; I alone gets an
   explicit note). **Flagged** for evaluation = strong or some (fixed in config before any sandbox analysis).
8. **Supporting context** (account ages, shared rare links) is shown but never changes a label. **Explanations** come
   from the stronger model, which sees only the evidence JSON and is checked for forbidden wording.

## Run locally

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # .venv/bin/pip on Linux/macOS
cp .env.example .env                               # fill in; export the variables in your shell for local runs
.venv/Scripts/python -m uvicorn app.main:app --reload
.venv/Scripts/python -m pytest -q
```

## Deploy (Hostinger VPS, Docker Compose + Caddy HTTPS)

1. Point the domain at the VPS IP (an A record; the `srvXXXXXX.hstgr.cloud` hostname also works). Open ports 80 and 443.
2. On the VPS:
   ```bash
   git clone <repo> polartrap && cd polartrap
   cp .env.example .env    # DOMAIN, ANON_HMAC_KEY, ADMIN_PASSWORD, AI_ROUTER_API_KEY, AI_MODEL_CHEAP, AI_MODEL_STRONG
   GIT_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build
   curl https://$DOMAIN/api/health
   ```
3. Redeploy after `git pull` with the same command. Data lives in the `pt_data` volume.

## Building the reference corpus (do this once the AI Router key is set, BEFORE analysing sandbox data)

Snapshots are in `data/corpus/` (gitignored: raw public posts). Only text-free statistics and the style model are committed.

```bash
export AI_ROUTER_API_KEY=... AI_MODEL_CHEAP=...
python -m app.corpus overlap --name health1     # overlap report (before stance)
python -m app.corpus build   --name health1     # stance on ~4,900 interactions (~620 LLM calls), then build
git add eval/reference && git commit -m "Freeze reference corpus health1" && git push
# on the VPS: git pull && docker compose up -d --build
```

`build` prints the overlap report and how many antagonistic pairs have an evaluable Key I / Key S. If fewer than ~50
pairs are evaluable, add threads from the same cluster (`python -m app.corpus discover ...`), fetch a new snapshot
and rebuild. Do this **before** looking at any sandbox result. The thread list is in `eval/corpus_threads_health1.txt`.

## Evaluation

Run locally on a copy of the sandbox database, so results are reproducible and tied to a commit:

```bash
docker compose cp app:/data/polartrap.db ./data/polartrap.db      # on the VPS, then copy the file to your machine
python -m eval.run_eval --real-snapshot health1_eval --out eval/results.json
git add eval/results.json && git commit -m "Pilot results" && git push   # /metrics serves the committed file
```

- Sandbox: per-type flag rates (A/B detection, C/D false flags), label distribution, confusion table, ablation
  (I only / S only / I+S rule). 95% cluster bootstrap over **participants**. Header: "Exploratory pilot — N participants, M pairs".
- Real Bluesky (`health1_eval`, 4 held-out threads): pairs tested, label distribution, and flags vs. two null versions of
  the same data (time-shifted timestamps; random cross-thread re-pairing). No precision/FPR claim.
- Evidence-quality review: team members rate evidence cards (clear / weak / misleading) on the analysis page;
  `run_eval` reports percent agreement and Cohen's κ.

## Running the sandbox

1. Admin opens `/sandbox/admin` (browser asks for `ADMIN_PASSWORD`; username can be anything).
2. **Threads:** paste root text, or import it from a Bluesky URL (handles and links are removed automatically).
   Pick topics where volunteers genuinely hold different views, so C/D pairs can take their real side.
3. **Pairs:** choose type A–D and enter pseudonymous participant IDs (`P01`…, never names). Type A uses the same ID twice.
   You get two one-time codes, shown once.
   - A: one person, two personas with opposite stances
   - B: two people who secretly agree in advance to stage the argument
   - C: two people who genuinely disagree
   - D: two people who genuinely disagree and reply fast and regularly, without coordinating
4. **Tell each participant their partner's display name** (shown with the codes) and ask them to reply to each other
   directly at least 4 times in at least 3 threads. Key I needs ≥ 4 A↔B exchanges.
5. Targets per persona: ≥ 10 replies across ≥ 3 threads, ≥ 10 feed posts, ≥ 600 words. **Anyone who can should write
   1,500+ words**: below that, Key S runs at reduced volume and the best possible label is "Some signals".
6. Participants log in at `/sandbox/login`. Two personas = two browser profiles (or normal + private window).
   Pasting is blocked; all text must be hand-written.

## Methodology decisions (locked)

1. Positive class = known coordinated antagonistic activity, not "the same operator".
2. All interaction signals together count as at most one key (Key I). The second key comes from outside the interaction (Key S).
3. Thresholds come from a frozen, unlabeled reference corpus and are committed before any sandbox analysis.
4. Small samples are reported as "Exploratory pilot (N participants)" with intervals.
5. Evidence that can't be evaluated gives "Insufficient evidence". A percentile is never a probability.

Implementation choices made on top of the spec: flagged = strong or some; reference matching hierarchy
(exact → drop posts bin → words bin only → exchanges bin only → all); block weights above; time-shift null of ±1–7 days with 20 shifts.

## Known limitations

- **Domain shift:** sandbox pairs are written live in one room, the reference is Bluesky. Sandbox personas also have far
  less text than Bluesky accounts, so their style controls are often found only after widening (matching quality is
  shown on every card). Short texts tend to look *less* similar, so this biases towards fewer flags.
- **B vs D:** with interaction evidence alone, a staged argument between two people may be indistinguishable from an intense genuine one.
- Low-text accounts are `insufficient_evidence`; English only; style-varying operators can evade Key S.
- Pandas from the original spec was dropped (unused).

## Ethics

Read-only on Bluesky. The public UI uses `acct_` + 12 hex chars of `HMAC-SHA256(ANON_HMAC_KEY, did)`. Real handles appear
only at `GET /api/admin/analysis/{id}` (admin password). Sandbox volunteers consent and are stored under pseudonymous IDs.
Explanations receive no post text or handles.
