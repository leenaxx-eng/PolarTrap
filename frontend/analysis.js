const $ = (s) => document.querySelector(s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const jobId = new URLSearchParams(location.search).get("job");
const BADGE = { strong_signals_needs_review: "strong", some_signals_needs_review: "some",
  no_strong_signals_detected: "none", insufficient_evidence: "insufficient" };
const COMP_NAMES = { recurrence: "Threads with disagreement", regularity: "Reply-latency regularity",
  alternation: "Turn alternation", simultaneity: "Near-simultaneous posting (vs time-shift null)" };
const fmtPct = (p) => (p == null || Number.isNaN(p) ? "–" : `${Math.round(p)}th pct`);
const fmtNum = (v, d = 2) => (v == null ? "–" : Number(v).toFixed(d));

async function poll() {
  if (!jobId) { $("#status").textContent = "No analysis selected. Start one from the home page."; return; }
  const r = await fetch("/api/analysis/" + encodeURIComponent(jobId));
  if (!r.ok) { $("#status").textContent = "Analysis not found."; return; }
  const d = await r.json();
  if (d.status === "done") { $("#status").hidden = true; render(d.result); return; }
  if (d.status === "error") { $("#status").textContent = "Analysis failed: " + d.error; return; }
  $("#status").textContent = `${d.status === "queued" ? "Queued" : "Running"}: ${d.progress || ""}…`;
  setTimeout(poll, 2000);
}

function kv(table, k, v) { const tr = el("tr"); tr.append(el("td", null, k), el("td", null, v)); table.append(tr); }

function keyBox(title, key, kind) {
  const box = el("div", "key");
  box.append(el("b", null, title));
  if (key.insufficient_data || key.value === null && kind === "S") {
    box.classList.add("off");
    box.append(el("div", "small", "Not evaluated: " + (key.reason || "insufficient data")));
  } else {
    const p = el("div", "pct" + (key.extreme ? " extreme" : ""), fmtPct(key.percentile));
    box.append(p);
    if (key.low_reference_quality) box.append(el("div", "small err", "Low reference quality: fewer than 50 comparable reference pairs, so this key cannot count as extreme."));
  }
  const t = el("table");
  if (kind === "I") {
    kv(t, "A↔B exchanges", key.exchanges);
    kv(t, "Replies (agree / disagree / neutral / unknown)",
      ["agree", "disagree", "neutral", "unknown"].map((k) => (key.relations || {})[k] || 0).join(" / "));
    for (const [c, v] of Object.entries(key.components || {})) {
      const pct = (key.component_percentiles || {})[c];
      let raw = v.available ? fmtNum(v.value) : "n/a";
      if (c === "regularity" && v.available) raw = `median ${Math.round(v.median_latency_s)} s, σ(log10) ${v.std_log10_s}`;
      if (c === "simultaneity" && v.available) raw = `${v.observed} observed vs ${v.null_mean} expected`;
      kv(t, COMP_NAMES[c], `${raw}${pct != null ? " · " + fmtPct(pct) : ""} (n=${v.n})`);
    }
  } else if (kind === "S") {
    kv(t, "Similarity (block-weighted cosine)", fmtNum(key.value, 3));
    kv(t, "Words (A / B)", `${key.words_a} / ${key.words_b}`);
    kv(t, "Posts (A / B)", `${key.posts_a} / ${key.posts_b}`);
    kv(t, "Data volume", key.volume);
    if (key.blocks) kv(t, "Blocks", Object.entries(key.blocks).map(([k, v]) => `${k} ${fmtNum(v)}`).join(", "));
  }
  if (key.controls != null) kv(t, "Reference controls", key.controls);
  if (key.matching) kv(t, "Matching", key.matching);
  box.append(t);
  return box;
}

function pairCard(p, analysisId) {
  const c = el("div", "card pair");
  const h = el("h3"); h.textContent = `${p.a} ↔ ${p.b} `;
  h.append(el("span", "badge " + BADGE[p.label], p.label_text));
  c.append(h);
  c.append(el("div", "small muted", `Positions: ${p.a} ${p.positions[p.a]}, ${p.b} ${p.positions[p.b]}`));
  if (p.missing && p.missing.length) {
    const ul = el("ul", "small muted"); p.missing.forEach((m) => ul.append(el("li", null, m))); c.append(el("div", "small", "What's missing:"), ul);
  }
  const keys = el("div", "keys");
  keys.append(keyBox("Key I: interaction pattern (one key)", p.I, "I"), keyBox("Key S: writing style (topic words removed)", p.S, "S"));
  const l = el("div", "key off"); l.append(el("b", null, "Key L: long-term schedule"), el("div", "small", p.L.reason));
  keys.append(l);
  c.append(keys);
  for (const n of p.notes || []) c.append(el("div", "small notice", n));
  const sup = p.supporting || {};
  const sd = el("div", "small muted");
  sd.append(el("b", null, "Context only (never creates a label): "));
  const lc = sup.lifecycle || {};
  sd.append(el("span", null, lc.available
    ? `account ages ${lc.age_days[0]} and ${lc.age_days[1]} days, created ${lc.creation_gap_days} days apart${lc.both_young ? " (both under 90 days)" : ""}${lc.note ? " — " + lc.note : ""}. `
    : `lifecycle: ${lc.reason || "not available"}. `));
  const sl = sup.shared_links || {};
  if (sl.available) {
    sd.append(el("span", null, sl.shared_rare_links.length
      ? `Shared rare links: ${sl.shared_rare_links.map((x) => `${x.link} (posted by ${x.accounts_posting} accounts)`).join(", ")}. `
      : "No shared rare links. "));
    for (const x of sl.not_counted) sd.append(el("div", null, `${x.link}: ${x.why}`));
  }
  c.append(sd);
  if (p.explanation) c.append(el("p", null, p.explanation));
  const rv = el("div", "review small");
  rv.append(el("span", "muted", "Reviewer rating of this evidence: "));
  for (const rating of ["clear", "weak", "misleading"]) {
    const b = el("button", "secondary", rating);
    b.addEventListener("click", async () => {
      let who = localStorage.getItem("pt_reviewer");
      if (!who) { who = prompt("Your reviewer name (team members only)"); if (!who) return; try { localStorage.setItem("pt_reviewer", who); } catch (_) {} }
      const r = await fetch("/api/feedback", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ analysis_id: analysisId, pair_key: p.pair_key, reviewer: who, rating }) });
      b.textContent = r.ok ? rating + " ✓" : "error";
    });
    rv.append(b);
  }
  c.append(rv);
  return c;
}

