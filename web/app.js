"use strict";

const state = {
  users: [],
  totalChunks: 0,
  currentUser: null,
};

const SUGGESTIONS = [
  "What is the Q3 infrastructure budget?",
  "What is the Atlas migration rollback plan?",
  "What is the L4 engineer salary band?",
  "What caused the June payments outage?",
  "How many paid time off days do we get?",
  "What is the Project Hawk offer range?",
];

const el = (id) => document.getElementById(id);

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

function toast(message) {
  const t = el("toast");
  t.textContent = message;
  t.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => (t.hidden = true), 4000);
}

function groupChip(group) {
  const label = group.replace(/^group:/, "");
  const cls = group === "group:admin" ? "chip chip-admin" : "chip chip-group";
  return `<span class="${cls}">${escapeHtml(label)}</span>`;
}

async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

/* ---------- Bootstrap ---------- */

async function checkHealth() {
  const pill = el("health-pill");
  try {
    const res = await fetch("/health");
    if (!res.ok) throw new Error();
    pill.textContent = "online";
    pill.className = "stat-pill ok";
  } catch {
    pill.textContent = "offline";
    pill.className = "stat-pill down";
  }
}

async function loadUsers() {
  const data = await (await fetch("/api/users")).json();
  state.users = data.users;
  state.totalChunks = data.total_chunks;
  el("corpus-stat").textContent = `${data.total_chunks} chunks indexed`;
  renderUsers();
  selectUser(state.users[0]);
}

function renderUsers() {
  const list = el("user-list");
  list.innerHTML = "";
  for (const user of state.users) {
    const card = document.createElement("button");
    card.className = "user-card";
    card.dataset.userId = user.user_id;
    const pct = Math.round((user.visible_chunks / state.totalChunks) * 100);
    card.innerHTML = `
      <div class="name">
        <span>${escapeHtml(user.name)}</span>
        <span class="visible">${user.visible_chunks}/${state.totalChunks} · ${pct}%</span>
      </div>
      <div class="user-groups">${user.groups.map(groupChip).join("")}</div>`;
    card.addEventListener("click", () => selectUser(user));
    list.appendChild(card);
  }
}

function selectUser(user) {
  state.currentUser = user;
  document.querySelectorAll(".user-card").forEach((c) => {
    c.classList.toggle("is-active", c.dataset.userId === user.user_id);
  });
  el("ask-user-name").textContent = user.name;
  const pct = Math.round((user.visible_chunks / state.totalChunks) * 100);
  el("ask-user-visibility").textContent = `can see ${user.visible_chunks} of ${state.totalChunks} chunks (${pct}%)`;
}

/* ---------- Ask ---------- */

function renderSuggestions() {
  const box = el("suggestions");
  box.innerHTML = "";
  for (const q of SUGGESTIONS) {
    const b = document.createElement("button");
    b.className = "suggestion";
    b.type = "button";
    b.textContent = q;
    b.addEventListener("click", () => {
      el("ask-input").value = q;
      el("ask-form").requestSubmit();
    });
    box.appendChild(b);
  }
}

function renderAnswer(data) {
  el("ask-empty").hidden = true;
  el("ask-result").hidden = false;

  const withCitations = escapeHtml(data.answer).replace(
    /\[([A-Za-z0-9_-]+)\]/g,
    (_, id) => `<cite data-doc="${id}">${id}</cite>`
  );
  el("answer-body").innerHTML = withCitations || "<em>No answer produced.</em>";
  el("answer-latency").textContent = `${Math.round(data.latency_ms.total)} ms`;

  el("answer-citations").innerHTML = data.citations.length
    ? `<span class="muted">Cited sources:</span> ` +
      data.citations.map((c) => `<span class="chip chip-group">${escapeHtml(c)}</span>`).join("")
    : `<span class="muted">No sources cited.</span>`;

  el("evidence-count").textContent = `${data.evidence.length} authorized chunk(s)`;
  const list = el("evidence-list");
  list.innerHTML = "";
  if (!data.evidence.length) {
    list.innerHTML = `<p class="muted">No permitted evidence matched this question for this user.</p>`;
  }
  for (const ev of data.evidence) {
    const item = document.createElement("div");
    item.className = "evidence-item" + (ev.cited ? " cited" : "");
    item.dataset.doc = ev.doc_id;
    const acl = ev.allowed_principals.length
      ? ev.allowed_principals.map(groupChip).join("")
      : `<span class="chip chip-admin">admin only</span>`;
    item.innerHTML = `
      <div class="ev-head">
        <span class="ev-title">${escapeHtml(ev.title)}</span>
        <span class="ev-meta">
          <span class="chip chip-src">${escapeHtml(ev.source)}</span>
          <span class="chip chip-ghost mono">${escapeHtml(ev.doc_id)}</span>
          <span class="score-badge">${ev.score}</span>
          ${ev.cited ? '<span class="cited-badge">cited</span>' : ""}
        </span>
      </div>
      <div class="ev-text">${escapeHtml(ev.text.slice(0, 320))}${ev.text.length > 320 ? "&hellip;" : ""}</div>
      <div class="ev-acl"><span class="lbl">visible to:</span> ${acl}</div>`;
    list.appendChild(item);
  }

  renderTrace(data.trace, data.latency_ms);
  wireCitationClicks();
}

