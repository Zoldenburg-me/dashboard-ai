"""SQLite data layer shared by the HTTP server, the CLI and the MCP server.

Locally, SQLite runs in WAL mode: any number of readers work concurrently with
one writer, and writers queue on a busy timeout instead of failing, so many
agent processes can share the same file. On Vercel the same SQL runs against
Turso (hosted libSQL) through turso.py.
"""
import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager

DB_PATH = os.environ.get("AGENT_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "agents.db"))

TASK_STATUSES = ("todo", "in_progress", "blocked", "review", "done")
AGENT_STATUSES = ("idle", "working", "blocked", "offline")
PROJECT_STATUSES = ("active", "paused", "completed", "archived")
PRIORITIES = ("low", "medium", "high", "urgent")
OFFLINE_AFTER_SECONDS = 300

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active',
    repo        TEXT NOT NULL DEFAULT '',
    branch      TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    role            TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'idle',
    status_note     TEXT NOT NULL DEFAULT '',
    current_task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    model           TEXT NOT NULL DEFAULT '',
    host            TEXT NOT NULL DEFAULT '',
    parent_id       INTEGER REFERENCES agents(id) ON DELETE SET NULL,
    metrics         TEXT NOT NULL DEFAULT '{}',
    last_seen       REAL,
    created_at      REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'todo',
    priority     TEXT NOT NULL DEFAULT 'medium',
    assignee_id  INTEGER REFERENCES agents(id) ON DELETE SET NULL,
    created_by   INTEGER REFERENCES agents(id) ON DELETE SET NULL,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    started_at   REAL,
    completed_at REAL
);
CREATE TABLE IF NOT EXISTS todos (
    id         INTEGER PRIMARY KEY,
    task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    text       TEXT NOT NULL,
    done       INTEGER NOT NULL DEFAULT 0,
    position   INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES agents(id) ON DELETE SET NULL,
    created_at REAL NOT NULL,
    done_at    REAL
);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    ts         REAL NOT NULL,
    agent_id   INTEGER REFERENCES agents(id) ON DELETE SET NULL,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    task_id    INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    kind       TEXT NOT NULL,
    message    TEXT NOT NULL DEFAULT '',
    data       TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS hosts (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE,
    data      TEXT NOT NULL DEFAULT '{}',
    last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS teams (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS team_projects (
    team_id    INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    PRIMARY KEY (team_id, project_id)
);
CREATE TABLE IF NOT EXISTS tokens (
    id         INTEGER PRIMARY KEY,
    team_id    INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    label      TEXT NOT NULL DEFAULT '',
    agent      TEXT,
    token_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    last_used  REAL,
    revoked_at REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee_id);
CREATE INDEX IF NOT EXISTS idx_todos_task ON todos(task_id, position);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(id);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id, id);
CREATE INDEX IF NOT EXISTS idx_events_agent ON events(agent_id, id);
"""


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


class Forbidden(Exception):
    pass


def connect(path=None):
    """Local SQLite file by default; Turso when TURSO_DATABASE_URL is set (Vercel)."""
    if os.environ.get("TURSO_DATABASE_URL") and path is None:
        import turso
        return turso.connect()
    path = path or DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Columns added after the first release; init() adds any that an older database lacks.
MIGRATIONS = [
    ("projects", "repo", "TEXT NOT NULL DEFAULT ''"),
    ("projects", "branch", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "model", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "host", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "parent_id", "INTEGER REFERENCES agents(id) ON DELETE SET NULL"),
    ("agents", "metrics", "TEXT NOT NULL DEFAULT '{}'"),
    ("agents", "team_id", "INTEGER REFERENCES teams(id) ON DELETE SET NULL"),
]

# Numeric host stats an agent or script can report (all optional).
HOST_FIELDS = ("gpu", "engine", "vram_used_gb", "vram_total_gb", "ram_used_gb", "ram_total_gb",
               "cpu_pct", "tokens_per_sec", "temp_c", "power_w")


def init(conn):
    with write(conn):
        for stmt in SCHEMA.split(";"):
            if stmt.strip():
                conn.execute(stmt)
        for table, col, ddl in MIGRATIONS:
            have = {r[0] for r in conn.execute("SELECT name FROM pragma_table_info(?)", (table,))}
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


@contextmanager
def write(conn):
    """BEGIN IMMEDIATE takes the write lock up front so concurrent writers
    wait on busy_timeout rather than failing mid-transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _row(r):
    return dict(r) if r is not None else None


def _rows(rs):
    return [dict(r) for r in rs]


def _check(value, allowed, field):
    if value not in allowed:
        raise Invalid(f"{field} must be one of {', '.join(allowed)} (got {value!r})")


def _log(conn, kind, message, agent_id=None, project_id=None, task_id=None, data=None):
    conn.execute(
        "INSERT INTO events (ts, agent_id, project_id, task_id, kind, message, data) VALUES (?,?,?,?,?,?,?)",
        (time.time(), agent_id, project_id, task_id, kind, message, json.dumps(data or {})),
    )


# ---------- lookups ----------

def resolve_agent(conn, ref, create=True):
    """Accept an agent id or name. Unknown names are registered on the fly so
    an agent can start logging work without a separate setup step."""
    if ref is None or ref == "":
        return None
    if isinstance(ref, int) or str(ref).isdigit():
        r = conn.execute("SELECT id FROM agents WHERE id=?", (int(ref),)).fetchone()
        if not r:
            raise NotFound(f"agent {ref} not found")
        return r["id"]
    r = conn.execute("SELECT id FROM agents WHERE name=?", (ref,)).fetchone()
    if r:
        return r["id"]
    if not create:
        raise NotFound(f"agent {ref!r} not found")
    now = time.time()
    cur = conn.execute("INSERT INTO agents (name, created_at, last_seen) VALUES (?,?,?)", (ref, now, now))
    _log(conn, "agent.registered", f"{ref} registered", agent_id=cur.lastrowid)
    return cur.lastrowid


def resolve_project(conn, ref):
    if isinstance(ref, int) or str(ref).isdigit():
        r = conn.execute("SELECT id FROM projects WHERE id=?", (int(ref),)).fetchone()
    else:
        r = conn.execute("SELECT id FROM projects WHERE name=?", (ref,)).fetchone()
    if not r:
        raise NotFound(f"project {ref!r} not found")
    return r["id"]


def _task(conn, task_id):
    r = conn.execute("SELECT * FROM tasks WHERE id=?", (int(task_id),)).fetchone()
    if not r:
        raise NotFound(f"task {task_id} not found")
    return r


def _touch(conn, agent_id):
    if agent_id:
        conn.execute("UPDATE agents SET last_seen=? WHERE id=?", (time.time(), agent_id))


# ---------- projects ----------

def create_project(conn, name, description="", status="active", agent=None, repo="", branch="", team_id=None):
    """team_id grants the new project to that team (a team creating its own project)."""
    _check(status, PROJECT_STATUSES, "status")
    if not isinstance(name, str) or not name.strip():
        raise Invalid("name is required")
    with write(conn):
        aid = resolve_agent(conn, agent)
        now = time.time()
        try:
            cur = conn.execute(
                "INSERT INTO projects (name, description, status, repo, branch, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (name.strip(), description, status, repo or "", branch or "", now, now),
            )
        except sqlite3.IntegrityError:
            # same wording as a rename clash, and nothing about whose project it is: team tokens see this too
            raise Invalid(f"project name {name.strip()!r} is taken; choose another")
        if team_id:
            conn.execute("INSERT INTO team_projects (team_id, project_id) VALUES (?,?)", (team_id, cur.lastrowid))
        _log(conn, "project.created", f"Project {name} created", aid, cur.lastrowid)
        _touch(conn, aid)
    return get_project(conn, cur.lastrowid)


def update_project(conn, ref, agent=None, **fields):
    allowed = {k: v for k, v in fields.items() if k in ("name", "description", "status", "repo", "branch") and v is not None}
    if "status" in allowed:
        _check(allowed["status"], PROJECT_STATUSES, "status")
    if "name" in allowed:
        if not isinstance(allowed["name"], str) or not allowed["name"].strip():
            raise Invalid("name must be a non-empty string")
        allowed["name"] = allowed["name"].strip()
    with write(conn):
        pid = resolve_project(conn, ref)
        aid = resolve_agent(conn, agent)
        if allowed:
            sets = ", ".join(f"{k}=?" for k in allowed)
            try:
                conn.execute(f"UPDATE projects SET {sets}, updated_at=? WHERE id=?", (*allowed.values(), time.time(), pid))
            except sqlite3.IntegrityError:
                raise Invalid(f"project name {allowed.get('name')!r} is taken; choose another")
            _log(conn, "project.updated", "Project updated: " + ", ".join(f"{k}={v}" for k, v in allowed.items()), aid, pid)
        _touch(conn, aid)
    return get_project(conn, pid)


TEAM_PROJECTS = "SELECT project_id FROM team_projects WHERE team_id=?"


def list_projects(conn, team_id=None):
    where, args = (f"WHERE p.id IN ({TEAM_PROJECTS})", (team_id,)) if team_id else ("", ())
    return _rows(conn.execute(f"""
        SELECT p.*,
          (SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id) AS task_count,
          (SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id AND t.status='done') AS done_count,
          (SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id AND t.status='blocked') AS blocked_count,
          (SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id AND t.status='in_progress') AS active_count,
          (SELECT COUNT(DISTINCT t.assignee_id) FROM tasks t WHERE t.project_id=p.id AND t.status NOT IN ('done') AND t.assignee_id IS NOT NULL) AS agent_count,
          (SELECT MAX(e.ts) FROM events e WHERE e.project_id=p.id) AS last_activity
        FROM projects p {where} ORDER BY (p.status='active') DESC, last_activity DESC
    """, args))


def get_project(conn, ref):
    pid = resolve_project(conn, ref)
    p = [x for x in list_projects(conn) if x["id"] == pid][0]
    p["tasks"] = list_tasks(conn, project=pid)
    return p


# ---------- agents ----------

def _agent_view(r):
    a = dict(r)
    a["metrics"] = json.loads(a.get("metrics") or "{}")
    if a["last_seen"] is None or time.time() - a["last_seen"] > OFFLINE_AFTER_SECONDS:
        a["effective_status"] = "offline"
    else:
        a["effective_status"] = a["status"]
    return a


def list_agents(conn, team_id=None):
    where, args = ("WHERE a.team_id=?", (team_id,)) if team_id else ("", ())
    rs = conn.execute(f"""
        SELECT a.*, t.title AS current_task_title, t.project_id AS current_project_id, p.name AS current_project_name,
          par.name AS parent_name, tm.name AS team_name,
          (SELECT COUNT(*) FROM tasks x WHERE x.assignee_id=a.id AND x.status!='done') AS open_tasks,
          (SELECT COUNT(*) FROM tasks x WHERE x.assignee_id=a.id AND x.status='done') AS done_tasks
        FROM agents a
        LEFT JOIN tasks t ON t.id=a.current_task_id
        LEFT JOIN projects p ON p.id=t.project_id
        LEFT JOIN agents par ON par.id=a.parent_id
        LEFT JOIN teams tm ON tm.id=a.team_id
        {where}
        ORDER BY a.last_seen DESC
    """, args)
    return [_agent_view(r) for r in rs]


def get_agent(conn, ref):
    aid = resolve_agent(conn, ref, create=False)
    a = [x for x in list_agents(conn) if x["id"] == aid][0]
    a["tasks"] = list_tasks(conn, assignee=aid)
    a["events"] = list_events(conn, agent=aid, limit=50)
    return a


def rename_agent(conn, ref, name):
    """Give an agent a new name; its tasks and history stay attached."""
    name = (name or "").strip()
    if not name or name.isdigit():
        raise Invalid("name must be non-empty and not a number")
    with write(conn):
        aid = resolve_agent(conn, ref, create=False)
        old = conn.execute("SELECT name FROM agents WHERE id=?", (aid,)).fetchone()["name"]
        if old != name:
            if conn.execute("SELECT 1 FROM agents WHERE name=?", (name,)).fetchone():
                raise Invalid(f"agent name {name!r} is taken; choose another")
            conn.execute("UPDATE agents SET name=?, last_seen=? WHERE id=?", (name, time.time(), aid))
            _log(conn, "agent.renamed", f"{old} is now {name}", aid)
    return get_agent(conn, aid)


def heartbeat(conn, agent, status=None, note=None, role=None, task_id=None, model=None, host=None, parent=None, metrics=None):
    """Register/refresh an agent. Call periodically so the dashboard knows it is alive.
    metrics (e.g. vram_gb, tokens_per_sec, context_used, context_max, quant) is merged
    into the agent's current metrics; a null value removes a key."""
    if status is not None:
        _check(status, AGENT_STATUSES, "status")
    with write(conn):
        aid = resolve_agent(conn, agent)
        prev = conn.execute("SELECT status, status_note, metrics FROM agents WHERE id=?", (aid,)).fetchone()
        sets, vals = ["last_seen=?"], [time.time()]
        if metrics:
            merged = {**json.loads(prev["metrics"] or "{}"), **metrics}
            sets.append("metrics=?")
            vals.append(json.dumps({k: v for k, v in merged.items() if v is not None}))
        if parent is not None:
            pid = resolve_agent(conn, parent) if parent != "" else None
            if pid == aid:
                raise Invalid("an agent cannot be its own parent")
            sets.append("parent_id=?")
            vals.append(pid)
        for col, val in (("status", status), ("status_note", note), ("role", role), ("model", model), ("host", host)):
            if val is not None:
                sets.append(f"{col}=?")
                vals.append(val)
        if task_id is not None:
            sets.append("current_task_id=?")
            vals.append(int(task_id) if task_id else None)
        conn.execute(f"UPDATE agents SET {', '.join(sets)} WHERE id=?", (*vals, aid))
        if (status is not None and status != prev["status"]) or (note and note != prev["status_note"]):
            _log(conn, "agent.status", f"{status or prev['status']}" + (f": {note}" if note else ""), aid)
    return get_agent(conn, aid)


# ---------- hosts ----------

def _host_view(r):
    h = dict(r)
    h.update(json.loads(h.pop("data") or "{}"))
    h["online"] = time.time() - h["last_seen"] <= OFFLINE_AFTER_SECONDS
    return h


def report_host(conn, name, **stats):
    """Upsert a machine's hardware stats (see HOST_FIELDS). Unknown keys are ignored."""
    if not name:
        raise Invalid("host name is required")
    data = {k: v for k, v in stats.items() if k in HOST_FIELDS and v is not None}
    with write(conn):
        r = conn.execute("SELECT data FROM hosts WHERE name=?", (name,)).fetchone()
        if r:
            merged = {**json.loads(r["data"] or "{}"), **data}
            conn.execute("UPDATE hosts SET data=?, last_seen=? WHERE name=?", (json.dumps(merged), time.time(), name))
        else:
            conn.execute("INSERT INTO hosts (name, data, last_seen) VALUES (?,?,?)", (name, json.dumps(data), time.time()))
    return _host_view(conn.execute("SELECT * FROM hosts WHERE name=?", (name,)).fetchone())


def list_hosts(conn):
    return [_host_view(r) for r in conn.execute("SELECT * FROM hosts ORDER BY last_seen DESC")]


# ---------- tasks ----------

def list_tasks(conn, project=None, status=None, assignee=None, limit=500, team_id=None):
    q = """SELECT t.*, p.name AS project_name, a.name AS assignee_name,
             (SELECT COUNT(*) FROM todos d WHERE d.task_id=t.id) AS todo_count,
             (SELECT COUNT(*) FROM todos d WHERE d.task_id=t.id AND d.done=1) AS todo_done
           FROM tasks t JOIN projects p ON p.id=t.project_id LEFT JOIN agents a ON a.id=t.assignee_id WHERE 1=1"""
    args = []
    if team_id:
        q += f" AND t.project_id IN ({TEAM_PROJECTS})"
        args.append(team_id)
    if project is not None:
        q += " AND t.project_id=?"
        args.append(resolve_project(conn, project))
    if status:
        statuses = status.split(",") if isinstance(status, str) else status
        q += f" AND t.status IN ({','.join('?' * len(statuses))})"
        args += statuses
    if assignee is not None:
        q += " AND t.assignee_id=?"
        args.append(resolve_agent(conn, assignee, create=False))
    q += """ ORDER BY CASE t.status WHEN 'in_progress' THEN 0 WHEN 'blocked' THEN 1 WHEN 'review' THEN 2 WHEN 'todo' THEN 3 ELSE 4 END,
             CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, t.updated_at DESC LIMIT ?"""
    args.append(int(limit))
    return _rows(conn.execute(q, args))


def get_task(conn, task_id):
    _task(conn, task_id)
    t = _row(conn.execute("""SELECT t.*, p.name AS project_name, a.name AS assignee_name FROM tasks t
        JOIN projects p ON p.id=t.project_id LEFT JOIN agents a ON a.id=t.assignee_id WHERE t.id=?""", (int(task_id),)).fetchone())
    t["todos"] = _rows(conn.execute("SELECT * FROM todos WHERE task_id=? ORDER BY position, id", (int(task_id),)))
    t["events"] = list_events(conn, task=int(task_id), limit=100)
    return t


def create_task(conn, project, title, description="", priority="medium", assignee=None, status="todo", agent=None, todos=None):
    _check(priority, PRIORITIES, "priority")
    _check(status, TASK_STATUSES, "status")
    if not title or not str(title).strip():
        raise Invalid("title is required")
    with write(conn):
        pid = resolve_project(conn, project)
        aid = resolve_agent(conn, agent)
        assignee_id = resolve_agent(conn, assignee)
        now = time.time()
        cur = conn.execute(
            """INSERT INTO tasks (project_id, title, description, status, priority, assignee_id, created_by,
               created_at, updated_at, started_at, completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, title.strip(), description, status, priority, assignee_id, aid, now, now,
             now if status == "in_progress" else None, now if status == "done" else None),
        )
        tid = cur.lastrowid
        _log(conn, "task.created", f"Created task: {title}", aid, pid, tid)
        for i, text in enumerate(todos or []):
            conn.execute("INSERT INTO todos (task_id, text, position, created_by, created_at) VALUES (?,?,?,?,?)",
                         (tid, text, i, aid, now))
        if status == "in_progress" and assignee_id:
            conn.execute("UPDATE agents SET current_task_id=?, status='working' WHERE id=?", (tid, assignee_id))
        _touch(conn, aid)
    return get_task(conn, tid)


def update_task(conn, task_id, agent=None, note=None, **fields):
    """Change status/priority/assignee/title/description. Status transitions
    stamp started_at/completed_at and keep the assignee's current task in sync."""
    if fields.get("status") is not None:
        _check(fields["status"], TASK_STATUSES, "status")
    if fields.get("priority") is not None:
        _check(fields["priority"], PRIORITIES, "priority")
    with write(conn):
        t = _task(conn, task_id)
        aid = resolve_agent(conn, agent)
        changes = {}
        for k in ("title", "description", "status", "priority"):
            if fields.get(k) is not None and fields[k] != t[k]:
                changes[k] = fields[k]
        if "assignee" in fields and fields["assignee"] is not None:
            new_assignee = resolve_agent(conn, fields["assignee"]) if fields["assignee"] != "" else None
            if new_assignee != t["assignee_id"]:
                changes["assignee_id"] = new_assignee
        now = time.time()
        if changes:
            if changes.get("status") == "in_progress" and not t["started_at"]:
                changes["started_at"] = now
            if "status" in changes:
                changes["completed_at"] = now if changes["status"] == "done" else None
            sets = ", ".join(f"{k}=?" for k in changes)
            conn.execute(f"UPDATE tasks SET {sets}, updated_at=? WHERE id=?", (*changes.values(), now, t["id"]))
            assignee_id = changes.get("assignee_id", t["assignee_id"])
            status = changes.get("status", t["status"])
            if assignee_id and status == "in_progress":
                conn.execute("UPDATE agents SET current_task_id=?, status='working' WHERE id=?", (t["id"], assignee_id))
            elif assignee_id and status in ("done", "review", "todo"):
                conn.execute("UPDATE agents SET current_task_id=NULL, status='idle' WHERE id=? AND current_task_id=?", (assignee_id, t["id"]))
            elif assignee_id and status == "blocked":
                conn.execute("UPDATE agents SET status='blocked' WHERE id=? AND current_task_id=?", (assignee_id, t["id"]))
            desc = []
            if "status" in changes:
                desc.append(f"{t['status']} → {changes['status']}")
            if "assignee_id" in changes:
                name = conn.execute("SELECT name FROM agents WHERE id=?", (changes["assignee_id"],)).fetchone() if changes["assignee_id"] else None
                desc.append(f"assigned to {name['name'] if name else 'nobody'}")
            for k in ("title", "priority", "description"):
                if k in changes:
                    desc.append(f"{k} updated")
            kind = "task.completed" if changes.get("status") == "done" else "task.updated"
            _log(conn, kind, f"{t['title']}: " + ", ".join(desc) + (f" — {note}" if note else ""), aid, t["project_id"], t["id"])
        elif note:
            _log(conn, "task.note", note, aid, t["project_id"], t["id"])
        _touch(conn, aid)
    return get_task(conn, task_id)


def log_progress(conn, task_id, message, agent=None):
    """Free-form progress note on a task: the main way agents record what they did."""
    if not message:
        raise Invalid("message is required")
    with write(conn):
        t = _task(conn, task_id)
        aid = resolve_agent(conn, agent)
        _log(conn, "task.progress", message, aid, t["project_id"], t["id"])
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (time.time(), t["id"]))
        _touch(conn, aid)
    return {"ok": True}


def claim_next_task(conn, agent, project=None, team_id=None):
    """Atomically pick the highest-priority unassigned todo and start it.
    BEGIN IMMEDIATE guarantees two agents can never claim the same task.
    team_id limits the pick to that team's projects."""
    with write(conn):
        aid = resolve_agent(conn, agent)
        q = """SELECT id FROM tasks WHERE status='todo' AND assignee_id IS NULL"""
        args = []
        if team_id:
            q += f" AND project_id IN ({TEAM_PROJECTS})"
            args.append(team_id)
        if project is not None:
            q += " AND project_id=?"
            args.append(resolve_project(conn, project))
        q += " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, created_at LIMIT 1"
        r = conn.execute(q, args).fetchone()
        if not r:
            _touch(conn, aid)
            return None
        now = time.time()
        conn.execute("UPDATE tasks SET assignee_id=?, status='in_progress', started_at=COALESCE(started_at, ?), updated_at=? WHERE id=?",
                     (aid, now, now, r["id"]))
        conn.execute("UPDATE agents SET current_task_id=?, status='working', last_seen=? WHERE id=?", (r["id"], now, aid))
        t = _task(conn, r["id"])
        _log(conn, "task.claimed", f"Claimed: {t['title']}", aid, t["project_id"], t["id"])
    return get_task(conn, r["id"])


def delete_task(conn, task_id, agent=None):
    with write(conn):
        t = _task(conn, task_id)
        aid = resolve_agent(conn, agent)
        conn.execute("DELETE FROM tasks WHERE id=?", (t["id"],))
        _log(conn, "task.deleted", f"Deleted task: {t['title']}", aid, t["project_id"])
    return {"ok": True}


# ---------- todos ----------

def add_todo(conn, task_id, text, agent=None):
    if not text:
        raise Invalid("text is required")
    with write(conn):
        t = _task(conn, task_id)
        aid = resolve_agent(conn, agent)
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM todos WHERE task_id=?", (t["id"],)).fetchone()[0]
        cur = conn.execute("INSERT INTO todos (task_id, text, position, created_by, created_at) VALUES (?,?,?,?,?)",
                           (t["id"], text, pos, aid, time.time()))
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (time.time(), t["id"]))
        _log(conn, "todo.added", f"Todo added: {text}", aid, t["project_id"], t["id"])
        _touch(conn, aid)
    return _row(conn.execute("SELECT * FROM todos WHERE id=?", (cur.lastrowid,)).fetchone())


