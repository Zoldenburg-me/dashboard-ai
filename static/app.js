"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const STATUS = {
  todo: { label: "To do", tone: "gray", icon: "radio_button_unchecked" },
  in_progress: { label: "In progress", tone: "primary", icon: "progress_activity" },
  blocked: { label: "Blocked", tone: "red", icon: "block" },
  review: { label: "Review", tone: "amber", icon: "rate_review" },
  done: { label: "Done", tone: "green", icon: "check_circle" },
};
const AGENT_STATUS = {
  working: { label: "Working", tone: "primary" }, idle: { label: "Idle", tone: "green" },
  blocked: { label: "Blocked", tone: "red" }, offline: { label: "Offline", tone: "gray" },
};
const PRIORITIES = ["urgent", "high", "medium", "low"];
const EV = {
  "task.created": ["add_task", "cyan"], "task.claimed": ["front_hand", "primary"], "task.updated": ["sync_alt", "primary"],
  "task.completed": ["task_alt", "green"], "task.progress": ["edit_note", "gray"], "task.note": ["sticky_note_2", "gray"],
  "task.deleted": ["delete", "red"], "todo.added": ["playlist_add", "cyan"], "todo.done": ["check", "green"],
  "todo.reopened": ["undo", "amber"], "project.created": ["create_new_folder", "cyan"], "project.updated": ["folder", "gray"],
  "agent.registered": ["person_add", "primary"], "agent.status": ["smart_toy", "amber"],
};
const PALETTE = ["#7c8cff", "#3ccf91", "#f4b740", "#ff6b6b", "#4cc9f0", "#b07cff", "#ff8fab", "#5eead4", "#fb923c"];
const colorFor = (s) => { let h = 0; for (const c of String(s)) h = (h * 31 + c.charCodeAt(0)) >>> 0; return PALETTE[h % PALETTE.length]; };
const initials = (n) => String(n || "?").split(/[\s\-_.]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";

function ago(ts) {
  if (!ts) return "never";
  const s = Date.now() / 1000 - ts;
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
const fmtTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");

async function api(path, opts = {}) {
  const res = await fetch("/api" + path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json", "X-Agent": "dashboard" },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast"), 2600);
}

const pill = (label, tone, cls = "") => `<span class="pill tone-${tone} ${cls}"><span class="dot"></span>${esc(label)}</span>`;
const statusPill = (s) => pill(STATUS[s]?.label || s, STATUS[s]?.tone || "gray");
const agentPill = (s) => pill(AGENT_STATUS[s]?.label || s, AGENT_STATUS[s]?.tone || "gray", s);
const avatar = (name, cls = "avatar") => `<span class="${cls}" style="background:${colorFor(name)}">${esc(initials(name))}</span>`;
const empty = (icon, text) => `<div class="empty"><span class="ms">${icon}</span>${text}</div>`;

function progressBar(p) {
  const total = p.task_count || 0;
  const pct = (n) => (total ? (n / total) * 100 : 0);
  return `<div class="progress">
      <i style="width:${pct(p.done_count)}%;background:var(--green)"></i>
      <i style="width:${pct(p.active_count)}%;background:var(--primary)"></i>
      <i style="width:${pct(p.blocked_count)}%;background:var(--red)"></i>
    </div>
    <div class="progress-label"><span>${p.done_count}/${total} done</span><span>${total ? Math.round(pct(p.done_count)) : 0}%</span></div>`;
}

const freshIds = new Set();
function eventItem(e, fresh = false) {
  const [icon, tone] = EV[e.kind] || ["bolt", "gray"];
  const who = e.agent_name ? `<b>${esc(e.agent_name)}</b> ` : "";
  const meta = [
    e.project_name ? `<a href="#/projects/${e.project_id}">${esc(e.project_name)}</a>` : "",
    e.task_id && e.task_title ? `<a href="#" data-task="${e.task_id}">#${e.task_id} ${esc(e.task_title)}</a>` : "",
    `<span title="${esc(fmtTime(e.ts))}">${ago(e.ts)}</span>`,
  ].filter(Boolean).join(" · ");
  if (freshIds.has(e.id)) fresh = true;
  return `<div class="ev${fresh ? " fresh" : ""}">
    <div class="ev-icon tone-${tone}"><span class="ms">${icon}</span></div>
    <div><div class="ev-text">${who}${esc(e.message)}</div><div class="ev-meta">${meta}</div></div>
  </div>`;
}

function agentCard(a) {
  const task = a.current_task_title
    ? `<span class="proj">${esc(a.current_project_name || "")}</span>#${a.current_task_id} ${esc(a.current_task_title)}`
    : `<span class="muted">${esc(a.status_note || "No active task")}</span>`;
  return `<div class="agent-card" data-href="#/agents/${a.id}">
    <div class="agent-top">${avatar(a.name)}
      <div style="min-width:0"><div class="agent-name">${esc(a.name)}</div><div class="agent-role">${esc(a.role || "agent")}</div></div>
      ${agentPill(a.effective_status)}
    </div>
    <div class="agent-task">${task}</div>
    <div class="agent-meta">
      <span><span class="ms">pending_actions</span>${a.open_tasks} open</span>
      <span><span class="ms">task_alt</span>${a.done_tasks} done</span>
      <span style="margin-left:auto"><span class="ms">schedule</span>${ago(a.last_seen)}</span>
    </div>
  </div>`;
}

function taskCard(t) {
  return `<div class="tcard" draggable="true" data-task="${t.id}">
    <div class="tcard-title">${esc(t.title)}</div>
    <div class="tcard-foot">
      <span class="prio prio-${t.priority}">${t.priority}</span>
      ${t.todo_count ? `<span><span class="ms">checklist</span>${t.todo_done}/${t.todo_count}</span>` : ""}
      <span class="mono">#${t.id}</span>
      <span class="who">${t.assignee_name ? avatar(t.assignee_name, "mini-avatar") + esc(t.assignee_name) : '<span class="muted">Unassigned</span>'}</span>
    </div>
  </div>`;
}

function taskTable(tasks, showProject = true) {
  if (!tasks.length) return empty("inbox", "No tasks match.");
  return `<div class="table-wrap"><table><thead><tr>
    <th>Task</th>${showProject ? "<th>Project</th>" : ""}<th>Status</th><th>Priority</th><th>Assignee</th><th>Todos</th><th>Updated</th>
  </tr></thead><tbody>${tasks.map((t) => `<tr data-task="${t.id}">
    <td><span class="mono muted">#${t.id}</span> ${esc(t.title)}</td>
    ${showProject ? `<td class="muted">${esc(t.project_name)}</td>` : ""}
    <td>${statusPill(t.status)}</td>
    <td><span class="prio prio-${t.priority}">${t.priority}</span></td>
    <td>${t.assignee_name ? `<span style="display:inline-flex;gap:6px;align-items:center">${avatar(t.assignee_name, "mini-avatar")}${esc(t.assignee_name)}</span>` : '<span class="muted">—</span>'}</td>
    <td class="muted">${t.todo_count ? `${t.todo_done}/${t.todo_count}` : "—"}</td>
    <td class="muted">${ago(t.updated_at)}</td>
  </tr>`).join("")}</tbody></table></div>`;
}

/* ---------------- views ---------------- */
const state = { projects: [], route: "", taskFilter: { status: "", project: "", assignee: "" }, search: "" };

async function viewOverview() {
  const [s, agents, projects, events] = await Promise.all([api("/stats"), api("/agents"), api("/projects"), api("/events?limit=25")]);
  const max = Math.max(1, ...s.activity_by_hour);
  const kpi = (icon, tone, label, value, hint) =>
    `<div class="card kpi"><div class="label"><span class="ms tone-${tone}">${icon}</span>${label}</div><div class="value">${value}</div><div class="hint">${hint}</div></div>`;
  setCrumbs([["Overview"]]);
  return `
  <div class="page-head"><div><h1>Mission control</h1><p class="subtitle">What every agent is doing right now, across all projects.</p></div></div>
  <div class="grid kpis">
    ${kpi("smart_toy", "primary", "Agents working", `${s.agents_working}<span class="muted" style="font-size:16px"> / ${s.agents_total}</span>`, `${s.agents_online} online · ${s.agents_blocked} blocked`)}
    ${kpi("pending_actions", "cyan", "Open tasks", s.tasks_open, `${s.tasks_in_progress} in progress`)}
    ${kpi("task_alt", "green", "Done (24h)", s.tasks_done_24h, `${s.todos_done_24h} todos checked off`)}
    ${kpi("block", "red", "Blocked", s.tasks_blocked, s.tasks_blocked ? "needs attention" : "all clear")}
    <div class="card kpi"><div class="label"><span class="ms tone-amber">monitoring</span>Activity (24h)</div>
      <div class="spark" style="margin-top:10px">${s.activity_by_hour.map((n, i) => `<i style="height:${(n / max) * 100}%" title="${n} events, ${24 - i}h ago"></i>`).join("")}</div>
      <div class="hint">${s.events_24h} events</div></div>
  </div>
  <div class="grid split">
    <div class="grid" style="align-content:start">
      <section class="card"><div class="card-head"><h2><span class="ms">smart_toy</span>Agents</h2><a class="link" href="#/agents">View all</a></div>
        <div class="card-body">${agents.length ? `<div class="agent-grid">${agents.slice(0, 9).map(agentCard).join("")}</div>`
          : empty("smart_toy", "No agents yet. Agents appear when they first write, e.g.<br><code>python agentctl.py --agent my-bot hb --status idle</code>")}</div></section>
      <section class="card"><div class="card-head"><h2><span class="ms">folder_open</span>Projects</h2><a class="link" href="#/projects">View all</a></div>
        <div class="card-body">${projects.length ? `<div class="project-list">${projects.slice(0, 8).map((p) => `
          <div class="project-row" data-href="#/projects/${p.id}">
            <div style="min-width:0"><div class="pname"><span class="nav-projects"><span class="swatch" style="background:${colorFor(p.name)};display:inline-block"></span></span>${esc(p.name)}</div><div class="pdesc">${esc(p.description || "—")}</div></div>
            <div>${progressBar(p)}</div>
            <div class="muted" style="font-size:12px;text-align:right">${ago(p.last_activity)}</div>
          </div>`).join("")}</div>` : empty("create_new_folder", "No projects yet. Create one with the <b>Project</b> button.")}</div></section>
    </div>
    <section class="card" style="align-self:start"><div class="card-head"><h2><span class="ms">timeline</span>Live activity</h2><a class="link" href="#/activity">Full log</a></div>
      <div class="card-body"><div class="feed" id="liveFeed">${events.map((e) => eventItem(e)).join("") || empty("timeline", "Nothing yet.")}</div></div></section>
  </div>`;
}

async function viewProjects() {
  const projects = await api("/projects");
  setCrumbs([["Projects"]]);
  return `<div class="page-head"><div><h1>Projects</h1><p class="subtitle">${projects.length} projects</p></div></div>
  ${projects.length ? `<div class="project-cards">${projects.map((p) => `
    <div class="card project-card" data-href="#/projects/${p.id}">
      <div style="display:flex;gap:10px;align-items:center"><span class="avatar" style="background:${colorFor(p.name)}"><span class="ms">folder</span></span>
        <div style="min-width:0;flex:1"><div class="pname">${esc(p.name)}</div><div class="pdesc">${esc(p.description || "No description")}</div></div>
        ${pill(p.status, p.status === "active" ? "green" : "gray")}</div>
      <div>${progressBar(p)}</div>
      <div class="stats"><div><b>${p.task_count - p.done_count}</b>open</div><div><b>${p.active_count}</b>in progress</div><div><b>${p.blocked_count}</b>blocked</div><div><b>${p.agent_count}</b>agents</div>
        <div style="margin-left:auto;text-align:right"><b style="font-size:12px;font-weight:500">${ago(p.last_activity)}</b>last activity</div></div>
    </div>`).join("")}</div>` : `<div class="card">${empty("create_new_folder", "No projects yet.")}</div>`}`;
}

async function viewProject(id) {
  const [p, events] = await Promise.all([api(`/projects/${id}`), api(`/events?project=${id}&limit=40`)]);
  setCrumbs([["Projects", "#/projects"], [p.name]]);
  const tasks = filterSearch(p.tasks);
  const cols = Object.keys(STATUS).map((s) => {
    const list = tasks.filter((t) => t.status === s);
    return `<div class="col" data-status="${s}"><div class="col-head"><span class="ms" style="color:var(--${STATUS[s].tone === "gray" ? "text-3" : STATUS[s].tone})">${STATUS[s].icon}</span>${STATUS[s].label}<span class="n">${list.length}</span></div>
      <div class="col-body">${list.map(taskCard).join("")}</div></div>`;
  }).join("");
  return `<div class="page-head">
      <div><h1>${esc(p.name)}</h1><p class="subtitle">${esc(p.description || "No description")}</p></div><div class="spacer"></div>
      <select class="chip" id="projStatus">${["active", "paused", "completed", "archived"].map((s) => `<option ${s === p.status ? "selected" : ""}>${s}</option>`).join("")}</select>
      <button class="btn btn-primary" id="projNewTask" data-project="${p.id}"><span class="ms">add</span>Task</button>
    </div>
    <div class="grid kpis" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr))">
      <div class="card kpi"><div class="label">Progress</div><div style="margin-top:12px">${progressBar(p)}</div></div>
      <div class="card kpi"><div class="label">Open</div><div class="value">${p.task_count - p.done_count}</div></div>
      <div class="card kpi"><div class="label">In progress</div><div class="value">${p.active_count}</div></div>
      <div class="card kpi"><div class="label">Blocked</div><div class="value" style="color:${p.blocked_count ? "var(--red)" : "inherit"}">${p.blocked_count}</div></div>
      <div class="card kpi"><div class="label">Agents</div><div class="value">${p.agent_count}</div></div>
    </div>
    <div class="board" id="board" data-project="${p.id}">${cols}</div>
    <section class="card" style="margin-top:18px"><div class="card-head"><h2><span class="ms">timeline</span>Project activity</h2></div>
      <div class="card-body"><div class="feed">${events.map((e) => eventItem(e)).join("") || empty("timeline", "No activity yet.")}</div></div></section>`;
}

async function viewAgents() {
  const agents = await api("/agents");
  setCrumbs([["Agents"]]);
  const by = (s) => agents.filter((a) => a.effective_status === s).length;
  return `<div class="page-head"><div><h1>Agents</h1><p class="subtitle">${agents.length} registered · ${by("working")} working · ${by("idle")} idle · ${by("blocked")} blocked · ${by("offline")} offline</p></div></div>
    ${agents.length ? `<div class="agent-grid">${agents.map(agentCard).join("")}</div>` : `<div class="card">${empty("smart_toy", "No agents yet.")}</div>`}`;
}

async function viewAgent(id) {
  const a = await api(`/agents/${id}`);
  setCrumbs([["Agents", "#/agents"], [a.name]]);
  return `<div class="page-head" style="align-items:center">${avatar(a.name)}
      <div><h1>${esc(a.name)}</h1><p class="subtitle">${esc(a.role || "agent")} · last seen ${ago(a.last_seen)}${a.status_note ? " · " + esc(a.status_note) : ""}</p></div>
      <div class="spacer"></div>${agentPill(a.effective_status)}</div>
    <div class="grid split">
      <section class="card"><div class="card-head"><h2><span class="ms">checklist</span>Tasks</h2></div><div class="card-body" style="padding:0 4px 8px">${taskTable(a.tasks)}</div></section>
      <section class="card" style="align-self:start"><div class="card-head"><h2><span class="ms">timeline</span>Recent work</h2></div>
        <div class="card-body"><div class="feed">${a.events.map((e) => eventItem(e)).join("") || empty("timeline", "No activity.")}</div></div></section>
    </div>`;
}

async function viewTasks() {
  const f = state.taskFilter;
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
  const [tasks, agents] = await Promise.all([api("/tasks?" + qs), api("/agents")]);
  setCrumbs([["Tasks"]]);
  const chips = [["", "All"], ...Object.entries(STATUS).map(([k, v]) => [k, v.label])]
    .map(([k, l]) => `<button class="chip ${f.status === k ? "on" : ""}" data-fstatus="${k}">${l}</button>`).join("");
  return `<div class="page-head"><div><h1>Tasks</h1><p class="subtitle">${tasks.length} tasks</p></div></div>
    <div class="filters">${chips}
      <select class="chip" id="fProject"><option value="">All projects</option>${state.projects.map((p) => `<option value="${p.id}" ${String(f.project) === String(p.id) ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select>
      <select class="chip" id="fAssignee"><option value="">Any agent</option>${agents.map((a) => `<option value="${a.id}" ${String(f.assignee) === String(a.id) ? "selected" : ""}>${esc(a.name)}</option>`).join("")}</select>
    </div>
    <section class="card" style="padding:0 4px 6px">${taskTable(filterSearch(tasks))}</section>`;
}

async function viewActivity() {
  const events = await api("/events?limit=300");
  setCrumbs([["Activity"]]);
  return `<div class="page-head"><div><h1>Activity log</h1><p class="subtitle">Every change agents and people make is recorded here.</p></div></div>
    <section class="card"><div class="card-body" style="padding-top:14px"><div class="feed" id="liveFeed">${events.map((e) => eventItem(e)).join("") || empty("timeline", "Nothing yet.")}</div></div></section>`;
}

function filterSearch(tasks) {
  const q = state.search.trim().toLowerCase();
  if (!q) return tasks;
  return tasks.filter((t) => `${t.title} ${t.description} ${t.assignee_name || ""} #${t.id}`.toLowerCase().includes(q));
}

function setCrumbs(parts) {
  $("#crumbs").innerHTML = parts.map(([l, h], i) =>
    (i ? '<span class="ms" style="font-size:16px">chevron_right</span>' : "") + (h ? `<a href="${h}">${esc(l)}</a>` : `<b>${esc(l)}</b>`)).join("");
}

/* ---------------- router ---------------- */
async function render() {
  const hash = location.hash.replace(/^#/, "") || "/";
  const [, section, id] = hash.split("/");
  state.route = hash;
  $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === (section || "overview")));
  try {
    state.projects = await api("/projects");
    renderNavProjects(section === "projects" ? id : null);
    const html = section === "projects" && id ? await viewProject(id)
      : section === "projects" ? await viewProjects()
      : section === "agents" && id ? await viewAgent(id)
      : section === "agents" ? await viewAgents()
      : section === "tasks" ? await viewTasks()
      : section === "activity" ? await viewActivity()
      : await viewOverview();
    const y = window.scrollY;
    $("#view").innerHTML = html;
    window.scrollTo(0, y);
    bindView();
  } catch (e) {
    $("#view").innerHTML = `<div class="card">${empty("error", esc(e.message))}</div>`;
  }
}

function renderNavProjects(activeId) {
  $("#navProjects").innerHTML = state.projects.filter((p) => p.status !== "archived").map((p) =>
    `<a href="#/projects/${p.id}" class="${String(activeId) === String(p.id) ? "active" : ""}"><span class="swatch" style="background:${colorFor(p.name)}"></span><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.name)}</span><span class="count">${p.task_count - p.done_count}</span></a>`).join("")
    || '<div class="muted" style="padding:4px 10px;font-size:12px">None yet</div>';
}

function bindView() {
  $$("[data-href]").forEach((el) => el.addEventListener("click", () => (location.hash = el.dataset.href)));
  $$(".filters [data-fstatus]").forEach((el) => el.addEventListener("click", () => { state.taskFilter.status = el.dataset.fstatus; render(); }));
  $("#fProject")?.addEventListener("change", (e) => { state.taskFilter.project = e.target.value; render(); });
  $("#fAssignee")?.addEventListener("change", (e) => { state.taskFilter.assignee = e.target.value; render(); });
  $("#projNewTask")?.addEventListener("click", (e) => newTaskModal(e.currentTarget.dataset.project));
  $("#projStatus")?.addEventListener("change", async (e) => {
    await api(`/projects/${$("#board").dataset.project}`, { method: "PATCH", body: { status: e.target.value } });
    toast("Project updated"); render();
  });
  const board = $("#board");
  if (board) {
    $$(".tcard", board).forEach((c) => {
      c.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/plain", c.dataset.task); c.classList.add("dragging"); });
      c.addEventListener("dragend", () => c.classList.remove("dragging"));
    });
    $$(".col", board).forEach((col) => {
      col.addEventListener("dragover", (e) => { e.preventDefault(); col.classList.add("drop"); });
      col.addEventListener("dragleave", () => col.classList.remove("drop"));
      col.addEventListener("drop", async (e) => {
        e.preventDefault(); col.classList.remove("drop");
        const id = e.dataTransfer.getData("text/plain");
        try { await api(`/tasks/${id}`, { method: "PATCH", body: { status: col.dataset.status } }); render(); } catch (err) { toast(err.message, true); }
      });
    });
  }
}

/* event delegation for task links anywhere (cards, rows, feed) */
document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-task]");
  if (t && !e.target.closest(".drawer")) { e.preventDefault(); openTask(t.dataset.task); }
});

