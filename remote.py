"""HTTP drop-in for db.py, used by agentctl.py and mcp_server.py when
AGENT_DASH_URL is set (agents talking to a hosted dashboard).
Functions mirror db.py's signatures; the conn argument is ignored."""
import json
import os
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

from db import AGENT_STATUSES, PRIORITIES, PROJECT_STATUSES, TASK_STATUSES, Invalid, NotFound  # noqa: F401

URL = os.environ.get("AGENT_DASH_URL", "").rstrip("/")
TOKEN = os.environ.get("AGENT_DASH_TOKEN", "")
DB_PATH = None


def _call(method, path, body=None, query=None, agent=None):
    if query:
        path += "?" + urlencode({k: v for k, v in query.items() if v is not None})
    req = urllib.request.Request(URL + path, method=method, data=json.dumps(body or {}).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    if agent:
        req.add_header("X-Agent", str(agent))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        msg = json.loads(e.read() or b"{}").get("error", e.reason) if e.code != 401 else "unauthorized: set AGENT_DASH_TOKEN"
        raise (NotFound if e.code == 404 else Invalid)(msg)


def _q(ref):
    return quote(str(ref), safe="")


def _clean(d):
    return {k: v for k, v in d.items() if v is not None}


def connect(path=None):
    return None


def init(conn):
    pass


def stats(conn):
    return _call("GET", "/api/stats")


def list_projects(conn):
    return _call("GET", "/api/projects")


def get_project(conn, ref):
    return _call("GET", f"/api/projects/{_q(ref)}")


def create_project(conn, name, description="", status="active", agent=None, repo="", branch=""):
    return _call("POST", "/api/projects", {"name": name, "description": description, "status": status,
                                           "repo": repo, "branch": branch}, agent=agent)


def update_project(conn, ref, agent=None, **fields):
    return _call("PATCH", f"/api/projects/{_q(ref)}", _clean(fields), agent=agent)


def get_agent(conn, ref):
    return _call("GET", f"/api/agents/{_q(ref)}")


def heartbeat(conn, agent, status=None, note=None, role=None, task_id=None, model=None, host=None, parent=None, metrics=None):
    return _call("POST", f"/api/agents/{_q(agent)}/heartbeat", _clean({"status": status, "note": note, "role": role, "task_id": task_id,
                                                                       "model": model, "host": host, "parent": parent, "metrics": metrics}))


def report_host(conn, name, **stats):
    return _call("POST", f"/api/hosts/{_q(name)}", _clean(stats))


def list_hosts(conn):
    return _call("GET", "/api/hosts")


def claim_next_task(conn, agent, project=None):
    return _call("POST", f"/api/agents/{_q(agent)}/claim", _clean({"project": project}))


def list_tasks(conn, project=None, status=None, assignee=None, limit=500):
    return _call("GET", "/api/tasks", query={"project": project, "status": status, "assignee": assignee, "limit": limit})


def get_task(conn, task_id):
    return _call("GET", f"/api/tasks/{int(task_id)}")


def create_task(conn, project, title, description="", priority="medium", assignee=None, status="todo", agent=None, todos=None):
    return _call("POST", "/api/tasks", _clean({"project": project, "title": title, "description": description, "priority": priority,
                                               "assignee": assignee, "status": status, "todos": todos}), agent=agent)


def update_task(conn, task_id, agent=None, note=None, **fields):
    return _call("PATCH", f"/api/tasks/{int(task_id)}", _clean({**fields, "note": note}), agent=agent)


def log_progress(conn, task_id, message, agent=None):
    return _call("POST", f"/api/tasks/{int(task_id)}/progress", {"message": message}, agent=agent)


def add_todo(conn, task_id, text, agent=None):
    return _call("POST", f"/api/tasks/{int(task_id)}/todos", {"text": text}, agent=agent)


def set_todo(conn, todo_id, done=None, text=None, agent=None):
    return _call("PATCH", f"/api/todos/{int(todo_id)}", _clean({"done": done, "text": text}), agent=agent)


def list_events(conn, since=None, project=None, agent=None, task=None, limit=100):
    return _call("GET", "/api/events", query={"since": since, "project": project, "agent": agent, "task": task, "limit": limit})
