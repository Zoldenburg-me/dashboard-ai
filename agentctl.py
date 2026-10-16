#!/usr/bin/env python3
"""Command-line client for agents. Writes straight to the SQLite file, so it
works with or without the server running, from any number of processes.

Identify yourself with --agent NAME or the AGENT_NAME env var. Output is JSON.
For a hosted dashboard set AGENT_DASH_URL (and AGENT_DASH_TOKEN) to go over HTTP.

  agentctl.py hb --status working --note "reading codebase"
  agentctl.py project add web-app --desc "Customer portal"
  agentctl.py task add web-app "Add login page" --priority high --todo "form" --todo "validation"
  agentctl.py task claim --project web-app
  agentctl.py task log 12 "Login form rendered; wiring API next"
  agentctl.py todo add 12 "Write tests"
  agentctl.py todo done 31
  agentctl.py task set 12 --status done --note "merged"
  agentctl.py task list --project web-app --status todo,in_progress
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if os.environ.get("AGENT_DASH_URL"):
    import remote as db  # noqa: E402  (hosted dashboard over HTTP)
else:
    import db  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agentctl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent", default=os.environ.get("AGENT_NAME"), help="your agent name (or $AGENT_NAME)")
    ap.add_argument("--db", default=None, help="SQLite file (default: $AGENT_DB or data/agents.db)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    hb = sub.add_parser("hb", help="heartbeat / set your status")
    hb.add_argument("--status", choices=db.AGENT_STATUSES)
    hb.add_argument("--note")
    hb.add_argument("--role")
    hb.add_argument("--task", type=int, help="current task id (0 to clear)")

    pr = sub.add_parser("project").add_subparsers(dest="sub", required=True)
    p = pr.add_parser("add"); p.add_argument("name"); p.add_argument("--desc", default="")
    pr.add_parser("list")
    p = pr.add_parser("show"); p.add_argument("ref")
    p = pr.add_parser("set"); p.add_argument("ref"); p.add_argument("--status", choices=db.PROJECT_STATUSES); p.add_argument("--desc"); p.add_argument("--name")

    tk = sub.add_parser("task").add_subparsers(dest="sub", required=True)
    p = tk.add_parser("add"); p.add_argument("project"); p.add_argument("title"); p.add_argument("--desc", default="")
    p.add_argument("--priority", default="medium", choices=db.PRIORITIES); p.add_argument("--assignee")
    p.add_argument("--status", default="todo", choices=db.TASK_STATUSES); p.add_argument("--todo", action="append", default=[])
    p.add_argument("--mine", action="store_true", help="assign to yourself")
    p = tk.add_parser("list"); p.add_argument("--project"); p.add_argument("--status"); p.add_argument("--assignee")
    p.add_argument("--mine", action="store_true")
    p = tk.add_parser("show"); p.add_argument("id", type=int)
    p = tk.add_parser("set"); p.add_argument("id", type=int); p.add_argument("--status", choices=db.TASK_STATUSES)
    p.add_argument("--priority", choices=db.PRIORITIES); p.add_argument("--assignee"); p.add_argument("--title"); p.add_argument("--desc"); p.add_argument("--note")
    p = tk.add_parser("start"); p.add_argument("id", type=int)
    p = tk.add_parser("done"); p.add_argument("id", type=int); p.add_argument("--note")
    p = tk.add_parser("block"); p.add_argument("id", type=int); p.add_argument("--note", required=True)
    p = tk.add_parser("log"); p.add_argument("id", type=int); p.add_argument("message")
    p = tk.add_parser("claim"); p.add_argument("--project")

    td = sub.add_parser("todo").add_subparsers(dest="sub", required=True)
    p = td.add_parser("add"); p.add_argument("task", type=int); p.add_argument("text")
    p = td.add_parser("done"); p.add_argument("id", type=int)
    p = td.add_parser("undo"); p.add_argument("id", type=int)

    ev = sub.add_parser("events"); ev.add_argument("--project"); ev.add_argument("--since", type=int); ev.add_argument("--limit", type=int, default=20)
    sub.add_parser("stats")

    a = ap.parse_args(argv)
    if a.db:
        db.DB_PATH = os.path.abspath(a.db)
    c = db.connect()
    db.init(c)
    me = a.agent

    def need_agent():
        if not me:
            ap.error("this command needs --agent NAME or $AGENT_NAME")
        return me

    sc = getattr(a, "sub", None)
    try:
        if a.cmd == "hb":
            out = db.heartbeat(c, need_agent(), a.status, a.note, a.role, a.task)
            out.pop("events", None)
        elif a.cmd == "project":
            out = {"add": lambda: db.create_project(c, a.name, a.desc, agent=me),
                   "list": lambda: db.list_projects(c),
                   "show": lambda: db.get_project(c, a.ref),
                   "set": lambda: db.update_project(c, a.ref, me, name=a.name, description=a.desc, status=a.status)}[sc]()
        elif a.cmd == "task":
            if sc == "add":
                out = db.create_task(c, a.project, a.title, a.desc, a.priority, need_agent() if a.mine else a.assignee, a.status, me, a.todo)
            elif sc == "list":
                out = db.list_tasks(c, a.project, a.status, need_agent() if a.mine else a.assignee)
            elif sc == "show":
                out = db.get_task(c, a.id)
            elif sc == "set":
                out = db.update_task(c, a.id, me, a.note, status=a.status, priority=a.priority, assignee=a.assignee, title=a.title, description=a.desc)
            elif sc == "start":
                out = db.update_task(c, a.id, need_agent(), status="in_progress", assignee=me)
            elif sc == "done":
                out = db.update_task(c, a.id, me, a.note, status="done")
            elif sc == "block":
                out = db.update_task(c, a.id, me, a.note, status="blocked")
            elif sc == "log":
                out = db.log_progress(c, a.id, a.message, me)
            elif sc == "claim":
                out = db.claim_next_task(c, need_agent(), a.project) or {"claimed": None, "message": "no unassigned todo tasks"}
        elif a.cmd == "todo":
            out = {"add": lambda: db.add_todo(c, a.task, a.text, me),
                   "done": lambda: db.set_todo(c, a.id, True, agent=me),
                   "undo": lambda: db.set_todo(c, a.id, False, agent=me)}[sc]()
        elif a.cmd == "events":
            out = db.list_events(c, since=a.since, project=a.project, limit=a.limit)
        elif a.cmd == "stats":
            out = db.stats(c)
    except (db.NotFound, db.Invalid) as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
