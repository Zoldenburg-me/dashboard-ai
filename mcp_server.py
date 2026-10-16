#!/usr/bin/env python3
"""Minimal MCP (stdio, JSON-RPC 2.0) server exposing the tracker as tools.

Register once for every folder with Claude Code:
  claude mcp add agent-tracker --scope user -- python3 /path/to/dashboard-ai/mcp_server.py

Each agent picks its own name with tracker_register. The name is remembered for
the folder it runs in (~/.config/agent-tracker/names.json), so it comes back
after a restart. AGENT_NAME, if set, fixes the name instead.
Every agent runs its own copy; they all share the same database.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if os.environ.get("AGENT_DASH_URL"):
    import remote as db  # noqa: E402  (hosted dashboard over HTTP)
else:
    import db  # noqa: E402

FIXED_NAME = os.environ.get("AGENT_NAME")
FOLDER = os.path.abspath(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
NAMES_FILE = os.environ.get("AGENT_TRACKER_NAMES", os.path.expanduser("~/.config/agent-tracker/names.json"))


def _names():
    try:
        with open(NAMES_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_name(name):
    names = _names()
    names[FOLDER] = name
    os.makedirs(os.path.dirname(NAMES_FILE), exist_ok=True)
    tmp = f"{NAMES_FILE}.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(names, f, indent=1)
    os.replace(tmp, NAMES_FILE)


ME = FIXED_NAME or _names().get(FOLDER)


def me():
    if not ME:
        raise db.Invalid("you have no agent name yet: call tracker_register with a name of your choice first")
    return ME


def register(c, a):
    """Choose this agent's name. Renames the existing record so tasks and history follow."""
    global ME
    name = (a.get("name") or "").strip()
    if FIXED_NAME and name != FIXED_NAME:
        raise db.Invalid(f"name is fixed to {FIXED_NAME!r} by AGENT_NAME")
    if not name or name.isdigit():
        raise db.Invalid("name must be non-empty and not a number")
    if name != ME:
        try:
            taken = db.get_agent(c, name)
        except db.NotFound:
            taken = None
        if taken:
            raise db.Invalid(f"agent name {name!r} is taken; choose another")
        renamed = False
        if ME:
            try:
                db.rename_agent(c, ME, name)
                renamed = True
            except db.NotFound:
                pass
        ME = name
        if not FIXED_NAME:
            _save_name(name)
    else:
        renamed = False
    out = db.heartbeat(c, ME, a.get("status", "idle"), a.get("note"), a.get("role"), None, a.get("model"), a.get("host"))
    return {"registered_as": ME, "renamed": renamed, "agent": out}


