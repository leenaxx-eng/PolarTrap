import math

import pytest

from app import config
from app.bluesky import account_created_at, did_from_at_uri, is_english, parse_post_url, ts
from app.dataset import Dataset, Post
from app.decide import I_ALONE_NOTE, decide
from app.reference import Reference, percentile
from app.signals import interaction, style
from app.stance import parse_results


# --- URL parsing / helpers ----------------------------------------------------
def test_parse_post_url():
    assert parse_post_url("https://bsky.app/profile/alice.bsky.social/post/3kabc") == ("alice.bsky.social", "3kabc")
    assert parse_post_url("https://bsky.app/profile/did:plc:xyz/post/3kabc?x=1") == ("did:plc:xyz", "3kabc")
    assert parse_post_url("at://did:plc:xyz/app.bsky.feed.post/3kabc") == ("did:plc:xyz", "3kabc")
    for bad in ["", "https://example.com/post/1", "https://bsky.app/profile/alice", "at://did:plc:x/app.bsky.feed.like/1"]:
        with pytest.raises(ValueError):
            parse_post_url(bad)


def test_did_and_lang_helpers():
    assert did_from_at_uri("at://did:plc:abc/app.bsky.feed.post/1") == "did:plc:abc"
    assert did_from_at_uri(None) is None and did_from_at_uri("sandbox://post/1") is None
    assert is_english({"langs": ["en-US"], "text": "x"}) and not is_english({"langs": ["de"], "text": "Hallo"})
    assert is_english({"text": "plain ascii words"}) and not is_english({"text": ""})


def test_created_at_fallback():
    assert account_created_at({"createdAt": "2024-01-01T00:00:00Z"}, []) == ("2024-01-01T00:00:00Z", False)
    posts = [{"created_at": "2025-03-01T00:00:00Z"}, {"created_at": "2024-06-01T00:00:00Z"}]
    assert account_created_at({}, posts) == ("2024-06-01T00:00:00Z", True)
    assert account_created_at(None, []) == (None, True)
    assert ts("2024-01-01T00:00:00.123456789Z") == pytest.approx(1704067200.123456)


# --- stance parsing -----------------------------------------------------------
def test_stance_parse_valid_and_invalid():
    out = parse_results('{"results":[{"id":0,"relation":"disagree","confidence":0.9},'
                        '{"id":1,"relation":"Agree","confidence":"x"}]}', [0, 1, 2])
    assert out[0] == ("disagree", 0.9)
    assert out[1] == ("agree", None)
    assert out[2] == ("unknown", None)          # missing -> unknown, never neutral
    assert parse_results("I can't help with that", [0]) == {0: ("unknown", None)}
    assert parse_results(None, [0]) == {0: ("unknown", None)}
    assert parse_results('{"results":[{"id":0,"relation":"hostile"}]}', [0]) == {0: ("unknown", None)}
    assert parse_results('```json\n{"results":[{"id":0,"relation":"neutral","confidence":2}]}\n```', [0]) == \
        {0: ("neutral", 1.0)}


def test_stance_without_router_is_unknown_and_not_cached(tmp_path, monkeypatch):
    from app import stance
    from app.db import connect, init_db
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.setattr(config, "AI_ROUTER_API_KEY", "")
    init_db()
    res = stance.classify([("parent", "reply"), ("parent", "reply")])
    assert [r["relation"] for r in res] == ["unknown", "unknown"]
    con = connect()
    assert con.execute("SELECT COUNT(*) FROM stance_cache").fetchone()[0] == 0
    con.close()


# --- Key I ----------------------------------------------------------------------
def make_ds(n_ex=6, gap=30.0):
    ds = Dataset("sandbox")
    root = "r1"
    ds.add_post(Post(root, "root", "claim", 0.0))
    prev = root
    t = 100.0
    for k in range(n_ex):
        who = "A" if k % 2 == 0 else "B"
        uri = f"p{k}"
        ds.add_post(Post(uri, who, f"text {k}", t, parent_uri=prev, root_uri=root))
        prev = uri
        t += gap
    ds.build_interactions({"A", "B"})
    for i in ds.interactions:
        i.relation = "disagree"
    return ds


