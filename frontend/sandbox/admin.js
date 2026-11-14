const $ = (s) => document.querySelector(s);

async function call(path, body) {
  const r = await fetch("/api/sandbox/admin" + path, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(Array.isArray(d.detail) ? d.detail.map((x) => x.msg).join("; ") : d.detail || r.status);
  return d;
}

$("#pairForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const d = await call("/personas", { pair_type: $("#ptype").value, participant_ids: [$("#p1").value, $("#p2").value] });
    $("#pairOut").textContent = `pair ${d.pair_id} (type ${d.pair_type}) - codes shown once:\n` +
      d.personas.map((p) => `${p.participant_id}  ${p.display_name}  code: ${p.code}  (persona_id ${p.persona_id})`).join("\n");
    load();
  } catch (err) { $("#pairOut").textContent = "Error: " + err.message; }
});

$("#threadForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const d = await call("/threads", { title: $("#ttitle").value, topic: $("#ttopic").value || null, root_text: $("#troot").value });
    $("#threadOut").textContent = "Added thread " + d.id; e.target.reset(); load();
  } catch (err) { $("#threadOut").textContent = "Error: " + err.message; }
});

$("#urlForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const d = await call("/threads_from_url", { url: $("#uurl").value.trim(), title: $("#utitle").value, topic: $("#utopic").value || null });
    $("#urlOut").textContent = "Added thread " + d.id; e.target.reset(); load();
  } catch (err) { $("#urlOut").textContent = "Error: " + err.message; }
});

$("#codeForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const d = await call("/personas/" + encodeURIComponent($("#pid").value.trim()) + "/code", {});
    $("#codeOut").textContent = `${d.display_name}: ${d.code}`;
  } catch (err) { $("#codeOut").textContent = "Error: " + err.message; }
});

async function load() {
  const d = await call("/progress");
  $("#summary").textContent = `${d.participants} participants, ${d.pairs} pairs, ${d.threads.length} threads. ` +
    Object.entries(d.by_type).map(([t, v]) => `${t}: ${v.personas} personas, ${v.targets_met} met targets, ${v.words} words`).join(" | ");
  const t = $("#ptable"); t.replaceChildren();
  const head = ["type", "pair", "participant", "persona", "display name", "replies", "threads", "feed", "words", "done", "last post"];
  const tr = document.createElement("tr");
  head.forEach((h) => { const th = document.createElement("th"); th.textContent = h; tr.append(th); });
  t.append(tr);
  for (const p of d.personas) {
    const r = document.createElement("tr");
    [p.pair_type, p.pair_id, p.participant_id, p.id, p.display_name, p.progress.replies, p.progress.threads,
     p.progress.feed_posts, p.progress.words, p.progress.targets_met ? "yes" : "", p.last_post || ""]
      .forEach((v) => { const td = document.createElement("td"); td.textContent = v ?? ""; r.append(td); });
    const td = document.createElement("td");
    if (p.pair_id) {
      const b = document.createElement("button"); b.className = "secondary small"; b.textContent = "delete pair";
      b.addEventListener("click", async () => {
        if (!confirm(`Delete pair ${p.pair_id} (both personas, their codes and ALL their posts)? This cannot be undone.`)) return;
        const r2 = await fetch("/api/sandbox/admin/pairs/" + encodeURIComponent(p.pair_id), { method: "DELETE" });
        if (!r2.ok) alert("Delete failed: " + r2.status);
        load();
      });
      td.append(b);
    }
    r.append(td);
    t.append(r);
  }
}
$("#reload").addEventListener("click", load);
load().catch((e) => ($("#summary").textContent = "Error: " + e.message));
