"use strict";
// All dynamic content is rendered with textContent; nothing here builds HTML strings.

const state = { snapshot: null, loadedAt: null, timer: null, inFlight: false };
const $ = (id) => document.getElementById(id);

function el(tag, opts, children) {
  const node = document.createElement(tag);
  if (opts) {
    if (opts.cls) node.className = opts.cls;
    if (opts.text !== undefined && opts.text !== null) node.textContent = String(opts.text);
    if (opts.title) node.title = String(opts.title);
    if (opts.colspan) node.colSpan = opts.colspan;
  }
  for (const child of children || []) if (child) node.appendChild(child);
  return node;
}

const fmtInt = (n) => (n === null || n === undefined ? null : Number(n).toLocaleString("en-US"));
const fmtPct = (r) => (r === null || r === undefined ? null : (r * 100).toFixed(1) + "%");

function fmtMs(ms) {
  if (ms === null || ms === undefined) return null;
  if (ms < 1000) return ms + " ms";
  const s = Math.round(ms / 1000);
  if (s < 60) return s + " s";
  const m = Math.floor(s / 60);
  if (m < 60) return m + "m " + (s % 60) + "s";
  return Math.floor(m / 60) + "h " + (m % 60) + "m";
}

function fmtTime(iso) {
  return iso ? iso.replace("T", " ").replace("Z", " UTC") : null;
}

function val(text, cls) {
  return text === null || text === undefined
    ? el("span", { cls: "unknown-val", text: "unknown" })
    : el("span", { cls: cls || "", text });
}

function badge(kind, text) {
  return el("span", { cls: "badge " + kind, text: text || kind });
}

function cell(children, cls) {
  return el("td", { cls }, Array.isArray(children) ? children : [children]);
}

function th(text, cls) {
  return el("th", { text, cls });
}

function shortId(id) {
  return id ? id.slice(0, 8) : null;
}

// ---------------------------------------------------------------- rendering

function renderNotices(snap) {
  const box = $("notices");
  box.replaceChildren();
  for (const n of snap.notices) {
    box.appendChild(el("div", { cls: "notice " + n.level, text: n.message }));
  }
}

function card(label, value, note) {
  return el("div", { cls: "card", title: note || "" }, [
    el("div", { cls: "v", text: value }),
    el("div", { cls: "l", text: label }),
  ]);
}

function renderSummary(snap) {
  const s = snap.summary;
  const box = $("summary");
  box.replaceChildren();
  box.appendChild(card("calls observed", fmtInt(s.calls_total)));
  box.appendChild(card("successful", fmtInt(s.by_outcome.success || 0)));
  const failed = ["error", "timeout", "cancelled"].reduce((a, k) => a + (s.by_outcome[k] || 0), 0);
  box.appendChild(card("failed / timed out / cancelled", fmtInt(failed)));
  const unsure = ["partial", "in_doubt", "unknown", "conflict"].reduce((a, k) => a + (s.by_outcome[k] || 0), 0);
  box.appendChild(card("uncertain outcome", fmtInt(unsure)));
  box.appendChild(card("calls with interval estimate", fmtInt(s.estimates) + (s.small_sample ? " (small sample)" : "")));
  box.appendChild(card("median execution (success)", fmtMs(s.median_run_ms_success) || "unknown",
    s.run_ms_samples + " sample(s)"));
  box.appendChild(card("source", snap.source.complete ? "complete" : "INCOMPLETE",
    snap.source.state_dir));
}