def test_interaction_too_few_exchanges():
    ds = make_ds(n_ex=3)  # p0 replies to root (not a pair interaction) -> 2 A<->B exchanges
    res = interaction.compute(ds, "A", "B", ds.pair_interactions()[("A", "B")])
    assert res["insufficient_data"] and "at least 4" in res["reason"]


def test_interaction_components():
    ds = make_ds(n_ex=8, gap=30.0)
    inter = ds.pair_interactions()[("A", "B")]
    res = interaction.compute(ds, "A", "B", inter)
    assert res["exchanges"] == 7 and not res["insufficient_data"]
    c = res["components"]
    assert c["recurrence"]["value"] == 1.0
    assert c["regularity"]["value"] == pytest.approx(0.0)       # identical latencies
    assert c["alternation"]["value"] == 1.0                     # strict alternation
    assert c["simultaneity"]["available"] is False              # direct replies excluded -> too few posts


def test_interaction_empty():
    ds = Dataset("live")
    res = interaction.compute(ds, "A", "B", [])
    assert res["insufficient_data"] and res["exchanges"] == 0


def test_simultaneity_deterministic_and_null():
    ta = [i * 1000.0 for i in range(20)]
    tb = [t + 10 for t in ta]
    r1 = interaction.simultaneity(ta, tb, "k")
    r2 = interaction.simultaneity(ta, tb, "k")
    assert r1 == r2 and r1["observed"] == 20 and r1["value"] > 5


# --- Key S ----------------------------------------------------------------------
TEXT_A = ("honestly i dont think thats right... like, at all!!! people keep saying stuff and "
          "nobody ever checks anything lol. ") * 3
TEXT_B = ("I would respectfully disagree; the evidence, as far as I can tell, points elsewhere. "
          "Moreover, one should consider the broader context before drawing conclusions. ") * 3


@pytest.fixture(scope="module")
def model():
    accounts = {f"u{i}": [TEXT_A if i % 2 else TEXT_B] * 8 + [f"extra post number {i} about cats"] for i in range(10)}
    threads = {"t1": "vaccines measles outbreak vaccines", "t2": "raw milk farm outbreak"}
    return style.StyleModel.fit(accounts, threads)


def test_style_volume_thresholds():
    assert style.volume(1500, 2000) == "full"
    assert style.volume(600, 5000) == "reduced"
    assert style.volume(499, 5000) == "insufficient"


def test_style_empty_and_one_post(model):
    r = style.compute(model, [], ["hello world"], set())
    assert r["insufficient_data"] and r["value"] is None and r["words_a"] == 0
    r = style.compute(model, ["just one short post"], [TEXT_A * 20], set())
    assert r["volume"] == "insufficient" and "500 words" in r["reason"]


def test_style_identical_vs_different(model):
    same = style.compute(model, [TEXT_A] * 15, [TEXT_A] * 15, set())
    diff = style.compute(model, [TEXT_A] * 15, [TEXT_B] * 15, set())
    assert same["volume"] in ("reduced", "full")
    assert same["value"] == pytest.approx(1.0, abs=1e-6)
    assert diff["value"] < same["value"] - 0.3


def test_style_no_model():
    r = style.compute(None, [TEXT_A] * 20, [TEXT_B] * 20, set())
    assert r["insufficient_data"] and "style model" in r["reason"]


def test_masking():
    m = style.mask("Check https://x.com/a and @bob.bsky.social #Vaccines 42 times. Then Kennedy said measles",
                   {"measles"})
    assert "https" not in m and "@bob" not in m and "#Vaccines" not in m and "42" not in m
    assert "Kennedy" not in m and "measles" not in m and "Check" in m and "Then" in m


# --- reference / percentiles -------------------------------------------------------------
def test_percentile_midrank():
    assert percentile(5, [1, 2, 3, 4]) == 100.0
    assert percentile(0, [1, 2, 3, 4]) == 0.0
    assert percentile(2, [1, 2, 3, 4]) == 37.5
    assert math.isnan(percentile(1, []))


