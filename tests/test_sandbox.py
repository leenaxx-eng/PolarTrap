from tests.conftest import ADMIN


def make_pair(client, ptype="C", ids=("P01", "P02")):
    r = client.post("/api/sandbox/admin/personas", auth=ADMIN,
                    json={"pair_type": ptype, "participant_ids": list(ids)})
    assert r.status_code == 200, r.text
    return r.json()


def make_thread(client):
    r = client.post("/api/sandbox/admin/threads", auth=ADMIN,
                    json={"title": "Remote work", "topic": "work", "root_text": "Offices are dead."})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def login(client, code):
    client.cookies.clear()
    return client.post("/api/sandbox/login", json={"code": code, "consent": True})


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_pages_served(client):
    for path in ["/", "/about", "/metrics", "/sandbox/login", "/sandbox", "/sandbox/thread"]:
        assert client.get(path).status_code == 200, path


def test_admin_requires_password(client):
    assert client.get("/api/sandbox/admin/progress").status_code == 401
    assert client.get("/api/sandbox/admin/progress", auth=("admin", "wrong")).status_code == 401
    assert client.get("/api/sandbox/admin/progress", auth=ADMIN).status_code == 200


def test_pair_type_validation(client):
    bad = client.post("/api/sandbox/admin/personas", auth=ADMIN,
                      json={"pair_type": "A", "participant_ids": ["P01", "P02"]})
    assert bad.status_code == 400
    bad = client.post("/api/sandbox/admin/personas", auth=ADMIN,
                      json={"pair_type": "C", "participant_ids": ["P01", "P01"]})
    assert bad.status_code == 400
    bad = client.post("/api/sandbox/admin/personas", auth=ADMIN,
                      json={"pair_type": "C", "participant_ids": ["Jane Doe", "P02"]})
    assert bad.status_code == 400
    assert make_pair(client, "A", ("P01", "P01"))["pair_type"] == "A"


def test_code_single_use_and_cookie_flags(client):
    code = make_pair(client)["personas"][0]["code"]
    r = login(client, code)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "pt_session=" in cookie and "httponly" in cookie and "secure" in cookie
    assert "samesite=strict" in cookie
    assert code not in r.text  # nothing secret echoed
    assert client.get("/api/sandbox/me").status_code == 200
    # second use fails
    assert login(client, code).status_code == 401


def test_code_normalisation(client):
    code = make_pair(client)["personas"][0]["code"]
    assert login(client, " " + code.lower().replace("-", "") + " ").status_code == 200


def test_consent_required(client):
    code = make_pair(client)["personas"][0]["code"]
    client.cookies.clear()
    r = client.post("/api/sandbox/login", json={"code": code, "consent": False})
    assert r.status_code == 400
    assert login(client, code).status_code == 200  # code not burnt by refused consent


def test_reissue_invalidates_old_code(client):
    p = make_pair(client)["personas"][0]
    r = client.post(f"/api/sandbox/admin/personas/{p['persona_id']}/code", auth=ADMIN)
    assert login(client, p["code"]).status_code == 401
    assert login(client, r.json()["code"]).status_code == 200


def test_endpoints_need_session(client):
    client.cookies.clear()
    for path in ["/api/sandbox/me", "/api/sandbox/threads", "/api/sandbox/feed"]:
        assert client.get(path).status_code == 401
    assert client.post("/api/sandbox/post", json={"text": "hi"}).status_code == 401


def test_posting_threads_and_progress(client):
    tid = make_thread(client)
    a, b = make_pair(client)["personas"]
    login(client, a["code"])
    r = client.post("/api/sandbox/post", json={"text": "I disagree, offices matter.", "thread_id": tid})
    assert r.status_code == 200
    a_uri = r.json()["uri"]
    client.post("/api/sandbox/post", json={"text": "Lovely sunny day here."})
    prog = client.get("/api/sandbox/me").json()["progress"]
    assert prog == {"replies": 1, "threads": 1, "feed_posts": 1, "words": 8, "targets_met": False}

    login(client, b["code"])
    r = client.post("/api/sandbox/post", json={"text": "No they don't.", "thread_id": tid,
                                               "parent_uri": a_uri})
    assert r.status_code == 200
    t = client.get(f"/api/sandbox/threads/{tid}").json()
    assert [x["author"] for x in t["replies"]] == [a["display_name"], b["display_name"]]
    assert t["replies"][1]["parent_uri"] == a_uri
    # feed shows feed posts only, not thread roots or replies
    feed = client.get("/api/sandbox/feed").json()["posts"]
    assert [p["text"] for p in feed] == ["Lovely sunny day here."]
    # participant-facing responses never expose pair labels
    assert "pair_type" not in client.get(f"/api/sandbox/threads/{tid}").text


def test_parent_must_be_in_thread(client):
    t1, t2 = make_thread(client), make_thread(client)
    a = make_pair(client)["personas"][0]
    login(client, a["code"])
    uri = client.post("/api/sandbox/post", json={"text": "x", "thread_id": t1}).json()["uri"]
    r = client.post("/api/sandbox/post", json={"text": "y", "thread_id": t2, "parent_uri": uri})
    assert r.status_code == 400
    r = client.post("/api/sandbox/post", json={"text": "y", "parent_uri": uri})
    assert r.status_code == 400


def test_empty_and_long_posts_rejected(client):
    a = make_pair(client)["personas"][0]
    login(client, a["code"])
    assert client.post("/api/sandbox/post", json={"text": "   "}).status_code == 400
    assert client.post("/api/sandbox/post", json={"text": "x" * 1001}).status_code == 422


def test_rate_limit(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "MIN_SECONDS_BETWEEN_POSTS", 60)
    a = make_pair(client)["personas"][0]
    login(client, a["code"])
    assert client.post("/api/sandbox/post", json={"text": "one"}).status_code == 200
    assert client.post("/api/sandbox/post", json={"text": "two"}).status_code == 429


def test_logout(client):
    a = make_pair(client)["personas"][0]
    login(client, a["code"])
    tok = client.cookies.get("pt_session")
    assert tok
    client.post("/api/sandbox/logout")
    client.cookies.clear()
    # replaying the old token must fail: the session row is deleted server-side
    assert client.get("/api/sandbox/me", headers={"Cookie": f"pt_session={tok}"}).status_code == 401


def test_admin_progress_counts(client):
    make_pair(client, "A", ("P01", "P01"))
    make_pair(client, "D", ("P02", "P03"))
    d = client.get("/api/sandbox/admin/progress", auth=ADMIN).json()
    assert d["participants"] == 3 and d["pairs"] == 2
    assert d["by_type"]["A"]["personas"] == 2 and d["by_type"]["D"]["personas"] == 2
