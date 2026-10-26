"use strict";

// ---------- vocabularies (mirror backend/models.py) ----------
const LEVELS = {
  1: "Level 1 - Kinship Care", 2: "Level 2 - General Foster Care", 3: "Level 3 - Specialized Care",
  4: "Level 4 - Therapeutic Foster Care", 5: "Level 5 - Medical Foster Care",
};
const label = (s) => String(s).replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
const pairs = (values) => values.map((v) => [v, label(v)]);
const GENDERS = ["female", "male", "nonbinary", "undisclosed"];
const NEEDS = ["physical", "developmental", "emotional", "behavioral", "medical", "learning"];
const DURATIONS = ["emergency", "respite", "short_term", "long_term", "permanent"];
const STATUSES = ["inquiry", "in_review", "approved", "licensed", "on_hold", "denied", "closed"];
const GOALS = ["reunification", "adoption", "guardianship", "kinship_placement", "appla"];
const REASONS = ["Neglect", "Physical abuse", "Sexual abuse", "Emotional abuse", "Parental substance use", "Domestic violence in home",
  "Abandonment", "Caregiver incarceration", "Caregiver death or illness", "Medical neglect", "Housing instability", "Child behavior", "Rejection by caregiver"];

const OPTIONS = {
  gender: [["", ""], ...pairs(GENDERS)],
  "gender-only": pairs(GENDERS.slice(0, 3)),
  need: pairs(NEEDS),
  "need-select": pairs(NEEDS),
  severity: pairs(["mild", "moderate", "severe"]),
  duration: pairs(DURATIONS),
  "duration-select": pairs(DURATIONS),
  status: pairs(STATUSES),
  level: Object.entries(LEVELS),
  "level-child": Object.entries(LEVELS).slice(1),
  goal: pairs(GOALS).map(([v, l]) => [v, v === "appla" ? "APPLA (planned permanent living)" : l]),
  "goal-optional": [["", "None"], ...pairs(GOALS)],
  placement: pairs(["awaiting_placement", "placed", "on_hold"]),
  reason: REASONS.map((r) => [r, r]),
  children: () => [["", "Not in system"], ...state.children.map((c) => [c.id, `${c.display_name} (${c.id})`])],
};

const state = { user: null, parents: [], children: [], placements: [], editing: { pform: null, cform: null }, openChild: null };
// The server enforces every permission; the UI only hides what a role can't use.
const can = (perm) => !!state.user?.permissions.includes(perm);

// ---------- helpers ----------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const list = (items, cls = "") => (items?.length ? `<ul class="tight ${cls}">${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : `<p class="muted">None</p>`);

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (res.status === 204) return null;
  const body = await res.json().catch(() => ({}));
  if (res.status === 401 && path !== "/api/auth/login") showGate("login");
  if (res.status === 403 && /change your password/.test(body.detail || "")) showGate("pwchange");
  if (!res.ok) {
    const detail = Array.isArray(body.detail)
      ? body.detail.map((d) => `${d.loc.slice(1).join(".")}: ${d.msg}`).join("; ")
      : body.detail || res.statusText;
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return body;
}

function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.className = "toast"), error ? 6000 : 3000);
}

function getPath(obj, path) {
  return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
}
function setPath(obj, path, value) {
  const keys = path.split(".");
  let o = obj;
  keys.slice(0, -1).forEach((k) => (o = o[k] ??= {}));
  o[keys.at(-1)] = value;
}

// ---------- option filling ----------
function fillOptions(root) {
  $$("select[data-options]", root).forEach((sel) => {
    const src = OPTIONS[sel.dataset.options];
    const opts = typeof src === "function" ? src() : src;
    const current = sel.value;
    sel.innerHTML = opts.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
    if (current && opts.some(([v]) => v === current)) sel.value = current;
  });
  $$("[data-checkgroup]", root).forEach((box) => {
    if (box.children.length) return;
    box.innerHTML = OPTIONS[box.dataset.options]
      .map(([v, l]) => `<label class="check"><input type="checkbox" value="${esc(v)}" /> ${esc(l)}</label>`).join("");
  });
}

// ---------- form <-> JSON ----------
function readField(el) {
  const type = el.dataset.type;
  if (type === "bool") return el.checked;
  const raw = el.value.trim();
  if (type === "csv") return raw ? raw.split(",").map((s) => s.trim()).filter(Boolean) : [];
  if (raw === "") return el.tagName === "SELECT" || el.type === "date" || el.type === "number" ? undefined : "";
  if (type === "int" || el.type === "number") return Number(raw);
  return raw;
}