function renderTrace(trace, latency) {
  const body = el("trace-body");
  const steps = [];

  steps.push(`
    <div class="trace-step">
      <div class="t-title">1 · Query planning ${trace.planner_fallback ? "(fallback: single query)" : ""}</div>
      <div class="t-detail">Decomposed into ${trace.subqueries.length} sub-quer${trace.subqueries.length === 1 ? "y" : "ies"}:
        ${trace.subqueries.map((q) => `<span class="mono">&ldquo;${escapeHtml(q)}&rdquo;</span>`).join(", ")}
        · ${Math.round(latency.planning)} ms</div>
    </div>`);

  const rounds = trace.rounds || [];
  for (const round of rounds) {
    const searches = (round.tool_calls || []).filter((c) => c.tool === "search" && !c.error);
    const others = (round.tool_calls || []).filter((c) => c.tool !== "search");
    const label = round.type === "plan"
      ? "2 · Permission-filtered retrieval"
      : `2 · Agent refinement round ${round.round}`;
    const reason = round.type === "refine" && round.reason
      ? `<div class="t-detail"><em>Agent judged evidence insufficient: ${escapeHtml(round.reason)}</em></div>`
      : "";
    const details = searches.map((r) => {
      const filtered = r.total_candidates - r.allowed_candidates;
      return `
        <div class="t-detail">
          Query <span class="mono">&ldquo;${escapeHtml(r.args.query)}&rdquo;</span>:
          searched ${r.allowed_candidates} authorized chunks
          (<strong>${filtered}</strong> restricted chunks excluded before scoring),
          returned ${r.returned}.
        </div>`;
    });
    const toolNotes = others.map((c) => `
      <div class="t-detail">Tool <span class="mono">${escapeHtml(c.tool)}</span> called${c.error ? ` (error: ${escapeHtml(c.error)})` : ""}.</div>`);
    steps.push(`
      <div class="trace-step">
        <div class="t-title">${label}</div>
        ${reason}${details.join("")}${toolNotes.join("")}
      </div>`);
    if (round.assessment && round.assessment.sufficient) {
      steps.push(`
        <div class="trace-step">
          <div class="t-title">2 · Sufficiency check · ${Math.round(latency.assessment || 0)} ms</div>
          <div class="t-detail">Agent judged the gathered evidence sufficient${round.assessment.reason ? `: ${escapeHtml(round.assessment.reason)}` : ""}.</div>
        </div>`);
    }
  }

  steps.push(`
    <div class="trace-step">
      <div class="t-title">3 · Independent verification · ${Math.round(latency.verification)} ms</div>
      <div class="t-detail">${trace.verified_chunks} chunk(s) re-confirmed against this user's permissions.
        Rejections: ${trace.verification_rejections.length
          ? `<span style="color:var(--danger)">${trace.verification_rejections.join(", ")}</span>`
          : "none"}.</div>
    </div>`);

  steps.push(`
    <div class="trace-step">
      <div class="t-title">4 · Answer synthesis &amp; citation sanitization · ${Math.round(latency.synthesis)} ms</div>
      <div class="t-detail">Answer generated from verified evidence only; unauthorized or invented citations stripped.</div>
    </div>`);

  const critic = trace.critic || {};
  if (critic.verdict && critic.verdict !== "skipped" && critic.verdict !== "unavailable") {
    const cls = critic.verdict === "grounded" ? "" : ' style="color:var(--danger)"';
    const claims = (critic.unsupported_claims || []).length
      ? ` Unsupported claims flagged: ${critic.unsupported_claims.map((c) => `&ldquo;${escapeHtml(c)}&rdquo;`).join("; ")}`
      : "";
    steps.push(`
      <div class="trace-step">
        <div class="t-title">5 · Groundedness critic (advisory) · ${Math.round(latency.critic || 0)} ms</div>
        <div class="t-detail">Verdict: <strong${cls}>${escapeHtml(critic.verdict.replace("_", " "))}</strong>.${claims}</div>
      </div>`);
  }

  body.innerHTML = steps.join("");
}