function renderInflight(snap) {
  const box = $("inflight");
  box.replaceChildren();
  if (!snap.inflight.length) {
    box.appendChild(el("div", { cls: "empty", text: "No in-flight markers observed." }));
    return;
  }
  const head = el("tr", null, ["State", "Phase", "Call", "Model", "Thread", "Observed since", "Elapsed", "Note"].map((t) => th(t)));
  const rows = snap.inflight.map((m) => {
    const notes = [];
    if (m.recorded_in_telemetry) notes.push("call already recorded in telemetry - marker is likely stale");
    else if (m.possibly_stale) notes.push("long-lived or completed marker - may be stale");
    if (m.reason) notes.push("reason: " + m.reason);
    if (m.kind === "running") notes.push("observed state, not proof of a live process");
    return el("tr", null, [
      cell(badge(m.kind, m.kind === "unknown" ? (m.state || "unknown") : m.kind)),
      cell(val(m.phase)),
      cell(val(shortId(m.call_id))),
      cell(val(m.model)),
      cell(val(shortId(m.thread_id))),
      cell(val(fmtTime(m.since || m.started_at))),
      cell(val(fmtMs(m.elapsed_s === null ? null : m.elapsed_s * 1000))),
      cell(el("span", { cls: "est-note", text: notes.join("; ") })),
    ]);
  });
  box.appendChild(el("div", { cls: "table-wrap" }, [el("table", null, [el("thead", null, [head]), el("tbody", null, rows)])]));
}

function usageCell(u) {
  const rows = [["in", u.input_tokens], ["cached", u.cached_input_tokens], ["out", u.output_tokens],
    ["of which reasoning", u.reasoning_output_tokens]];
  return el("div", null, rows.map(([k, v]) => {
    const d = el("div", null);
    d.appendChild(el("span", { cls: "muted", text: k + " " }));
    d.appendChild(val(fmtInt(v)));
    return d;
  }));
}

// Raw counters are only labelled session cumulative when the backend established that basis.
function basisLabel(c) {
  return c.usage_basis === "session_cumulative"
    ? el("div", { cls: "est-note basis-known", text: "session cumulative" })
    : el("div", { cls: "est-note basis-unknown", text: "basis unknown (unsupported CLI or schema)" });
}

function rawUsageCell(c) {
  const box = usageCell(c.usage_raw);
  box.insertBefore(basisLabel(c), box.firstChild);
  return box;
}

function estimateCell(c) {
  const e = c.estimate;
  if (c.usage_delta && (e.status === "eligible_estimate" || e.status === "fresh_baseline_estimate")) {
    const d = c.usage_delta;
    const box = usageCell(d);
    box.appendChild(el("div", {
      cls: "est-note",
      text: e.status === "fresh_baseline_estimate" ? "estimate (fresh thread, zero baseline)" : "interval estimate, not billed cost",
    }));
    return box;
  }
  const label = e.status === "not_applicable" ? "not applicable" : "not estimated";
  const box = el("div", null, [el("span", { cls: "unknown-val", text: label })]);
  if (e.reasons.length) box.appendChild(el("div", { cls: "est-note", text: e.reasons[0].text }));
  return box;
}

function ratioText(c) {
  const parts = [];
  if (c.ratios.cache_delta !== null) parts.push("est. cached " + fmtPct(c.ratios.cache_delta));
  if (c.ratios.cache_raw !== null) parts.push("raw cached " + fmtPct(c.ratios.cache_raw));
  return parts.length ? parts.join(" / ") : null;
}

function detailsCell(c) {
  const items = [];
  for (const r of c.estimate.reasons) items.push(r.text);
  if (c.error_category || c.error_code) items.push("error: " + [c.error_category, c.error_code].filter(Boolean).join(" / "));
  if (c.exit_code !== null && c.exit_code !== 0) items.push("exit code " + c.exit_code);
  if (c.inference_may_have_started === true && c.outcome_class !== "success") items.push("inference may have started");
  if (c.pointer_state) items.push("pointer: " + c.pointer_state);
  if (c.history) items.push("history: " + c.history);
  if (c.session_state) items.push("session: " + c.session_state);
  if (c.cli_version) items.push("CLI " + c.cli_version + " (usage basis: " + c.usage_basis.replace("_", " ") + ")");
  for (const note of c.durations.notes) items.push(note.replace(/_/g, " "));
  for (const w of c.warnings) items.push("warning: " + w);
  if (c.warnings_total > c.warnings.length) items.push("+" + (c.warnings_total - c.warnings.length) + " more warning(s)");
  if (c.repo && c.repo.changed === true) items.push("repository changed during call");
  if (c.call_id) items.push("call " + c.call_id);
  if (!items.length) return el("span", { cls: "muted", text: "-" });
  return el("details", null, [
    el("summary", { text: items.length + " note(s)" }),
    el("ul", null, items.map((t) => el("li", { text: t }))),
  ]);
}