function writeField(el, value) {
  if (el.dataset.type === "bool") el.checked = !!value;
  else if (el.dataset.type === "csv") el.value = (value || []).join(", ");
  else el.value = value ?? "";
}

function serialize(form) {
  const data = {};
  $$("[name]", form).forEach((el) => {
    if (el.closest(".item")) return;
    const v = readField(el);
    if (v !== undefined) setPath(data, el.name, v);
  });
  $$("[data-checkgroup]", form).forEach((box) => {
    setPath(data, box.dataset.checkgroup, $$("input:checked", box).map((i) => i.value));
  });
  $$("[data-repeater]", form).forEach((rep) => {
    const items = $$(".item", rep).map((item) => {
      const obj = {};
      $$("[data-field]", item).forEach((el) => {
        const v = readField(el);
        if (v !== undefined && v !== "") obj[el.dataset.field] = v;
        else if (el.dataset.type === "bool") obj[el.dataset.field] = false;
      });
      return obj;
    });
    setPath(data, rep.dataset.repeater, items);
  });
  if (form.id === "pform" && !$("#has-co").checked) data.co_applicant = null;
  return data;
}

function addItem(form, path, values = {}) {
  const rep = $(`[data-repeater="${path}"]`, form);
  const node = $(`#${rep.dataset.template}`).content.firstElementChild.cloneNode(true);
  fillOptions(node);
  $$("[data-field]", node).forEach((el) => writeField(el, values[el.dataset.field]));
  node.querySelector(".remove").onclick = () => node.remove();
  rep.appendChild(node);
}

function resetForm(form) {
  form.reset();
  $$("[data-repeater]", form).forEach((r) => (r.innerHTML = ""));
  $$(".invalid", form).forEach((el) => el.classList.remove("invalid"));
  state.editing[form.id] = null;
  if (form.id === "pform") toggleCo(false);
  setFormTitle(form);
}

function populate(form, record) {
  resetForm(form);
  $$("[name]", form).forEach((el) => {
    if (!el.closest(".item")) writeField(el, getPath(record, el.name));
  });
  $$("[data-checkgroup]", form).forEach((box) => {
    const vals = getPath(record, box.dataset.checkgroup) || [];
    $$("input", box).forEach((i) => (i.checked = vals.includes(i.value)));
  });
  $$("[data-repeater]", form).forEach((rep) => {
    (getPath(record, rep.dataset.repeater) || []).forEach((v) => addItem(form, rep.dataset.repeater, v));
  });
  if (form.id === "pform") toggleCo(!!record.co_applicant);
  state.editing[form.id] = record.id;
  setFormTitle(form);
}

function setFormTitle(form) {
  const h = $("h2", form);
  h.textContent = state.editing[form.id] ? `${h.dataset.titleEdit} · ${state.editing[form.id]}` : h.dataset.titleNew;
}

function toggleCo(on) {
  $("#has-co").checked = on;
  $("#co-fields").hidden = !on;
}

function validate(form) {
  let ok = true;
  $$("[required]", form).forEach((el) => {
    const hidden = el.closest("[hidden]");
    const bad = !hidden && !el.value.trim();
    el.classList.toggle("invalid", bad);
    if (bad) ok = false;
  });
  return ok;
}

async function submitForm(form, kind) {
  if (!validate(form)) {
    toast("Please fill in the required fields.", true);
    $(".invalid", form)?.focus();
    return;
  }
  const id = state.editing[form.id];
  const base = kind === "parent" ? "/api/parents" : "/api/children";
  try {
    const saved = await api(id ? `${base}/${id}` : base, { method: id ? "PUT" : "POST", body: JSON.stringify(serialize(form)) });
    toast(`${saved.display_name} saved (${saved.id}).`);
    resetForm(form);
    await loadAll();
    show("dashboard");
    openRecord(kind, saved.id);
  } catch (e) {
    toast(`Could not save: ${e.message}`, true);
  }
}

// ---------- navigation ----------
function show(view) {
  const tab = $(`.tabs button[data-view="${view}"]`);
  if (tab?.dataset.perm && !can(tab.dataset.perm)) return;
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === view));
  $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  if (view === "matching") renderBoard();
  if (view === "child-form") fillOptions($("#cform"));
  if (view === "users") renderUsers();
  if (view === "access") renderAccessLog();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ---------- data & dashboard ----------
