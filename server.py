"""Dashboard + JSON API server. Standard library only.

    python server.py [--host 0.0.0.0] [--port 8787] [--db data/agents.db]
"""
import argparse
import base64
import hmac
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import access
import db
import mcp_server

# Set AGENT_DASH_TOKEN when exposing the server publicly. Browsers log in with
# HTTP Basic auth (any username, token as password); agents send
# "Authorization: Bearer <token>".
TOKEN = os.environ.get("AGENT_DASH_TOKEN", "")


def _agent_tokens(raw):
    """Extra per-agent tokens, "name:token,name:token". Bearer only, so they never open
    the browser dashboard; remove one entry to revoke that agent alone. An entry without
    "name:" still counts as a token, so a missing name can't silently switch auth off."""
    tokens = []
    for entry in raw.split(","):
        name, sep, token = entry.partition(":")
        token = (token if sep else name).strip()
        if token:
            tokens.append(token)
    return tokens


AGENT_TOKENS = _agent_tokens(os.environ.get("AGENT_DASH_AGENT_TOKENS", ""))
# The root agent every one of your agents should report to (checked by /api/rules).
MASTER = os.environ.get("AGENT_DASH_MASTER", "taskmaster")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_local = threading.local()


# Serverless hosts (Vercel) can't hold a stream open; the UI polls instead.
STREAMING = not os.environ.get("VERCEL")
_init_lock = threading.Lock()
_initialized = False


def conn():
    global _initialized
    if not hasattr(_local, "conn"):
        _local.conn = db.connect()
    if not _initialized:
        with _init_lock:
            if not _initialized:
                db.init(_local.conn)
                _initialized = True
    return _local.conn


ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


def agent_of(req, body):
    return body.get("agent") or req.headers.get("X-Agent")


def api(req):
    """db.py, or a TeamScope that limits a team token to its own projects and agents."""
    return access.for_identity(req.ident)


@route("GET", r"/api/config")
def _config(req, q, body):
    return {"stream": STREAMING, "store": "turso" if os.environ.get("TURSO_DATABASE_URL") else "sqlite"}


@route("GET", r"/api/stats")
def _stats(req, q, body):
    return api(req).stats(conn())


@route("GET", r"/api/projects")
def _projects(req, q, body):
    return api(req).list_projects(conn())


@route("POST", r"/api/projects")
def _create_project(req, q, body):
    return api(req).create_project(conn(), body.get("name"), body.get("description", ""), body.get("status", "active"), agent_of(req, body),
                             body.get("repo", ""), body.get("branch", ""))


@route("GET", r"/api/projects/(?P<ref>[^/]+)")
def _project(req, q, body, ref):
    return api(req).get_project(conn(), ref)


@route("PATCH", r"/api/projects/(?P<ref>[^/]+)")
def _update_project(req, q, body, ref):
    return api(req).update_project(conn(), ref, agent_of(req, body), **{k: body.get(k) for k in ("name", "description", "status", "repo", "branch")})


@route("GET", r"/api/agents")
def _agents(req, q, body):
    return api(req).list_agents(conn())


@route("GET", r"/api/agents/(?P<ref>[^/]+)")
def _agent(req, q, body, ref):
    return api(req).get_agent(conn(), ref)


@route("PATCH", r"/api/agents/(?P<ref>[^/]+)")
def _rename_agent(req, q, body, ref):
    return api(req).rename_agent(conn(), ref, body.get("name"))


@route("POST", r"/api/agents/(?P<ref>[^/]+)/heartbeat")
def _heartbeat(req, q, body, ref):
    return api(req).heartbeat(conn(), ref, body.get("status"), body.get("note"), body.get("role"), body.get("task_id"),
                        body.get("model"), body.get("host"), body.get("parent"), body.get("metrics"))


@route("POST", r"/api/agents/(?P<ref>[^/]+)/claim")
def _claim(req, q, body, ref):
    return api(req).claim_next_task(conn(), ref, body.get("project"))


@route("GET", r"/api/hosts")
def _hosts(req, q, body):
    return api(req).list_hosts(conn())


@route("POST", r"/api/hosts/(?P<name>[^/]+)")
def _report_host(req, q, body, name):
    return api(req).report_host(conn(), name, **body)


@route("GET", r"/api/tasks")
def _tasks(req, q, body):
    return api(req).list_tasks(conn(), project=q.get("project"), status=q.get("status"), assignee=q.get("assignee"), limit=q.get("limit", 500))


@route("POST", r"/api/tasks")
def _create_task(req, q, body):
    return api(req).create_task(conn(), body.get("project"), body.get("title"), body.get("description", ""),
                          body.get("priority", "medium"), body.get("assignee"), body.get("status", "todo"),
                          agent_of(req, body), body.get("todos"))