function render(R) {
  $("#report").hidden = false;
  for (const b of R.banners) $("#banners").append(el("div", "notice", b));
  $("#rootText").textContent = R.root.text || "";
  const s = R.summary;
  const cards = [["Comments", s.comments], ["Accounts", s.accounts], ["Antagonistic pairs", s.antagonistic_pairs],
    ["Pairs needing review", s.flagged_pairs], ["Unclassified replies (unknown)", s.unknown],
    ["Reference pairs", s.reference.available ? s.reference.pairs : "not built"]];
  for (const [k, v] of cards) { const d = el("div"); d.append(el("b", null, v), el("span", "small muted", k)); $("#metricCards").append(d); }
  const cols = { pro: $("#colPro"), con: $("#colCon") };
  for (const p of R.participants) (cols[p.position] || $("#colOther")).append(el("li", null, `${p.id} (${p.posts_in_thread})`));
  const groups = { flagged: [], no: [], ins: [] };
  for (const p of R.pairs) (p.flagged ? groups.flagged : p.label === "insufficient_evidence" ? groups.ins : groups.no).push(p);
  if (!groups.flagged.length) $("#flagged").append(el("p", "muted", "No pair reached the review threshold."));
  groups.flagged.forEach((p) => $("#flagged").append(pairCard(p, R.analysis_id)));
  $("#sumNo").textContent = `No strong signals detected (${groups.no.length})`;
  groups.no.forEach((p) => $("#noStrong").append(pairCard(p, R.analysis_id)));
  $("#sumIns").textContent = `Insufficient evidence (${groups.ins.length})`;
  groups.ins.forEach((p) => $("#insufficient").append(pairCard(p, R.analysis_id)));
  for (const n of R.notes || []) $("#notes").append(el("p", null, n));
  $("#notes").append(el("p", null, `Thresholds: extreme ≥ ${R.thresholds.extreme_percentile}th percentile; ≥ ${R.thresholds.min_controls} reference controls; Key I needs ≥ ${R.thresholds.min_exchanges} exchanges; Key S needs ≥ ${R.thresholds.style_reduced_words} words per account (full volume ≥ ${R.thresholds.style_full_words}). Non-English posts skipped: ${s.non_english_skipped}. Hidden/blocked replies: ${s.hidden_replies}.`));
}

poll();