async function loadUsage() {
  if (!can("audit:read")) return;
  const u = await api("/api/ai/usage");
  const failed = u.runs - u.model_runs - u.reused;
  $("#ai-usage").innerHTML = `<div class="usage">
      <div><b>${u.model_runs}</b>AI runs</div><div><b>${u.reused}</b>reused (free)</div>
      <div><b>${failed}</b>stopped / rejected</div><div><b>$${u.total_cost_usd.toFixed(4)}</b>total AI cost</div></div>
    ${u.recent.length ? `<table><tr><th>When</th><th>Record</th><th>Type</th><th>Outcome</th><th>Cost</th><th>Withheld</th></tr>
      ${u.recent.slice(0, 8).map((r) => `<tr><td>${new Date(r.created_at).toLocaleString()}</td><td>${esc(r.subject_id)}</td><td>${esc(r.run_type)}</td>
      <td><span class="pill ${r.outcome === "ok" ? "ok" : r.outcome === "reused" ? "" : "bad"}">${esc(r.outcome)}</span></td>
      <td>$${(r.cost_usd || 0).toFixed(4)}</td><td>${esc((r.withheld_fields || []).length || "")}</td></tr>`).join("")}</table>` : ""}`;
}

async function loadAll() {
  [state.parents, state.children, state.placements] = await Promise.all([api("/api/parents"), api("/api/children"), api("/api/placements")]);
  renderDashboard();
  loadUsage().catch(() => {});
}

const statusPill = (s) => {
  const cls = { licensed: "ok", approved: "ok", in_review: "warn", inquiry: "warn", on_hold: "warn", denied: "bad", closed: "bad" }[s] || "";
  return `<span class="pill ${cls}">${esc(label(s))}</span>`;
};
const placementPill = (s) => `<span class="pill ${s === "placed" ? "ok" : s === "on_hold" ? "warn" : "bad"}">${esc(label(s))}</span>`;

function renderDashboard() {
  const waiting = state.children.filter((c) => c.caseworker.placement_status === "awaiting_placement").length;
  const ready = state.parents.filter((p) => ["licensed", "approved"].includes(p.caseworker.application_status));
  const beds = ready.reduce((n, p) => n + p.home.available_beds, 0);
  $("#stats").innerHTML = [
    [waiting, "children awaiting placement"],
    [ready.length, "approved / licensed homes"],
    [beds, "open beds in approved homes"],
    [state.placements.length, "placements recorded"],
  ].map(([n, t]) => `<div class="stat"><b>${n}</b>${t}</div>`).join("");

  $("#child-list").innerHTML = state.children.map((c) => `
    <button class="card" data-open="child" data-id="${c.id}">
      <div><h3>${esc(c.display_name)}, ${c.age}</h3>
        <p>${esc(c.education.school_name || "No school recorded")} · goal: ${esc(label(c.goals.permanency_goal))}${c.sibling_group_size > 1 ? ` · sibling group of ${c.sibling_group_size}` : ""}</p></div>
      <div class="pills"><span class="pill lvl">L${c.caseworker.required_care_level}</span>${placementPill(c.caseworker.placement_status)}</div>
    </button>`).join("") || `<p class="muted">No children yet.</p>`;

  $("#parent-list").innerHTML = state.parents.map((p) => `
    <button class="card" data-open="parent" data-id="${p.id}">
      <div><h3>${esc(p.display_name)}</h3>
        <p>${esc(p.home.address.city || p.home.address.county || "")} · ${p.home.available_beds} bed(s) · ages ${p.preferences.age_min}-${p.preferences.age_max}</p></div>
      <div class="pills"><span class="pill lvl">L${p.licensing.care_level}</span>${statusPill(p.caseworker.application_status)}</div>
    </button>`).join("") || `<p class="muted">No foster homes yet.</p>`;

  const name = (arr, id) => arr.find((r) => r.id === id)?.display_name || id;
  $("#placement-log").innerHTML = state.placements.length
    ? `<table><tr><th>When</th><th>Child</th><th>Home</th><th>Decided by</th><th>Note</th></tr>${state.placements.map((p) => `
        <tr><td>${new Date(p.created_at).toLocaleString()}</td><td>${esc(name(state.children, p.child_id))}</td>
        <td>${esc(name(state.parents, p.parent_id))}</td><td>${esc(p.decided_by)}</td><td>${esc(p.note)}</td></tr>`).join("")}</table>`
    : "No placements recorded yet.";
}

