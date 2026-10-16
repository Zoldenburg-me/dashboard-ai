"use strict";
/* Synapse Local // Agent Control Center. UI follows the Google Stitch export
   (Bauhaus neo-brutalist); every number shown comes from the tracker API. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pad = (n, w = 2) => String(n ?? 0).padStart(w, "0");
const num = (v, d = 1) => (v === null || v === undefined || v === "" || isNaN(v) ? "—" : Number(v).toFixed(d).replace(/\.0+$/, ""));
const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);
const ms = (n) => `<span class="material-symbols-outlined">${n}</span>`;

const PRIO = {
  urgent: { p: "P0", label: "Critical", cls: "bg-secondary text-on-primary" },
  high: { p: "P1", label: "High", cls: "bg-primary-container text-on-primary-container" },
  medium: { p: "P2", label: "Med", cls: "bg-surface-container-highest text-on-surface" },
  low: { p: "P3", label: "Low", cls: "bg-surface-container text-on-surface-variant" },
};
const PRIORITIES = ["urgent", "high", "medium", "low"];
const COLS = [
  { s: "todo", t: "01 // Backlog", dot: "bg-on-surface-variant" },
  { s: "blocked", t: "02 // Blocked", dot: "bg-secondary" },
  { s: "in_progress", t: "03 // In Execution (Live)", dot: "bg-secondary animate-pulse", dark: true },
  { s: "review", t: "04 // Review & Merge", dot: "bg-tertiary" },
  { s: "done", t: "05 // Completed", dot: "bg-primary" },
];
const STATUS_LABEL = { todo: "Backlog", blocked: "Blocked", in_progress: "Executing", review: "Review", done: "Done" };
const AGENT_BADGE = {
  working: "bg-tertiary text-on-tertiary", idle: "bg-primary-container text-on-primary-container",
  blocked: "bg-secondary text-on-primary", offline: "bg-surface-container-highest text-on-surface-variant",
};
const ACCENTS = ["bg-secondary", "bg-tertiary", "bg-primary-container", "bg-primary"];
const accent = (s) => { let h = 0; for (const c of String(s)) h = (h * 31 + c.charCodeAt(0)) >>> 0; return ACCENTS[h % ACCENTS.length]; };
const onAccent = (cls) => (cls === "bg-primary-container" ? "text-on-primary-container" : "text-on-primary");
const initial = (n) => (String(n || "?").trim()[0] || "?").toUpperCase();

function ago(ts) {
  if (!ts) return "never";
  const s = Date.now() / 1000 - ts;
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
function dur(ts) {
  if (!ts) return "—";
  const s = Math.max(0, Date.now() / 1000 - ts);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m ${Math.floor(s % 60)}s`;
}
const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

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
  t.className = `fixed bottom-5 left-1/2 -translate-x-1/2 z-[60] px-4 py-2 border-2 border-primary font-label font-bold text-xs uppercase shadow-[3px_3px_0_0_#1a1a1a] ${err ? "bg-secondary text-on-primary" : "bg-primary-container"}`;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 2800);
}

/* ---------------- shared state ---------------- */
const S = { stats: {}, projects: [], agents: [], hosts: [], config: { stream: true }, route: "", filter: { project: "", assignee: "", priority: "" }, freshIds: new Set() };

async function loadShared() {
  [S.stats, S.projects, S.agents, S.hosts] = await Promise.all([api("/stats"), api("/projects"), api("/agents"), api("/hosts")]);
  renderChrome();
}

/* ---------------- small components ---------------- */
const chip = (text, cls = "bg-surface-container-highest text-on-surface") => `<span class="px-1.5 py-0.5 text-[9px] font-mono font-bold uppercase whitespace-nowrap ${cls}">${esc(text)}</span>`;
const prioChip = (p) => chip(`${PRIO[p]?.p || ""} ${PRIO[p]?.label || p}`, PRIO[p]?.cls);
const agentBadge = (st) => chip(st, AGENT_BADGE[st] || AGENT_BADGE.offline);
const avatar = (name, size = "w-10 h-10 text-lg") => { const a = accent(name); return `<span class="${size} ${a} ${onAccent(a)} border-2 border-primary grid place-items-center font-headline font-bold shrink-0">${esc(initial(name))}</span>`; };
function bar(p, cls = "bg-primary", h = "h-1.5", bg = "bg-surface-dim") {
  return `<div class="w-full ${bg} ${h}"><div class="${cls} ${h}" style="width:${Math.max(0, Math.min(100, p || 0))}%"></div></div>`;
}
const sectionHead = (n, title, sub = "", right = "") => `
  <div class="flex flex-wrap items-end justify-between gap-3 mb-4">
    <div class="flex flex-wrap items-center gap-3">
      <span class="px-2 py-0.5 bg-primary text-on-primary text-[10px] font-headline font-bold uppercase">Section ${n}</span>
      <h2 class="font-headline font-bold text-xl lg:text-2xl uppercase tracking-tight">${title}</h2>
      ${sub ? `<span class="text-[11px] font-mono text-on-surface-variant uppercase">// ${sub}</span>` : ""}
    </div>${right}
  </div>`;
const emptyBox = (icon, title, body) => `
  <div class="border-2 border-dashed border-on-surface-variant/50 p-6 text-center flex flex-col items-center gap-2">
    <span class="material-symbols-outlined text-3xl text-on-surface-variant">${icon}</span>
    <div class="font-headline font-bold text-xs uppercase">${title}</div>
    <div class="text-[11px] font-mono text-on-surface-variant">${body}</div>
  </div>`;
const statBox = (label, value, sub, cls = "bg-surface-container") => `
  <div class="${cls} border-2 border-primary px-4 py-2 flex flex-col min-w-[110px]">
    <span class="text-[10px] font-headline uppercase font-bold opacity-80">${label}</span>
    <span class="text-2xl font-headline font-bold">${value} <span class="text-xs font-normal">${sub}</span></span>
  </div>`;
const tag = (e) => `[${String(e.agent_name || (e.kind || "").split(".")[0] || "sys").toUpperCase()}]`;
function logLines(events, limit = 12) {
  if (!events.length) return `<div class="text-surface-dim/70">> waiting for output…</div>`;
  return events.slice(0, limit).reverse().map((e) => {
    const warn = /blocked|error|fail/i.test(e.message) || e.kind === "task.deleted";
    const tc = e.kind === "task.completed" || e.kind === "todo.done" ? "text-primary-fixed" : warn ? "text-secondary" : "text-tertiary-fixed-dim";
    return `<div class="flex gap-2 ${S.freshIds.has(e.id) ? "fresh" : ""}"><span class="text-secondary font-bold shrink-0">[${clock(e.ts)}]</span><span class="${tc} shrink-0">${esc(tag(e))}</span><span class="${warn ? "text-error-container" : ""}">${esc(e.message)}${e.task_id ? ` <span class="text-surface-dim/60">#${e.task_id}</span>` : ""}</span></div>`;
  }).join("");
}

function hostTotals() {
  const s = S.stats;
  return { vram: s.vram_total_gb ? pct(s.vram_used_gb, s.vram_total_gb) : null, ram: s.ram_total_gb ? pct(s.ram_used_gb, s.ram_total_gb) : null };
}

function renderChrome() {
  const s = S.stats, a = S.agents;
  const active = a.filter((x) => x.effective_status !== "offline").length;
  $("#onlineDot").className = `inline-block w-2 h-2 rounded-full ${active ? "bg-secondary animate-pulse" : "bg-on-surface-variant"}`;
  $("#onlineText").textContent = `${S.config.stream ? "Online" : "Online (polling)"} [${active} agents active, ${s.agents_offline || 0} dormant]`;
  const hw = [];
  if (s.ram_total_gb) hw.push(`<span class="text-tertiary">RAM:</span> ${num(s.ram_used_gb)}GB/${num(s.ram_total_gb)}GB`);
  if (s.vram_total_gb) hw.push(`<span class="text-secondary">VRAM:</span> ${num(s.vram_used_gb)}GB/${num(s.vram_total_gb)}GB${S.hosts[0]?.gpu ? " " + esc(S.hosts[0].gpu) : ""}`);
  if (s.cpu_pct !== null && s.cpu_pct !== undefined) hw.push(`<span class="text-on-surface-variant">CPU:</span> ${s.cpu_pct}%`);
  $("#hwChips").innerHTML = hw.map((h) => `<div class="px-2.5 py-1 bg-surface-container border-2 border-primary text-[11px] font-label font-bold">${h}</div>`).join("");
  $("#repoChipText").textContent = `Org: ${s.repos_connected || 0} repos connected`;
  const section = (S.route.split("/")[1] || "projects");
  const vram = hostTotals().vram;
  const nav = [
    ["projects", "01 // Projects & Kanban", `<span class="text-[10px] bg-surface-container-highest px-1.5 py-0.5 border border-primary">${S.projects.length}</span>`],
    ["agents", "02 // Agent Fleet", `<span class="text-[10px] ${s.agents_blocked ? "bg-secondary" : "bg-primary"} text-on-primary px-1.5 py-0.5 font-bold">${s.agents_working || 0}/${s.agents_blocked || 0}</span>`],
    ["dispatch", "03 // Task Dispatch", `<span class="text-[10px] bg-tertiary text-on-tertiary px-1.5 py-0.5 font-bold">${S.config.stream ? "LIVE" : "POLL"}</span>`],
    ["compute", "04 // Compute & VRAM", `<span class="text-[10px] ${vram > 90 ? "bg-secondary" : "bg-primary"} text-on-primary px-1.5 py-0.5">${vram === null ? "—" : vram + "%"}</span>`],
    ["repos", "05 // Repositories", `<span class="text-[10px] bg-surface-container-highest px-1.5 py-0.5 border border-primary">${s.repos_connected || 0}</span>`],
    ["dex", "06 // Agent Dex", `<span class="text-[10px] border border-primary px-1.5 py-0.5">DEX</span>`],
  ];
  $("#nav").innerHTML = nav.map(([k, label, badge]) => `
    <a href="#/${k}" class="flex items-center justify-between gap-2 px-3 py-2.5 border-2 border-primary text-sm font-label font-bold uppercase transition-all ${section === k ? "bg-primary-container shadow-[2px_2px_0px_0px_#1a1a1a]" : "text-on-surface-variant hover:bg-surface-container hover:text-on-surface"}"><span>${label}</span>${badge}</a>`).join("");
  const h = S.hosts[0];
  const row = (k, v, cls = "font-bold") => `<div class="flex justify-between gap-2"><span class="text-on-surface-variant">${k}</span><span class="${cls} truncate">${v}</span></div>`;
  $("#nodeEnv").innerHTML = row("HOST:", esc(h ? h.name : "none reported")) +
    row(`${esc(h?.gpu || "GPU")}:`, h ? (h.online ? "ACTIVE" : "OFFLINE") : "—", h?.online ? "text-secondary font-bold" : "font-bold") +
    row("STORE:", S.config.store === "turso" ? "TURSO" : "SQLITE") + row("FEED:", S.config.stream ? "SSE STREAM" : "POLL 3S");
}

/* ---------------- 01 // projects & kanban ---------------- */
function filteredTasks(tasks) {
  const f = S.filter;
  return tasks.filter((t) => (!f.project || String(t.project_id) === String(f.project)) &&
    (!f.assignee || String(t.assignee_id) === String(f.assignee)) && (!f.priority || t.priority === f.priority));
}

function projectCard(p, tasks) {
  const open = tasks.filter((t) => t.project_id === p.id && t.status !== "done");
  const top = PRIORITIES.find((pr) => open.some((t) => t.priority === pr));
  const agents = [...new Set(open.map((t) => t.assignee_name).filter(Boolean))];
  const blocked = p.blocked_count > 0;
  const prog = pct(p.done_count, p.task_count);
  return `
  <div class="${blocked ? "bg-secondary-fixed" : "bg-surface-container"} p-4 flex flex-col justify-between gap-3 cursor-pointer hover:bg-surface-bright transition-all" data-href="#/projects/${p.id}">
    <div class="flex flex-col gap-2">
      <div class="flex items-start justify-between gap-2">
        ${blocked ? chip("Stalled // Blocked", "bg-secondary text-on-primary") : top ? prioChip(top) : chip(p.status)}
        <span class="text-xs font-mono font-bold ${blocked ? "text-secondary" : "text-on-surface-variant"}">${blocked ? `${p.blocked_count} BLOCKED` : `${p.active_count} LIVE`}</span>
      </div>
      <h3 class="font-headline font-bold text-base uppercase tracking-tight leading-snug">${esc(p.name)}</h3>
      <p class="text-xs text-on-surface-variant line-clamp-2">${esc(p.description || "No description")}</p>
    </div>
    <div class="flex flex-col gap-2">
      <div class="flex justify-between text-xs font-mono"><span>Sprint Progress</span><span class="font-bold">${prog}%</span></div>
      ${bar(prog, blocked ? "bg-secondary" : prog > 66 ? "bg-tertiary" : prog > 33 ? "bg-primary" : "bg-primary-container")}
      <div class="flex items-center justify-between gap-2 text-[11px] font-mono">
        <span class="truncate">${ms("smart_toy").replace("material-symbols-outlined", "material-symbols-outlined text-sm")} ${esc(agents.join(" + ") || "Unassigned")}</span>
        ${p.repo ? `<span class="px-1 border border-primary/40 truncate max-w-[45%]">repo:${esc(p.repo.split("/").pop())}</span>` : ""}
      </div>
    </div>
  </div>`;
}