function outcomeCell(c) {
  const label = c.outcome || "unknown";
  const kids = [badge(c.outcome_class, label)];
  if (c.outcome_class === "partial" || c.outcome_class === "in_doubt") {
    kids.push(el("div", { cls: "est-note", text: "recovery uncertain" }));
  }
  if (c.usage_present === false && c.outcome_class === "success") {
    kids.push(el("div", { cls: "est-note", text: "no usage reported" }));
  }
  return el("div", null, kids);
}

function filteredCalls(snap) {
  const f = { model: $("f-model").value, tool: $("f-tool").value, session: $("f-session").value, outcome: $("f-outcome").value };
  return snap.calls.filter((c) =>
    (!f.model || c.route.model === f.model) &&
    (!f.tool || c.tool === f.tool) &&
    (!f.session || c.session.name === f.session) &&
    (!f.outcome || c.outcome_class === f.outcome));
}

function renderCalls(snap) {
  const box = $("calls");
  box.replaceChildren();
  const calls = filteredCalls(snap);
  $("filter-count").textContent = calls.length + " of " + snap.summary.calls_total + " call(s)" +
    (snap.summary.calls_served < snap.summary.calls_total ? " (newest " + snap.summary.calls_served + " served)" : "");
  if (!snap.calls.length) {
    box.appendChild(el("div", { cls: "empty", text: "No calls recorded yet. The dashboard only reads existing bridge telemetry." }));
    return;
  }
  if (!calls.length) {
    box.appendChild(el("div", { cls: "empty", text: "No calls match the current filters." }));
    return;
  }
  const head = el("tr", null, [
    th("Started"), th("Tool"), th("Route"), th("Session"), th("Outcome"),
    th("Duration (exec / end-to-end / queue)"),
    th("Raw counters (as logged)"), th("Estimated for this call"), th("Cache"), th("Notes"),
  ]);
  const rows = calls.map((c) => {
    const resumedText = c.session.resumed === true ? "resumed" : c.session.resumed === false ? "fresh" : null;
    const dur = c.durations;
    return el("tr", null, [
      cell(val(fmtTime(c.started_at)), "nowrap"),
      cell(val(c.tool), "nowrap"),
      cell([val(c.route.model), el("div", { cls: "est-note", text: c.route.effort ? "effort " + c.route.effort : "" })], "nowrap"),
      cell([val(c.session.name), el("div", { cls: "est-note", text: [resumedText || "resume unknown", shortId(c.session.thread_id)].filter(Boolean).join(" · ") })], "nowrap"),
      cell(outcomeCell(c)),
      cell(el("div", null, [
        el("div", null, [el("span", { cls: "muted", text: "exec " }), val(fmtMs(dur.run_ms))]),
        el("div", null, [el("span", { cls: "muted", text: "total " }), val(fmtMs(dur.end_to_end_ms))]),
        el("div", null, [el("span", { cls: "muted", text: "queue " }), val(fmtMs(dur.queue_ms))]),
      ])),
      cell(rawUsageCell(c), "num"),
      cell(estimateCell(c), "num"),
      cell(val(ratioText(c))),
      cell(detailsCell(c)),
    ]);
  });
  box.appendChild(el("table", null, [el("thead", null, [head]), el("tbody", null, rows)]));
}