def _ref_pair(ex, words, posts, s, comps):
    return {"exchanges": ex, "words_min": words, "posts_min": posts, "I_ok": ex >= 4,
            "I": comps, "S_ok": s is not None, "S": s, "S_volume": "full"}


def test_reference_low_quality_and_widening(monkeypatch):
    monkeypatch.setattr(config, "MIN_CONTROLS", 50)
    comps = {"recurrence": 1.0, "regularity": -0.5, "alternation": 0.5, "simultaneity": 1.0}
    ref = Reference({"pairs": [_ref_pair(5, 2000, 150, 0.1 * (i % 10), comps) for i in range(30)]})
    rec = _ref_pair(5, 2000, 150, 0.99, comps)
    s = ref.score_S(rec)
    assert s["controls"] == 30 and s["low_reference_quality"] and not s["extreme"]
    # 60 pairs, only 30 share the post bin -> widening drops the post bin
    pairs = [_ref_pair(5, 2000, 150 if i < 30 else 10, 0.01 * i, comps) for i in range(60)]
    ref = Reference({"pairs": pairs})
    s = ref.score_S(rec)
    assert s["controls"] == 60 and s["matching"].startswith("widened") and s["extreme"]


def test_reference_missing():
    ref = Reference(None)
    assert not ref.available and ref.score_S({"exchanges": 4, "words_min": 0, "posts_min": 0})["low_reference_quality"]


def test_combined_I_percentile():
    pairs = [_ref_pair(5, 2000, 150, 0.1, {"recurrence": float(i % 5), "regularity": -1.0 + i / 100,
                                           "alternation": i / 100, "simultaneity": 1.0}) for i in range(80)]
    ref = Reference({"pairs": pairs})
    top = _ref_pair(5, 2000, 150, 0.1, {"recurrence": 9.0, "regularity": 0.0, "alternation": 1.0, "simultaneity": 9.0})
    r = ref.score_I(top)
    assert r["percentile"] == 100.0 and r["extreme"] and r["controls"] == 80


# --- labels ----------------------------------------------------------------------
I_EXT = {"insufficient_data": False, "extreme": True, "low_reference_quality": False}
I_NO = {"insufficient_data": False, "extreme": False, "low_reference_quality": False}
I_INS = {"insufficient_data": True, "reason": "2 exchanges", "extreme": False}
S_FULL_EXT = {"insufficient_data": False, "volume": "full", "extreme": True, "low_reference_quality": False}
S_RED_EXT = {**S_FULL_EXT, "volume": "reduced"}
S_FULL_NO = {**S_FULL_EXT, "extreme": False}
S_INS = {"insufficient_data": True, "volume": "insufficient", "reason": "have 100 and 200 words"}


def test_label_insufficient_evidence():
    d = decide(I_EXT, S_INS)
    assert d["label"] == "insufficient_evidence" and not d["flagged"]
    assert any("100 and 200" in m for m in d["missing"])
    d = decide(I_EXT, {**S_FULL_EXT, "low_reference_quality": True})
    assert d["label"] == "insufficient_evidence"


def test_label_strong_and_some():
    assert decide(I_EXT, S_FULL_EXT)["label"] == "strong_signals_needs_review"
    assert decide(I_EXT, S_RED_EXT)["label"] == "some_signals_needs_review"
    assert decide(I_NO, S_FULL_EXT)["label"] == "some_signals_needs_review"
    assert decide(I_INS, S_FULL_EXT)["label"] == "some_signals_needs_review"
    assert decide(I_EXT, S_FULL_EXT)["flagged"] and decide(I_NO, S_FULL_EXT)["flagged"]


def test_label_no_strong_and_I_alone_note():
    d = decide(I_EXT, S_FULL_NO)
    assert d["label"] == "no_strong_signals_detected" and I_ALONE_NOTE in d["notes"] and not d["flagged"]
    d = decide(I_NO, S_RED_EXT)
    assert d["label"] == "no_strong_signals_detected" and I_ALONE_NOTE not in d["notes"]
    d = decide({**I_EXT, "low_reference_quality": True}, S_FULL_NO)
    assert d["label"] == "no_strong_signals_detected" and I_ALONE_NOTE not in d["notes"]