// ---------- record drawer ----------
const kv = (rows) => `<dl class="kv">${rows.filter(([, v]) => v !== undefined && v !== "" && v !== null)
  .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;

function openRecord(kind, id) {
  const r = (kind === "parent" ? state.parents : state.children).find((x) => x.id === id);
  if (!r) return;
  const body = kind === "parent" ? parentDetail(r) : childDetail(r);
  $("#drawer-body").innerHTML = `${body}
    <div class="actions">
      ${can("records:write") ? `<button class="btn ghost" data-edit="${kind}" data-id="${id}">Edit form</button>` : ""}
      ${kind === "child" ? `<button class="btn primary" data-match="${id}">Find homes</button>` : ""}
      ${can("ai:run") ? `<button class="btn ai" data-summary="${kind}" data-id="${id}">Generate AI case summary</button>` : ""}
    </div>
    <div id="summary-box"></div>`;
  $("#drawer").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
  api(`/api/ai/summary/${id}`).then((rep) => renderSummary(rep)).catch(() => {});
}

function closeDrawer() {
  $("#drawer").classList.remove("open");
  $("#drawer").setAttribute("aria-hidden", "true");
}

function parentDetail(p) {
  const a = p.applicant, h = p.home, pr = p.preferences, cw = p.caseworker;
  const members = p.household_members.map((m) => `${esc(m.name)} (${esc(m.relationship)}${m.age != null ? `, ${m.age}` : ""})`).join("<br>");
  return `<h2>${esc(p.display_name)}</h2><p class="muted">${p.id} · ${esc(LEVELS[p.licensing.care_level])} · ${statusPill(cw.application_status)}</p>
    <section><h3>Household (${p.household_size})</h3>${kv([
      ["Applicant", `${esc(a.first_name)} ${esc(a.last_name)}${a.occupation ? ` · ${esc(a.occupation)}` : ""}`],
      ["Co-applicant", p.co_applicant ? `${esc(p.co_applicant.first_name)} ${esc(p.co_applicant.last_name)}${p.co_applicant.occupation ? ` · ${esc(p.co_applicant.occupation)}` : ""}` : ""],
      ["Other members", members],
      ["Languages", esc([a.primary_language, ...a.other_languages].join(", "))],
      ["Home", esc([h.address.city, h.address.county && `${h.address.county} County`, h.school_district].filter(Boolean).join(" · "))],
      ["Beds", `${h.available_beds} available of ${h.bedrooms} bedrooms`],
      ["Pets", esc(h.pets || "None")],
    ])}</section>
    <section><h3>Preferences</h3>${kv([
      ["Ages", `${pr.age_min}-${pr.age_max}`],
      ["Gender", esc(pr.genders.map(label).join(", ") || "No preference")],
      ["Siblings", pr.accepts_sibling_groups ? `Yes, up to ${pr.max_sibling_group_size}` : "No"],
      ["Duration", esc(pr.durations.map(label).join(", ") || "Any")],
      ["Adoption", pr.interested_in_adoption ? "Interested" : "Not seeking"],
      ["Reunification", pr.supports_reunification ? "Supports" : "Hesitant"],
      ["Prepared for", esc(pr.comfortable_with_needs.map(label).join(", ") || "-")],
      ["Trainings", esc(p.licensing.trainings.join(", ") || "-")],
    ])}</section>
    <section><h3>Caseworker record</h3>${kv([
      ["Caseworker", esc(cw.assigned_caseworker)],
      ["Background checks", cw.background_checks_complete ? "Complete" : "<b>Incomplete</b>"],
      ["History", `${cw.prior_placements} placements, ${cw.placement_disruptions} disruptions`],
      ["Foster history", esc(cw.foster_history)],
      ["Internal notes", esc(cw.internal_notes)],
    ])}</section>`;
}

function childDetail(c) {
  const d = c.demographics, cw = c.caseworker;
  return `<h2>${esc(c.display_name)}, ${c.age}</h2><p class="muted">${c.id} · needs ${esc(LEVELS[cw.required_care_level])} · ${placementPill(cw.placement_status)}</p>
    <section><h3>About</h3>${kv([
      ["Gender", esc(label(d.gender))],
      ["Languages", esc([d.primary_language, ...d.other_languages].join(", "))],
      ["ICWA", d.icwa_applies ? `<b>Applies</b> · ${esc(d.tribal_affiliation)}` : ""],
      ["Entered care", esc(c.entry.date_entered_care || "")],
      ["Reasons", esc(c.entry.reasons.join(", "))],
      ["School", esc([c.education.school_name, c.education.school_district, c.education.grade && `grade ${c.education.grade}`].filter(Boolean).join(" · "))],
      ["IEP / 504", c.education.has_iep_or_504 ? "Yes" : ""],
    ])}</section>
    <section><h3>Needs</h3>${list(c.health.needs.map((n) => `${label(n.category)} (${n.severity}): ${n.description}`))}
      ${kv([["Medications", esc(c.health.medications)], ["Allergies", esc(c.health.allergies)], ["Pet allergy", c.health.pet_allergy ? "Yes" : ""]])}</section>
    <section><h3>Family & goals</h3>${kv([
      ["Siblings", c.siblings.map((s) => `${esc(s.name)}${s.age != null ? ` (${s.age})` : ""}${s.place_together ? " · place together" : ""}`).join("<br>")],
      ["Permanency goal", esc(label(c.goals.permanency_goal)) + (c.goals.concurrent_goal ? ` (concurrent: ${esc(label(c.goals.concurrent_goal))})` : "")],
      ["Expected stay", esc(label(c.goals.expected_duration))],
      ["Known relatives", esc(c.goals.known_relatives.join(", "))],
      ["Child's wishes", esc(c.goals.childs_wishes)],
    ])}</section>
    <section><h3>Caseworker</h3>${kv([
      ["Caseworker", esc(cw.assigned_caseworker)],
      ["Safety", esc(cw.safety_considerations)],
      ["Restrictions", [cw.no_younger_children_in_home && "No younger children", cw.must_be_only_child_in_home && "Only child in home"].filter(Boolean).join(", ")],
      ["Internal notes", esc(cw.internal_notes)],
    ])}</section>`;
}

function renderSummary(rep) {
  const s = rep.report;
  $("#summary-box").innerHTML = `${withheldNotice(rep)}<div class="ai-box">
    <h3>AI case summary <span class="badge">gpt-6-luna</span></h3>
    <p><b>${esc(s.headline)}</b></p><p>${esc(s.summary)}</p>
    <div class="cols3">
      <div><h4>Strengths</h4>${list(s.strengths)}</div>
      <div><h4>Needs & considerations</h4>${list(s.needs_and_considerations)}</div>
      <div><h4>Caseworker flags</h4>${list(s.caseworker_flags, "cautions")}</div>
    </div>
    <div class="cols3">
      <div><h4>Missing information</h4>${list(s.missing_information)}</div>
      <div style="grid-column: span 2"><h4>Suggested next steps</h4>${list(s.suggested_next_steps)}</div>
    </div>
    ${runFooter(rep, "AI summaries can be wrong: verify against the case file.")}
  </div>`;
}

function withheldNotice(rep) {
  const w = rep.meta?.withheld_fields || [];
  return w.length ? `<div class="notice">Safety filter: ${w.length} field(s) contained text addressed to an AI system and were
    withheld from the model. Review them in the case file: ${esc(w.join(", "))}.</div>` : "";
}

function runFooter(rep, note) {
  const m = rep.meta || {};
  const cost = m.model === "rules engine only" ? "no AI call needed" :
    `${m.requests} request(s), ${(m.input_tokens + m.output_tokens).toLocaleString()} tokens, $${(m.cost_usd ?? 0).toFixed(4)}, ${m.seconds}s`;
  return `<p class="disclaimer">Generated ${new Date(rep.generated_at).toLocaleString()} · ${esc(cost)}${rep.reused ? " · <b>reused: nothing changed since this report, so no new AI call</b>" : ""}.
    ${note}</p>`;
}

async function busy(btn, text, fn) {
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>${text}`;
  try { await fn(); } catch (e) { toast(e.message, true); } finally { btn.disabled = false; btn.innerHTML = original; }
}

