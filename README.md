# Agent Command — agent & project dashboard

A live dashboard showing what every agent is doing on every project, with each task, todo and progress note recorded as it happens.

- **Zero dependencies**: Python 3.9+ standard library only.
- **SQLite everywhere**: locally one file (`data/agents.db`) in WAL mode; hosted, the same SQL runs on Turso (`turso.py`, standard library HTTP client). Many agents can read and write at the same time. Writers queue briefly instead of failing.
- **Four ways for agents to write**: HTTP API, CLI, MCP tools, or `import db`. All of them land in the same file. With `AGENT_DASH_URL` set, the CLI and MCP server talk to a hosted dashboard over HTTP instead.
- **Optional auth**: set `AGENT_DASH_TOKEN` to require a token. Browsers log in with any username and the token as password; agents send `Authorization: Bearer <token>`.
- **Live UI**: locally the dashboard streams new events over SSE, including writes made straight to the file. On Vercel it polls every 3 seconds.

```
python3 server.py                 # http://127.0.0.1:8787  (--host 0.0.0.0 + AGENT_DASH_TOKEN to expose)
python3 demo_agents.py            # optional: seed sample data + 5 simulated agents
python3 -m unittest discover -s tests -v   # Turso tests also run if TURSO_DATABASE_URL is set (see tests/test_turso.py)
```

## Data model

| Table      | What it holds |
|------------|---------------|
| `projects` | name, description, status (`active/paused/completed/archived`) |
| `agents`   | name, role, status (`idle/working/blocked`), status note, current task, last heartbeat. Agents count as offline after 5 minutes without any write. |
| `tasks`    | project, title, description, status (`todo/in_progress/blocked/review/done`), priority (`low/medium/high/urgent`), assignee, timestamps |
| `todos`    | checklist items on a task |
| `events`   | append-only log of every change: the activity feed and audit trail |

Projects, agents, tasks and todos hold only the current state and are overwritten in place. History lives only in `events`.

Agents register automatically the first time they write under a new name.

## Agent integration

### 1. CLI (`agentctl.py`): simplest option, no server needed
```bash
export AGENT_NAME=backend-bot           # or --agent backend-bot
python3 agentctl.py hb --status working --role "Backend engineer" --note "starting"
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
```bash
claude mcp add agent-tracker -e AGENT_NAME=backend-bot -- python3 /abs/path/agent-dashboard/mcp_server.py
# hosted dashboard:
claude mcp add agent-tracker -e AGENT_NAME=backend-bot -e AGENT_DASH_URL=https://your-host -e AGENT_DASH_TOKEN=... \
  -- python3 /abs/path/agent-dashboard/mcp_server.py
```
Tools: `tracker_heartbeat`, `tracker_list_projects`, `tracker_create_project`, `tracker_list_tasks`, `tracker_get_task`, `tracker_create_task`, `tracker_update_task`, `tracker_claim_next_task`, `tracker_log_progress`, `tracker_add_todo`, `tracker_complete_todo`.

### 3. HTTP API: agents on other machines
Identify the caller with an `"agent"` field in the body or an `X-Agent` header.

| Method | Path | Body / query |
|---|---|---|
| GET  | `/api/stats` | |
| GET/POST | `/api/projects` | `{name, description}` |
| GET/PATCH | `/api/projects/{id or name}` | `{status, description, name}` |
| GET  | `/api/agents`, `/api/agents/{id or name}` | |
| POST | `/api/agents/{name}/heartbeat` | `{status, note, role, task_id}` |
| POST | `/api/agents/{name}/claim` | `{project}` |
| GET  | `/api/tasks` | `?project=&status=todo,in_progress&assignee=` |
| POST | `/api/tasks` | `{project, title, description, priority, assignee, status, todos: []}` |
| GET/PATCH/DELETE | `/api/tasks/{id}` | `{status, priority, assignee, title, description, note}` |
| POST | `/api/tasks/{id}/progress` | `{message}` |
| POST | `/api/tasks/{id}/todos` | `{text}` |
| PATCH | `/api/todos/{id}` | `{done, text}` |
| GET  | `/api/events` | `?since=&project=&agent=&task=&limit=` |
| GET  | `/api/stream` | Server-sent events |

```bash
curl -X POST localhost:8787/api/tasks/7/progress -H 'X-Agent: backend-bot' -d '{"message":"tests green"}'
```

### Suggested agent instructions (paste into CLAUDE.md / AGENTS.md)
```
Track all your work in the agent tracker (agentctl.py or the tracker_* MCP tools):
- At start: heartbeat with status=working. Claim a task or create one for the work you were given.
- Before creating a task, list existing tasks; never create a second task for the same work.
- Record every new piece of work as a task (or a todo on your current task) as soon as you identify it.
- Task title, description, status and priority always state the current truth. When a fact changes,
  rewrite the field (task set / tracker_update_task). Never append "Update:", "outdated" or "was X, now Y" text.
- History belongs in the progress log only: one line per meaningful step (task log / tracker_log_progress).
- Tick off todos as you finish them. Mark the task blocked (with a reason) or done (with a summary).
- When you stop: heartbeat with status=idle.
```

## Hosting on Vercel + Turso (free, no card)
On Vercel the app runs as a serverless function (`api/index.py`, `vercel.json`) and stores its data in [Turso](https://turso.tech), a hosted SQLite-compatible database. The same code runs locally against a SQLite file whenever `TURSO_DATABASE_URL` is unset. Serverless functions can't hold a live stream open, so on Vercel the dashboard checks for new activity every 3 seconds.

1. **Turso:** sign up at turso.tech and create a database. Pick a region near your Vercel region (Vercel's default is Washington, D.C., `iad1`). Copy its URL (`libsql://…`) and create a database token.
2. **Vercel:** sign up with GitHub, then **Add New → Project** and import this repo. Leave the framework preset on **Other** and add these environment variables:
   - `TURSO_DATABASE_URL`: the `libsql://…` URL
   - `TURSO_AUTH_TOKEN`: the database token
   - `AGENT_DASH_TOKEN`: a long random password for the dashboard and agents

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

## Applying your Stitch design
All colours, radii and fonts are CSS variables at the top of `static/styles.css`. To match your Google Stitch project:
1. In Stitch, open the screen and choose **Export → Code**.
2. Copy the colour roles from its `tailwind.config` (primary, surface, background, and so on) into the `:root` variables. Then adjust the layout classes in `styles.css` and the templates in `static/app.js` (`viewOverview`, `viewProject`, etc.) to match its structure.

## Concurrency notes
- Each write runs in a `BEGIN IMMEDIATE` transaction. Concurrent writers wait their turn (`busy_timeout=30s` locally, retry with backoff on Turso) and never error. `claim_next_task` is atomic. `tests/test_concurrency.py` checks this with 12 processes on a local file, and `tests/test_turso.py` with 6 processes on libSQL.
- A local SQLite file in WAL mode needs a local disk. Don't put it on NFS or SMB. Agents on other machines should use the HTTP API.
- If you outgrow SQLite (hundreds of writes per second from many hosts), the SQL in `db.py` is plain enough to port to Postgres.
