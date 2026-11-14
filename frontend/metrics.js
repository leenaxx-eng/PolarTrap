const $ = (s) => document.querySelector(s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const pct = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);
const TYPE_NAMES = { A: "A: one person, two personas", B: "B: staged by two people", C: "C: genuine disagreement",
  D: "D: genuine, fast and regular" };

function table(t, head, rows) {
  const tr = el("tr"); head.forEach((h) => tr.append(el("th", null, h))); t.append(tr);
  for (const r of rows) { const row = el("tr"); r.forEach((v) => row.append(el("td", null, v ?? "–"))); t.append(row); }
}
const rateCell = (x) => (x.pairs ? `${x.flagged}/${x.pairs} = ${pct(x.rate)}` : "no pairs");
const ciCell = (x) => (x.ci95 ? `${pct(x.ci95.lo)} – ${pct(x.ci95.hi)}` : "–");

(async () => {
  const d = await (await fetch("/api/metrics")).json();
  if (!d.available) { $("#none").hidden = false; return; }
  $("#m").hidden = false;
  const sb = d.sandbox;
  $("#header").textContent = sb.header;
  $("#meta").textContent = `Generated ${d.generated_at} · commit ${d.commit} · extreme threshold ${d.config.extreme_percentile}th percentile · ${d.config.bootstrap_reps} bootstrap reps · seed ${d.config.seed}`;
  $("#flagLabels").textContent = d.config.flag_labels.join(" or ");
  table($("#perType"), ["Type", "Participants", "Flagged / pairs", "95% interval"],
    Object.entries(sb.per_type).map(([t, x]) => [TYPE_NAMES[t], x.participants, rateCell(x), ciCell(x)]));
  const labs = new Set(); Object.values(sb.label_distribution).forEach((o) => Object.keys(o).forEach((k) => labs.add(k)));
  table($("#labels"), ["Type", ...labs], Object.entries(sb.label_distribution).map(([t, o]) => [t, ...[...labs].map((l) => o[l] || 0)]));
  table($("#confusion"), ["Cell", "Pairs"], Object.entries(sb.confusion));
  const ab = sb.ablation;
  table($("#ablation"), ["Type", "Key I extreme only", "Key S extreme only", "I + S rule (label)"],
    ["A", "B", "C", "D"].map((t) => [t, ...["I_only", "S_only", "I_plus_S_rule"].map((k) => `${rateCell(ab[k][t])} (${ciCell(ab[k][t])})`)]));
  $("#stance").textContent = `${sb.stance.interactions} sandbox replies classified: ` +
    Object.entries(sb.stance.relations).map(([k, v]) => `${k} ${v}`).join(", ") + ". Unknown = classification failed; excluded, never treated as neutral.";
  const real = d.real;
  if (real) {
    $("#real").append(el("p", null, `${real.pairs_tested} antagonistic pairs tested in ${real.snapshot.threads} held-out threads. ` +
      `Flags on real data: ${real.flags_real}. Under the time-shift null: mean ${real.flags_null_time_shift.mean?.toFixed(1)} ` +
      `(range ${real.flags_null_time_shift.min}–${real.flags_null_time_shift.max}, ${real.flags_null_time_shift.runs} runs). ` +
      `Under random re-pairing: mean ${real.flags_null_repairing.mean?.toFixed(1)} (range ${real.flags_null_repairing.min}–${real.flags_null_repairing.max}).`));
    $("#real").append(el("p", null, "Labels: " + Object.entries(real.label_distribution).map(([k, v]) => `${k} ${v}`).join(", ")));
    $("#real").append(el("p", "muted", real.note));
  } else $("#real").append(el("p", "muted", "Not run yet."));
  const rv = d.review;
  $("#review").append(el("p", null, `${rv.ratings} ratings on ${rv.items} evidence cards; ${rv.items_with_2plus_reviewers} rated by two or more reviewers.` +
    (rv.shared_items ? ` Agreement between the two most active reviewers on ${rv.shared_items} shared cards: ${pct(rv.percent_agreement)}, Cohen's κ = ${rv.cohen_kappa == null ? "n/a" : rv.cohen_kappa.toFixed(2)}.` : "")));
  const ref = d.reference;
  $("#ref").append(el("p", null, ref.available
    ? `Snapshot "${ref.snapshot.name}" (${ref.snapshot.created_at}, hash ${ref.snapshot.content_hash.slice(0, 12)}…): ${ref.pairs} antagonistic reference pairs; ` +
      `${ref.summary.I_evaluable} with an evaluable interaction key, ${ref.summary.S_evaluable} with an evaluable style key. Reference pairs are a comparison distribution, not confirmed innocent accounts.`
    : "Reference corpus not built."));
  $("#disclaimer").textContent = `${d.disclaimer} Signals tested: ${d.signals_tested.join("; ")}.`;
})();