// ---------- matching ----------
const meter = (score) => `<span class="score"><span class="meter"><i style="width:${score}%"></i></span>${score.toFixed(0)}</span>`;

async function renderBoard() {
  const rows = await api("/api/match/board");
  $("#board").innerHTML = rows.length ? `<table>
    <tr><th>Child</th><th>Eligible homes</th><th>Proposed home</th><th>Score</th><th>Alternatives</th></tr>
    ${rows.map((r) => `<tr class="clickable" data-match="${r.child_id}">
      <td><b>${esc(r.child_name)}</b>${r.sibling_group_size > 1 ? ` <span class="pill">siblings ×${r.sibling_group_size}</span>` : ""}</td>
      <td>${r.eligible_homes ? r.eligible_homes : `<span class="pill bad">none</span>`}</td>
      <td>${r.proposed ? esc(r.proposed.parent_name) : `<span class="muted">No home with capacity: recruitment need</span>`}</td>
      <td>${r.proposed ? meter(r.proposed.score) : ""}</td>
      <td class="muted">${esc(r.alternatives.map((a) => `${a.parent_name} (${a.score.toFixed(0)})`).join(", "))}</td>
    </tr>`).join("")}</table>`
    : `<p class="muted">No children are awaiting placement.</p>`;
}

async function openMatch(childId) {
  closeDrawer();
  if (!$("#matching").classList.contains("active")) show("matching");
  state.openChild = childId;
  const child = state.children.find((c) => c.id === childId);
  const results = await api(`/api/match/child/${childId}`);
  const box = $("#match-detail");
  box.hidden = false;
  box.innerHTML = `<div class="panel-head">
      <div><h2>Homes for ${esc(child.display_name)}, ${child.age}</h2>
      <p class="muted">Needs ${esc(LEVELS[child.caseworker.required_care_level])} · ${esc(label(child.goals.permanency_goal))}${child.sibling_group_size > 1 ? ` · sibling group of ${child.sibling_group_size}` : ""}</p></div>
      ${can("ai:run") ? `<button class="btn ai" id="ask-ai">Ask AI agent to recommend</button>` : ""}
    </div>
    <div id="ai-report"></div>
    ${results.map(homeRow).join("")}`;
  if ($("#ask-ai")) $("#ask-ai").onclick = (e) => busy(e.currentTarget, "Agent is reviewing the caseload…", async () => {
    const rep = await api(`/api/ai/match/${childId}`, { method: "POST" });
    renderAiReport(rep);
    if (rep.reused) toast("Nothing changed since the last report, so it was reused at no cost.");
    loadUsage();
  });
  api(`/api/ai/match/${childId}`).then(renderAiReport).catch(() => {});
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

function homeRow(r) {
  return `<div class="home-row ${r.eligible ? "" : "ineligible"}">
    <header>
      <div><h3>${esc(r.parent_name)} <span class="muted" style="font-weight:400;font-size:.85rem">${r.parent_id}</span></h3></div>
      <div class="pills">${r.eligible ? meter(r.score) : `<span class="pill bad">Not eligible</span>`}
        ${!r.eligible ? "" : can("placements:approve")
          ? `<button class="btn primary small" data-place="${r.parent_id}">Approve placement</button>`
          : `<span class="pill" title="Only supervisors can approve placements">Supervisor approval required</span>`}</div>
    </header>
    ${r.blockers.length ? list(r.blockers, "blockers") : ""}
    ${r.eligible && r.cautions.length ? list(r.cautions, "cautions") : ""}
    ${r.eligible ? `<details><summary>Score breakdown</summary><div class="factors">${r.factors.map((f) => `
      <div class="factor"><span>${esc(f.name)}</span><span class="score"><span class="meter"><i style="width:${(f.points / f.max_points) * 100}%"></i></span>${f.points}/${f.max_points}</span>
      <span class="muted">${esc(f.detail)}</span></div>`).join("")}</div></details>` : ""}
  </div>`;
}

function renderAiReport(rep) {
  const r = rep.report;
  $("#ai-report").innerHTML = `${withheldNotice(rep)}<div class="ai-box">
    <h3>AI placement recommendation <span class="badge">gpt-6-luna</span></h3>
    <p>${esc(r.child_summary)}</p>
    ${r.departs_from_engine_order ? `<div class="notice">The AI ranked these homes in a different order from the rules engine's scores.
      Read "Why this order" and confirm the reasoning against the case file before relying on it.</div>` : ""}
    ${r.recommendations.map((x) => `<div class="rec">
      <header><div><span class="rank">${x.rank}</span><b>${esc(x.parent_name)}</b> <span class="muted">${x.parent_id}</span></div>${meter(x.compatibility_score)}</header>
      <p>${esc(x.fit_summary)}</p>
      <div class="cols3"><div><h4>Strengths</h4>${list(x.strengths)}</div><div><h4>Concerns</h4>${list(x.concerns, "cautions")}</div>
      <div><h4>Ask before placing</h4>${list(x.questions_for_caseworker)}</div></div>
      <div class="evidence">Based on: ${(x.evidence || []).map((e) => `<code>${esc(e)}</code>`).join(" ")}</div>
    </div>`).join("") || `<p><b>No eligible home.</b></p>`}
    <h4 style="margin:14px 0 4px">Why this order</h4><p>${esc(r.ranking_rationale)}</p>
    ${r.gaps_if_no_good_match ? `<h4 style="margin:10px 0 4px">Recruitment gap</h4><p>${esc(r.gaps_if_no_good_match)}</p>` : ""}
    <h4 style="margin:10px 0 4px">Next steps</h4>${list(r.caseworker_next_steps)}
    ${runFooter(rep, "This is decision support only. The placement decision belongs to the caseworker and supervisor.")}
  </div>`;
}

async function approvePlacement(parentId) {
  const child = state.children.find((c) => c.id === state.openChild);
  const home = state.parents.find((p) => p.id === parentId);
  if (!confirm(`Approve placement of ${child.display_name} with ${home.display_name}?\n\nThis will be logged under your name (${state.user.full_name}).`)) return;
  const note = prompt("Optional note for the placement log:") || "";
  try {
    await api("/api/placements", { method: "POST", body: JSON.stringify({ child_id: child.id, parent_id: parentId, note }) });
    toast(`Placement recorded: ${child.display_name} → ${home.display_name}`);
    await loadAll();
    $("#match-detail").hidden = true;
    renderBoard();
  } catch (e) {
    toast(e.message, true);
  }
}

// ---------- wiring ----------
document.addEventListener("click", (e) => {
  const t = e.target.closest("button, tr");
  if (!t) return;
  if (t.dataset.view) show(t.dataset.view);
  else if (t.dataset.goto) { resetForm($(t.dataset.goto === "parent-form" ? "#pform" : "#cform")); show(t.dataset.goto); }
  else if (t.dataset.open) openRecord(t.dataset.open, t.dataset.id);
  else if (t.dataset.add) addItem(t.closest("form"), t.dataset.add);
  else if (t.dataset.reset) resetForm($(`#${t.dataset.reset}`));
  else if (t.dataset.match) openMatch(t.dataset.match);
  else if (t.dataset.place) approvePlacement(t.dataset.place);
  else if (t.dataset.edit) {
    const kind = t.dataset.edit;
    const form = $(kind === "parent" ? "#pform" : "#cform");
    fillOptions(form);
    populate(form, (kind === "parent" ? state.parents : state.children).find((r) => r.id === t.dataset.id));
    closeDrawer();
    show(kind === "parent" ? "parent-form" : "child-form");
  } else if (t.dataset.summary) {
    busy(t, "Summarizing…", async () => {
      const rep = await api(`/api/ai/summary/${t.dataset.summary}/${t.dataset.id}`, { method: "POST" });
      renderSummary(rep);
      if (rep.reused) toast("Nothing changed since the last summary, so it was reused at no cost.");
      loadUsage();
    });
  }
});
$("#drawer-close").onclick = closeDrawer;
$("#drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") closeDrawer(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
$("#has-co").onchange = (e) => toggleCo(e.target.checked);
$("#refresh-board").onclick = renderBoard;
$("#pform").onsubmit = (e) => { e.preventDefault(); submitForm(e.target, "parent"); };
$("#cform").onsubmit = (e) => { e.preventDefault(); submitForm(e.target, "child"); };

// ---------- sign-in, password change, role-aware UI ----------
function showGate(which) {
  $$(".gate").forEach((g) => (g.hidden = g.id !== which));
  document.body.classList.toggle("gated", !!which);
  if (which) {
    closeDrawer();
    if (which === "login") state.user = null;
    setTimeout(() => $(`#${which} input`)?.focus(), 50);
  }
}

function applyRole() {
  const u = state.user;
  $("#userbar").innerHTML = u ? `<span class="who"><b>${esc(u.full_name)}</b><span class="pill role">${esc(label(u.role))}</span></span>
    <button class="btn ghost small" id="btn-pw">Change password</button>
    <button class="btn ghost small" id="btn-logout">Sign out</button>` : "";
  $$("[data-perm]").forEach((el) => (el.hidden = !can(el.dataset.perm)));
  if ($("#btn-logout")) {
    $("#btn-logout").onclick = async () => { await api("/api/auth/logout", { method: "POST" }).catch(() => {}); showGate("login"); };
    $("#btn-pw").onclick = () => showGate("pwchange");
  }
}

async function start(user) {
  state.user = user;
  if (user.must_change_password) { applyRole(); showGate("pwchange"); return; }
  showGate(null);
  applyRole();
  const first = $$(".tabs button").find((b) => !b.hidden);
  if (first) show(first.dataset.view);
  if (can("records:read")) await loadAll();
}

$("#login-form").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  $("#login-error").textContent = "";
  try {
    const user = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username: f.username.value, password: f.password.value }) });
    f.reset();
    await start(user);
  } catch (err) {
    $("#login-error").textContent = err.message;
  }
};