def set_todo(conn, todo_id, done=None, text=None, agent=None):
    with write(conn):
        d = conn.execute("SELECT d.*, t.project_id FROM todos d JOIN tasks t ON t.id=d.task_id WHERE d.id=?", (int(todo_id),)).fetchone()
        if not d:
            raise NotFound(f"todo {todo_id} not found")
        aid = resolve_agent(conn, agent)
        if done is not None and bool(done) != bool(d["done"]):
            conn.execute("UPDATE todos SET done=?, done_at=? WHERE id=?", (int(bool(done)), time.time() if done else None, d["id"]))
            _log(conn, "todo.done" if done else "todo.reopened", f"{'✓' if done else '↺'} {d['text']}", aid, d["project_id"], d["task_id"])
        if text:
            conn.execute("UPDATE todos SET text=? WHERE id=?", (text, d["id"]))
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (time.time(), d["task_id"]))
        _touch(conn, aid)
    return _row(conn.execute("SELECT * FROM todos WHERE id=?", (int(todo_id),)).fetchone())


# ---------- teams & tokens ----------
# A team is an outside group with its own agents. Its tokens see and change only the
# projects granted to it, and only act as the team's own agents. Tokens are stored
# hashed; the plain token is returned once, when it is created.

TOKEN_TOUCH_SECONDS = 60


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _team_id(conn, ref):
    if isinstance(ref, int) or str(ref).isdigit():
        r = conn.execute("SELECT id FROM teams WHERE id=?", (int(ref),)).fetchone()
    else:
        r = conn.execute("SELECT id FROM teams WHERE name=?", (ref,)).fetchone()
    if not r:
        raise NotFound(f"team {ref!r} not found")
    return r["id"]


