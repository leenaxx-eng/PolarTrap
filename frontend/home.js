async function start(path, body, msg) {
  msg.textContent = "Starting...";
  try {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { msg.textContent = Array.isArray(d.detail) ? d.detail.map((x) => x.msg).join("; ") : d.detail || "Error"; return; }
    location.href = "/analysis?job=" + encodeURIComponent(d.job_id);
  } catch (err) { msg.textContent = "Network error"; }
}

document.getElementById("f").addEventListener("submit", (e) => {
  e.preventDefault();
  start("/api/analyze", { thread_url: document.getElementById("url").value.trim() }, document.getElementById("msg"));
});

document.getElementById("sf").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = document.getElementById("sthread").value;
  if (v) start("/api/analyze_sandbox", { sandbox_thread_id: Number(v) }, document.getElementById("smsg"));
});

(async () => {
  const sel = document.getElementById("sthread");
  try {
    const d = await (await fetch("/api/analyses/sandbox_threads")).json();
    for (const t of d.threads) { const o = document.createElement("option"); o.value = t.id; o.textContent = t.title; sel.append(o); }
    if (!d.threads.length) document.getElementById("sf").hidden = true;
  } catch (_) { document.getElementById("sf").hidden = true; }
})();