$("#pw-form").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  $("#pw-error").textContent = "";
  if (f.new_password.value !== f.confirm_password.value) { $("#pw-error").textContent = "The new passwords don't match."; return; }
  try {
    await api("/api/auth/password", { method: "POST", body: JSON.stringify({ current_password: f.current_password.value, new_password: f.new_password.value }) });
    f.reset();
    toast("Password changed.");
    await start(await api("/api/auth/me"));
  } catch (err) {
    $("#pw-error").textContent = err.message;
  }
};
$("#pw-cancel").onclick = () => { if (state.user && !state.user.must_change_password) showGate(null); };

// ---------- staff accounts (admin) ----------
async function renderUsers() {
  const users = await api("/api/users");
  const roles = ["caseworker", "supervisor", "admin"];
  $("#user-list").innerHTML = `<table><tr><th>Username</th><th>Name</th><th>Role</th><th>Status</th><th>Last sign-in</th><th></th></tr>
    ${users.map((u) => `<tr data-user="${u.id}">
      <td><b>${esc(u.username)}</b></td><td>${esc(u.full_name)}</td>
      <td><select data-user-role="${u.id}" ${u.id === state.user.id ? "disabled" : ""}>${roles.map((r) => `<option value="${r}" ${r === u.role ? "selected" : ""}>${label(r)}</option>`).join("")}</select></td>
      <td>${u.active ? `<span class="pill ok">Active</span>` : `<span class="pill bad">Disabled</span>`}${u.must_change_password ? ` <span class="pill warn">Must change password</span>` : ""}</td>
      <td class="muted">${u.last_login ? new Date(u.last_login).toLocaleString() : "Never"}</td>
      <td class="row-actions">${u.id === state.user.id ? `<span class="muted">You</span>` : `
        <button class="btn ghost small" data-user-toggle="${u.id}" data-active="${u.active}">${u.active ? "Disable" : "Enable"}</button>
        <button class="btn ghost small" data-user-reset="${u.id}">Reset password</button>`}</td>
    </tr>`).join("")}</table>`;
  $$("[data-user-role]").forEach((sel) => (sel.onchange = () => updateUser(sel.dataset.userRole, { role: sel.value })));
  $$("[data-user-toggle]").forEach((b) => (b.onclick = () => updateUser(b.dataset.userToggle, { active: b.dataset.active !== "true" })));
  $$("[data-user-reset]").forEach((b) => (b.onclick = () => {
    const pw = prompt("Temporary password (12+ characters). The user must change it at next sign-in:");
    if (pw) updateUser(b.dataset.userReset, { temporary_password: pw });
  }));
}