function wireCitationClicks() {
  document.querySelectorAll("cite[data-doc]").forEach((c) => {
    c.addEventListener("click", () => {
      const target = document.querySelector(`.evidence-item[data-doc="${c.dataset.doc}"]`);
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "center" });
        target.style.transition = "outline 0.2s";
        target.style.outline = "2px solid var(--brand)";
        setTimeout(() => (target.style.outline = "none"), 1200);
      }
    });
  });
}

async function onAsk(event) {
  event.preventDefault();
  const question = el("ask-input").value.trim();
  if (!question || !state.currentUser) return;
  const btn = el("ask-btn");
  btn.disabled = true;
  btn.textContent = "Thinking";
  el("ask-empty").hidden = true;
  el("ask-result").hidden = false;
  el("answer-body").innerHTML = `<div class="loading"><span class="spinner"></span> Planning, retrieving, verifying, and synthesizing&hellip;</div>`;
  el("answer-citations").innerHTML = "";
  el("evidence-list").innerHTML = "";
  el("evidence-count").textContent = "";
  el("trace-body").innerHTML = "";
  try {
    const data = await api("/api/ask", {
      user_id: state.currentUser.user_id,
      question,
    });
    renderAnswer(data);
  } catch (err) {
    el("answer-body").innerHTML = `<span style="color:var(--danger)">${escapeHtml(err.message)}</span>`;
    toast(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Ask";
  }
}

/* ---------- Compare ---------- */

const MODE_LABELS = {
  bm25: "Keyword (BM25)",
  vector: "Semantic (vectors)",
  hybrid: "Hybrid (RRF fusion)",
  "hybrid+rerank": "Hybrid + reranker",
};

async function onCompare(event) {
  event.preventDefault();
  const query = el("compare-input").value.trim();
  if (!query || !state.currentUser) return;
  const btn = el("compare-btn");
  btn.disabled = true;
  btn.textContent = "Running";
  el("compare-grid").innerHTML = `<div class="loading"><span class="spinner"></span> Running all four retrieval modes&hellip;</div>`;
  el("compare-visibility").hidden = true;
  try {
    const data = await api("/api/search", {
      user_id: state.currentUser.user_id,
      query,
    });
    renderCompare(data);
  } catch (err) {
    el("compare-grid").innerHTML = `<span style="color:var(--danger)">${escapeHtml(err.message)}</span>`;
    toast(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Compare";
  }
}

function renderCompare(data) {
  const filtered = data.total_chunks - data.visible_chunks;
  const banner = el("compare-visibility");
  banner.hidden = false;
  banner.innerHTML = `As <strong>${escapeHtml(state.currentUser.name)}</strong>, all modes search ${data.visible_chunks} authorized chunks — ${filtered} chunks are excluded up front and never ranked.`;

  const grid = el("compare-grid");
  grid.innerHTML = "";
  for (const mode of ["bm25", "vector", "hybrid", "hybrid+rerank"]) {
    const md = data.modes[mode];
    const lat = md.latency_ms.total ? `${Math.round(md.latency_ms.total)} ms` : "";
    const card = document.createElement("div");
    card.className = "mode-card";
    const rows = md.results.length
      ? md.results
          .map(
            (r, i) => `
        <div class="mode-result">
          <div class="mr-title">${i + 1}. ${escapeHtml(r.title)}</div>
          <div class="mr-meta">
            <span class="chip chip-src">${escapeHtml(r.source)}</span>
            <span class="mono">${escapeHtml(r.doc_id)}</span>
            <span class="score-badge">${r.score}</span>
          </div>
        </div>`
          )
          .join("")
      : `<p class="muted">No results.</p>`;
    card.innerHTML = `<h4>${MODE_LABELS[mode]}</h4><div class="mode-latency">${lat}</div>${rows}`;
    grid.appendChild(card);
  }
}

/* ---------- Tabs ---------- */

function wireTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("is-active"));
      document.querySelectorAll(".panel").forEach((p) => p.classList.remove("is-active"));
      tab.classList.add("is-active");
      document.querySelector(`.panel[data-panel="${tab.dataset.tab}"]`).classList.add("is-active");
    });
  });
}

/* ---------- Init ---------- */

async function init() {
  wireTabs();
  renderSuggestions();
  el("ask-form").addEventListener("submit", onAsk);
  el("compare-form").addEventListener("submit", onCompare);
  await checkHealth();
  try {
    await loadUsers();
  } catch (err) {
    toast("Could not load users. Is the server running and indexed?");
  }
}

init();
