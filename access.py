"""Who is calling, and what they may touch. Used by the HTTP API and the remote MCP endpoint.

Three kinds of caller:
  admin    - the master token (AGENT_DASH_TOKEN), or anyone when auth is off: everything,
             including managing teams and their tokens.
  internal - your own agents' tokens (AGENT_DASH_AGENT_TOKENS): the whole board.
  team     - a team token (created with `agentctl.py team token`): only the projects granted
             to that team, and only as that team's own agents. A token bound to one agent
             always acts as that agent.

for_identity() returns an object with db.py's function signatures, so callers don't
change: internal callers get db itself, teams get a TeamScope that checks every call.
"""
import sqlite3
import time

import db


def for_identity(ident):
    return TeamScope(ident) if ident and ident.get("kind") == "team" else db


class TeamScope:
    def __init__(self, ident):
        self.team_id = ident["team_id"]
        self.team = ident["team"]
        self.bound = ident.get("agent")

    # ---------- checks ----------

    def _project(self, conn, ref):
        """Project id, if granted to this team. Others look like they don't exist."""
        try:
            pid = db.resolve_project(conn, ref)
        except db.NotFound:
            pid = None
        if pid is None or not conn.execute("SELECT 1 FROM team_projects WHERE team_id=? AND project_id=?",
                                           (self.team_id, pid)).fetchone():
            raise db.NotFound(f"project {ref!r} not found")
        return pid

    def _task(self, conn, task_id):
        try:
            t = db._task(conn, task_id)
        except (db.NotFound, ValueError):
            raise db.NotFound(f"task {task_id} not found")
        self._project(conn, t["project_id"])
        return t

    def _member(self, conn, ref, create=True):
        """A name for one of this team's agents. New names join the team; names that
        belong to your own agents or another team are refused."""
        if ref is None or ref == "":
            return None
        col, val = ("id", int(ref)) if isinstance(ref, int) or str(ref).isdigit() else ("name", str(ref))
        r = conn.execute(f"SELECT name, team_id FROM agents WHERE {col}=?", (val,)).fetchone()
        if r:
            if r["team_id"] != self.team_id:
                raise db.Forbidden(f"agent {ref!r} belongs to someone else; choose another name")
            return r["name"]
        if col == "id" or not create:
            raise db.NotFound(f"agent {ref!r} not found")
        now = time.time()
        try:
            with db.write(conn):
                cur = conn.execute("INSERT INTO agents (name, team_id, created_at, last_seen) VALUES (?,?,?,?)",
                                   (val, self.team_id, now, now))
                db._log(conn, "agent.registered", f"{val} registered (team {self.team})", agent_id=cur.lastrowid)
        except sqlite3.IntegrityError:
            return self._member(conn, ref, create=False)  # another request created it first
        return val

    def _acting(self, conn, agent):
        """The agent a write is made as. Required for teams, so every change is attributed."""
        if self.bound:
            if agent and agent != self.bound:
                raise db.Forbidden(f"this token can only act as {self.bound!r}")
            agent = self.bound
        if not agent:
            raise db.Invalid("say which agent you are: an 'agent' field or an X-Agent header")
        return self._member(conn, agent)

    def _granted_ids(self, conn):
        return {r[0] for r in conn.execute(db.TEAM_PROJECTS, (self.team_id,))}

    # ---------- reads ----------

    def stats(self, conn):
        raise db.Forbidden("team tokens can't read board-wide stats")

    def list_projects(self, conn):
        return db.list_projects(conn, team_id=self.team_id)

    def get_project(self, conn, ref):
        return db.get_project(conn, self._project(conn, ref))

    def list_agents(self, conn):
        return db.list_agents(conn, team_id=self.team_id)

    def get_agent(self, conn, ref):
        a = db.get_agent(conn, self._member(conn, ref, create=False))
        granted = self._granted_ids(conn)
        a["tasks"] = [t for t in a["tasks"] if t["project_id"] in granted]
        a["events"] = [e for e in a["events"] if e["project_id"] is None or e["project_id"] in granted]
        return a

    def list_hosts(self, conn):
        raise db.Forbidden("team tokens can't read host stats")

    def list_tasks(self, conn, project=None, status=None, assignee=None, limit=500):
        if project is not None:
            project = self._project(conn, project)
        return db.list_tasks(conn, project, status, assignee, limit, team_id=self.team_id)

    def get_task(self, conn, task_id):
        self._task(conn, task_id)
        return db.get_task(conn, task_id)

    def list_events(self, conn, since=None, project=None, agent=None, task=None, limit=100):
        if project is not None:
            project = self._project(conn, project)
        if task is not None:
            self._task(conn, task)
        return db.list_events(conn, since, project, agent, task, limit, team_id=self.team_id)

    def check_rules(self, conn, master=None, quiet_minutes=30):
        return db.check_rules(conn, team_id=self.team_id, quiet_minutes=quiet_minutes)

    # ---------- writes ----------

    def create_project(self, conn, name, description="", status="active", agent=None, repo="", branch=""):
        return db.create_project(conn, name, description, status, self._acting(conn, agent), repo, branch, team_id=self.team_id)

    def update_project(self, conn, ref, agent=None, **fields):
        pid = self._project(conn, ref)
        return db.update_project(conn, pid, self._acting(conn, agent), **fields)

    def rename_agent(self, conn, ref, name):
        if self.bound:
            raise db.Forbidden("an agent-bound token can't rename its agent")
        old = self._member(conn, ref, create=False)
        name = (name or "").strip()
        if name and conn.execute("SELECT 1 FROM agents WHERE name=?", (name,)).fetchone():
            raise db.Invalid(f"agent name {name!r} is taken; choose another")
        return db.rename_agent(conn, old, name)

    def heartbeat(self, conn, agent, status=None, note=None, role=None, task_id=None, model=None, host=None, parent=None, metrics=None):
        agent = self._acting(conn, agent)
        if parent:
            parent = self._member(conn, parent, create=False)
        if task_id:
            self._task(conn, task_id)
        return self.get_agent(conn, db.heartbeat(conn, agent, status, note, role, task_id, model, host, parent, metrics)["id"])

    def report_host(self, conn, name, **stats):
        raise db.Forbidden("team tokens can't report host stats")

    def claim_next_task(self, conn, agent, project=None):
        agent = self._acting(conn, agent)
        if project is not None:
            project = self._project(conn, project)
        return db.claim_next_task(conn, agent, project, team_id=self.team_id)

    def create_task(self, conn, project, title, description="", priority="medium", assignee=None, status="todo", agent=None, todos=None):
        pid = self._project(conn, project)
        return db.create_task(conn, pid, title, description, priority, self._member(conn, assignee), status,
                              self._acting(conn, agent), todos)

    def update_task(self, conn, task_id, agent=None, note=None, **fields):
        self._task(conn, task_id)
        if fields.get("assignee"):
            fields["assignee"] = self._member(conn, fields["assignee"])
        return db.update_task(conn, task_id, self._acting(conn, agent), note, **fields)

    def delete_task(self, conn, task_id, agent=None):
        self._task(conn, task_id)
        return db.delete_task(conn, task_id, self._acting(conn, agent))

    def log_progress(self, conn, task_id, message, agent=None):
        self._task(conn, task_id)
        return db.log_progress(conn, task_id, message, self._acting(conn, agent))

    def add_todo(self, conn, task_id, text, agent=None):
        self._task(conn, task_id)
        return db.add_todo(conn, task_id, text, self._acting(conn, agent))

    def set_todo(self, conn, todo_id, done=None, text=None, agent=None):
        r = conn.execute("SELECT task_id FROM todos WHERE id=?", (int(todo_id),)).fetchone()
        if not r:
            raise db.NotFound(f"todo {todo_id} not found")
        self._task(conn, r["task_id"])
        return db.set_todo(conn, todo_id, done, text, self._acting(conn, agent))
