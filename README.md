# Synapse Local // Agent Control Center

A live dashboard showing what every agent is doing on every project, with each task, todo and progress note recorded as it happens.

- **Zero dependencies**: Python 3.9+ standard library only.
- **SQLite everywhere**: locally one file (`data/agents.db`) in WAL mode; hosted, the same SQL runs on Turso (`turso.py`, standard library HTTP client). Many agents can read and write at the same time. Writers queue briefly instead of failing.
- **Four ways for agents to write**: HTTP API, CLI, MCP tools, or `import db`. All of them land in the same file. With `AGENT_DASH_URL` set, the CLI and MCP server talk to a hosted dashboard over HTTP instead.
- **Optional auth**: set `AGENT_DASH_TOKEN` to require a token. Browsers log in with any username and the token as password; agents send `Authorization: Bearer <token>`. To give a teammate or an outside agent its own token, set `AGENT_DASH_AGENT_TOKENS="grokbot:<token>,alex:<token>"`: these work only as `Bearer` tokens (not for the browser login), and removing an entry revokes that one agent. Each still reads and writes the whole board. The agent uses its token as its own `AGENT_DASH_TOKEN` (the CLI and MCP server send that as `Bearer`). The browser login still needs the master `AGENT_DASH_TOKEN` on the server; with only agent tokens set, the dashboard UI stays locked.
- **Teams for outside agents**: a team token (stored hashed in the database, no redeploy) sees and changes only the projects granted to its team, and acts only as that team's own agents, so it can't write as yours. See [Teams](#teams-outside-agents).
- **Roles**: one master agent (e.g. `taskmaster`) watches the board and enforces the rules, one Main agent per folder does the work and reports it, and sub-sessions report nothing. See [Roles](#roles-master-main-sub).
- **Live UI**: locally the dashboard streams new events over SSE, including writes made straight to the file. On Vercel it polls every 3 seconds.

```
python3 server.py                 # http://127.0.0.1:8787  (--host 0.0.0.0 + AGENT_DASH_TOKEN to expose)
python3 demo_agents.py            # optional: seed sample data + 5 simulated agents
python3 -m unittest discover -s tests -v   # run from the repo folder; Turso tests also run if TURSO_DATABASE_URL is set
```

## Data model

| Table      | What it holds |
|------------|---------------|
| `projects` | name, description, status (`active/paused/completed/archived`), git repo, branch |
| `agents`   | name, role, status (`idle/working/blocked`), status note, current task, model, host, lead agent (cluster), metrics (`vram_gb`, `tokens_per_sec`, `context_used`, `context_max`, `quant`, …), last heartbeat. Agents count as offline after 5 minutes without any write. |
| `hosts`    | per machine: `gpu`, `engine`, `vram_used_gb`, `vram_total_gb`, `ram_used_gb`, `ram_total_gb`, `cpu_pct`, `tokens_per_sec`, `temp_c`, `power_w`, last report |
| `tasks`    | project, title, description, status (`todo/in_progress/blocked/review/done`), priority (`low/medium/high/urgent`), assignee, timestamps |
| `todos`    | checklist items on a task |
| `events`   | append-only log of every change: the activity feed and audit trail |
| `teams`, `team_projects`, `tokens` | outside teams, the projects each may access, and their tokens (hashed; optionally bound to one agent) |

Projects, agents, hosts, tasks and todos hold only the current state and are overwritten in place. History lives only in `events`.

Agents register automatically the first time they write under a new name.

## Agent integration

### 1. CLI (`agentctl.py`): simplest option, no server needed
```bash
export AGENT_NAME=backend-bot           # or --agent backend-bot
python3 agentctl.py hb --status working --role "Backend engineer" --note "starting" \
    --model qwen-2.5-coder --host workstation-01 --parent claudia --metric vram_gb=6.2 --metric tokens_per_sec=84
python3 agentctl.py host report --auto            # GPU/VRAM via nvidia-smi, RAM, CPU load of this machine
python3 agentctl.py task claim --project data-pipeline     # atomic: no two agents get the same task
python3 agentctl.py task add data-pipeline "Add retries" --priority high --todo "backoff" --todo "DLQ" --mine
python3 agentctl.py task log 7 "Implemented exponential backoff"
python3 agentctl.py todo done 21
python3 agentctl.py task block 7 --note "need DB creds"
python3 agentctl.py task done 7 --note "merged in #42"
python3 agentctl.py task list --mine --status todo,in_progress
```
Point at a different file with `--db PATH` or `AGENT_DB=PATH`. For a hosted dashboard, set `AGENT_DASH_URL=https://your-host` and `AGENT_DASH_TOKEN=...`.

### 2. MCP server: Claude Code and other MCP clients
On each machine that runs Claude Code agents, run this once (and again any time to update):
```bash
curl -fsSL https://YOUR-DASHBOARD/install.sh | sh
```
It asks for the dashboard password (hidden), checks it against the dashboard, installs the code in `~/.agent-tracker/app`, removes any older `agent-tracker` entries (including per-folder ones, which would otherwise override it; `~/.claude.json` is backed up first) adds the tracker for every folder, and adds the [token-usage hooks](#claude-code-hooks-token-usage-at-no-model-cost) (`~/.claude/settings.json` is backed up first). `/install.sh` is public and holds no secrets.

Manual setup instead:
```bash
claude mcp add agent-tracker --scope user -e AGENT_DASH_URL=https://your-host -e AGENT_DASH_TOKEN=... \
  -- python3 /abs/path/dashboard-ai/mcp_server.py
```
Each agent chooses its own name by calling `tracker_register` first; the server tells it to on startup. Names are unique: if one is taken, the agent gets an error and picks another. The name is remembered per folder in `~/.config/agent-tracker/names.json`, so the agent keeps it after a restart. Calling `tracker_register` again with a new name renames the agent and keeps its tasks and history. Set `-e AGENT_NAME=...` to fix the name instead.
Tools: `tracker_register` (name, role, model, host), `tracker_heartbeat` (status, model, host, parent, metrics), `tracker_report_host`, `tracker_list_projects`, `tracker_create_project`, `tracker_list_tasks`, `tracker_get_task`, `tracker_create_task`, `tracker_update_task`, `tracker_claim_next_task`, `tracker_log_progress`, `tracker_add_todo`, `tracker_complete_todo`.

### Roles: master, Main, sub
Set `AGENT_TRACKER_ROLE` in the environment Claude Code starts with; the MCP server and the hooks both read it.

| Role | Who | Tracker tools |
|---|---|---|
| `master` | one session that enforces the rules, e.g. `AGENT_TRACKER_ROLE=master AGENT_NAME=taskmaster claude` | read the board, `tracker_check_rules`, `tracker_create_task`, `tracker_update_task`, `tracker_log_progress` |
| `main` (default) | one agent per folder; heartbeats with `parent=taskmaster` | all task tools |
| `sub` | sub-sessions a Main starts: `AGENT_TRACKER_ROLE=sub claude …` | none. They load no tool definitions and can't write as their Main |

Claude Code's built-in subagents share their Main's session, so they need nothing. A git worktree is a different folder, so a session there gets its own name.

`tracker_check_rules` (also `GET /api/rules` and `agentctl.py rules`) lists what breaks the tracking rules, most severe first:
- agents that say working or blocked but have gone silent
- agents working without a claimed task
- your agents that report to nobody (when the master agent exists)
- in-progress tasks with no assignee, or with no progress for `quiet_minutes` (default 30)
- agents with several tasks in progress
- open tasks with the same title in one project

The server reads the master's name from `AGENT_DASH_MASTER` (default `taskmaster`).

### Claude Code hooks: token usage at no model cost
`install.sh` adds `hooks/claude_hook.py` to `~/.claude/settings.json` for two events. It runs outside the model, so it costs no tokens:
- **Stop**: adds up the session's token usage from its transcript and reports, as the Main agent's metrics, the folder's total over the last 24 hours. That covers the Main and all its sub-sessions (`tokens_in_24h`, `tokens_out_24h`, `cache_read_24h`, `cache_write_24h`, `sessions_24h`), plus `context_used` and the model. Sub-sessions only store their usage locally; the Main's next Stop reports it.
- **SessionEnd**: marks the Main agent idle.

The hook finds the dashboard in `~/.config/agent-tracker/env`, which `install.sh` writes, and never fails a session. Set `AGENT_TRACKER_DEBUG=1` to see its errors.

### Remote MCP: no install
`POST /mcp` speaks MCP over streamable HTTP. Any MCP client that supports remote servers connects with the URL and a Bearer token, for example:
```bash
claude mcp add --transport http agent-tracker https://your-host/mcp \
  --header "Authorization: Bearer <token>" --header "X-Agent: acme-openclaw"
```
The agent is the token's bound agent, or the `X-Agent` header. `X-Agent-Role: master` gives the master tool set.

### 3. HTTP API: agents on other machines
Identify the caller with an `"agent"` field in the body or an `X-Agent` header.

| Method | Path | Body / query |
|---|---|---|
| GET  | `/api/stats` | |
| GET/POST | `/api/projects` | `{name, description, repo, branch}` |
| GET/PATCH | `/api/projects/{id or name}` | `{status, description, name, repo, branch}` |
| GET  | `/api/agents`, `/api/agents/{id or name}` | |
| PATCH | `/api/agents/{id or name}` | `{name}`: rename; 400 if the name is taken |
| POST | `/api/agents/{name}/heartbeat` | `{status, note, role, task_id, model, host, parent, metrics: {}}` |
| GET  | `/api/hosts` | |
| POST | `/api/hosts/{name}` | `{gpu, engine, vram_used_gb, vram_total_gb, ram_used_gb, ram_total_gb, cpu_pct, tokens_per_sec, temp_c, power_w}` |
| POST | `/api/agents/{name}/claim` | `{project}` |
| GET  | `/api/tasks` | `?project=&status=todo,in_progress&assignee=` |
| POST | `/api/tasks` | `{project, title, description, priority, assignee, status, todos: []}` |
| GET/PATCH/DELETE | `/api/tasks/{id}` | `{status, priority, assignee, title, description, note}` |
| POST | `/api/tasks/{id}/progress` | `{message}` |
| POST | `/api/tasks/{id}/todos` | `{text}` |
| PATCH | `/api/todos/{id}` | `{done, text}` |
| GET  | `/api/events` | `?since=&project=&agent=&task=&limit=` |
| GET  | `/api/stream` | Server-sent events |
| GET  | `/api/whoami` | who the token is: `admin`, `internal` or `team` (with team and bound agent) |
| GET  | `/api/rules` | `?master=&quiet_minutes=`: rule breaks, see [Roles](#roles-master-main-sub) |
| POST | `/mcp` | remote MCP endpoint |
| GET/POST | `/api/teams` | admin only. `{name}` |
| GET  | `/api/teams/{name}` | admin only: projects, agents, tokens (never the token itself) |
| POST | `/api/teams/{name}/tokens` | admin only. `{label, agent}`; the response holds the token, shown once |
| DELETE | `/api/tokens/{id}` | admin only: revoke |
| POST/DELETE | `/api/teams/{name}/projects/{project}` | admin only: grant or take away a project |

```bash
curl -X POST localhost:8787/api/tasks/7/progress -H 'X-Agent: backend-bot' -d '{"message":"tests green"}'
```

### Teams: outside agents
For agents you don't run yourself (another team's OpenClaw, a contractor's bot). Manage teams with the master token, from the CLI (locally, or against the hosted dashboard with `AGENT_DASH_URL` and the master `AGENT_DASH_TOKEN`) or the API:
```bash
python3 agentctl.py team add acme
python3 agentctl.py team grant acme web-app                       # acme sees only granted projects
python3 agentctl.py team token acme --label "acme ci"             # prints the token once
python3 agentctl.py team token acme --agent acme-openclaw         # this token can only act as acme-openclaw
python3 agentctl.py team show acme                                # projects, agents, tokens
python3 agentctl.py team revoke 3                                 # revoke token 3 at once
```
A team token:
- sees only its granted projects, their tasks and events, and its own agents; other projects return 404
- writes only as its own agents. A name it uses first joins the team; names that belong to your agents or another team get 403. Every write must name an agent (`agent` field or `X-Agent` header), unless the token is bound to one.
- creates projects that are granted to its team automatically
- can't open the dashboard, the live stream, board-wide stats, host stats or team management

Team tokens need `AGENT_DASH_TOKEN` set on the server; with auth off, everything is open.

### Suggested agent instructions (paste into CLAUDE.md / AGENTS.md)
```
Track all your work in the agent tracker (agentctl.py or the tracker_* MCP tools):
- At start: if you have no tracker name yet, choose a short unique one and register (tracker_register).
- Then: heartbeat with status=working, your model, host and lead agent (if any). Claim a task or create one for the work you were given.
- Include metrics you know (vram_gb, tokens_per_sec, context_used, context_max) in heartbeats.
- Before creating a task, list existing tasks; never create a second task for the same work.
- Record every new piece of work as a task (or a todo on your current task) as soon as you identify it.
- Task title, description, status and priority always state the current truth. When a fact changes,
  rewrite the field (task set / tracker_update_task). Never append "Update:", "outdated" or "was X, now Y" text.
- History belongs in the progress log only: one line per meaningful step (task log / tracker_log_progress).
- Tick off todos as you finish them. Mark the task blocked (with a reason) or done (with a summary).
- When you stop: heartbeat with status=idle.
- Report to the master: heartbeat with parent=taskmaster.
```

## Hosting on Vercel + Turso (free, no card)
On Vercel the app runs as a serverless function (`api/index.py`, `vercel.json`) and stores its data in [Turso](https://turso.tech), a hosted SQLite-compatible database. The same code runs locally against a SQLite file whenever `TURSO_DATABASE_URL` is unset. Serverless functions can't hold a live stream open, so on Vercel the dashboard checks for new activity every 3 seconds.

1. **Turso:** sign up at turso.tech and create a database. Pick a region near your Vercel region (Vercel's default is Washington, D.C., `iad1`). Copy its URL (`libsql://…`) and create a database token.
2. **Vercel:** sign up with GitHub, then **Add New → Project** and import this repo. Leave the framework preset on **Other** and add these environment variables:
   - `TURSO_DATABASE_URL`: the `libsql://…` URL
   - `TURSO_AUTH_TOKEN`: the database token
   - `AGENT_DASH_TOKEN`: a long random password for the dashboard and agents
   - `AGENT_DASH_AGENT_TOKENS` (optional): per-agent tokens, `name:token,name:token`. Redeploy after changing it.

   Vercel's Turso integration (Marketplace → Turso) can create the database and set the first two variables for you.
3. Deploy. Every push to `main` redeploys. The dashboard is at `https://<project>.vercel.app`. Log in with any username and `AGENT_DASH_TOKEN` as the password.

Tables are created automatically on the first request. `/healthz` is the unauthenticated health check.

## Self-hosting
Run `python3 server.py --host 0.0.0.0` with `AGENT_DASH_TOKEN` set, or use Docker:
```bash
docker build -t agent-dashboard .
docker run -d -p 8080:8080 -v agent-data:/data -e AGENT_DASH_TOKEN=... agent-dashboard
```
Put it behind HTTPS (Caddy, nginx or the platform's TLS) so the token isn't sent in clear text. With a local SQLite file, run one instance only. Setting `TURSO_DATABASE_URL` stores the data in Turso instead.

## Screens
The UI is built from the Google Stitch export "Local AI Agent Control Center" (Bauhaus neo-brutalist design system: Tailwind config in `static/index.html`, extras in `static/styles.css`). Every number comes from the tracker; panels whose data hasn't been reported say so instead of showing sample values.

| Module | Shows | Data source |
|---|---|---|
| 01 // Projects & Kanban | project cards with sprint progress, instant dispatch, filters, 5-column sprint board (drag to change status), filtered empty state, stdout bus | projects, tasks, events |
| Project board + task inspector | per-project board, throughput, task drawer with milestones and the task's stdout | tasks, todos, events, agent metrics |
| 02 // Agent Fleet | clusters (lead + sub-agents), blocked-agent alert, agent inspector | agents (`parent`, `model`, `metrics`) |
| 03 // Task Dispatch | dispatch composer (priority P0–P3, auto/manual routing, milestones), live streams with inject/abort, load balancer, repo queue grid | tasks, agents, projects |
| 04 // Compute & VRAM | host VRAM/RAM/tokens/thermals, per-agent allocation matrix, VRAM quota, recommendations | hosts, agent metrics |
| 05 // Repositories | projects with linked repos, projects awaiting one | projects (`repo`, `branch`) |
| 06 // Agent Dex | collectible-card view of each agent: level = tasks completed, velocity, completion rate | agents |

Priorities map to Stitch's levels: `urgent` = P0 Critical, `high` = P1, `medium` = P2, `low` = P3.

## Concurrency notes
- Each write runs in a `BEGIN IMMEDIATE` transaction. Concurrent writers wait their turn (`busy_timeout=30s` locally, retry with backoff on Turso) and never error. `claim_next_task` is atomic. `tests/test_concurrency.py` checks this with 12 processes on a local file, and `tests/test_turso.py` with 6 processes on libSQL.
- A local SQLite file in WAL mode needs a local disk. Don't put it on NFS or SMB. Agents on other machines should use the HTTP API.
- If you outgrow SQLite (hundreds of writes per second from many hosts), the SQL in `db.py` is plain enough to port to Postgres.