/* ---------------- task drawer ---------------- */
let drawerTask = null;
async function openTask(id) {
  drawerTask = id;
  const [t, agents] = await Promise.all([api(`/tasks/${id}`), api("/agents")]);
  const d = $("#drawer");
  const opts = (vals, cur, fmt = (v) => v) => vals.map((v) => `<option value="${v}" ${v === cur ? "selected" : ""}>${esc(fmt(v))}</option>`).join("");
  const done = t.todos.filter((x) => x.done).length;
  d.innerHTML = `
    <div class="drawer-head">
      <div class="row"><span class="mono muted">#${t.id}</span><a class="muted" href="#/projects/${t.project_id}">${esc(t.project_name)}</a>
        <span style="flex:1"></span><button class="icon-btn" id="dDelete" title="Delete task"><span class="ms">delete</span></button>
        <button class="icon-btn" id="dClose" title="Close"><span class="ms">close</span></button></div>
      <div class="drawer-title" contenteditable="true" id="dTitle" spellcheck="false">${esc(t.title)}</div>
      <div class="muted" style="font-size:12px">Created ${fmtTime(t.created_at)} · updated ${ago(t.updated_at)}${t.completed_at ? " · completed " + fmtTime(t.completed_at) : ""}</div>
    </div>
    <div class="drawer-sec"><div class="props">
      <label>Status</label><select id="dStatus">${opts(Object.keys(STATUS), t.status, (v) => STATUS[v].label)}</select>
      <label>Priority</label><select id="dPriority">${opts(PRIORITIES, t.priority)}</select>
      <label>Assignee</label><select id="dAssignee"><option value="">Unassigned</option>${agents.map((a) => `<option value="${a.id}" ${a.id === t.assignee_id ? "selected" : ""}>${esc(a.name)}</option>`).join("")}</select>
      <label>Started</label><span>${fmtTime(t.started_at)}</span>
    </div></div>
    ${t.description ? `<div class="drawer-sec"><h3>Description</h3><div class="desc">${esc(t.description)}</div></div>` : ""}
    <div class="drawer-sec"><h3>Todos <span class="muted" style="text-transform:none;letter-spacing:0">${done}/${t.todos.length}</span></h3>
      ${t.todos.map((x) => `<label class="todo ${x.done ? "done" : ""}"><input type="checkbox" data-todo="${x.id}" ${x.done ? "checked" : ""}><span>${esc(x.text)}</span></label>`).join("") || '<div class="muted" style="font-size:13px">No todos yet.</div>'}
      <form class="inline-form" id="dTodoForm"><input placeholder="Add a todo…" id="dTodoText"><button class="btn btn-ghost">Add</button></form>
    </div>
    <div class="drawer-sec"><h3>Progress log</h3>
      <form class="inline-form" id="dNoteForm" style="margin:0 0 12px"><input placeholder="Add a note…" id="dNoteText"><button class="btn btn-ghost">Post</button></form>
      <div class="feed">${t.events.map((e) => eventItem(e)).join("") || '<div class="muted">No history.</div>'}</div>
    </div>`;
  d.classList.add("open"); d.setAttribute("aria-hidden", "false");
  $("#drawerBackdrop").classList.add("open");

  const patch = async (body) => { try { await api(`/tasks/${id}`, { method: "PATCH", body }); openTask(id); render(); } catch (e) { toast(e.message, true); } };
  $("#dClose").onclick = closeDrawer;
  $("#dStatus").onchange = (e) => patch({ status: e.target.value });
  $("#dPriority").onchange = (e) => patch({ priority: e.target.value });
  $("#dAssignee").onchange = (e) => patch({ assignee: e.target.value || "" });
  $("#dTitle").onblur = (e) => { const v = e.target.textContent.trim(); if (v && v !== t.title) patch({ title: v }); };
  $("#dTitle").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); e.target.blur(); } };
  $("#dDelete").onclick = async () => { if (confirm("Delete this task?")) { await api(`/tasks/${id}`, { method: "DELETE" }); closeDrawer(); render(); toast("Task deleted"); } };
  $$("[data-todo]", d).forEach((cb) => cb.onchange = async () => { await api(`/todos/${cb.dataset.todo}`, { method: "PATCH", body: { done: cb.checked } }); openTask(id); render(); });
  $("#dTodoForm").onsubmit = async (e) => { e.preventDefault(); const v = $("#dTodoText").value.trim(); if (!v) return; await api(`/tasks/${id}/todos`, { method: "POST", body: { text: v } }); openTask(id); render(); };
  $("#dNoteForm").onsubmit = async (e) => { e.preventDefault(); const v = $("#dNoteText").value.trim(); if (!v) return; await api(`/tasks/${id}/progress`, { method: "POST", body: { message: v } }); openTask(id); };
}
function closeDrawer() {
  drawerTask = null;
  $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true");
  $("#drawerBackdrop").classList.remove("open");
}
$("#drawerBackdrop").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