@route("GET", r"/api/tasks/(?P<tid>\d+)")
def _task(req, q, body, tid):
    return api(req).get_task(conn(), tid)


@route("PATCH", r"/api/tasks/(?P<tid>\d+)")
def _update_task(req, q, body, tid):
    return api(req).update_task(conn(), tid, agent_of(req, body), body.get("note"),
                          **{k: body.get(k) for k in ("title", "description", "status", "priority", "assignee") if k in body})


@route("DELETE", r"/api/tasks/(?P<tid>\d+)")
def _delete_task(req, q, body, tid):
    return api(req).delete_task(conn(), tid, agent_of(req, body))


@route("POST", r"/api/tasks/(?P<tid>\d+)/progress")
def _progress(req, q, body, tid):
    return api(req).log_progress(conn(), tid, body.get("message"), agent_of(req, body))


@route("POST", r"/api/tasks/(?P<tid>\d+)/todos")
def _add_todo(req, q, body, tid):
    return api(req).add_todo(conn(), tid, body.get("text"), agent_of(req, body))


@route("PATCH", r"/api/todos/(?P<did>\d+)")
def _set_todo(req, q, body, did):
    return api(req).set_todo(conn(), did, body.get("done"), body.get("text"), agent_of(req, body))


@route("GET", r"/api/whoami")
def _whoami(req, q, body):
    return {k: v for k, v in req.ident.items() if k != "token_id"}


@route("GET", r"/api/rules")
def _rules(req, q, body):
    return api(req).check_rules(conn(), master=q.get("master") or MASTER, quiet_minutes=int(q.get("quiet_minutes") or 30))


# ---------- teams (admin only: the master token, or anyone when auth is off) ----------

def admin(req):
    if req.ident.get("kind") != "admin":
        raise db.Forbidden("only the master token can manage teams")
    return conn()


@route("GET", r"/api/teams")
def _teams(req, q, body):
    return db.list_teams(admin(req))


@route("POST", r"/api/teams")
def _create_team(req, q, body):
    return db.create_team(admin(req), body.get("name"))


@route("GET", r"/api/teams/(?P<ref>[^/]+)")
def _team(req, q, body, ref):
    return db.get_team(admin(req), ref)


@route("POST", r"/api/teams/(?P<ref>[^/]+)/tokens")
def _create_token(req, q, body, ref):
    return db.create_token(admin(req), ref, body.get("label", ""), body.get("agent"))


@route("DELETE", r"/api/tokens/(?P<kid>\d+)")
def _revoke_token(req, q, body, kid):
    return db.revoke_token(admin(req), kid)


@route("POST", r"/api/teams/(?P<ref>[^/]+)/projects/(?P<project>[^/]+)")
def _grant(req, q, body, ref, project):
    return db.grant_project(admin(req), ref, project)


@route("DELETE", r"/api/teams/(?P<ref>[^/]+)/projects/(?P<project>[^/]+)")
def _ungrant(req, q, body, ref, project):
    return db.grant_project(admin(req), ref, project, grant=False)


