document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = document.getElementById("msg");
  const url = document.getElementById("url").value.trim();
  msg.textContent = "Starting...";
  try {
    const r = await fetch("/api/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thread_url: url }),
    });
    if (r.status === 404 || r.status === 405) { msg.textContent = "Thread analysis is not live yet."; return; }
    const d = await r.json();
    if (!r.ok) { msg.textContent = d.detail || "Error"; return; }
    location.href = "/analysis?job=" + encodeURIComponent(d.job_id);
  } catch (err) { msg.textContent = "Network error"; }
});
