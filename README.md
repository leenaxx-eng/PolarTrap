# PolarTrap

> Bot detectors find accounts that agree too much. PolarTrap looks for accounts that disagree
> suspiciously well, and tells you when it can't tell.

**Claim:** PolarTrap detects *unusual patterns of coordinated-looking antagonistic interaction* and
shows the evidence for human review. It never states that accounts are run by the same person.

Junction 2026 · Hostinger challenge "Decode the Noise".

## Status

| Milestone | State |
|---|---|
| M1 skeleton, health, home, minimal sandbox, Docker + Caddy | done |
| M2 Bluesky thread import, stance, Pro/Con view | next |
| M3 frozen reference corpus + overlap report | - |
| M4 Key I, Key S, percentiles, labels, evidence cards | - |
| M5 evaluation + Metrics page | - |

## Run locally

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows; use .venv/bin/pip on Linux/macOS
export ADMIN_PASSWORD=dev ANON_HMAC_KEY=dev
.venv/Scripts/python -m uvicorn app.main:app --reload
.venv/Scripts/python -m pytest -q
```

Open http://localhost:8000. Browsers accept the `Secure` session cookie on `http://localhost`.
Set `COOKIE_SECURE=0` only for plain-http testing on another hostname.

## Deploy (Hostinger VPS, Docker Compose + Caddy HTTPS)

1. Point the domain at the VPS IP (A record). The VPS hostname `srvXXXXXX.hstgr.cloud` also works.
   Open ports 80 and 443 in the VPS firewall.
2. On the VPS:
   ```bash
   git clone <repo> polartrap && cd polartrap
   cp .env.example .env    # set DOMAIN, ANON_HMAC_KEY, ADMIN_PASSWORD (long random values)
   GIT_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build
   curl https://$DOMAIN/api/health
   ```
3. Redeploy after a pull: the same `docker compose up -d --build` command. Data lives in the `pt_data` volume.
   Back it up with `docker compose cp app:/data/polartrap.db ./backup.db`.

## Running the sandbox

1. Admin opens `/sandbox/admin` (browser asks for `ADMIN_PASSWORD`; the username can be anything).
2. **Add threads:** paste root-post text copied from real Bluesky threads, **with handles and names removed**.
3. **Create pairs:** choose type A–D and enter pseudonymous participant IDs (`P01`, `P02`…, never names).
   Type A uses the same ID twice. You get two one-time codes, shown once. Hand them out privately.
   - A: one person, two personas with opposite stances
   - B: two people who secretly agree in advance to stage the argument
   - C: two people who genuinely disagree
   - D: two people who genuinely disagree and reply fast and regularly, without coordinating
4. Participants go to `/sandbox/login`, enter the code and tick consent. Someone running two personas uses two
   browser profiles (or one normal and one private window).
5. Targets per persona: at least 10 replies across at least 3 threads, at least 10 feed posts, 600+ words in total.
   Text must be hand-written; pasting is blocked in the UI.
6. Progress: `/sandbox/admin`, or `GET /api/sandbox/admin/progress` (JSON).

Lost session? Re-issue a code for the persona on the admin page; the old unused code becomes invalid.

**Pair labels** are stored in `sandbox_labels`. Only admin endpoints and `eval/` read that table.

## Locked methodology decisions

1. Positive class = known coordinated antagonistic activity, not "the same operator".
2. All interaction-derived signals count together as at most one key (Key I). A second key must come from outside the
   interaction (writing style, Key S; long-term schedule, Key L).
3. Thresholds come from a frozen, unlabeled reference corpus and are committed in `app/config.py` before any sandbox
   analysis. The sandbox is used only for testing.
4. Small-sample results are reported as an "Exploratory pilot (N participants)", with uncertainty.
5. When evidence can't be evaluated, the answer is "Insufficient evidence". A percentile is never a probability.

## Ethics

Read-only on Bluesky. Public UI uses `acct_` + 12 hex chars of `HMAC-SHA256(ANON_HMAC_KEY, did)`. Sandbox
volunteers consent and are stored only under pseudonymous IDs. See `/about` for limitations.