def create_team(conn, name):
    name = (name or "").strip()
    if not name or name.isdigit():
        raise Invalid("team name must be non-empty and not a number")
    with write(conn):
        try:
            cur = conn.execute("INSERT INTO teams (name, created_at) VALUES (?,?)", (name, time.time()))
        except sqlite3.IntegrityError:
            raise Invalid(f"team {name!r} already exists")
        _log(conn, "team.created", f"Team {name} created")
    return get_team(conn, cur.lastrowid)


def get_team(conn, ref):
    tid = _team_id(conn, ref)
    t = _row(conn.execute("SELECT * FROM teams WHERE id=?", (tid,)).fetchone())
    t["projects"] = [r["name"] for r in conn.execute(
        "SELECT p.name FROM projects p JOIN team_projects tp ON tp.project_id=p.id WHERE tp.team_id=? ORDER BY p.name", (tid,))]
    t["agents"] = [r["name"] for r in conn.execute("SELECT name FROM agents WHERE team_id=? ORDER BY name", (tid,))]
    t["tokens"] = _rows(conn.execute(
        "SELECT id, label, agent, created_at, last_used, revoked_at FROM tokens WHERE team_id=? ORDER BY id", (tid,)))
    return t


def list_teams(conn):
    return [get_team(conn, r["id"]) for r in conn.execute("SELECT id FROM teams ORDER BY name")]