async function updateUser(id, changes) {
  try {
    await api(`/api/users/${id}`, { method: "PATCH", body: JSON.stringify(changes) });
    toast("Account updated. The user's open sessions were signed out.");
  } catch (e) {
    toast(e.message, true);
  }
  renderUsers();
}

$("#user-form").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    const u = await api("/api/users", { method: "POST", body: JSON.stringify({
      username: f.username.value.trim(), full_name: f.full_name.value.trim(), role: f.role.value, temporary_password: f.temporary_password.value }) });
    f.reset();
    toast(`Account ${u.username} created. Share the temporary password securely; they must change it at first sign-in.`);
    renderUsers();
  } catch (err) {
    toast(err.message, true);
  }
};

// ---------- access log (supervisor, admin) ----------
async function renderAccessLog() {
  const rows = await api("/api/audit/access");
  $("#access-list").innerHTML = rows.length ? `<table><tr><th>When</th><th>User</th><th>Action</th><th>Result</th><th>Detail</th></tr>
    ${rows.map((r) => `<tr><td class="muted">${new Date(r.at).toLocaleString()}</td>
      <td>${esc(r.username || "-")}${r.role ? ` <span class="muted">(${esc(r.role)})</span>` : ""}</td>
      <td>${esc(r.action)}</td>
      <td><span class="pill ${r.status < 300 ? "ok" : r.status === 403 || r.status === 401 ? "bad" : "warn"}">${esc(r.status ?? "")}</span></td>
      <td class="muted">${esc(r.detail || "")}</td></tr>`).join("")}</table>` : `<p class="muted">No activity yet.</p>`;
}
$("#refresh-access").onclick = renderAccessLog;

fillOptions(document);
api("/api/auth/me").then(start).catch(() => showGate("login"));