def S(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


STR, INT = {"type": "string"}, {"type": "integer"}
ENUM = lambda vals: {"type": "string", "enum": list(vals)}

TOOLS = {
    "tracker_register": ("Choose your agent name and register on the dashboard. Call this first. Pick a short unique name "
                         "(e.g. 'zold-backend'); if it is taken you get an error and should choose another. The name is "
                         "remembered for this folder, and calling again with a new name renames you, keeping your tasks and history.",
                         S({"name": STR, "role": STR, "model": STR, "host": STR, "status": ENUM(db.AGENT_STATUSES), "note": STR}, ["name"]),
                         register),
    "tracker_heartbeat": ("Report you are alive and what you are doing. Call at the start of work and when your status changes. "
                          "Optionally report the model you run on, your host machine, the lead agent you work under, and metrics "
                          "such as tokens_per_sec, vram_gb, context_used, context_max, quant.",
                          S({"status": ENUM(db.AGENT_STATUSES), "note": STR, "role": STR, "model": STR, "host": STR, "parent": STR,
                             "metrics": {"type": "object"}}),
                          lambda c, a: db.heartbeat(c, me(), a.get("status"), a.get("note"), a.get("role"), None,
                                                    a.get("model"), a.get("host"), a.get("parent"), a.get("metrics"))),
    "tracker_report_host": ("Report a machine's hardware stats (all optional): " + ", ".join(db.HOST_FIELDS) + ".",
                            S({"name": STR, **{f: STR if f in ("gpu", "engine") else {"type": "number"} for f in db.HOST_FIELDS}}, ["name"]),
                            lambda c, a: db.report_host(c, a["name"], **{k: v for k, v in a.items() if k != "name"})),
    "tracker_list_projects": ("List projects with task counts.", S({}), lambda c, a: db.list_projects(c)),
    "tracker_create_project": ("Create a project, optionally linked to a git repo and branch.",
                               S({"name": STR, "description": STR, "repo": STR, "branch": STR}, ["name"]),
                               lambda c, a: db.create_project(c, a["name"], a.get("description", ""), agent=me(),
                                                              repo=a.get("repo", ""), branch=a.get("branch", ""))),
    "tracker_list_tasks": ("List tasks. status may be comma-separated, e.g. 'todo,in_progress'. mine=true for your own tasks.",
                           S({"project": STR, "status": STR, "mine": {"type": "boolean"}}),
                           lambda c, a: db.list_tasks(c, a.get("project"), a.get("status"), me() if a.get("mine") else None)),
    "tracker_get_task": ("Get a task with its todos and history.", S({"task_id": INT}, ["task_id"]), lambda c, a: db.get_task(c, a["task_id"])),
    "tracker_create_task": ("Record a new task as soon as you identify work. Check tracker_list_tasks first: never create a second task for the same work. Set assign_to_me to own it.",
                            S({"project": STR, "title": STR, "description": STR, "priority": ENUM(db.PRIORITIES),
                               "status": ENUM(db.TASK_STATUSES), "assign_to_me": {"type": "boolean"},
                               "todos": {"type": "array", "items": STR}}, ["project", "title"]),
                            lambda c, a: db.create_task(c, a["project"], a["title"], a.get("description", ""), a.get("priority", "medium"),
                                                        me() if a.get("assign_to_me") else None, a.get("status", "todo"), me(), a.get("todos"))),
    "tracker_update_task": ("Change a task's status/priority/assignee/title/description. Rewrite fields to the current truth; never append 'Update:' or 'was X, now Y' text. Include a note describing what happened (it goes to the history log).",
                            S({"task_id": INT, "status": ENUM(db.TASK_STATUSES), "priority": ENUM(db.PRIORITIES), "assignee": STR,
                               "title": STR, "description": STR, "note": STR}, ["task_id"]),
                            lambda c, a: db.update_task(c, a["task_id"], me(), a.get("note"), **{k: a.get(k) for k in ("status", "priority", "assignee", "title", "description")})),
    "tracker_claim_next_task": ("Atomically claim the highest-priority unassigned todo task (optionally within a project).",
                                S({"project": STR}), lambda c, a: db.claim_next_task(c, me(), a.get("project")) or {"claimed": None}),
    "tracker_log_progress": ("Record what you just did on a task (append-only history, one line per step). Use often.", S({"task_id": INT, "message": STR}, ["task_id", "message"]),
                             lambda c, a: db.log_progress(c, a["task_id"], a["message"], me())),
    "tracker_add_todo": ("Add a checklist todo to a task.", S({"task_id": INT, "text": STR}, ["task_id", "text"]),
                         lambda c, a: db.add_todo(c, a["task_id"], a["text"], me())),
    "tracker_complete_todo": ("Mark a todo done (done=false reopens).", S({"todo_id": INT, "done": {"type": "boolean"}}, ["todo_id"]),
                              lambda c, a: db.set_todo(c, a["todo_id"], a.get("done", True), agent=me())),
}


def handle(c, msg):
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if method == "initialize":
        hint = (f"You are registered on the agent dashboard as {ME!r}." if ME else
                "Before other tracker calls, choose a short unique agent name and call tracker_register with it.")
        return {"protocolVersion": params.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                "serverInfo": {"name": "agent-tracker", "version": "1.1.0"},
                "instructions": hint + " Record every task and todo in the tracker as you work."}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": s} for n, (d, s, _) in TOOLS.items()]}
    if method == "tools/call":
        name = params.get("name")
        if name not in TOOLS:
            return {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}
        try:
            out = TOOLS[name][2](c, params.get("arguments") or {})
            return {"content": [{"type": "text", "text": json.dumps(out, default=str)}]}
        except (db.NotFound, db.Invalid, KeyError) as e:
            return {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}
    if method == "ping":
        return {}
    if mid is not None:
        raise LookupError(method)
    return None


def main():
    c = db.connect()
    db.init(c)
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        try:
            result = handle(c, msg)
            reply = {"jsonrpc": "2.0", "id": msg.get("id"), "result": result}
        except LookupError as e:
            reply = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32601, "message": f"method not found: {e}"}}
        if msg.get("id") is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