def create_token(conn, team, label="", agent=None):
    """New token for a team. With agent set, the token always acts as that one agent."""
    raw = "tk_" + secrets.token_urlsafe(32)
    agent = (agent or "").strip() or None
    with write(conn):
        tid = _team_id(conn, team)
        if agent:
            r = conn.execute("SELECT team_id FROM agents WHERE name=?", (agent,)).fetchone()
            if r and r["team_id"] != tid:
                raise Invalid(f"agent name {agent!r} already belongs to someone else")
        cur = conn.execute("INSERT INTO tokens (team_id, label, agent, token_hash, created_at) VALUES (?,?,?,?,?)",
                           (tid, label or "", agent, _hash(raw), time.time()))
    return {"id": cur.lastrowid, "team_id": tid, "label": label or "", "agent": agent, "token": raw}


def revoke_token(conn, token_id):
    with write(conn):
        r = conn.execute("SELECT id FROM tokens WHERE id=?", (int(token_id),)).fetchone()
        if not r:
            raise NotFound(f"token {token_id} not found")
        conn.execute("UPDATE tokens SET revoked_at=COALESCE(revoked_at, ?) WHERE id=?", (time.time(), r["id"]))
    return {"ok": True}


def grant_project(conn, team, project, grant=True):
    """Give a team access to a project (grant=False takes it away)."""
    with write(conn):
        tid = _team_id(conn, team)
        pid = resolve_project(conn, project)
        if grant:
            conn.execute("INSERT OR IGNORE INTO team_projects (team_id, project_id) VALUES (?,?)", (tid, pid))
        else:
            conn.execute("DELETE FROM team_projects WHERE team_id=? AND project_id=?", (tid, pid))
    return get_team(conn, tid)


