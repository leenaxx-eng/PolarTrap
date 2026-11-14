// Shared sandbox client. Page chosen by <body data-page="...">.
const $ = (s) => document.querySelector(s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const fmt = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

async function api(path, opts = {}) {
  const r = await fetch("/api/sandbox" + path, {
    credentials: "same-origin",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    ...opts,
  });
  let d = {};
  try { d = await r.json(); } catch (_) {}
  if (r.status === 401 && document.body.dataset.page !== "login") { location.href = "/sandbox/login"; throw new Error("auth"); }
  if (!r.ok) {
    const msg = Array.isArray(d.detail) ? d.detail.map((x) => x.msg).join("; ") : (d.detail || "Error " + r.status);
    throw new Error(msg);
  }
  return d;
}

function renderProgress(me) {
  const box = $("#progress"); if (!box) return;
  box.replaceChildren();
  const labels = { replies: "Replies", threads: "Threads replied in", feed_posts: "Feed posts", words: "Words total" };
  for (const k of Object.keys(labels)) {
    const v = me.progress[k], t = me.targets[k];
    const d = el("div", v >= t ? "met" : "");
    d.append(el("b", null, `${v} / ${t}`), el("div", "small muted", labels[k]));
    box.append(d);
  }
  const who = $("#who"); if (who) who.textContent = me.display_name;
  const done = $("#done"); if (done) done.hidden = !me.progress.targets_met;
}

async function refreshMe() { renderProgress(await api("/me")); }

function noPaste(textarea) {
  textarea.addEventListener("paste", (e) => { e.preventDefault(); flash("Please type your text by hand (no pasting)."); });
  textarea.addEventListener("drop", (e) => e.preventDefault());
}

function flash(msg, ok = false) {
  const f = $("#flash"); if (!f) return;
  f.textContent = msg; f.className = ok ? "small okc" : "small err";
  clearTimeout(flash._t); flash._t = setTimeout(() => (f.textContent = ""), 5000);
}

function wireLogout() {
  const b = $("#logout"); if (!b) return;
  b.addEventListener("click", async () => { await api("/logout", { method: "POST" }); location.href = "/sandbox/login"; });
}

// ---- pages -------------------------------------------------------------
const pages = {
  async login() {
    $("#f").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api("/login", { method: "POST", body: JSON.stringify({ code: $("#code").value, consent: $("#consent").checked }) });
        location.href = "/sandbox";
      } catch (err) { $("#msg").textContent = err.message; }
    });
  },

  async home() {
    wireLogout(); await refreshMe();
    const { threads } = await api("/threads");
    const list = $("#threads"); list.replaceChildren();
    if (!threads.length) list.append(el("p", "muted", "No threads yet - ask the team to add some."));
    for (const t of threads) {
      const c = el("div", "card");
      const a = el("a", null, t.title); a.href = "/sandbox/thread?id=" + t.id;
      const h = el("div"); h.append(a);
      c.append(h, el("div", "small muted", `${t.topic || ""} · ${t.replies} replies`));
      c.append(el("div", "small", t.root_text.length > 220 ? t.root_text.slice(0, 220) + "…" : t.root_text));
      list.append(c);
    }
  },

  async thread() {
    wireLogout();
    const id = new URLSearchParams(location.search).get("id");
    let parent = null;
    const ta = $("#text"); noPaste(ta);
    const setParent = (uri, author) => {
      parent = uri;
      $("#replying").textContent = uri ? `Replying to ${author}` : "Replying to the original post";
      $("#cancel").hidden = !uri;
      ta.focus();
    };
    $("#cancel").addEventListener("click", () => setParent(null));

    async function load() {
      const d = await api("/threads/" + encodeURIComponent(id));
      document.title = d.thread.title;
      $("#title").textContent = d.thread.title;
      $("#rootText").textContent = d.root.text;
      const byParent = {};
      for (const r of d.replies) (byParent[r.parent_uri] ||= []).push(r);
      const box = $("#replies"); box.replaceChildren();
      const draw = (parentUri, container, depth) => {
        for (const r of byParent[parentUri] || []) {
          const p = el("div", "post" + (r.author === d.me ? " mine" : ""));
          const meta = el("div", "meta", `${r.author || "?"}${r.author === d.me ? " (you)" : ""} · ${fmt(r.created_at)} `);
          const btn = el("button", "secondary small", "Reply"); btn.style.padding = "0 8px";
          btn.addEventListener("click", () => setParent(r.uri, r.author));
          meta.append(btn);
          p.append(meta, el("div", "text", r.text));
          container.append(p);
          draw(r.uri, depth < 6 ? p : container, depth + 1);
        }
      };
      draw(d.root.uri, box, 0);
      if (!d.replies.length) box.append(el("p", "muted", "No replies yet."));
    }

    $("#f").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = $("#send"); btn.disabled = true;
      try {
        const r = await api("/post", { method: "POST", body: JSON.stringify({ text: ta.value, thread_id: Number(id), parent_uri: parent }) });
        ta.value = ""; setParent(null); flash("Posted.", true);
        await load(); await refreshMe();
      } catch (err) { flash(err.message); } finally { btn.disabled = false; }
    });
    await load(); await refreshMe();
    setInterval(() => load().catch(() => {}), 8000);
  },

  async feed() {
    wireLogout();
    const ta = $("#text"); noPaste(ta);
    async function load() {
      const d = await api("/feed");
      const box = $("#posts"); box.replaceChildren();
      for (const p of d.posts) {
        const e = el("div", "post" + (p.author === d.me ? " mine" : ""));
        e.append(el("div", "meta", `${p.author || "?"}${p.author === d.me ? " (you)" : ""} · ${fmt(p.created_at)}`), el("div", "text", p.text));
        box.append(e);
      }
      if (!d.posts.length) box.append(el("p", "muted", "No posts yet."));
    }
    $("#f").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = $("#send"); btn.disabled = true;
      try {
        await api("/post", { method: "POST", body: JSON.stringify({ text: ta.value }) });
        ta.value = ""; flash("Posted.", true); await load(); await refreshMe();
      } catch (err) { flash(err.message); } finally { btn.disabled = false; }
    });
    await load(); await refreshMe();
    setInterval(() => load().catch(() => {}), 15000);
  },
};

pages[document.body.dataset.page]?.().catch((e) => { if (e.message !== "auth") flash(e.message); });
