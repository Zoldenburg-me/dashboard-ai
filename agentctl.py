#!/usr/bin/env python3
"""Command-line client for agents. Writes straight to the SQLite file, so it
works with or without the server running, from any number of processes.

Identify yourself with --agent NAME or the AGENT_NAME env var. Output is JSON.
For a hosted dashboard set AGENT_DASH_URL (and AGENT_DASH_TOKEN) to go over HTTP.

  agentctl.py hb --status working --note "reading codebase" --model claude-sonnet --metric tokens_per_sec=84
  agentctl.py host report --auto            # this machine's GPU/VRAM/RAM/CPU (run every minute or so)
  agentctl.py project add web-app --desc "Customer portal"
  agentctl.py task add web-app "Add login page" --priority high --todo "form" --todo "validation"
  agentctl.py task claim --project web-app
  agentctl.py task log 12 "Login form rendered; wiring API next"
  agentctl.py todo add 12 "Write tests"
  agentctl.py todo done 31
  agentctl.py task set 12 --status done --note "merged"
  agentctl.py task list --project web-app --status todo,in_progress
  agentctl.py rules --master taskmaster       # what breaks the tracking rules right now

Teams (outside groups with their own agents; needs the master token when hosted):
  agentctl.py team add acme
  agentctl.py team grant acme web-app         # acme's tokens see only granted projects
  agentctl.py team token acme --label "acme laptop" [--agent acme-openclaw]
  agentctl.py team revoke 3
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


def num(v):
    """CLI values arrive as strings; keep numbers numeric so the dashboard can chart them."""
    try:
        return int(v) if str(v).lstrip("-").isdigit() else float(v)
    except (TypeError, ValueError):
        return v


def parse_metrics(pairs):
    out = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--metric expects KEY=VALUE, got {pair!r}")
        k, v = pair.split("=", 1)
        out[k.strip()] = num(v.strip()) if v.strip() else None
    return out or None


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
    hb.add_argument("--model", help="model the agent runs on, e.g. claude-sonnet, qwen-2.5-coder")
    hb.add_argument("--host", help="machine the agent runs on (matches `host report` names)")
    hb.add_argument("--parent", help="lead agent this one works under ('' to clear)")
    hb.add_argument("--metric", action="append", default=[], metavar="KEY=VALUE",
                    help="e.g. vram_gb=4.8, tokens_per_sec=84, context_used=14200, context_max=128000, quant=Q4_K_M")

    hs = sub.add_parser("host").add_subparsers(dest="sub", required=True)
    p = hs.add_parser("report", help="report a machine's hardware stats")
    p.add_argument("name", nargs="?", help="host name (default: this machine's hostname)")
    p.add_argument("--auto", action="store_true", help="read stats from this machine (nvidia-smi, /proc/meminfo, load)")
    for f in db.HOST_FIELDS:
        p.add_argument("--" + f.replace("_", "-"), dest=f)
    hs.add_parser("list")

    pr = sub.add_parser("project").add_subparsers(dest="sub", required=True)
    p = pr.add_parser("add"); p.add_argument("name"); p.add_argument("--desc", default="")
    p.add_argument("--repo", default="", help="git repo, e.g. github.com/org/name"); p.add_argument("--branch", default="")
    pr.add_parser("list")
    p = pr.add_parser("show"); p.add_argument("ref")
    p = pr.add_parser("set"); p.add_argument("ref"); p.add_argument("--status", choices=db.PROJECT_STATUSES); p.add_argument("--desc"); p.add_argument("--name")
    p.add_argument("--repo"); p.add_argument("--branch")

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
    p = sub.add_parser("rules", help="list breaks of the tracking rules")
    p.add_argument("--master", default=os.environ.get("AGENT_DASH_MASTER", "taskmaster"),
                   help="root agent your agents report to (default: $AGENT_DASH_MASTER or taskmaster)")
    p.add_argument("--quiet-minutes", type=int, default=30, help="in-progress task with no progress for this long counts as quiet")

    tm = sub.add_parser("team").add_subparsers(dest="sub", required=True)
    p = tm.add_parser("add"); p.add_argument("name")
    tm.add_parser("list")
    p = tm.add_parser("show"); p.add_argument("name")
    p = tm.add_parser("grant", help="give the team access to a project"); p.add_argument("name"); p.add_argument("project")
    p = tm.add_parser("ungrant"); p.add_argument("name"); p.add_argument("project")
    p = tm.add_parser("token", help="create a token (shown once)"); p.add_argument("name"); p.add_argument("--label", default="")
    p.add_argument("--agent", help="bind the token to one agent name")
    p = tm.add_parser("revoke", help="revoke a token by id"); p.add_argument("id", type=int)

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
            out = db.heartbeat(c, need_agent(), a.status, a.note, a.role, a.task, a.model, a.host, a.parent, parse_metrics(a.metric))
            out.pop("events", None)
        elif a.cmd == "host":
            if sc == "list":
                out = db.list_hosts(c)
            else:
                import hoststats
                stats = hoststats.collect() if a.auto else {}
                stats.update({f: num(getattr(a, f)) for f in db.HOST_FIELDS if getattr(a, f) is not None})
                out = db.report_host(c, a.name or hoststats.hostname(), **stats)
        elif a.cmd == "project":
            out = {"add": lambda: db.create_project(c, a.name, a.desc, agent=me, repo=a.repo, branch=a.branch),
                   "list": lambda: db.list_projects(c),
                   "show": lambda: db.get_project(c, a.ref),
                   "set": lambda: db.update_project(c, a.ref, me, name=a.name, description=a.desc, status=a.status,
                                                    repo=a.repo, branch=a.branch)}[sc]()
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
        elif a.cmd == "rules":
            out = db.check_rules(c, master=a.master, quiet_minutes=a.quiet_minutes)
        elif a.cmd == "team":
            out = {"add": lambda: db.create_team(c, a.name),
                   "list": lambda: db.list_teams(c),
                   "show": lambda: db.get_team(c, a.name),
                   "grant": lambda: db.grant_project(c, a.name, a.project),
                   "ungrant": lambda: db.grant_project(c, a.name, a.project, grant=False),
                   "token": lambda: db.create_token(c, a.name, a.label, a.agent),
                   "revoke": lambda: db.revoke_token(c, a.id)}[sc]()
    except (db.NotFound, db.Invalid, db.Forbidden) as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