def token_identity(conn, token):
    """The team (and bound agent, if any) behind a live token, or None."""
    r = conn.execute("""SELECT k.id, k.team_id, k.agent, k.last_used, t.name AS team FROM tokens k
                        JOIN teams t ON t.id=k.team_id WHERE k.token_hash=? AND k.revoked_at IS NULL""",
                     (_hash(token),)).fetchone()
    if not r:
        return None
    now = time.time()
    if not r["last_used"] or now - r["last_used"] > TOKEN_TOUCH_SECONDS:
        conn.execute("UPDATE tokens SET last_used=? WHERE id=?", (now, r["id"]))
    return {"token_id": r["id"], "team_id": r["team_id"], "team": r["team"], "agent": r["agent"]}


# ---------- rules ----------

SEVERITY = ("high", "medium", "low")


def check_rules(conn, team_id=None, master=None, quiet_minutes=30):
    """Board-wide rule check for a master agent: everything that breaks the tracking rules,
    most severe first. master names the root agent every other agent of yours should report to."""
    now = time.time()
    out = []

    def add(rule, severity, message, agent=None, task_id=None, project=None):
        out.append({"rule": rule, "severity": severity, "message": message, "agent": agent, "task_id": task_id, "project": project})

    agents = list_agents(conn, team_id)
    master_id = next((a["id"] for a in agents if a["name"] == master), None) if master else None
    for a in agents:
        if a["status"] in ("working", "blocked") and a["effective_status"] == "offline":
            mins = int((now - (a["last_seen"] or 0)) / 60)
            add("agent_silent", "high", f"{a['name']} says {a['status']} but has sent nothing for {mins} min", a["name"])
        if a["effective_status"] == "working" and not a["current_task_id"]:
            add("working_without_task", "medium", f"{a['name']} is working without a claimed task", a["name"])
        if master_id and a["team_id"] is None and a["id"] != master_id and not a["parent_id"]:
            add("no_parent", "low", f"{a['name']} reports to nobody; heartbeat with parent={master}", a["name"])

    in_progress = list_tasks(conn, status="in_progress", team_id=team_id)
    per_agent = {}
    for t in in_progress:
        if not t["assignee_id"]:
            add("in_progress_unassigned", "medium", f"task {t['id']} is in progress with no assignee", None, t["id"], t["project_name"])
        else:
            per_agent.setdefault(t["assignee_name"], []).append(t["id"])
        quiet = (now - t["updated_at"]) / 60
        if quiet > quiet_minutes:
            add("task_quiet", "medium", f"task {t['id']} has had no progress for {int(quiet)} min",
                t["assignee_name"], t["id"], t["project_name"])
    for name, ids in per_agent.items():
        if len(ids) > 1:
            add("several_in_progress", "low", f"{name} has {len(ids)} tasks in progress: {', '.join(map(str, ids))}", name)

    seen = {}
    for t in list_tasks(conn, status="todo,in_progress,blocked,review", limit=5000, team_id=team_id):
        seen.setdefault((t["project_id"], " ".join(t["title"].lower().split())), []).append(t)
    for dupes in seen.values():
        if len(dupes) > 1:
            ids = sorted(t["id"] for t in dupes)
            add("duplicate_task", "medium", f"tasks {', '.join(map(str, ids))} have the same title: {dupes[0]['title']!r}",
                None, ids[0], dupes[0]["project_name"])

    out.sort(key=lambda v: SEVERITY.index(v["severity"]))
    return {"checked_at": now, "violations": out, "counts": {s: sum(v["severity"] == s for v in out) for s in SEVERITY}}


