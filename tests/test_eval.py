import json

from eval import run_eval
from eval.run_eval import cluster_bootstrap, cohen_kappa
from tests.conftest import ADMIN


def test_cluster_bootstrap_resamples_participants():
    rows = [{"type": "A", "participants": ["P1", "P1"], "f": True},
            {"type": "A", "participants": ["P2", "P2"], "f": False},
            {"type": "C", "participants": ["P3", "P4"], "f": False}]
    ci = cluster_bootstrap(rows, "f", reps=500, seed=1)
    assert 0.0 <= ci["A"]["lo"] <= ci["A"]["hi"] <= 1.0
    assert ci["A"]["lo"] == 0.0 and ci["A"]["hi"] == 1.0   # 2 participants -> very wide
    assert ci["C"]["lo"] == ci["C"]["hi"] == 0.0
    assert cluster_bootstrap(rows, "f", 50, 7) == cluster_bootstrap(rows, "f", 50, 7)  # seeded


def test_cohen_kappa():
    assert cohen_kappa(["clear", "weak"], ["clear", "weak"]) == 1.0
    assert cohen_kappa([], []) is None
    assert cohen_kappa(["clear", "clear"], ["clear", "clear"]) is None  # no variation


def test_run_eval_end_to_end(client, tmp_path, monkeypatch):
    """Sandbox -> eval with a stubbed stance classifier (no network). Without a reference
    corpus every pair must come out as insufficient evidence, never as flagged."""
    from app import stance
    monkeypatch.setattr(stance, "classify", lambda pairs, workers=4: [{"relation": "disagree", "confidence": 1.0}
                                                                       for _ in pairs])
    tid = client.post("/api/sandbox/admin/threads", auth=ADMIN,
                      json={"title": "t", "root_text": "Claim."}).json()["id"]
    pairs = [client.post("/api/sandbox/admin/personas", auth=ADMIN,
                         json={"pair_type": t, "participant_ids": ids}).json()
             for t, ids in [("A", ["P1", "P1"]), ("C", ["P2", "P3"])]]
    for pr in pairs:
        last = None
        for k in range(6):
            persona = pr["personas"][k % 2]
            client.cookies.clear()
            client.post("/api/sandbox/login", json={"code": persona["code"], "consent": True})
            r = client.post("/api/sandbox/post", json={"text": f"reply number {k} here", "thread_id": tid,
                                                       "parent_uri": last})
            assert r.status_code == 200, r.text
            last = r.json()["uri"]
            if k < 4:  # reissue so the persona can log in again next round
                code = client.post(f"/api/sandbox/admin/personas/{persona['persona_id']}/code", auth=ADMIN).json()
                persona["code"] = code["code"]
    out = tmp_path / "results.json"
    run_eval.main(["--out", str(out), "--reps", "100"])
    res = json.loads(out.read_text())
    sb = res["sandbox"]
    assert sb["header"] == "Exploratory pilot — 3 participants, 2 pairs"
    assert sb["per_type"]["A"]["pairs"] == 1 and sb["per_type"]["C"]["pairs"] == 1
    assert all(r["antagonistic"] for r in sb["pairs_detail"])
    assert all(not r["flagged"] for r in sb["pairs_detail"])
    assert {r["label"] for r in sb["pairs_detail"]} == {"insufficient_evidence"}
    assert client.get("/api/metrics").json()["available"] in (True, False)