function kanbanCard(t, events) {
  const p = PRIO[t.priority];
  const proj = `<span class="px-1.5 py-0.5 text-[9px] font-mono font-bold bg-primary text-on-primary uppercase truncate max-w-[60%]">${esc(t.project_name)}</span>`;
  const done = t.status === "done";
  const who = t.assignee_name ? `${avatar(t.assignee_name, "w-5 h-5 text-[10px]")}<span class="truncate">${esc(t.assignee_name)}</span>` : `<span>UNASSIGNED</span>`;
  let body = "";
  if (t.status === "in_progress") {
    body = `<div class="term p-2 max-h-28 overflow-hidden">${logLines(events.filter((e) => e.task_id === t.id), 4)}</div>`;
  } else if (t.description && !done) {
    body = `<p class="text-xs text-on-surface-variant line-clamp-2">${esc(t.description)}</p>`;
  }
  const actions = {
    todo: `<button class="text-[10px] font-headline font-bold uppercase text-tertiary hover:underline" data-act="start" data-id="${t.id}">${ms("play_arrow").replace("material-symbols-outlined", "material-symbols-outlined text-xs")} Start</button>`,
    blocked: `<button class="px-2 py-1 bg-primary text-on-primary text-[10px] font-headline font-bold uppercase hover:bg-secondary" data-act="unblock" data-id="${t.id}">Unblock</button>`,
    in_progress: `<button class="px-2 py-1 border border-primary text-[10px] font-headline font-bold uppercase hover:bg-primary hover:text-on-primary" data-task="${t.id}">Inspect Log</button><button class="px-2 py-1 bg-secondary text-on-primary text-[10px] font-headline font-bold uppercase" data-act="pause" data-id="${t.id}">Block</button>`,
    review: `<button class="px-2 py-1 bg-primary text-on-primary text-[10px] font-headline font-bold uppercase hover:bg-primary-container hover:text-on-primary-container w-full" data-act="merge" data-id="${t.id}">Sign & Merge</button>`,
    done: "",
  }[t.status];
  return `
  <div class="tcard bg-surface p-3.5 flex flex-col gap-2.5 hover:bg-surface-bright cursor-pointer ${t.status === "in_progress" ? "shadow-[3px_3px_0_0_#1a1a1a] border-2 border-primary" : ""}" draggable="true" data-task="${t.id}">
    <div class="flex items-start justify-between gap-1">${proj}${t.status === "in_progress" ? `<span class="flex items-center gap-1 text-[9px] font-mono font-bold text-secondary"><span class="w-1.5 h-1.5 bg-secondary animate-ping"></span>RUNNING</span>` : `<span class="px-1.5 py-0.5 text-[9px] font-headline font-bold ${p.cls}">${p.p}</span>`}</div>
    <h4 class="font-headline font-bold text-sm leading-tight ${done ? "line-through text-on-surface-variant" : ""}">${esc(t.title)}</h4>
    ${body}
    <div class="flex items-center justify-between gap-2 text-[10px] font-mono text-on-surface-variant bg-surface-container-low p-1.5">
      <span class="flex items-center gap-1 min-w-0">${who}</span>
      <span class="shrink-0">${t.todo_count ? `${ms("checklist").replace("material-symbols-outlined", "material-symbols-outlined text-xs")} ${t.todo_done}/${t.todo_count}` : ""} #${t.id}</span>
    </div>
    ${actions ? `<div class="flex items-center justify-between gap-2" data-stop>${actions}</div>` : ""}
  </div>`;
}

function board(tasks, events) {
  return `<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4 items-start pb-4" id="board">${COLS.map((c) => {
    let list = tasks.filter((t) => t.status === c.s);
    let older = 0;
    if (c.s === "done" && list.length > 6) {  // keep the board scannable: newest completions only
      list = list.sort((a, b) => (b.completed_at || 0) - (a.completed_at || 0));
      older = list.length - 6;
      list = list.slice(0, 6);
    }
    return `<div class="col bg-surface-container-low flex flex-col min-w-0" data-status="${c.s}">
      <div class="p-3 ${c.dark ? "bg-primary text-primary-fixed" : "bg-surface-container"} flex items-center justify-between">
        <div class="flex items-center gap-2"><span class="w-2.5 h-2.5 ${c.dot}"></span><span class="font-headline font-bold text-xs uppercase tracking-wider">${c.t}</span></div>
        <span class="px-2 py-0.5 ${c.dark ? "bg-secondary text-on-primary" : "bg-surface-container-highest"} font-mono text-xs font-bold">${list.length + older}</span>
      </div>
      <div class="p-3 flex flex-col gap-3 min-h-[120px]">${list.map((t) => kanbanCard(t, events)).join("") || `<div class="text-[11px] font-mono text-on-surface-variant text-center py-6 border-2 border-dashed border-surface-dim">EMPTY</div>`}
        ${older ? `<a href="#/tasks-done" class="text-[10px] font-mono text-center text-on-surface-variant" onclick="event.preventDefault()">+ ${older} older completed</a>` : ""}</div>
    </div>`;
  }).join("")}</div>`;
}

function filteredEmpty(allCount) {
  const f = S.filter;
  const proj = S.projects.find((p) => String(p.id) === String(f.project));
  const ag = S.agents.find((a) => String(a.id) === String(f.assignee));
  const idle = S.agents.filter((a) => a.effective_status === "idle");
  const desc = [proj && `project ${esc(proj.name).toUpperCase()}`, ag && `agent ${esc(ag.name)}`, f.priority && `${PRIO[f.priority].label} priority`].filter(Boolean).join(" // ");
  return `
  <div class="relative bg-surface-bright brut p-6 lg:p-10 overflow-hidden">
    <div class="absolute -top-2 -right-2 w-48 h-48 stripes opacity-90 hidden md:block" style="clip-path:polygon(0 0,100% 0,100% 100%)"></div>
    <div class="flex items-center gap-3 text-xs font-mono font-bold text-secondary uppercase"><span class="w-12 h-3 hazard inline-block"></span>Null_set // workflow pipeline interrupted</div>
    <h2 class="font-headline font-bold text-4xl lg:text-6xl uppercase tracking-tighter mt-4">Board pipeline empty</h2>
    <div class="font-headline font-bold text-lg lg:text-2xl uppercase text-on-surface-variant mt-2">No tasks match this filter /// <span class="text-sm lg:text-base font-mono text-secondary border-2 border-secondary bg-secondary-fixed px-2">FILTER: [${desc}]</span></div>
    <p class="mt-5 max-w-3xl">Zero kanban records match the current filter. ${allCount} task${allCount === 1 ? "" : "s"} exist outside it.</p>
    <div class="flex flex-wrap gap-3 mt-6">
      <button class="btn btn-yellow text-sm px-5 py-3" id="emptyNewTask">${ms("bolt")} Dispatch task for this filter</button>
      <button class="btn bg-surface text-sm px-5 py-3" id="emptyReset">${ms("restart_alt")} Reset filters [All (${allCount})]</button>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mt-8 pt-6 border-t-2 border-primary">
      <div class="border-2 border-primary p-3 bg-surface-container"><div class="text-[10px] font-mono uppercase">Matched tasks</div><div class="font-headline font-bold text-2xl text-secondary">00</div></div>
      <div class="border-2 border-primary p-3 bg-surface-container"><div class="text-[10px] font-mono uppercase">Unassigned backlog</div><div class="font-headline font-bold text-2xl">${pad(S.stats.tasks_unassigned)}</div></div>
      <div class="border-2 border-primary p-3 bg-surface-container"><div class="text-[10px] font-mono uppercase">Available agents</div><div class="font-headline font-bold text-2xl text-tertiary">${pad(idle.length)} IDLE</div></div>
      <div class="border-2 border-primary p-3 bg-surface-container"><div class="text-[10px] font-mono uppercase">Fleet VRAM</div><div class="font-headline font-bold text-2xl">${S.stats.vram_total_gb ? `${num(S.stats.vram_used_gb)} GB` : "—"}</div></div>
    </div>
  </div>`;
}

function stdoutAndCapacity(events) {
  const s = S.stats;
  const online = s.agents_online || 0;
  return `
  <section class="bg-surface-container-high p-4 lg:p-6 flex flex-col lg:flex-row gap-6">
    <div class="flex-1 min-w-0 flex flex-col gap-2">
      <div class="flex items-center justify-between gap-2 flex-wrap">
        <div class="flex items-center gap-2"><span class="w-2 h-2 bg-secondary animate-ping"></span><span class="text-xs font-headline font-bold uppercase tracking-wider">Synapse local stdout // subprocess bus</span></div>
        <span class="text-[11px] font-mono text-on-surface-variant">TAIL -F EVENTS (${s.events_24h || 0} / 24H)</span>
      </div>
      <div class="term p-4 flex flex-col gap-1.5 max-h-72 overflow-y-auto">${logLines(events, 14)}</div>
    </div>
    <div class="w-full lg:w-80 bg-surface p-4 flex flex-col gap-4 border-2 border-primary">
      <span class="text-[10px] font-headline font-bold uppercase tracking-widest text-on-surface-variant">Fleet capacity matrix</span>
      <div class="flex flex-col gap-1"><div class="flex justify-between text-xs font-mono"><span>Agent concurrency</span><span class="font-bold text-tertiary">${s.agents_working || 0} / ${online} working</span></div>${bar(pct(s.agents_working, online), "bg-tertiary")}</div>
      <div class="flex flex-col gap-1"><div class="flex justify-between text-xs font-mono"><span>Local VRAM reservation</span><span class="font-bold text-secondary">${s.vram_total_gb ? `${num(s.vram_used_gb)}GB / ${num(s.vram_total_gb)}GB` : "not reported"}</span></div>${bar(hostTotals().vram, "bg-secondary")}</div>
      <div class="flex flex-col gap-1"><div class="flex justify-between text-xs font-mono"><span>Todos checked (24h)</span><span class="font-bold">${s.todos_done_24h || 0} / ${(s.todos_done_24h || 0) + (s.todos_open || 0)}</span></div>${bar(pct(s.todos_done_24h, (s.todos_done_24h || 0) + (s.todos_open || 0)), "bg-primary")}</div>
      <a href="#/compute" class="btn btn-dark mt-auto">${ms("terminal")} Open compute & VRAM</a>
    </div>
  </section>`;
}