# ---------- events / stats ----------

def list_events(conn, since=None, project=None, agent=None, task=None, limit=100, team_id=None):
    """team_id keeps events in that team's projects, plus its own agents' project-less events."""
    q = """SELECT e.*, a.name AS agent_name, p.name AS project_name, t.title AS task_title FROM events e
           LEFT JOIN agents a ON a.id=e.agent_id LEFT JOIN projects p ON p.id=e.project_id LEFT JOIN tasks t ON t.id=e.task_id WHERE 1=1"""
    args = []
    if team_id:
        q += f" AND (e.project_id IN ({TEAM_PROJECTS}) OR (e.project_id IS NULL AND a.team_id=?))"
        args += [team_id, team_id]
    if since is not None:
        q += " AND e.id>?"
        args.append(int(since))
    if project is not None:
        q += " AND e.project_id=?"
        args.append(resolve_project(conn, project))
    if agent is not None:
        q += " AND e.agent_id=?"
        args.append(resolve_agent(conn, agent, create=False))
    if task is not None:
        q += " AND e.task_id=?"
        args.append(int(task))
    q += " ORDER BY e.id DESC LIMIT ?"
    args.append(int(limit))
    return _rows(conn.execute(q, args))


def last_event_id(conn):
    return conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]


def stats(conn):
    day_start = time.time() - 86400
    agents = list_agents(conn)
    c = lambda q, *a: conn.execute(q, a).fetchone()[0]
    activity = _rows(conn.execute("""
        SELECT CAST((ts - ?) / 3600 AS INTEGER) AS bucket, COUNT(*) AS n FROM events WHERE ts>=? GROUP BY bucket ORDER BY bucket
    """, (day_start, day_start)))
    hosts = [h for h in list_hosts(conn) if h["online"]]
    total = lambda k: round(sum(float(h.get(k) or 0) for h in hosts), 1)
    return {
        "hosts_online": len(hosts),
        "vram_used_gb": total("vram_used_gb"), "vram_total_gb": total("vram_total_gb"),
        "ram_used_gb": total("ram_used_gb"), "ram_total_gb": total("ram_total_gb"),
        "cpu_pct": round(sum(float(h.get("cpu_pct") or 0) for h in hosts) / len(hosts)) if hosts else None,
        "tokens_per_sec": total("tokens_per_sec"),
        "repos_connected": c("SELECT COUNT(*) FROM projects WHERE repo!=''"),
        "agents_offline": sum(1 for a in agents if a["effective_status"] == "offline"),
        "tasks_todo": c("SELECT COUNT(*) FROM tasks WHERE status='todo'"),
        "tasks_review": c("SELECT COUNT(*) FROM tasks WHERE status='review'"),
        "tasks_unassigned": c("SELECT COUNT(*) FROM tasks WHERE status='todo' AND assignee_id IS NULL"),
        "agents_total": len(agents),
        "agents_working": sum(1 for a in agents if a["effective_status"] == "working"),
        "agents_blocked": sum(1 for a in agents if a["effective_status"] == "blocked"),
        "agents_online": sum(1 for a in agents if a["effective_status"] != "offline"),
        "projects_active": c("SELECT COUNT(*) FROM projects WHERE status='active'"),
        "tasks_open": c("SELECT COUNT(*) FROM tasks WHERE status!='done'"),
        "tasks_in_progress": c("SELECT COUNT(*) FROM tasks WHERE status='in_progress'"),
        "tasks_blocked": c("SELECT COUNT(*) FROM tasks WHERE status='blocked'"),
        "tasks_done_24h": c("SELECT COUNT(*) FROM tasks WHERE completed_at>=?", day_start),
        "todos_open": c("SELECT COUNT(*) FROM todos WHERE done=0"),
        "todos_done_24h": c("SELECT COUNT(*) FROM todos WHERE done_at>=?", day_start),
        "events_24h": c("SELECT COUNT(*) FROM events WHERE ts>=?", day_start),
        "activity_by_hour": [next((x["n"] for x in activity if x["bucket"] == h), 0) for h in range(24)],
        "last_event_id": last_event_id(conn),
    }