/* ---------------- modals ---------------- */
function modal(title, bodyHtml, onSubmit) {
  const m = $("#modal");
  m.innerHTML = `<form method="dialog" id="mForm"><div class="modal-head">${title}</div><div class="modal-body">${bodyHtml}</div>
    <div class="modal-foot"><button class="btn btn-ghost" value="cancel" formnovalidate>Cancel</button><button class="btn btn-primary" value="ok">Create</button></div></form>`;
  $("#mForm").addEventListener("submit", async (e) => {
    if (e.submitter?.value !== "ok") return;
    e.preventDefault();
    try { await onSubmit(Object.fromEntries(new FormData(e.target))); m.close(); render(); } catch (err) { toast(err.message, true); }
  });
  m.showModal();
  $("input, textarea", m)?.focus();
}

function newProjectModal() {
  modal("New project", `<label>Name<input name="name" required></label><label>Description<textarea name="description" rows="3"></textarea></label>`,
    async (f) => { const p = await api("/projects", { method: "POST", body: f }); toast("Project created"); location.hash = `#/projects/${p.id}`; });
}

async function newTaskModal(projectId) {
  if (!state.projects.length) return newProjectModal();
  const agents = await api("/agents");
  const cur = projectId || (state.route.match(/^\/projects\/(\d+)/) || [])[1];
  modal("New task", `
    <label>Title<input name="title" required></label>
    <div class="row2">
      <label>Project<select name="project">${state.projects.map((p) => `<option value="${p.id}" ${String(p.id) === String(cur) ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
      <label>Priority<select name="priority">${PRIORITIES.map((p) => `<option ${p === "medium" ? "selected" : ""}>${p}</option>`).join("")}</select></label>
    </div>
    <label>Assignee<select name="assignee"><option value="">Unassigned (any agent can claim)</option>${agents.map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join("")}</select></label>
    <label>Description<textarea name="description" rows="3"></textarea></label>
    <label>Todos (one per line)<textarea name="todos" rows="3"></textarea></label>`,
    async (f) => {
      f.todos = (f.todos || "").split("\n").map((s) => s.trim()).filter(Boolean);
      if (!f.assignee) delete f.assignee;
      await api("/tasks", { method: "POST", body: f });
      toast("Task created");
    });
}

$("#newProjectBtn").onclick = newProjectModal;
$("#newTaskBtn").onclick = () => newTaskModal();
$("#menuBtn").onclick = () => $("#sidebar").classList.toggle("open");
$("#sidebar").addEventListener("click", (e) => { if (e.target.closest("a")) $("#sidebar").classList.remove("open"); });
let searchT;
$("#search").addEventListener("input", (e) => {
  state.search = e.target.value;
  clearTimeout(searchT);
  searchT = setTimeout(() => { if (!/^\/(projects\/\d+|tasks)/.test(state.route)) location.hash = "#/tasks"; else render(); }, 200);
});

/* ---------------- live updates ---------------- */
let rerenderT;
function scheduleRerender() {
  clearTimeout(rerenderT);
  rerenderT = setTimeout(() => {
    if (document.activeElement?.closest("#view input, #view select, dialog")) return;
    render();
    if (drawerTask && !document.activeElement?.closest(".drawer")) openTask(drawerTask);
  }, 400);
}
function onEvents(evs) {
  if (!evs.length) return;
  evs.forEach((e) => { freshIds.add(e.id); setTimeout(() => freshIds.delete(e.id), 3000); });
  scheduleRerender();
}
async function startLive() {
  const cfg = await api("/config").catch(() => ({ stream: true }));
  if (cfg.stream) return connectStream();
  // Serverless hosting: poll for new events instead of holding a stream open.
  let last = (await api("/stats")).last_event_id;
  $("#liveDot").classList.add("on"); $("#liveText").textContent = "Live (polling)";
  setInterval(async () => {
    if (document.hidden) return;
    try {
      const evs = await api(`/events?since=${last}&limit=200`);
      if (evs.length) last = evs[0].id;
      onEvents(evs.reverse());
      $("#liveDot").classList.add("on");
    } catch { $("#liveDot").classList.remove("on"); }
  }, 3000);
}
function connectStream() {
  const es = new EventSource("/api/stream");
  es.addEventListener("hello", () => { $("#liveDot").classList.add("on"); $("#liveText").textContent = "Live"; });
  es.addEventListener("events", (m) => onEvents(JSON.parse(m.data)));
  es.onerror = () => { $("#liveDot").classList.remove("on"); $("#liveText").textContent = "Reconnecting…"; };
}

window.addEventListener("hashchange", render);
render();
startLive();
setInterval(() => { if (!document.hidden) scheduleRerender(); }, 30000); // refresh "x ago" and offline detection