function renderSessions(snap) {
  const box = $("sessions");
  box.replaceChildren();
  if (!snap.sessions.length) {
    box.appendChild(el("div", { cls: "empty", text: "No sessions observed." }));
    return;
  }
  const head = el("tr", null, ["Session", "Thread", "Model", "Calls seen", "Latest counters, session cumulative (input / cached / output)", "Continuity"].map((t) => th(t)));
  const rows = snap.sessions.map((s) => {
    const u = s.latest_cumulative;
    const usage = u ? [fmtInt(u.input_tokens), fmtInt(u.cached_input_tokens), fmtInt(u.output_tokens)].map((x) => x === null ? "unknown" : x).join(" / ") : null;
    return el("tr", null, [
      cell(val(s.name)), cell(val(shortId(s.thread_id))), cell(val(s.model)), cell(val(fmtInt(s.calls_observed))),
      cell(val(usage)),
      cell(el("span", { cls: "est-note", text: s.continuity_valid ? "baseline valid" : (s.continuity_note || "baseline not established") })),
    ]);
  });
  box.appendChild(el("table", null, [el("thead", null, [head]), el("tbody", null, rows)]));
}

function fillSelect(select, values, allLabel) {
  const previous = select.value;
  select.replaceChildren(el("option", { text: allLabel }));
  select.firstChild.value = "";
  for (const v of values) {
    const opt = el("option", { text: v });
    opt.value = v;
    select.appendChild(opt);
  }
  select.value = values.includes(previous) ? previous : "";
}

function renderFilters(snap) {
  const uniq = (f) => Array.from(new Set(snap.calls.map(f).filter(Boolean))).sort();
  fillSelect($("f-model"), uniq((c) => c.route.model), "All models");
  fillSelect($("f-tool"), uniq((c) => c.tool), "All tools");
  fillSelect($("f-session"), uniq((c) => c.session.name), "All sessions");
  fillSelect($("f-outcome"), uniq((c) => c.outcome_class), "All outcomes");
}

function renderAll(snap) {
  renderNotices(snap);
  renderSummary(snap);
  renderInflight(snap);
  renderFilters(snap);
  renderCalls(snap);
  renderSessions(snap);
  const lim = $("limitations");
  lim.replaceChildren(...snap.limitations.map((t) => el("li", { text: t })));
}

function setBanner(message) {
  const b = $("banner");
  b.textContent = message || "";
  b.classList.toggle("hidden", !message);
}

function updateTimestamp() {
  if (!state.snapshot) return;
  $("updated").textContent = "snapshot " + fmtTime(state.snapshot.generated_at);
}

// ------------------------------------------------------------------ loading

async function load() {
  if (state.inFlight) return;
  state.inFlight = true;
  $("refresh").disabled = true;
  try {
    const resp = await fetch("/api/snapshot", { method: "GET", cache: "no-store", credentials: "omit" });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const snap = await resp.json();
    if (!snap || snap.schema !== "obs-01/1") throw new Error("unexpected snapshot format");
    state.snapshot = snap;
    setBanner("");
    renderAll(snap);
    updateTimestamp();
  } catch (err) {
    const when = state.snapshot ? " Showing the last snapshot from " + fmtTime(state.snapshot.generated_at) + "." : "";
    setBanner("Could not load the snapshot (" + (err && err.message ? err.message : "error") + ")." + when);
  } finally {
    state.inFlight = false;
    $("refresh").disabled = false;
  }
}

function schedule() {
  if (state.timer) clearInterval(state.timer);
  state.timer = null;
  if ($("auto").checked) {
    state.timer = setInterval(load, Number($("interval").value) * 1000);
  }
}

$("refresh").addEventListener("click", load);
$("auto").addEventListener("change", schedule);
$("interval").addEventListener("change", schedule);
for (const id of ["f-model", "f-tool", "f-session", "f-outcome"]) {
  $(id).addEventListener("change", () => state.snapshot && renderCalls(state.snapshot));
}
schedule();
load();