@route("GET", r"/api/events")
def _events(req, q, body):
    return api(req).list_events(conn(), since=q.get("since"), project=q.get("project"), agent=q.get("agent"), task=q.get("task"), limit=q.get("limit", 100))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("AGENT_DASH_VERBOSE"):
            super().log_message(fmt, *args)

    def _send(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _identity(self):
        """Who is calling: admin (master token), internal (an AGENT_DASH_AGENT_TOKENS entry),
        team (a team token from the database), or None if not allowed in."""
        if not TOKEN and not AGENT_TOKENS:
            return {"kind": "admin"}
        h = self.headers.get("Authorization", "")
        if h.startswith("Bearer "):
            given = h[7:].strip()
            if not given:
                return None
            if TOKEN and hmac.compare_digest(given.encode(), TOKEN.encode()):
                return {"kind": "admin"}
            if any(hmac.compare_digest(given.encode(), t.encode()) for t in AGENT_TOKENS):
                return {"kind": "internal"}
            if given.startswith("tk_"):
                team = db.token_identity(conn(), given)
                if team:
                    return {"kind": "team", **team}
            return None
        if h.startswith("Basic "):  # browsers: master token only
            try:
                given = base64.b64decode(h[6:]).decode().split(":", 1)[1]
            except (ValueError, IndexError):
                return None
            if TOKEN and hmac.compare_digest(given.encode(), TOKEN.encode()):
                return {"kind": "admin"}
        return None

    def _dispatch(self, method):
        url = urlparse(self.path)
        if url.path == "/healthz":
            return self._send(200, {"ok": True})
        if url.path == "/install.sh":
            return self._install_script()
        self.ident = self._identity()
        if not self.ident:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Agent dashboard"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        team = self.ident["kind"] == "team"
        if url.path == "/mcp":
            return self._mcp(method)
        if method == "GET" and url.path == "/api/stream" and STREAMING:
            return self._send(403, {"error": "team tokens can't use the live stream"}) if team else self._stream()
        if method == "GET" and not url.path.startswith("/api/"):
            if team:
                return self._send(403, {"error": "team tokens can't open the dashboard"})
            return self._static(url.path)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        body = {}
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, {"error": "invalid JSON body"})
        for m, rx, fn in ROUTES:
            match = rx.match(url.path)
            if m == method and match:
                try:
                    args = {k: unquote(v) for k, v in match.groupdict().items()}
                    return self._send(200, fn(self, q, body, **args))
                except db.NotFound as e:
                    return self._send(404, {"error": str(e)})
                except db.Invalid as e:
                    return self._send(400, {"error": str(e)})
                except db.Forbidden as e:
                    return self._send(403, {"error": str(e)})
                except Exception as e:  # noqa: BLE001
                    return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        self._send(404, {"error": "not found"})

    def _mcp(self, method):
        """Remote MCP (streamable HTTP, stateless): any MCP client connects with this URL and a
        Bearer token, no install. The agent is the token's bound agent or the X-Agent header;
        X-Agent-Role (main or master) picks the tool set, as AGENT_TRACKER_ROLE does locally."""
        if method != "POST":
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")
        except json.JSONDecodeError:
            msg = None
        if not isinstance(msg, dict):
            return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "send one JSON-RPC object"}})
        if msg.get("id") is None:  # notification (e.g. notifications/initialized)
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        name = self.ident.get("agent") or self.headers.get("X-Agent")
        role = (self.headers.get("X-Agent-Role") or "main").strip().lower()
        role = role if role in ("main", "master") else "main"

        def me():
            if not name:
                raise db.Invalid("no agent name: add an X-Agent header to this MCP server's config, or use an agent-bound token")
            return name
        toolset = mcp_server.tools(api(self), me, role, remote=True)
        if self.ident["kind"] == "team":
            toolset.pop("tracker_report_host", None)
        hint = (f"You are on the agent dashboard as {name!r}." if name else
                "Tracker writes need a name: set an X-Agent header in this MCP server's config.")
        hint += (" You are the master agent: run tracker_check_rules regularly." if role == "master"
                 else " Record every task and todo in the tracker as you work.")
        try:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": mcp_server.handle(conn(), msg, toolset, hint)}
        except LookupError as e:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": f"method not found: {e}"}}
        self._send(200, reply)

    def _install_script(self):
        """Public: holds no secrets; it asks for the password on the user's machine."""
        proto = self.headers.get("X-Forwarded-Proto", "http")
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "localhost")
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "install.sh")) as f:
            body = f.read().replace("__DASH_URL__", f"{proto}://{host}")
        self._send(200, body.encode(), "text/x-shellscript; charset=utf-8")

    def _static(self, path):
        path = "/index.html" if path in ("", "/") else path
        full = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        if not full.startswith(STATIC) or not os.path.isfile(full):
            return self._send(404, b"not found", "text/plain")
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}.get(
            os.path.splitext(full)[1], "application/octet-stream")
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    def _stream(self):
        """Server-sent events: polls the events table, so it also picks up
        writes from agents that bypass the API and write to SQLite directly."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        c = db.connect()
        last = db.last_event_id(c)
        try:
            self.wfile.write(f"event: hello\ndata: {json.dumps({'last_event_id': last})}\n\n".encode())
            self.wfile.flush()
            idle = 0
            while True:
                time.sleep(1)
                new = list(reversed(db.list_events(c, since=last, limit=200)))
                if new:
                    last = new[-1]["id"]
                    self.wfile.write(f"event: events\ndata: {json.dumps(new, default=str)}\n\n".encode())
                    idle = 0
                else:
                    idle += 1
                    if idle >= 15:
                        self.wfile.write(b": ping\n\n")
                        idle = 0
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            c.close()

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")


def main():
    ap = argparse.ArgumentParser(description="Agent & project dashboard")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8787)))
    ap.add_argument("--db", default=None, help="SQLite file (default: data/agents.db or $AGENT_DB)")
    args = ap.parse_args()
    if args.db:
        db.DB_PATH = os.path.abspath(args.db)
    c = db.connect()
    db.init(c)
    c.close()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    if args.host not in ("127.0.0.1", "localhost") and not TOKEN and not AGENT_TOKENS:
        print("WARNING: listening publicly without AGENT_DASH_TOKEN; anyone can read and write.")
    store = os.environ.get("TURSO_DATABASE_URL") or db.DB_PATH
    print(f"Agent dashboard on http://{args.host}:{args.port}  (db: {store}, auth: {'on' if TOKEN or AGENT_TOKENS else 'off'})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