async function viewProjects() {
  const [tasks, events] = await Promise.all([api("/tasks?limit=1000"), api("/events?limit=40")]);
  const s = S.stats, f = S.filter;
  const shown = filteredTasks(tasks);
  const filtering = f.project || f.assignee || f.priority;
  const live = S.projects.filter((p) => p.status === "active").length;
  const opt = (v, label, cur) => `<option value="${esc(v)}" ${String(v) === String(cur) ? "selected" : ""}>${esc(label)}</option>`;
  return `
  <div class="bg-primary text-on-primary px-4 lg:px-6 py-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] font-mono uppercase">
    <span class="flex items-center gap-2 font-headline font-bold"><span class="w-2.5 h-2.5 bg-secondary"></span>Orchestrator: active dispatch matrix</span>
    <span>Hosts: <b>${s.hosts_online || 0}</b></span><span>// Active workers: <b class="text-primary-fixed">${s.agents_working || 0} parallel runners</b></span>
    <span>// Events 24h: <b class="text-tertiary-fixed-dim">${s.events_24h || 0}</b></span>
    ${s.tokens_per_sec ? `<span>// Throughput: <b class="text-primary-fixed">${num(s.tokens_per_sec)} t/s</b></span>` : ""}
  </div>
  <div class="p-4 lg:p-6 flex flex-col gap-6">
    <section>
      <div class="flex flex-wrap items-end justify-between gap-4 mb-5">
        <div>
          <div class="flex items-center gap-3 mb-2"><span class="px-2 py-0.5 bg-primary text-on-primary text-[10px] font-headline font-bold uppercase">Section 01</span><span class="text-[11px] font-mono text-on-surface-variant uppercase">// workspace_dispatch_overview</span></div>
          <h1 class="font-headline font-bold text-3xl md:text-5xl uppercase tracking-tighter leading-[.95]">Active projects &amp;<br>fleet operations</h1>
        </div>
        <div class="flex flex-wrap gap-2">
          ${statBox("Live projects", pad(live), `/ ${S.projects.length}`)}
          ${statBox("In execution", pad(s.tasks_in_progress), "running", "bg-primary text-primary-fixed")}
          ${statBox("Blocked", pad(s.tasks_blocked), "tasks", s.tasks_blocked ? "bg-secondary text-on-primary" : "bg-surface-container")}
        </div>
      </div>
      <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-4">
        ${S.projects.filter((p) => p.status !== "archived").map((p) => projectCard(p, tasks)).join("")}
        <div class="bg-primary text-on-primary p-4 flex flex-col justify-between gap-4 cursor-pointer group" id="connectCard">
          <div><div class="flex items-center justify-between"><span class="text-[10px] font-headline font-bold uppercase tracking-widest text-primary-fixed">Action module</span><span class="material-symbols-outlined text-primary-fixed group-hover:rotate-45 transition-transform">add</span></div>
            <h3 class="font-headline font-bold text-lg uppercase mt-3">Connect new project</h3>
            <p class="text-xs text-surface-dim mt-2">Create a project and link its Git repository so agents can pick up its work queue.</p></div>
          <button class="w-full py-2 px-3 bg-primary-fixed text-on-primary-fixed font-headline font-bold text-xs uppercase flex items-center justify-center gap-2">${ms("folder_open")} New project + repo</button>
        </div>
      </div>
    </section>

    <section class="bg-surface-container-high p-4 lg:p-6 flex flex-col gap-4">
      <form id="instantForm" class="flex flex-col lg:flex-row items-stretch gap-2">
        <div class="relative flex-1"><span class="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant">bolt</span>
          <input name="title" required class="w-full pl-10 pr-4 py-2.5 bg-surface text-xs font-mono border-2 border-primary focus:outline-none focus:bg-surface-bright" placeholder="INSTANT DISPATCH: type a task (e.g. 'Audit permit logic in payment core')…"></div>
        <select name="project" class="bg-surface text-xs font-headline font-bold uppercase px-3 py-2.5 border-2 border-primary">${S.projects.map((p) => opt(p.id, p.name, f.project || S.projects[0]?.id)).join("")}</select>
        <select name="assignee" class="bg-surface text-xs font-headline font-bold uppercase px-3 py-2.5 border-2 border-primary"><option value="">Auto-select agent (claim)</option>${S.agents.map((a) => opt(a.id, a.name, "")).join("")}</select>
        <button class="btn btn-dark px-5">${ms("send")} Dispatch</button>
      </form>
      <div class="flex flex-wrap items-center gap-2 text-xs font-headline font-bold uppercase">
        <span class="text-[10px] text-on-surface-variant mr-1">Filter projects:</span>
        <button class="px-3 py-1 border-2 border-primary ${!f.project ? "bg-primary text-on-primary" : "bg-surface"}" data-fproject="">All (${tasks.length})</button>
        ${S.projects.map((p) => `<button class="px-3 py-1 border-2 border-primary ${String(f.project) === String(p.id) ? "bg-primary text-on-primary" : "bg-surface"}" data-fproject="${p.id}">${esc(p.name)}</button>`).join("")}
      </div>
      <div class="flex flex-wrap items-center gap-4 text-xs font-mono">
        <label class="flex items-center gap-2">AGENT: <select id="fAssignee" class="bg-surface border-2 border-primary px-2 py-1"><option value="">All agents (${S.agents.length})</option>${S.agents.map((a) => opt(a.id, a.name, f.assignee)).join("")}</select></label>
        <label class="flex items-center gap-2">PRIORITY: <select id="fPriority" class="bg-surface border-2 border-primary px-2 py-1"><option value="">Any priority</option>${PRIORITIES.map((p) => opt(p, `${PRIO[p].p} ${PRIO[p].label}`, f.priority)).join("")}</select></label>
      </div>
    </section>

    <section>
      ${sectionHead("02", "Autonomous sprint board", S.config.stream ? "synced live" : "polling every 3s")}
      ${filtering && !shown.length ? filteredEmpty(tasks.length) : board(shown, events)}
    </section>
  </div>
  ${stdoutAndCapacity(events)}`;
}

async function viewProject(id) {
  const [p, events] = await Promise.all([api(`/projects/${id}`), api(`/events?project=${id}&limit=200`)]);
  const tasks = p.tasks;
  const agentsOn = [...new Set(tasks.filter((t) => t.status !== "done" && t.assignee_id).map((t) => t.assignee_id))].length;
  const now = Date.now() / 1000;
  const buckets = Array.from({ length: 24 }, (_, i) => events.filter((e) => e.ts > now - (24 - i) * 3600 && e.ts <= now - (23 - i) * 3600).length);
  const max = Math.max(1, ...buckets);
  const colors = ["bg-surface-dim", "bg-surface-dim", "bg-secondary", "bg-primary", "bg-primary-container"];
  return `
  <div class="p-4 lg:p-6 flex flex-col gap-6">
    <div class="flex flex-wrap items-center gap-3">
      <span class="px-2 py-1 bg-primary text-on-primary font-headline font-bold text-xs uppercase">Project #${pad(p.id)}</span>
      <h1 class="font-headline font-bold text-xl lg:text-2xl uppercase tracking-tight">${esc(p.name)}</h1>
      ${p.branch ? `<span class="text-xs font-mono">BRANCH: <b>${esc(p.branch)}</b></span>` : ""}
      ${p.repo ? `<span class="text-xs font-mono text-tertiary">${esc(p.repo)}</span>` : `<button class="btn bg-surface text-[10px]" data-linkrepo="${p.id}">${ms("link")} Link repo</button>`}
    </div>
    <div class="flex flex-wrap items-center gap-2">
      <span class="px-3 py-1 border-2 border-primary bg-surface-container font-headline font-bold text-xs uppercase">Sprint progress: ${pct(p.done_count, p.task_count)}%</span>
      <select id="projStatus" class="px-3 py-1 border-2 border-primary bg-surface font-headline font-bold text-xs uppercase">${["active", "paused", "completed", "archived"].map((s) => `<option ${s === p.status ? "selected" : ""}>${s}</option>`).join("")}</select>
      <button class="btn btn-dark" id="projNewTask">${ms("add")} Dispatch task</button>
    </div>
    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
      <div class="bg-surface-container brut p-4"><div class="text-[10px] font-headline font-bold uppercase">Active agents on repo</div><div class="font-headline font-bold text-3xl mt-1">${pad(agentsOn)} <span class="text-xs font-normal">/ ${pad(S.agents.length)} workers</span></div></div>
      <div class="bg-surface-container brut p-4"><div class="text-[10px] font-headline font-bold uppercase">Events today</div><div class="font-headline font-bold text-3xl mt-1 text-tertiary">${buckets.reduce((a, b) => a + b, 0)} <span class="text-xs font-normal text-on-surface">recorded</span></div></div>
      <div class="bg-surface-container brut p-4"><div class="text-[10px] font-headline font-bold uppercase">Completion rate</div><div class="font-headline font-bold text-3xl mt-1">${pct(p.done_count, p.task_count)}% <span class="text-xs font-normal ${p.blocked_count ? "text-secondary" : ""}">(${p.blocked_count} blocked)</span></div></div>
    </div>
    ${board(tasks, events)}
    <div class="bg-surface-container-low brut p-4">
      <div class="flex justify-between items-center mb-3"><span class="text-xs font-headline font-bold uppercase">Activity throughput (last 24 hours)</span><span class="text-xs font-mono font-bold text-tertiary">${events.length} events</span></div>
      <div class="flex items-end gap-1 h-16">${buckets.map((n, i) => `<div class="flex-1 ${n ? colors[i % colors.length] : "bg-surface-dim/50"}" style="height:${Math.max(6, (n / max) * 100)}%" title="${n} events, ${24 - i}h ago"></div>`).join("")}</div>
    </div>
    <div><div class="text-xs font-headline font-bold uppercase mb-2">Project stdout</div><div class="term p-4 max-h-80 overflow-y-auto flex flex-col gap-1">${logLines(events, 40)}</div></div>
  </div>`;
}

/* ---------------- 02 // agent fleet ---------------- */
async function viewAgents(selId) {
  const agents = S.agents;
  const sel = selId ? await api(`/agents/${selId}`) : null;
  const leads = agents.filter((a) => !a.parent_id || !agents.some((x) => x.id === a.parent_id));
  const kids = (a) => agents.filter((x) => x.parent_id === a.id);
  const blocked = agents.filter((a) => a.effective_status === "blocked");
  const vram = hostTotals().vram;
  const banner = blocked.length ? `
    <div class="bg-secondary text-on-primary p-4 lg:p-5 flex flex-col md:flex-row md:items-center gap-4 border-b-2 border-primary">
      <span class="w-12 h-12 bg-surface text-secondary grid place-items-center shrink-0 border-2 border-primary">${ms("warning")}</span>
      <div class="flex-1"><span class="bg-primary text-on-primary px-2 py-0.5 text-[10px] font-headline font-bold uppercase">Critical // ${blocked.length} agent${blocked.length > 1 ? "s" : ""} blocked</span>
        <p class="text-sm mt-1">${blocked.map((a) => `<b class="underline">${esc(a.name)}</b>${a.status_note ? ` (${esc(a.status_note)})` : ""}`).join(", ")} ${blocked.length > 1 ? "are" : "is"} blocked. Unblock their tasks or reassign the work.</p></div>
      <a href="#/agents/${blocked[0].id}" class="btn btn-yellow">Resolve</a>
    </div>` : "";
  const clusterCard = (a, i) => {
    const k = kids(a);
    const on = [a, ...k].filter((x) => x.effective_status !== "offline").length;
    return `<a href="#/agents/${a.id}" class="bg-surface brut p-4 flex flex-col justify-between gap-4 hover:bg-surface-bright">
      <div class="flex justify-between gap-2"><span class="text-xs font-mono uppercase">${pad(i + 1)} // ${esc(a.name)} cluster</span>${chip(a.model || "model?", "bg-tertiary-container text-on-surface")}</div>
      <div><div class="font-headline font-bold text-4xl uppercase tracking-tighter">${k.length + 1} ${k.length ? "workers" : "lead"}</div><div class="text-xs text-on-surface-variant">${esc([a.name, ...k.map((x) => x.name)].join(" • "))}</div></div>
      <div class="bg-surface-container p-2 flex justify-between text-[11px] font-mono"><span>SUB-AGENTS: ${k.length}</span><span class="text-tertiary">${pct(on, k.length + 1)}% ONLINE</span></div>
    </a>`;
  };
  const leadBlock = (a) => {
    const k = kids(a);
    const projs = [...new Set((a.current_project_name ? [a.current_project_name] : []))];
    return `
    <div class="bg-surface brut flex flex-col">
      <div class="p-3 border-b-2 border-primary flex flex-wrap items-center gap-3 text-xs font-mono">
        <span class="w-3 h-3 ${accent(a.name)}"></span><span class="font-headline font-bold text-sm uppercase">Cluster // ${esc(a.name)}</span>
        ${chip(`ID: ${a.id}`, "bg-tertiary-container text-on-surface")}${a.host ? `<span>HOST: <b>${esc(a.host)}</b></span>` : ""}
        ${a.metrics.tokens_per_sec ? `<span>PERF: <b class="text-tertiary">${num(a.metrics.tokens_per_sec)} tok/sec</b></span>` : ""}
      </div>
      <div class="p-4 flex flex-col gap-4">
        <div class="border-2 border-primary p-4 flex flex-col gap-3 bg-surface-bright">
          <div class="flex items-start gap-3">${avatar(a.name, "w-12 h-12 text-2xl")}
            <div class="flex-1 min-w-0"><div class="flex flex-wrap items-center gap-2"><span class="font-headline font-bold text-xl uppercase">${esc(a.name)}</span>${chip(`● ${a.role || "lead"}`, "bg-primary text-primary-fixed")}${agentBadge(a.effective_status)}</div>
              <p class="text-xs text-on-surface-variant mt-1">${esc(a.status_note || (a.current_task_title ? `Working on #${a.current_task_id} ${a.current_task_title}` : "No active task"))}</p></div>
            <a href="#/agents/${a.id}" class="btn btn-dark text-[10px]">Inspect</a>
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 bg-surface-container p-3 text-[11px] font-mono">
            <div><div class="text-[10px] uppercase text-on-surface-variant mb-1">Current project</div><b>${esc(projs[0] || "—")}</b></div>
            <div><div class="text-[10px] uppercase text-on-surface-variant mb-1">Concurrency load</div><b>${a.open_tasks} open · ${a.done_tasks} done</b></div>
            <div><div class="text-[10px] uppercase text-on-surface-variant mb-1">Allocated memory</div><b class="text-tertiary">${a.metrics.vram_gb !== undefined ? `${num(a.metrics.vram_gb)} GB VRAM` : "not reported"}</b></div>
          </div>
        </div>
        <div class="text-[11px] font-mono uppercase text-on-surface-variant">↳ Sub-agent hierarchy slots (${k.filter((x) => x.effective_status === "working").length}/${k.length} active)</div>
        ${k.map((x) => `
          <div class="ml-0 md:ml-6 flex items-center gap-3 p-3 border-2 ${x.effective_status === "blocked" ? "border-secondary bg-secondary-fixed" : "border-primary/20 bg-surface-container-low"}">
            ${avatar(x.name, "w-8 h-8 text-sm")}
            <div class="flex-1 min-w-0"><div class="flex flex-wrap items-center gap-2"><span class="font-headline font-bold uppercase">${esc(x.name)}</span>${agentBadge(x.effective_status)}<span class="text-[11px] font-mono text-on-surface-variant">${esc(x.role || "")} ${x.model ? "· " + esc(x.model) : ""}</span></div>
              <div class="text-xs text-on-surface-variant truncate">${esc(x.current_task_title ? `Active on ${x.current_project_name}: #${x.current_task_id} ${x.current_task_title}` : x.status_note || "Idle in pool")}</div></div>
            <a href="#/agents/${x.id}" class="btn bg-surface text-[10px]">View</a>
          </div>`).join("") || `<div class="ml-0 md:ml-6 text-[11px] font-mono text-on-surface-variant border-2 border-dashed border-surface-dim p-3">No sub-agents. Agents join this cluster by reporting parent=${esc(a.name)} in their heartbeat.</div>`}
      </div>
    </div>`;
  };
  return `${banner}
  <div class="p-4 lg:p-6 flex flex-col gap-6">
    <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
      ${leads.slice(0, 3).map(clusterCard).join("")}
      <div class="${vram > 90 ? "bg-secondary-fixed" : "bg-surface"} brut p-4 flex flex-col justify-between gap-4">
        <div class="flex justify-between"><span class="text-xs font-mono uppercase">${pad(Math.min(leads.length, 3) + 1)} // Hardware</span>${chip(vram > 90 ? "VRAM CRIT" : "VRAM", vram > 90 ? "bg-secondary text-on-primary" : "bg-surface-container-highest")}</div>
        <div class="font-headline font-bold text-4xl uppercase tracking-tighter ${vram > 90 ? "text-secondary" : ""}">${vram === null ? "No host" : vram + "% used"}</div>
        <div class="bg-surface-container p-2 text-[11px] font-mono">${S.stats.vram_total_gb ? `${num(S.stats.vram_used_gb)} / ${num(S.stats.vram_total_gb)} GB VRAM` : "Report stats: agentctl.py host report --auto"}</div>
      </div>
    </div>
    <div class="grid grid-cols-1 xl:grid-cols-[1fr_340px] gap-6 items-start">
      <div class="flex flex-col gap-6">
        <div class="bg-surface-container-high brut p-4 flex flex-wrap items-center gap-4">
          <span class="w-11 h-11 bg-primary text-on-primary grid place-items-center">${ms("account_tree")}</span>
          <div class="flex-1 min-w-[200px]"><div class="font-headline font-bold text-lg uppercase">Swarm hierarchy &amp; delegation</div><div class="text-xs text-on-surface-variant">${agents.length} agents in ${leads.length} cluster${leads.length === 1 ? "" : "s"}, from heartbeats</div></div>
          <button class="btn btn-yellow" id="registerAgent">${ms("person_add")} Register new agent</button>
        </div>
        ${leads.map(leadBlock).join("") || `<div class="bg-surface brut p-6">${emptyBox("smart_toy", "No agents yet", "Agents appear on their first heartbeat: agentctl.py --agent NAME hb --status idle")}</div>`}
      </div>
      ${sel ? agentInspector(sel) : `<div class="hidden xl:block bg-surface-container brut p-6">${emptyBox("manage_search", "Agent inspector", "Select an agent to inspect its telemetry")}</div>`}
    </div>
  </div>`;
}

function agentInspector(a) {
  const m = a.metrics || {};
  const ctx = m.context_max ? pct(m.context_used || 0, m.context_max) : null;
  const host = S.hosts.find((h) => h.name === a.host);
  return `
  <aside class="flex flex-col gap-4 xl:sticky xl:top-20">
    <div class="bg-primary text-on-primary p-4 flex items-center justify-between gap-2 brut">
      <span class="flex items-center gap-2 font-headline font-bold uppercase text-sm">${ms("tune")} Agent inspector // telemetry</span>
      ${agentBadge(a.effective_status)}
    </div>
    <div class="bg-surface brut p-4 flex flex-col gap-4">
      <div class="flex items-start gap-3">${avatar(a.name, "w-14 h-14 text-3xl")}<div class="min-w-0"><div class="font-headline font-bold text-2xl uppercase leading-none">${esc(a.name)}</div><div class="text-xs mt-1">${esc(a.role || "agent")}</div><div class="text-[10px] font-mono text-secondary mt-1">TARGET_ID: ${a.id}${a.parent_name ? ` · UNDER ${esc(a.parent_name)}` : ""}</div></div></div>
      <div class="bg-surface-container p-3 flex flex-col gap-2"><div class="flex justify-between text-xs font-mono"><span>CONTEXT WINDOW:</span><b>${m.context_max ? `${Number(m.context_used || 0).toLocaleString()} / ${Number(m.context_max).toLocaleString()} tok` : "not reported"}</b></div>${bar(ctx, "bg-primary", "h-3", "bg-surface")}</div>
      <div class="grid grid-cols-2 gap-3">
        <div class="bg-surface-container p-3"><div class="text-[10px] font-mono uppercase">VRAM bound</div><div class="font-headline font-bold text-xl ${host && host.vram_total_gb && m.vram_gb > host.vram_total_gb ? "text-secondary" : ""}">${num(m.vram_gb)} GB</div><div class="text-[10px] font-mono text-on-surface-variant">${host ? esc(host.gpu || host.name) : "host not reported"}</div></div>
        <div class="bg-surface-container p-3"><div class="text-[10px] font-mono uppercase">Speed</div><div class="font-headline font-bold text-xl text-tertiary">${num(m.tokens_per_sec)} t/s</div><div class="text-[10px] font-mono text-on-surface-variant">last heartbeat ${ago(a.last_seen)}</div></div>
      </div>
      <div class="bg-surface-container p-3 text-xs font-mono flex flex-col gap-1">
        <div class="flex justify-between"><span>MODEL_BASE:</span><b>${esc(a.model || "—")}</b></div>
        <div class="flex justify-between"><span>QUANT:</span><b>${esc(m.quant || "—")}</b></div>
        <div class="flex justify-between"><span>HOST:</span><b>${esc(a.host || "—")}</b></div>
        <div class="flex justify-between gap-2"><span>CURRENT TASK:</span><b class="text-right ${a.effective_status === "blocked" ? "text-secondary" : ""}">${a.current_task_id ? `#${a.current_task_id} ${esc(a.current_task_title)}` : "none"}</b></div>
      </div>
      <div class="text-[10px] font-headline font-bold uppercase tracking-wider">Executable commands</div>
      <button class="btn btn-yellow justify-between" data-assignto="${a.id}">${ms("assignment_add")} Assign new task ${ms("arrow_forward")}</button>
      ${a.current_task_id ? `<button class="btn bg-surface justify-between" data-task="${a.current_task_id}">${ms("visibility")} Open current task</button>` : ""}
      <button class="btn bg-surface justify-between" data-setstatus="${esc(a.name)}" data-to="idle">${ms("pause_circle")} Mark idle &amp; release task</button>
    </div>
    <div class="bg-surface brut p-4"><div class="text-[10px] font-headline font-bold uppercase mb-2">Tasks (${a.tasks.length})</div>
      ${a.tasks.slice(0, 8).map((t) => `<div class="flex items-center gap-2 py-1.5 border-t border-surface-dim cursor-pointer" data-task="${t.id}"><span class="text-[10px] font-mono text-on-surface-variant">#${t.id}</span><span class="text-xs flex-1 truncate">${esc(t.title)}</span>${chip(STATUS_LABEL[t.status])}</div>`).join("") || `<div class="text-xs text-on-surface-variant">No tasks</div>`}</div>
    <div class="term p-3 brut max-h-72 overflow-y-auto flex flex-col gap-1">${logLines(a.events, 20)}</div>
  </aside>`;
}

/* ---------------- 03 // task dispatch ---------------- */
async function viewDispatch() {
  const [running, events] = await Promise.all([api("/tasks?status=in_progress"), api("/events?limit=300")]);
  const s = S.stats;
  const util = pct(s.agents_working, s.agents_online);
  const dispatchCard = (t, i) => {
    const a = S.agents.find((x) => x.id === t.assignee_id) || { metrics: {} };
    const colors = ["bg-primary text-on-primary", "bg-tertiary text-on-tertiary", "bg-primary-container text-on-primary-container"];
    const p = PRIO[t.priority];
    return `
    <div class="bg-surface-container-high brut">
      <div class="p-3 flex flex-wrap items-center gap-2 border-b-2 border-primary">
        <span class="px-2 py-1 font-headline font-bold uppercase text-sm ${colors[i % 3]}">Agent: ${esc(t.assignee_name || "unassigned")}</span>
        <span class="font-mono text-xs font-bold ${t.priority === "urgent" ? "text-secondary" : "text-tertiary"}">[${p.p} ${p.label.toUpperCase()}]</span>
        <span class="font-mono text-xs">REPO: ${esc(S.projects.find((x) => x.id === t.project_id)?.repo || t.project_name)}</span>
        <span class="ml-auto flex gap-2"><button class="btn bg-surface text-[10px] py-1" data-inject="${t.id}">Inject context</button><button class="btn btn-red text-[10px] py-1" data-abort="${t.id}">Abort job</button></span>
      </div>
      <div class="p-3 flex justify-between gap-2 text-xs font-mono font-bold"><span class="cursor-pointer hover:underline" data-task="${t.id}">TASK: ${esc(t.title)}</span><span class="text-secondary shrink-0">RUNNING // #${t.id}</span></div>
      <div class="mx-3 term p-3 max-h-48 overflow-y-auto flex flex-col gap-1">${logLines(events.filter((e) => e.task_id === t.id), 8)}</div>
      <div class="m-3 border-2 border-primary bg-surface px-3 py-2 flex flex-wrap gap-x-6 gap-y-1 text-[11px] font-mono">
        <span>ALLOCATION: <b>${a.metrics.vram_gb !== undefined ? num(a.metrics.vram_gb) + " GB VRAM" : "—"}</b></span>
        <span>ENGINE: <b>${esc(a.model || "—")}</b></span>
        <span class="ml-auto ${t.priority === "urgent" ? "text-secondary" : "text-tertiary"}">PROGRESS: <b>${t.todo_count ? pct(t.todo_done, t.todo_count) + "%" : "—"}</b></span>
      </div>
    </div>`;
  };
  const load = S.agents.filter((a) => a.effective_status !== "offline");
  const maxOpen = Math.max(1, ...load.map((a) => a.open_tasks));
  const repoTile = (p) => {
    const busy = p.active_count;
    return `<a href="#/projects/${p.id}" class="border-2 border-primary bg-surface p-2 flex flex-col gap-1 shadow-[2px_2px_0_0_#1a1a1a] hover:bg-surface-bright">
      <span class="text-xs font-mono font-bold truncate ${busy ? "text-secondary" : ""}">${esc(p.repo ? p.repo.split("/").pop() : p.name)}</span>
      <span class="flex items-center justify-between gap-1">${busy ? chip(`${busy} busy`, "bg-secondary text-on-primary") : p.blocked_count ? chip("blocked", "bg-secondary-fixed text-secondary") : chip("idle")}<span class="text-[10px] font-mono text-on-surface-variant">${p.task_count - p.done_count} open</span></span>
    </a>`;
  };
  return `
  <div class="p-4 lg:p-6 flex flex-col gap-6">
    <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
      <div class="bg-surface brut p-4"><div class="flex justify-between text-xs font-headline font-bold uppercase"><span>Dispatch_bus // 24h</span><span class="w-3 h-3 bg-primary-container"></span></div><div class="font-headline font-bold text-4xl mt-3">${s.events_24h || 0} <span class="text-sm text-secondary">events</span></div><div class="text-[11px] font-mono mt-3">TODOS CHECKED: ${s.todos_done_24h || 0}</div></div>
      <div class="bg-primary-container brut p-4"><div class="flex justify-between text-xs font-headline font-bold uppercase"><span>Queued tasks</span>${ms("call_split")}</div><div class="font-headline font-bold text-4xl mt-3">${s.tasks_todo || 0} <span class="text-sm">in backlog</span></div><div class="text-[11px] font-mono mt-3">UNASSIGNED: ${s.tasks_unassigned || 0} // REVIEW: ${s.tasks_review || 0}</div></div>
      <div class="bg-surface brut p-4"><div class="flex justify-between text-xs font-headline font-bold uppercase"><span>Fleet utilization</span><span class="w-3 h-3 bg-secondary"></span></div><div class="font-headline font-bold text-4xl mt-3">${util}% <span class="text-sm text-tertiary">${s.agents_working || 0} running</span></div><div class="text-[11px] font-mono mt-3">DORMANT POOL: ${s.agents_offline || 0} agent${s.agents_offline === 1 ? "" : "s"}</div></div>
      <div class="bg-tertiary text-on-tertiary brut p-4"><div class="flex justify-between text-xs font-headline font-bold uppercase"><span>Repositories linked</span>${ms("fact_check")}</div><div class="font-headline font-bold text-4xl mt-3">${s.repos_connected || 0} / ${S.projects.length} <span class="text-sm">projects</span></div><div class="text-[11px] font-mono mt-3">BLOCKED TASKS: ${s.tasks_blocked || 0}</div></div>
    </div>
    <div class="grid grid-cols-1 xl:grid-cols-[minmax(0,420px)_1fr] gap-6 items-start">
      <div class="flex flex-col gap-6">
        <form id="composer" class="bg-surface-container-low brut">
          <div class="p-4 border-b-2 border-primary flex items-center justify-between bg-surface-container"><span class="flex items-center gap-2 font-headline font-bold uppercase text-sm">${ms("forward_to_inbox")} 01 // Ingress dispatch composer</span>${chip("Queue: ready", "bg-primary text-on-primary")}</div>
          <div class="p-4 flex flex-col gap-4">
            <div><div class="lbl"><span>Task directive name</span><span class="text-secondary">Required</span></div><input name="title" required class="field" placeholder="e.g. Audit AST transitions for state hydration"></div>
            <div><div class="lbl"><span>Target project / repository</span><span class="text-tertiary">${s.repos_connected || 0} linked</span></div>
              <select name="project" class="field">${S.projects.map((p) => `<option value="${p.id}">${esc(p.name)}${p.repo ? ` (${esc(p.repo.split("/").pop())})` : ""}</option>`).join("")}</select></div>
            <div><div class="lbl">Execution priority level</div><div class="grid grid-cols-4 gap-2" id="prioPick">
              ${PRIORITIES.map((p, i) => `<button type="button" data-prio="${p}" class="btn py-2 ${i === 1 ? "btn-yellow" : "bg-surface"}">${PRIO[p].p} ${PRIO[p].label}</button>`).join("")}</div><input type="hidden" name="priority" value="high"></div>
            <div><div class="lbl"><span>Routing strategy &amp; assignee</span>${chip("autonomous mesh", "bg-primary-container text-on-primary-container")}</div>
              <div class="grid grid-cols-2 gap-2" id="routePick">
                <button type="button" data-route="auto" class="text-left p-2 border-2 border-primary bg-primary text-on-primary"><div class="font-headline font-bold text-xs uppercase">Auto-select agent</div><div class="text-[10px] font-mono opacity-80">Unassigned; first free agent claims it</div></button>
                <button type="button" data-route="manual" class="text-left p-2 border-2 border-primary bg-surface"><div class="font-headline font-bold text-xs uppercase">Manual assign</div><div class="text-[10px] font-mono opacity-80">Bind to a specific agent</div></button>
              </div>
              <select name="assignee" class="field mt-2 hidden">${S.agents.map((a) => `<option value="${a.id}">${esc(a.name)} [${esc(a.effective_status)}]</option>`).join("")}</select></div>
            <div><div class="lbl"><span>Task execution instructions (markdown)</span><span>Stdin prompt</span></div><textarea name="description" rows="5" class="field" placeholder="## Scope of work&#10;- Inspect /src/…&#10;- Implement …"></textarea></div>
            <div><div class="lbl"><span>Execution milestones (one per line)</span><span>Todos</span></div><textarea name="todos" rows="3" class="field" placeholder="Parse discovery endpoints&#10;Generate test harness&#10;Run suite with coverage"></textarea></div>
            <button class="btn btn-dark py-4 text-sm shadow-[4px_4px_0_0_#ffcc00]">${ms("bolt")} Dispatch directive to mesh</button>
          </div>
        </form>
        <div class="bg-surface brut p-4 flex flex-col gap-3">
          <div class="flex justify-between items-center"><span class="flex items-center gap-2 font-headline font-bold uppercase text-sm">${ms("hub")} Dynamic load balancer</span><span class="text-[10px] font-mono text-secondary">${load.length} online</span></div>
          ${load.map((a, i) => `<div><div class="flex justify-between text-xs font-mono"><span>${esc(a.name)} (${esc(a.role || a.model || "agent")})</span><span class="font-bold">${a.open_tasks} open</span></div>${bar(pct(a.open_tasks, maxOpen), ["bg-secondary", "bg-tertiary", "bg-primary-container", "bg-primary"][i % 4], "h-3", "bg-surface-container")}</div>`).join("") || `<div class="text-xs text-on-surface-variant">No agents online.</div>`}
        </div>
      </div>
      <div class="flex flex-col gap-4">
        <div class="bg-primary text-on-primary brut p-4 flex flex-wrap items-center gap-3">
          <span class="flex items-center gap-2 font-headline font-bold uppercase"><span class="w-3 h-3 rounded-full bg-secondary animate-pulse"></span>02 // Active dispatch streams [${running.length} executing]</span>
        </div>
        ${running.map(dispatchCard).join("") || `<div class="bg-surface brut p-6">${emptyBox("hourglass_empty", "No tasks executing", "Dispatch a task, or have an agent run: agentctl.py task claim")}</div>`}
      </div>
    </div>
    <div class="bg-surface-container-low brut p-4 lg:p-6">
      <div class="flex flex-wrap justify-between items-center gap-2 pb-3 mb-4 border-b-2 border-primary"><span class="flex items-center gap-2 font-headline font-bold uppercase">${ms("lan")} Organization repository linker &amp; queue distribution</span><span class="text-xs font-mono"><span class="text-secondary">● ${S.projects.filter((p) => p.active_count).length} active</span> ○ ${S.projects.filter((p) => !p.active_count).length} idle</span></div>
      <div class="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-3">${S.projects.map(repoTile).join("")}</div>
    </div>
  </div>`;
}

/* ---------------- 04 // compute & vram ---------------- */
async function viewCompute() {
  const hosts = S.hosts, agents = S.agents, s = S.stats;
  if (!hosts.length) {
    return `<div class="p-4 lg:p-6"><div class="bg-surface-bright brut p-6 lg:p-10 flex flex-col gap-4">
      <div class="flex items-center gap-3 text-xs font-mono font-bold text-secondary uppercase"><span class="w-12 h-3 hazard inline-block"></span>No hardware telemetry received</div>
      <h1 class="font-headline font-bold text-4xl lg:text-5xl uppercase tracking-tighter">Compute &amp; VRAM</h1>
      <p class="max-w-2xl">No machine has reported its hardware yet. Run this on each machine your agents use, every minute or so (cron, systemd timer, or in the agent's loop):</p>
      <div class="term p-4 brut">$ python3 agentctl.py host report --auto<br>$ python3 agentctl.py host report --gpu "RTX 4090" --vram-used-gb 21.2 --vram-total-gb 24</div>
      <p class="text-sm text-on-surface-variant max-w-2xl"><b>--auto</b> reads RAM and CPU load, plus GPU memory, temperature and power through <span class="font-mono">nvidia-smi</span> when present. Agents can add their own VRAM, model and speed with <span class="font-mono">hb --metric vram_gb=4.8 --metric tokens_per_sec=84</span>.</p>
    </div></div>`;
  }
  const h0 = hosts[0];
  const vramPct = hostTotals().vram, ramPct = hostTotals().ram;
  const over = hosts.filter((h) => h.vram_total_gb && h.vram_used_gb / h.vram_total_gb > 0.95);
  const used = agents.reduce((a, x) => a + (Number(x.metrics.vram_gb) || 0), 0);
  const notes = [];
  if (over.length) notes.push(["memory", "VRAM headroom exhausted", `${over.map((h) => h.name).join(", ")} above 95% VRAM. Quantize or offload a model before dispatching more local work.`]);
  if (s.vram_total_gb && used > s.vram_total_gb) notes.push(["warning", "Agents overbooked", `Agents report ${num(used)} GB of VRAM against ${num(s.vram_total_gb)} GB available.`]);
  const blocked = agents.filter((a) => a.effective_status === "blocked");
  if (blocked.length) notes.push(["block", "Blocked agents", `${blocked.map((a) => a.name).join(", ")} blocked. Check their tasks for the reason.`]);
  const stale = hosts.filter((h) => !h.online);
  if (stale.length) notes.push(["cloud_off", "Stale telemetry", `${stale.map((h) => h.name).join(", ")} stopped reporting (${ago(stale[0].last_seen)}).`]);
  if (!notes.length) notes.push(["check_circle", "All nominal", "No VRAM pressure, blocked agents or stale hosts detected."]);
  const card = (cls, top, value, unit, foot, barP, barCls) => `<div class="${cls} brut p-4 flex flex-col justify-between gap-3 min-h-[170px]">${top}<div class="font-headline font-bold text-3xl">${value} <span class="text-sm font-normal">${unit}</span></div>${barP !== undefined ? bar(barP, barCls, "h-2.5", "bg-surface-dim") : ""}<div class="text-[10px] font-mono uppercase">${foot}</div></div>`;
  return `
  ${over.length ? `<div class="m-4 lg:m-6 mb-0 lg:mb-0 bg-secondary text-on-primary brut p-5 flex flex-col md:flex-row md:items-center gap-4">
    <span class="w-12 h-12 bg-surface text-secondary grid place-items-center border-2 border-primary shrink-0">${ms("warning")}</span>
    <div class="flex-1"><span class="bg-primary-container text-on-primary-container px-2 py-0.5 text-[10px] font-headline font-bold uppercase">Hard limit // VRAM exhausted</span><div class="font-headline font-bold text-2xl uppercase mt-1">${esc(over[0].name)}: insufficient VRAM headroom</div><p class="text-sm">${num(over[0].vram_used_gb)} of ${num(over[0].vram_total_gb)} GB in use on ${esc(over[0].gpu || "GPU")}.</p></div></div>` : ""}
  <div class="p-4 lg:p-6 flex flex-col gap-6">
    <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-4">
      <div class="bg-surface-container brut p-4 flex flex-col gap-2 min-h-[170px]"><div class="text-[10px] font-headline font-bold uppercase">Local runtime engine</div><div class="font-headline font-bold text-xl uppercase leading-tight">${esc(h0.name)}</div><div class="text-[11px] font-mono text-on-surface-variant">${esc(h0.gpu || "GPU not reported")}${h0.ram_total_gb ? ` · ${num(h0.ram_total_gb)}GB RAM` : ""}</div>
        <div class="mt-auto text-[11px] font-mono flex flex-col gap-0.5"><div class="flex justify-between"><span>ENGINE:</span><b class="text-tertiary">${esc(h0.engine || "—")}</b></div><div class="flex justify-between"><span>HOSTS ONLINE:</span><b>${s.hosts_online}/${hosts.length}</b></div><div class="flex justify-between"><span>REPORTED:</span><b>${ago(h0.last_seen)}</b></div></div></div>
      ${card("bg-surface", `<div class="flex justify-between text-[10px] font-headline font-bold uppercase"><span class="text-secondary">● ${esc(h0.gpu || "GPU")} VRAM</span>${vramPct !== null ? chip(vramPct + "%", "bg-secondary text-on-primary") : ""}</div>`, num(s.vram_used_gb), `/ ${num(s.vram_total_gb)} GB`, `Available: ${num((s.vram_total_gb || 0) - (s.vram_used_gb || 0))} GB`, vramPct, "bg-secondary")}
      ${card("bg-surface", `<div class="flex justify-between text-[10px] font-headline font-bold uppercase"><span>Host unified RAM</span>${ramPct !== null ? chip(ramPct + "%", "bg-primary text-on-primary") : ""}</div>`, num(s.ram_used_gb), `/ ${num(s.ram_total_gb)} GB`, `Headroom: ${num((s.ram_total_gb || 0) - (s.ram_used_gb || 0))} GB`, ramPct, "bg-primary")}
      ${card("bg-surface", `<div class="flex justify-between text-[10px] font-headline font-bold uppercase"><span class="text-tertiary">Tokens / sec</span>${chip("inference", "bg-tertiary text-on-tertiary")}</div>`, num(s.tokens_per_sec), "t/s", `Sum across ${s.hosts_online} host${s.hosts_online === 1 ? "" : "s"}`)}
      ${card("bg-surface", `<div class="flex justify-between text-[10px] font-headline font-bold uppercase"><span>Thermal &amp; wattage</span>${chip(h0.temp_c > 83 ? "hot" : "nominal", h0.temp_c > 83 ? "bg-secondary text-on-primary" : "bg-surface-container-highest")}</div>`, `${num(h0.temp_c, 0)}°C`, `${num(h0.power_w, 0)}W`, `CPU load: ${num(h0.cpu_pct, 0)}%`)}
    </div>
    <div class="grid grid-cols-1 xl:grid-cols-[1fr_360px] gap-6 items-start">
      <div class="bg-surface brut">
        <div class="p-4 lg:p-5 flex flex-wrap justify-between gap-3 border-b-2 border-primary"><div><div class="font-headline font-bold text-xl uppercase">Agent runtime allocation matrix</div><div class="text-xs text-on-surface-variant">Per-agent memory and model, from heartbeat metrics</div></div><span class="px-3 py-1 border-2 border-primary text-xs font-mono h-fit">${agents.filter((a) => a.effective_status !== "offline").length} SLOTS ACTIVE</span></div>
        <div class="overflow-x-auto"><table class="w-full text-sm"><thead class="bg-surface-container-high text-[11px] font-headline uppercase text-left"><tr><th class="p-3">Agent &amp; engine</th><th class="p-3">Quant</th><th class="p-3">VRAM</th><th class="p-3">Speed</th><th class="p-3">Context</th><th class="p-3">Status</th></tr></thead><tbody>
          ${agents.map((a) => { const m = a.metrics; const bad = a.effective_status === "blocked"; return `<tr class="border-t border-surface-dim ${bad ? "bg-secondary-fixed" : ""} cursor-pointer" data-href="#/agents/${a.id}"><td class="p-3"><div class="font-headline font-bold uppercase ${bad ? "text-secondary" : ""}">${bad ? "! " : ""}${esc(a.name)}</div><div class="text-[11px] text-on-surface-variant">${esc(a.model || "model not reported")}${a.host ? " // " + esc(a.host) : ""}</div></td>
            <td class="p-3">${m.quant ? chip(m.quant, m.quant === "API" ? "bg-tertiary-container text-tertiary" : "bg-surface-container-highest") : "—"}</td>
            <td class="p-3 font-mono ${m.vram_gb > 10 ? "text-secondary" : ""}">${m.vram_gb !== undefined ? num(m.vram_gb) + " GB" : "—"}</td><td class="p-3 font-mono">${m.tokens_per_sec !== undefined ? num(m.tokens_per_sec) + " t/s" : "—"}</td>
            <td class="p-3 font-mono text-xs">${m.context_max ? `${pct(m.context_used || 0, m.context_max)}%` : "—"}</td><td class="p-3">${agentBadge(a.effective_status)}</td></tr>`; }).join("")}
        </tbody></table></div>
      </div>
      <div class="bg-surface brut p-4 flex flex-col gap-4">
        <div class="flex justify-between items-start"><div class="font-headline font-bold text-lg uppercase leading-tight">VRAM / compute<br>quota by agent</div>${chip("from metrics", "border border-primary")}</div>
        ${agents.filter((a) => a.metrics.vram_gb !== undefined).map((a) => `<div><div class="flex justify-between text-xs font-mono"><span>${esc(a.name)}${a.model ? ` (${esc(a.model)})` : ""}</span><b class="${a.metrics.vram_gb > 10 ? "text-secondary" : ""}">${num(a.metrics.vram_gb)} GB</b></div>${bar(pct(a.metrics.vram_gb, s.vram_total_gb || used || 1), "bg-primary", "h-2", "bg-surface-dim")}</div>`).join("") || `<div class="text-xs text-on-surface-variant">No agent reports vram_gb yet.</div>`}
        <div class="flex justify-between text-xs font-mono pt-3 border-t-2 border-primary"><span>COMMITTED SUM:</span><b class="${s.vram_total_gb && used > s.vram_total_gb ? "text-secondary" : ""}">${num(used)} GB${s.vram_total_gb ? ` / ${num(s.vram_total_gb)} GB` : ""}</b></div>
      </div>
    </div>
    <div class="bg-surface-container-high brut p-4 lg:p-6">
      <div class="text-[10px] font-mono uppercase">Multi-agent concurrency diagnostics</div><div class="font-headline font-bold text-xl uppercase mb-4">System recommendations</div>
      <div class="grid grid-cols-1 md:grid-cols-3 gap-4">${notes.map(([icon, t, b]) => `<div class="bg-surface border-2 border-primary p-4"><div class="flex items-center gap-2 text-xs font-headline font-bold uppercase ${icon === "check_circle" ? "text-tertiary" : "text-secondary"}">${ms(icon)} ${t}</div><p class="text-xs font-mono text-on-surface-variant mt-2">${esc(b)}</p></div>`).join("")}</div>
    </div>
    ${hosts.length > 1 ? `<div class="bg-surface brut p-4"><div class="font-headline font-bold uppercase mb-3">All hosts</div><div class="grid grid-cols-1 md:grid-cols-3 gap-3">${hosts.map((h) => `<div class="border-2 border-primary p-3 ${h.online ? "" : "opacity-60"}"><div class="flex justify-between"><b class="font-headline uppercase">${esc(h.name)}</b>${chip(h.online ? "online" : "stale", h.online ? "bg-tertiary text-on-tertiary" : "")}</div><div class="text-[11px] font-mono mt-1">${esc(h.gpu || "")} VRAM ${num(h.vram_used_gb)}/${num(h.vram_total_gb)} GB · RAM ${num(h.ram_used_gb)}/${num(h.ram_total_gb)} GB</div></div>`).join("")}</div></div>` : ""}
  </div>`;
}

/* ---------------- 05 // repositories ---------------- */
async function viewRepos() {
  const linked = S.projects.filter((p) => p.repo), unlinked = S.projects.filter((p) => !p.repo);
  const tasks = await api("/tasks?limit=1000");
  const lead = (p) => { const c = {}; tasks.filter((t) => t.project_id === p.id && t.assignee_name).forEach((t) => (c[t.assignee_name] = (c[t.assignee_name] || 0) + 1)); return Object.entries(c).sort((a, b) => b[1] - a[1])[0]?.[0]; };
  const repoCard = (p) => {
    const bad = p.blocked_count > 0;
    return `<div class="bg-surface brut p-4 flex flex-col gap-3 ${bad ? "border-secondary" : ""}" style="${bad ? "border-color:#e63b2e" : ""}">
      <div class="flex justify-between items-start gap-2"><span class="font-headline font-bold text-lg uppercase truncate">${esc(p.repo.split("/").pop())}</span>${bad ? chip("blocked", "bg-secondary text-on-primary") : p.active_count ? chip("active", "bg-secondary text-on-primary") : chip(p.status)}</div>
      <p class="text-sm text-on-surface-variant line-clamp-2">${esc(p.description || p.name)}</p>
      ${bad ? `<div class="border-2 border-secondary bg-secondary-fixed text-secondary text-xs font-mono p-2 flex gap-2">${ms("warning")} ${p.blocked_count} blocked task${p.blocked_count > 1 ? "s" : ""} in ${esc(p.name)}</div>` : ""}
      <div class="flex flex-wrap justify-between items-center gap-2 pt-3 border-t border-surface-dim text-xs font-mono"><span class="border-2 border-primary px-2 py-0.5 font-bold">${esc(p.branch || "default branch")}</span><span>LEAD: <b>${esc(lead(p) || "unassigned")}</b></span></div>
      <div class="text-[11px] font-mono text-tertiary truncate">${esc(p.repo)}</div>
      <div class="flex gap-2"><a href="#/projects/${p.id}" class="btn bg-surface flex-1">${ms("view_kanban")} Open board</a><button class="btn bg-surface" data-linkrepo="${p.id}" title="Edit repo">${ms("edit")}</button></div>
    </div>`;
  };
  return `
  <div class="p-4 lg:p-6">
    <div class="brut bg-surface">
      <div class="bg-primary text-on-primary p-4 flex flex-wrap items-center gap-3">
        <span class="w-3.5 h-3.5 bg-primary-container"></span><span class="font-headline font-bold text-lg lg:text-xl uppercase text-primary-fixed">Repository discovery &amp; project onboarding</span>
        <span class="px-2 py-0.5 border border-surface-dim text-xs font-mono">${linked.length} repos linked</span>
        <span class="px-2 py-0.5 border border-primary-fixed text-xs font-mono text-primary-fixed">${unlinked.length} projects without repo</span>
      </div>
      <div class="p-4 border-b-2 border-primary flex flex-wrap gap-3 items-center">
        <div class="relative flex-1 min-w-[220px]"><span class="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2">search</span><input id="repoSearch" class="field pl-10" placeholder="FILTER BY REPO OR PROJECT NAME"></div>
        <span class="text-xs font-mono"><span class="text-secondary">■</span> LINKED: ${linked.length} &nbsp; <span class="text-primary-fixed-dim">■</span> UNLINKED: ${unlinked.length}</span>
      </div>
      <div class="p-4 lg:p-6 flex flex-col gap-6" id="repoBody">
        <div class="flex justify-between items-center border-b-2 border-primary pb-2"><span class="flex items-center gap-2 font-headline font-bold uppercase">${ms("radio_button_checked")} Active fleet repositories [${linked.length} attached]</span></div>
        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">${linked.map((p) => `<div data-rsearch="${esc((p.repo + " " + p.name).toLowerCase())}">${repoCard(p)}</div>`).join("") || emptyBox("folder_off", "No repositories linked", "Link a repo to a project below, or create a project with one.")}</div>
        <div class="flex justify-between items-center border-b-2 border-primary pb-2"><span class="flex items-center gap-2 font-headline font-bold uppercase">${ms("add_circle")} Projects awaiting a repository [${unlinked.length}]</span></div>
        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">${unlinked.map((p) => `<div data-rsearch="${esc(p.name.toLowerCase())}" class="bg-surface-container brut p-4 flex flex-col gap-3"><div class="flex justify-between"><span class="font-headline font-bold text-lg uppercase">${esc(p.name)}</span>${chip("unassigned", "border border-primary")}</div><p class="text-sm text-on-surface-variant line-clamp-2">${esc(p.description || "No description")}</p><button class="btn btn-yellow" data-linkrepo="${p.id}">${ms("link")} Link repository</button></div>`).join("") || `<div class="text-xs font-mono text-on-surface-variant">Every project has a repository.</div>`}</div>
      </div>
      <div class="p-4 border-t-2 border-primary bg-surface-container flex flex-wrap justify-end gap-3"><button class="btn btn-yellow py-3 px-5" id="connectRepo">${ms("rocket_launch")} Import repository &amp; create project</button></div>
    </div>
  </div>`;
}

/* ---------------- 06 // agent dex ---------------- */
const DEX_ICONS = ["star", "bolt", "hexagon", "radar", "pest_control", "diversity_3", "memory", "psychology", "rocket", "token"];
async function viewDex(selId) {
  const agents = S.agents;
  const sel = selId ? agents.find((a) => String(a.id) === String(selId)) : agents[0];
  const fainted = agents.filter((a) => a.effective_status === "blocked" || a.effective_status === "offline");
  const stat = (label, val, p, cls) => `<div><div class="flex justify-between text-[11px] font-mono uppercase"><span>${label}</span><b>${val}</b></div>${bar(p, cls, "h-2", "bg-surface-container-highest")}</div>`;
  const lv = (a) => a.done_tasks;
  const card = (a, i) => {
    const down = a.effective_status === "blocked" || a.effective_status === "offline";
    const total = a.done_tasks + a.open_tasks;
    return `<a href="#/dex/${a.id}" class="bg-surface brut flex flex-col ${down ? "" : "hover:bg-surface-bright"} ${sel && sel.id === a.id ? "outline outline-4 outline-primary-container" : ""}" style="${down ? "border-color:#e63b2e" : ""}">
      ${down ? `<div class="bg-secondary text-on-primary text-[10px] font-mono font-bold px-3 py-1.5 flex justify-between"><span>⚠ STATUS: ${a.effective_status.toUpperCase()}</span><span>${esc(a.status_note || "").slice(0, 26)}</span></div>` : ""}
      <div class="p-4 flex flex-col gap-3">
        <div class="flex justify-between items-start"><div><div class="text-[11px] font-mono text-secondary">NO. ${pad(i + 1, 3)}</div><div class="font-headline font-bold text-xl uppercase leading-tight">${esc(a.name)}</div><div class="text-[10px] font-mono uppercase text-on-surface-variant">${esc(a.role || "agent")}</div></div>
          <div class="text-right"><span class="px-2 py-0.5 text-xs font-headline font-bold border-2 border-primary ${down ? "bg-secondary text-on-primary" : "bg-primary text-primary-fixed"}">LV. ${lv(a)}</span><div class="text-[10px] font-mono mt-1">TASKS: ${a.done_tasks}/${total}</div></div></div>
        <div class="dots border-2 border-primary h-36 grid place-items-center relative ${down ? "bg-secondary-fixed" : "bg-surface-container-low"}">
          <span class="${accent(a.name)} ${onAccent(accent(a.name))} w-16 h-16 border-2 border-primary grid place-items-center shadow-[3px_3px_0_0_#1a1a1a]"><span class="material-symbols-outlined text-4xl">${DEX_ICONS[a.id % DEX_ICONS.length]}</span></span>
          <span class="absolute bottom-1 right-2 text-[9px] font-mono">${esc(a.model || "type: unknown")}</span>
          ${a.effective_status === "working" ? `<span class="absolute top-2 left-2 text-[9px] font-mono border border-primary bg-surface px-1">● ACTIVE</span>` : ""}
        </div>
      </div>
      <div class="border-t-2 border-primary p-4 flex flex-col gap-2">
        <div class="flex flex-wrap gap-1.5">${chip(a.effective_status, AGENT_BADGE[a.effective_status])}${a.metrics.quant ? chip(a.metrics.quant, "bg-tertiary-container text-on-surface") : ""}${a.host ? chip(a.host, "border border-primary") : ""}</div>
        ${stat("Token velocity", a.metrics.tokens_per_sec !== undefined ? `${num(a.metrics.tokens_per_sec)} t/s` : "—", pct(a.metrics.tokens_per_sec || 0, 150), "bg-tertiary")}
        ${stat("Completion rate", `${pct(a.done_tasks, total)}%`, pct(a.done_tasks, total), "bg-primary")}
        <div class="flex justify-between items-center gap-2 text-[11px] font-mono uppercase pt-1"><span>Signature move</span><span class="px-2 py-0.5 border-2 border-primary bg-primary-container font-headline font-bold truncate max-w-[60%]">${esc(a.current_task_title || "idle stance")}</span></div>
      </div>
    </a>`;
  };
  let inspector = "";
  if (sel) {
    const d = await api(`/agents/${sel.id}`);
    const total = d.done_tasks + d.open_tasks;
    inspector = `
    <aside class="flex flex-col gap-4 xl:sticky xl:top-20">
      <div class="bg-primary text-on-primary brut p-3 flex items-center gap-3"><span class="w-6 h-6 rounded-full bg-secondary border-2 border-surface"></span><span class="w-3 h-3 rounded-full bg-primary-container"></span><span class="w-3 h-3 rounded-full bg-surface"></span><span class="font-headline font-bold uppercase text-sm">Dex inspector // chip #${pad(agents.indexOf(sel) + 1, 3)}</span></div>
      <div class="bg-surface brut p-4 flex flex-col gap-4">
        <div class="flex items-start gap-3 border-b-2 border-primary pb-3"><span class="bg-secondary text-on-primary font-mono text-xs p-1.5 font-bold">NO.<br>${pad(agents.indexOf(sel) + 1, 3)}</span><div class="flex-1"><div class="font-headline font-bold text-2xl uppercase leading-none">${esc(d.name)}</div><div class="text-xs uppercase mt-1">${esc(d.role || "agent")} // ${esc(d.model || "unknown model")}</div></div><span class="bg-primary-container border-2 border-primary px-2 font-headline font-bold text-lg">LV.<br>${lv(d)}</span></div>
        <div class="bg-surface-container-low border-2 border-primary p-3 text-xs font-mono uppercase leading-relaxed"><span class="text-secondary">// field notes:</span><br>${esc(d.status_note || "No status note.")} Last seen ${ago(d.last_seen)} on ${esc(d.host || "an unreported host")}.${d.parent_name ? ` Serves under ${esc(d.parent_name)}.` : ""}</div>
        <div class="grid grid-cols-2 gap-3">
          <div class="border-2 border-primary p-2">${stat("VRAM hunger", d.metrics.vram_gb !== undefined ? num(d.metrics.vram_gb) + " GB" : "—", pct(d.metrics.vram_gb || 0, S.stats.vram_total_gb || 24), "bg-secondary")}</div>
          <div class="border-2 border-primary p-2">${stat("Velocity", d.metrics.tokens_per_sec !== undefined ? num(d.metrics.tokens_per_sec) : "—", pct(d.metrics.tokens_per_sec || 0, 150), "bg-tertiary")}</div>
          <div class="border-2 border-primary p-2">${stat("Completion", `${d.done_tasks}/${total}`, pct(d.done_tasks, total), "bg-primary")}</div>
          <div class="border-2 border-primary p-2">${stat("Open load", d.open_tasks, pct(d.open_tasks, 10), "bg-primary-container")}</div>
        </div>
        <div class="border-2 border-primary p-3"><div class="flex justify-between items-center mb-2"><span class="font-headline font-bold text-sm uppercase">Move set (recent tasks)</span>${chip(d.effective_status, AGENT_BADGE[d.effective_status])}</div>
          <div class="grid grid-cols-2 gap-2">${d.tasks.slice(0, 4).map((t, i) => `<div class="border border-primary p-2 text-[11px] font-mono cursor-pointer" data-task="${t.id}"><b>${pad(i + 1)}.</b> ${esc(t.title.slice(0, 34))}<div class="text-[10px] ${t.status === "done" ? "text-tertiary" : "text-secondary"}">${STATUS_LABEL[t.status]}</div></div>`).join("") || `<div class="text-xs text-on-surface-variant col-span-2">No moves learned yet.</div>`}</div></div>
      </div>
      <div class="grid grid-cols-2 gap-3"><button class="btn btn-yellow py-3" data-assignto="${d.id}">${ms("auto_fix_high")} Assign task</button><a class="btn bg-surface py-3" href="#/agents/${d.id}">${ms("monitoring")} Telemetry</a></div>
    </aside>`;
  }
  return `
  <div class="p-4 lg:p-6 border-b-2 border-primary flex flex-wrap items-center gap-3">
    <span class="px-3 py-1.5 bg-secondary text-on-primary brut-sm font-headline font-bold uppercase flex items-center gap-2"><span class="w-3 h-3 rounded-full bg-primary-container"></span>Sys.dex // synapse cluster registry</span>
    <span class="px-3 py-1 border-2 border-primary font-mono text-xs">REGISTERED: <b class="bg-primary text-on-primary px-1">${pad(agents.length)}</b></span>
    <span class="px-3 py-1 border-2 border-primary font-mono text-xs">ACTIVE IN PARTY: <b class="bg-primary-container px-1">${pad(S.stats.agents_working)}</b></span>
    <span class="px-3 py-1 border-2 border-secondary bg-secondary-fixed text-secondary font-mono text-xs">FAINTED / BLOCKED: <b class="bg-secondary text-on-primary px-1">${pad(fainted.length)}</b></span>
  </div>
  <div class="p-4 lg:p-6 grid grid-cols-1 xl:grid-cols-[1fr_380px] gap-6 items-start">
    <div><div class="flex justify-between items-center mb-4"><span class="flex items-center gap-2 font-headline font-bold text-lg uppercase"><span class="w-3 h-3 bg-secondary"></span>Synthetic entity roster</span><span class="text-xs font-mono">[GRID VIEW // 001-${pad(agents.length, 3)}]</span></div>
      <div class="grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-3 gap-5">${agents.map(card).join("") || emptyBox("catching_pokemon", "Dex empty", "No agents registered yet.")}</div></div>
    ${inspector}
  </div>`;
}

/* ---------------- task drawer (task inspector) ---------------- */
let drawerTask = null;
async function openTask(id) {
  drawerTask = id;
  const t = await api(`/tasks/${id}`);
  const a = S.agents.find((x) => x.id === t.assignee_id);
  const m = a?.metrics || {};
  const firstOpen = t.todos.find((x) => !x.done);
  const opt = (vals, cur, fmt = (v) => v) => vals.map((v) => `<option value="${v}" ${v === cur ? "selected" : ""}>${esc(fmt(v))}</option>`).join("");
  $("#drawer").innerHTML = `
    <div class="sticky top-0 z-10 bg-surface border-b-2 border-primary p-3 flex flex-wrap items-center gap-2">
      <span class="w-3 h-3 bg-secondary"></span><span class="font-headline font-bold uppercase text-sm">Task inspector</span>
      ${t.assignee_name ? chip(`${t.assignee_name}${a?.model ? ` [${a.model}]` : ""}`, "bg-primary-container text-on-primary-container") : chip("unassigned")}
      <span class="ml-auto flex gap-2"><button class="btn bg-surface text-[10px] py-1" id="dDelete">${ms("delete")}</button><button class="btn btn-red text-[10px] py-1" id="dClose">Esc ×</button></span>
    </div>
    <div class="p-4 flex flex-col gap-4">
      <div class="grid grid-cols-4 gap-2 text-center">
        ${[["VRAM alloc", m.vram_gb !== undefined ? num(m.vram_gb) + " GB" : "—", "text-secondary"], ["Speed", m.tokens_per_sec !== undefined ? num(m.tokens_per_sec) + " t/s" : "—", "text-tertiary"], ["Milestones", `${t.todos.filter((x) => x.done).length}/${t.todos.length}`, ""], ["Time elapsed", t.status === "done" ? "done" : dur(t.started_at), ""]]
          .map(([l, v, c]) => `<div class="border-2 border-primary p-2"><div class="text-[9px] font-headline font-bold uppercase">${l}</div><div class="font-headline font-bold ${c}">${v}</div></div>`).join("")}
      </div>
      <div class="flex items-start gap-2 text-xs font-headline font-bold uppercase"><span class="text-secondary">${ms("target")}</span><div class="flex-1"><span class="text-[10px] text-on-surface-variant">Target:</span><div class="text-base leading-tight outline-none focus:bg-surface-bright" contenteditable="true" id="dTitle" spellcheck="false">${esc(t.title)}</div></div><span class="px-2 py-1 border-2 border-primary bg-surface-container text-[10px] font-mono normal-case">#${t.id} · ${esc(t.project_name)}</span></div>
      <div class="grid grid-cols-3 gap-2 text-[11px] font-mono">
        <label>STATUS<select id="dStatus" class="field mt-1 py-1">${opt(Object.keys(STATUS_LABEL), t.status, (v) => STATUS_LABEL[v])}</select></label>
        <label>PRIORITY<select id="dPriority" class="field mt-1 py-1">${opt(PRIORITIES, t.priority, (v) => `${PRIO[v].p} ${PRIO[v].label}`)}</select></label>
        <label>ASSIGNEE<select id="dAssignee" class="field mt-1 py-1"><option value="">Unassigned</option>${S.agents.map((x) => `<option value="${x.id}" ${x.id === t.assignee_id ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></label>
      </div>
      ${t.description ? `<div class="border-2 border-primary bg-surface-container-low p-3 text-xs whitespace-pre-wrap">${esc(t.description)}</div>` : ""}
      <div class="border-2 border-primary p-3">
        <div class="text-[11px] font-headline font-bold uppercase mb-2">Execution pipeline milestones:</div>
        ${t.todos.map((x, i) => { const active = firstOpen && x.id === firstOpen.id && t.status === "in_progress"; return `
          <label class="flex items-start gap-2 p-1.5 text-xs font-mono ${active ? "bg-primary-container border-2 border-primary" : ""}">
            <input type="checkbox" data-todo="${x.id}" ${x.done ? "checked" : ""} class="mt-0.5 accent-[#0055ff]">
            <span class="flex-1 ${x.done ? "" : active ? "font-bold" : "text-on-surface-variant"}">${i + 1}. ${esc(x.text)}</span>
            <span class="text-[10px] font-bold ${x.done ? "" : active ? "text-secondary" : "text-on-surface-variant"}">${x.done ? "DONE" : active ? "ACTIVE NOW" : "PENDING"}</span></label>`; }).join("") || `<div class="text-xs text-on-surface-variant">No milestones yet.</div>`}
        <form id="dTodoForm" class="flex gap-2 mt-2"><input id="dTodoText" class="field py-1.5" placeholder="Add milestone…"><button class="btn bg-surface">Add</button></form>
      </div>
      <form id="dNoteForm" class="flex gap-2"><input id="dNoteText" class="field py-1.5" placeholder="Inject context / progress note…"><button class="btn btn-dark">Post</button></form>
    </div>
    <div class="term mx-4 mb-4 border-2 border-primary">
      <div class="flex justify-between items-center p-2 border-b border-surface-dim/30 text-[10px]"><span class="text-secondary font-bold">● TTY: TASK_${t.id} [STDOUT_STREAM]</span><span>${t.events.length} lines</span></div>
      <div class="p-3 flex flex-col gap-1">${logLines(t.events, 100)}</div>
    </div>`;
  $("#drawer").classList.add("open");
  $("#drawerBackdrop").classList.remove("hidden");
  const patch = async (body) => { try { await api(`/tasks/${id}`, { method: "PATCH", body }); refresh(); } catch (e) { toast(e.message, true); } };
  $("#dClose").onclick = closeDrawer;
  $("#dStatus").onchange = (e) => patch({ status: e.target.value });
  $("#dPriority").onchange = (e) => patch({ priority: e.target.value });
  $("#dAssignee").onchange = (e) => patch({ assignee: e.target.value || "" });
  $("#dTitle").onblur = (e) => { const v = e.target.textContent.trim(); if (v && v !== t.title) patch({ title: v }); };
  $("#dTitle").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); e.target.blur(); } };
  $("#dDelete").onclick = async () => { if (confirm("Delete this task?")) { await api(`/tasks/${id}`, { method: "DELETE" }); closeDrawer(); refresh(); toast("Task deleted"); } };
  $$("[data-todo]", $("#drawer")).forEach((cb) => (cb.onchange = async () => { await api(`/todos/${cb.dataset.todo}`, { method: "PATCH", body: { done: cb.checked } }); refresh(); }));
  $("#dTodoForm").onsubmit = async (e) => { e.preventDefault(); const v = $("#dTodoText").value.trim(); if (v) { await api(`/tasks/${id}/todos`, { method: "POST", body: { text: v } }); refresh(); } };
  $("#dNoteForm").onsubmit = async (e) => { e.preventDefault(); const v = $("#dNoteText").value.trim(); if (v) { await api(`/tasks/${id}/progress`, { method: "POST", body: { message: v } }); refresh(); } };
}
function closeDrawer() {
  drawerTask = null;
  $("#drawer").classList.remove("open");
  $("#drawerBackdrop").classList.add("hidden");
}

/* ---------------- modals ---------------- */
function modal(title, bodyHtml, submitLabel, onSubmit) {
  const root = $("#modalRoot");
  root.innerHTML = `<div class="fixed inset-0 z-[55] bg-primary/50 flex items-start sm:items-center justify-center p-4 overflow-y-auto" id="mBack">
    <form id="mForm" class="bg-surface brut w-full max-w-lg">
      <div class="bg-primary text-primary-fixed p-4 flex justify-between items-center"><span class="font-headline font-bold uppercase">${title}</span><button type="button" class="btn btn-red text-[10px] py-1" id="mClose">Close [Esc]</button></div>
      <div class="p-4 flex flex-col gap-3">${bodyHtml}</div>
      <div class="p-4 border-t-2 border-primary bg-surface-container flex justify-end gap-2"><button type="button" class="btn bg-surface" id="mCancel">Dismiss</button><button class="btn btn-yellow">${submitLabel}</button></div>
    </form></div>`;
  const close = () => (root.innerHTML = "");
  $("#mClose").onclick = close; $("#mCancel").onclick = close;
  $("#mBack").onclick = (e) => { if (e.target.id === "mBack") close(); };
  $("#mForm").onsubmit = async (e) => {
    e.preventDefault();
    try { await onSubmit(Object.fromEntries(new FormData(e.target))); close(); refresh(); } catch (err) { toast(err.message, true); }
  };
  $("input, textarea, select", root)?.focus();
}
const field = (label, name, attrs = "", value = "") => `<label><div class="lbl">${label}</div><input name="${name}" class="field" value="${esc(value)}" ${attrs}></label>`;

function newProjectModal() {
  modal("New project", field("Project name", "name", "required") + `<label><div class="lbl">Description</div><textarea name="description" rows="3" class="field"></textarea></label>` +
    `<div class="grid grid-cols-2 gap-3">${field("Git repository", "repo", 'placeholder="github.com/org/repo"')}${field("Branch", "branch", 'placeholder="main"')}</div>`,
    "Create project", async (f) => { const p = await api("/projects", { method: "POST", body: f }); toast("Project created"); location.hash = `#/projects/${p.id}`; });
}
function linkRepoModal(pid) {
  const p = S.projects.find((x) => String(x.id) === String(pid));
  modal(`Link repository // ${esc(p.name)}`, `<div class="grid grid-cols-2 gap-3">${field("Git repository", "repo", 'required placeholder="github.com/org/repo"', p.repo)}${field("Branch", "branch", 'placeholder="main"', p.branch)}</div>`,
    "Save repository", async (f) => { await api(`/projects/${p.id}`, { method: "PATCH", body: f }); toast("Repository linked"); });
}
function newTaskModal({ project, assignee } = {}) {
  if (!S.projects.length) return newProjectModal();
  const cur = project || (S.route.match(/^\/projects\/(\d+)/) || [])[1] || S.filter.project;
  modal("Dispatch task", field("Task directive", "title", "required") +
    `<div class="grid grid-cols-2 gap-3"><label><div class="lbl">Project</div><select name="project" class="field">${S.projects.map((p) => `<option value="${p.id}" ${String(p.id) === String(cur) ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
     <label><div class="lbl">Priority</div><select name="priority" class="field">${PRIORITIES.map((p) => `<option value="${p}" ${p === (S.filter.priority || "medium") ? "selected" : ""}>${PRIO[p].p} ${PRIO[p].label}</option>`).join("")}</select></label></div>
     <label><div class="lbl">Assignee</div><select name="assignee" class="field"><option value="">Unassigned (any agent can claim)</option>${S.agents.map((a) => `<option value="${a.id}" ${String(a.id) === String(assignee || S.filter.assignee) ? "selected" : ""}>${esc(a.name)}</option>`).join("")}</select></label>
     <label><div class="lbl">Instructions</div><textarea name="description" rows="3" class="field"></textarea></label>
     <label><div class="lbl">Milestones (one per line)</div><textarea name="todos" rows="3" class="field"></textarea></label>`,
    "Dispatch", async (f) => {
      f.todos = (f.todos || "").split("\n").map((s) => s.trim()).filter(Boolean);
      if (!f.assignee) delete f.assignee;
      await api("/tasks", { method: "POST", body: f });
      toast("Task dispatched");
    });
}
function registerAgentModal() {
  modal("Register agent", field("Agent name", "name", "required") + `<div class="grid grid-cols-2 gap-3">${field("Role", "role", 'placeholder="Backend engineer"')}${field("Model", "model", 'placeholder="claude-sonnet"')}</div>` +
    `<div class="grid grid-cols-2 gap-3"><label><div class="lbl">Lead agent (cluster)</div><select name="parent" class="field"><option value="">None (cluster lead)</option>${S.agents.map((a) => `<option>${esc(a.name)}</option>`).join("")}</select></label>${field("Host", "host", 'placeholder="workstation-01"')}</div>`,
    "Register", async (f) => {
      const name = f.name.trim(); delete f.name;
      Object.keys(f).forEach((k) => f[k] === "" && k !== "parent" && delete f[k]);
      await api(`/agents/${encodeURIComponent(name)}/heartbeat`, { method: "POST", body: { status: "idle", ...f } });
      toast("Agent registered");
    });
}

/* ---------------- router & events ---------------- */
async function render() {
  const hash = location.hash.replace(/^#/, "") || "/projects";
  const [, section, id] = hash.split("/");
  S.route = hash;
  $("#sidebar").classList.remove("open"); $("#navBackdrop").classList.add("hidden");
  try {
    await loadShared();
    const html = section === "projects" && id ? await viewProject(id)
      : section === "agents" ? await viewAgents(id)
      : section === "dispatch" ? await viewDispatch()
      : section === "compute" ? await viewCompute()
      : section === "repos" ? await viewRepos()
      : section === "dex" ? await viewDex(id)
      : await viewProjects();
    const y = window.scrollY;
    $("#view").innerHTML = html;
    window.scrollTo(0, y);
    bindView();
  } catch (e) {
    $("#view").innerHTML = `<div class="p-6"><div class="bg-secondary-fixed brut p-6">${emptyBox("error", "Request failed", esc(e.message))}</div></div>`;
  }
}
const refresh = () => { render(); if (drawerTask) openTask(drawerTask); };

function bindView() {
  const v = $("#view");
  $$("[data-href]", v).forEach((el) => el.addEventListener("click", (e) => { if (!e.target.closest("[data-task],[data-stop],a,button")) location.hash = el.dataset.href; }));
  $$("[data-fproject]", v).forEach((el) => (el.onclick = () => { S.filter.project = el.dataset.fproject; render(); }));
  $("#fAssignee", v)?.addEventListener("change", (e) => { S.filter.assignee = e.target.value; render(); });
  $("#fPriority", v)?.addEventListener("change", (e) => { S.filter.priority = e.target.value; render(); });
  $("#connectCard", v)?.addEventListener("click", newProjectModal);
  $("#connectRepo", v)?.addEventListener("click", newProjectModal);
  $("#registerAgent", v)?.addEventListener("click", registerAgentModal);
  $("#emptyReset", v)?.addEventListener("click", () => { S.filter = { project: "", assignee: "", priority: "" }; render(); });
  $("#emptyNewTask", v)?.addEventListener("click", () => newTaskModal({ project: S.filter.project, assignee: S.filter.assignee }));
  $("#projNewTask", v)?.addEventListener("click", () => newTaskModal());
  $("#projStatus", v)?.addEventListener("change", async (e) => { await api(`/projects/${S.route.split("/")[2]}`, { method: "PATCH", body: { status: e.target.value } }); toast("Project updated"); render(); });
  $("#repoSearch", v)?.addEventListener("input", (e) => { const q = e.target.value.toLowerCase(); $$("[data-rsearch]", v).forEach((el) => (el.style.display = el.dataset.rsearch.includes(q) ? "" : "none")); });
  $("#instantForm", v)?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    if (!f.assignee) delete f.assignee;
    try { await api("/tasks", { method: "POST", body: { ...f, priority: S.filter.priority || "medium" } }); toast("Task dispatched"); render(); } catch (err) { toast(err.message, true); }
  });
  const comp = $("#composer", v);
  if (comp) {
    $$("[data-prio]", comp).forEach((b) => (b.onclick = () => { comp.priority.value = b.dataset.prio; $$("[data-prio]", comp).forEach((x) => { x.classList.toggle("btn-yellow", x === b && b.dataset.prio !== "urgent"); x.classList.toggle("btn-red", x === b && b.dataset.prio === "urgent"); x.classList.toggle("bg-surface", x !== b); }); }));
    $$("[data-route]", comp).forEach((b) => (b.onclick = () => { const manual = b.dataset.route === "manual"; comp.assignee.classList.toggle("hidden", !manual); $$("[data-route]", comp).forEach((x) => { const on = x === b; x.classList.toggle("bg-primary", on); x.classList.toggle("text-on-primary", on); x.classList.toggle("bg-surface", !on); }); comp.dataset.manual = manual ? "1" : ""; }));
    comp.onsubmit = async (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(comp));
      f.todos = (f.todos || "").split("\n").map((s) => s.trim()).filter(Boolean);
      if (!comp.dataset.manual) delete f.assignee;
      try { await api("/tasks", { method: "POST", body: f }); toast("Directive dispatched"); comp.reset(); render(); } catch (err) { toast(err.message, true); }
    };
  }
  const brd = $("#board", v);
  if (brd) {
    $$(".tcard", brd).forEach((c) => {
      c.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/plain", c.dataset.task); c.classList.add("dragging"); });
      c.addEventListener("dragend", () => c.classList.remove("dragging"));
    });
    $$(".col", brd).forEach((col) => {
      col.addEventListener("dragover", (e) => { e.preventDefault(); col.classList.add("drop"); });
      col.addEventListener("dragleave", () => col.classList.remove("drop"));
      col.addEventListener("drop", async (e) => {
        e.preventDefault(); col.classList.remove("drop");
        try { await api(`/tasks/${e.dataTransfer.getData("text/plain")}`, { method: "PATCH", body: { status: col.dataset.status } }); render(); } catch (err) { toast(err.message, true); }
      });
    });
  }
}

document.addEventListener("click", async (e) => {
  const act = e.target.closest("[data-act]");
  if (act) {
    e.stopPropagation();
    const to = { start: "in_progress", unblock: "in_progress", pause: "blocked", merge: "done" }[act.dataset.act];
    let note;
    if (act.dataset.act === "pause") { note = prompt("Why is this task blocked?"); if (note === null) return; }
    try { await api(`/tasks/${act.dataset.id}`, { method: "PATCH", body: { status: to, note: note || undefined } }); refresh(); } catch (err) { toast(err.message, true); }
    return;
  }
  const inj = e.target.closest("[data-inject]");
  if (inj) { const msg = prompt("Context to inject into this task's log:"); if (msg) { await api(`/tasks/${inj.dataset.inject}/progress`, { method: "POST", body: { message: msg } }); refresh(); } return; }
  const ab = e.target.closest("[data-abort]");
  if (ab) { if (confirm("Abort this job? It returns to the backlog, unassigned.")) { await api(`/tasks/${ab.dataset.abort}`, { method: "PATCH", body: { status: "todo", assignee: "", note: "aborted from dispatch console" } }); refresh(); } return; }
  const lr = e.target.closest("[data-linkrepo]");
  if (lr) { e.stopPropagation(); return linkRepoModal(lr.dataset.linkrepo); }
  const at = e.target.closest("[data-assignto]");
  if (at) return newTaskModal({ assignee: at.dataset.assignto });
  const st = e.target.closest("[data-setstatus]");
  if (st) { await api(`/agents/${encodeURIComponent(st.dataset.setstatus)}/heartbeat`, { method: "POST", body: { status: st.dataset.to, task_id: 0 } }); toast("Agent updated"); refresh(); return; }
  const t = e.target.closest("[data-task]");
  if (t && !e.target.closest("#drawer")) { e.preventDefault(); openTask(t.dataset.task); }
});
$("#drawerBackdrop").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeDrawer(); $("#modalRoot").innerHTML = ""; } });
$("#newProjectBtn").onclick = newProjectModal;
$("#menuBtn").onclick = () => { $("#sidebar").classList.toggle("open"); $("#navBackdrop").classList.toggle("hidden"); };
$("#navBackdrop").onclick = () => { $("#sidebar").classList.remove("open"); $("#navBackdrop").classList.add("hidden"); };

/* ---------------- live updates ---------------- */
let rerenderT;
function scheduleRerender() {
  clearTimeout(rerenderT);
  rerenderT = setTimeout(() => {
    if (document.activeElement?.closest("#view input, #view select, #view textarea, #modalRoot, [contenteditable]")) return;
    render();
    if (drawerTask && !document.activeElement?.closest("#drawer")) openTask(drawerTask);
  }, 400);
}
function onEvents(evs) {
  if (!evs.length) return;
  evs.forEach((e) => { S.freshIds.add(e.id); setTimeout(() => S.freshIds.delete(e.id), 3000); });
  scheduleRerender();
}
async function startLive() {
  S.config = await api("/config").catch(() => ({ stream: true }));
  if (S.config.stream) {
    const es = new EventSource("/api/stream");
    es.addEventListener("events", (m) => onEvents(JSON.parse(m.data)));
    return;
  }
  let last = (await api("/stats")).last_event_id;
  setInterval(async () => {
    if (document.hidden) return;
    try {
      const evs = await api(`/events?since=${last}&limit=200`);
      if (evs.length) last = evs[0].id;
      onEvents(evs.reverse());
    } catch { /* next poll retries */ }
  }, 3000);
}

window.addEventListener("hashchange", render);
startLive().then(render);
setInterval(() => { if (!document.hidden) scheduleRerender(); }, 30000); // refresh "x ago", offline detection, host telemetry
