#!/usr/bin/env python3
"""Minimal MCP (stdio, JSON-RPC 2.0) server exposing the tracker as tools.

Register with Claude Code:
  claude mcp add agent-tracker -e AGENT_NAME=backend-bot -- python3 /path/to/agent-dashboard/mcp_server.py

Every agent runs its own copy; they all share the same SQLite file.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if os.environ.get("AGENT_DASH_URL"):
    import remote as db  # noqa: E402  (hosted dashboard over HTTP)
else:
    import db  # noqa: E402

ME = os.environ.get("AGENT_NAME", "mcp-agent")


def S(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


STR, INT = {"type": "string"}, {"type": "integer"}
ENUM = lambda vals: {"type": "string", "enum": list(vals)}

TOOLS = {
    "tracker_heartbeat": ("Report you are alive and what you are doing. Call at the start of work and when your status changes.",
                          S({"status": ENUM(db.AGENT_STATUSES), "note": STR, "role": STR}),
                          lambda c, a: db.heartbeat(c, ME, a.get("status"), a.get("note"), a.get("role"))),
    "tracker_list_projects": ("List projects with task counts.", S({}), lambda c, a: db.list_projects(c)),
    "tracker_create_project": ("Create a project.", S({"name": STR, "description": STR}, ["name"]),
                               lambda c, a: db.create_project(c, a["name"], a.get("description", ""), agent=ME)),
    "tracker_list_tasks": ("List tasks. status may be comma-separated, e.g. 'todo,in_progress'. mine=true for your own tasks.",
                           S({"project": STR, "status": STR, "mine": {"type": "boolean"}}),
                           lambda c, a: db.list_tasks(c, a.get("project"), a.get("status"), ME if a.get("mine") else None)),
    "tracker_get_task": ("Get a task with its todos and history.", S({"task_id": INT}, ["task_id"]), lambda c, a: db.get_task(c, a["task_id"])),
    "tracker_create_task": ("Record a new task as soon as you identify work. Check tracker_list_tasks first: never create a second task for the same work. Set assign_to_me to own it.",
                            S({"project": STR, "title": STR, "description": STR, "priority": ENUM(db.PRIORITIES),
                               "status": ENUM(db.TASK_STATUSES), "assign_to_me": {"type": "boolean"},
                               "todos": {"type": "array", "items": STR}}, ["project", "title"]),
                            lambda c, a: db.create_task(c, a["project"], a["title"], a.get("description", ""), a.get("priority", "medium"),
                                                        ME if a.get("assign_to_me") else None, a.get("status", "todo"), ME, a.get("todos"))),
    "tracker_update_task": ("Change a task's status/priority/assignee/title/description. Rewrite fields to the current truth; never append 'Update:' or 'was X, now Y' text. Include a note describing what happened (it goes to the history log).",
                            S({"task_id": INT, "status": ENUM(db.TASK_STATUSES), "priority": ENUM(db.PRIORITIES), "assignee": STR,
                               "title": STR, "description": STR, "note": STR}, ["task_id"]),
                            lambda c, a: db.update_task(c, a["task_id"], ME, a.get("note"), **{k: a.get(k) for k in ("status", "priority", "assignee", "title", "description")})),
    "tracker_claim_next_task": ("Atomically claim the highest-priority unassigned todo task (optionally within a project).",
                                S({"project": STR}), lambda c, a: db.claim_next_task(c, ME, a.get("project")) or {"claimed": None}),
    "tracker_log_progress": ("Record what you just did on a task (append-only history, one line per step). Use often.", S({"task_id": INT, "message": STR}, ["task_id", "message"]),
                             lambda c, a: db.log_progress(c, a["task_id"], a["message"], ME)),
    "tracker_add_todo": ("Add a checklist todo to a task.", S({"task_id": INT, "text": STR}, ["task_id", "text"]),
                         lambda c, a: db.add_todo(c, a["task_id"], a["text"], ME)),
    "tracker_complete_todo": ("Mark a todo done (done=false reopens).", S({"todo_id": INT, "done": {"type": "boolean"}}, ["todo_id"]),
                              lambda c, a: db.set_todo(c, a["todo_id"], a.get("done", True), agent=ME)),
}


def handle(c, msg):
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                "serverInfo": {"name": "agent-tracker", "version": "1.0.0"}}
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
