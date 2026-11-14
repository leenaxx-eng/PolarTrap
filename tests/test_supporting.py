from app.bluesky import record_links
from app.dataset import Dataset, Post
from app.explain import acceptable
from app.signals import lifecycle, links


def test_record_links_from_facets_and_embed():
    rec = {"facets": [{"features": [{"$type": "app.bsky.richtext.facet#link", "uri": "https://a.org/x"}]}],
           "embed": {"$type": "app.bsky.embed.external", "external": {"uri": "https://b.org/y"}}}
    assert record_links(rec) == ["https://a.org/x", "https://b.org/y"]
    assert record_links({}) == []


def test_shared_links_excludes_thread_link_and_common_domains():
    ds = Dataset("live")
    ds.add_post(Post("root", "R", "story", 0, links=("https://news.org/story",)))
    ds.add_post(Post("a1", "A", "x", 1, links=("https://news.org/story", "https://rare.net/blog/post1")))
    ds.add_post(Post("b1", "B", "y", 2, links=("https://www.news.org/story/", "https://rare.net/blog/post1")))
    ds.add_post(Post("a2", "A", "see https://youtube.com/watch", 3))
    ds.add_post(Post("b2", "B", "see https://youtube.com/watch", 4))
    ds.add_post(Post("c1", "C", "z", 5))
    idx = links.link_index(ds)
    thread_links = links.post_links(ds.posts["root"])
    r = links.compute(ds, "A", "B", idx, thread_links)
    assert [c["link"] for c in r["shared_rare_links"]] == ["rare.net/blog/post1"]
    whys = {e["link"]: e["why"] for e in r["not_counted"]}
    assert "discussed article" in whys["news.org/story"] and "common domain" in whys["youtube.com/watch"]
    assert r["score"] > 0


def test_lifecycle_missing_and_young():
    assert lifecycle.compute(None, {"created_at": "2026-01-01T00:00:00Z"})["available"] is False
    now = 1_800_000_000
    r = lifecycle.compute({"created_at": "2027-01-01T00:00:00Z"}, {"created_at": "2027-01-11T00:00:00Z",
                                                                   "created_at_estimated": True}, now=now)
    assert r["available"] and r["creation_gap_days"] == 10.0 and r["both_young"] and r["estimated"]


def test_explanation_wording_guard():
    ok = "Both accounts argued often. This is a signal for human review, not proof."
    assert acceptable(ok)
    assert not acceptable("These look like the same person.")
    assert not acceptable("Probably a bot network.")
    assert not acceptable("")


def test_llm_adapts_parameter_dialect(monkeypatch):
    import httpx

    from app import config, llm
    monkeypatch.setattr(config, "AI_ROUTER_API_KEY", "k")
    monkeypatch.setattr(config, "AI_MODEL_CHEAP", "m")
    seen = []

    def fake_post(url, json, headers, timeout):
        seen.append(dict(json))
        if "max_tokens" in json:
            return httpx.Response(400, text="Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens'.")
        if "temperature" in json:
            return httpx.Response(400, text="Unsupported value: 'temperature' does not support 0")
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
    monkeypatch.setattr(llm.httpx, "post", fake_post)
    assert llm.chat([{"role": "user", "content": "hi"}], retries=0) == "OK"
    assert len(seen) == 3 and seen[-1]["max_completion_tokens"] == 2400 and "temperature" not in seen[-1]
