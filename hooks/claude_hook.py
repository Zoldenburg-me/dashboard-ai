#!/usr/bin/env python3
"""Claude Code hook: reporting that costs no model tokens. install.sh registers it for
the Stop and SessionEnd events; it reads the hook's JSON on stdin.

  Stop        adds up this session's token usage from its transcript and stores it for
              the folder. In a Main (or master) session it then reports the folder's
              total over the last 24 hours - the Main plus all its sub-sessions - and
              the current context size as the agent's metrics.
  SessionEnd  marks the Main agent idle, so a closed session doesn't show as working.

Sub-sessions (AGENT_TRACKER_ROLE=sub) only store their usage; the Main's next Stop
reports it. The agent name is the one the MCP server remembers for the folder
(or AGENT_NAME). Errors never reach Claude: the hook always exits 0.
"""
import hashlib
import json
import os
import sys
import time

CONF = os.path.expanduser(os.environ.get("AGENT_TRACKER_HOME", "~/.config/agent-tracker"))
NAMES_FILE = os.environ.get("AGENT_TRACKER_NAMES", os.path.join(CONF, "names.json"))
WINDOW = 86400


def load_env():
    """install.sh keeps the dashboard URL and token here, since hooks don't get the MCP server's env."""
    try:
        with open(os.path.join(CONF, "env")) as f:
            for line in f:
                k, sep, v = line.strip().partition("=")
                if sep and k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


def transcript_usage(path):
    """Token totals for one session. A message can appear on several lines (one per
    content block); the last line carries its final usage, so keep one entry per id."""
    per_msg, last, model = {}, None, None
    with open(path) as f:
        for line in f:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            msg = entry.get("message") if entry.get("type") == "assistant" else None
            usage = msg.get("usage") if isinstance(msg, dict) else None
            if not usage:
                continue
            per_msg[msg.get("id") or len(per_msg)] = usage
            last, model = usage, msg.get("model") or model
    total = {k: sum(int(u.get(k) or 0) for u in per_msg.values())
             for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
    context = sum(int(last.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")) if last else 0
    return total, context, model


def record(folder, session, total):
    """Store this session's totals in the folder's usage file; return all sessions from the last 24h."""
    import fcntl
    path = os.path.join(CONF, "usage", hashlib.sha1(folder.encode()).hexdigest()[:16] + ".json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(path) as f:
                sessions = json.load(f)
        except (OSError, ValueError):
            sessions = {}
        now = time.time()
        sessions[session] = {**total, "updated": now}
        sessions = {k: v for k, v in sessions.items() if now - v.get("updated", 0) <= WINDOW}
        with open(path + ".tmp", "w") as f:
            json.dump(sessions, f)
        os.replace(path + ".tmp", path)
    return sessions


def agent_name(folder):
    if os.environ.get("AGENT_NAME"):
        return os.environ["AGENT_NAME"]
    try:
        with open(NAMES_FILE) as f:
            return json.load(f).get(folder)
    except (OSError, ValueError):
        return None


def main():
    event = json.load(sys.stdin)
    load_env()
    role = os.environ.get("AGENT_TRACKER_ROLE", "main").strip().lower()
    folder = os.path.abspath(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd())
    kind = event.get("hook_event_name")
    metrics, model = None, None
    if kind == "Stop" and event.get("transcript_path") and os.path.isfile(event["transcript_path"]):
        total, context, model = transcript_usage(event["transcript_path"])
        sessions = record(folder, event.get("session_id") or "unknown", total)
        metrics = {"tokens_in_24h": sum(s["input_tokens"] for s in sessions.values()),
                   "tokens_out_24h": sum(s["output_tokens"] for s in sessions.values()),
                   "cache_read_24h": sum(s["cache_read_input_tokens"] for s in sessions.values()),
                   "cache_write_24h": sum(s["cache_creation_input_tokens"] for s in sessions.values()),
                   "sessions_24h": len(sessions)}
        if context:
            metrics["context_used"] = context
    if role == "sub":
        return
    name = agent_name(folder)
    if not name or kind not in ("Stop", "SessionEnd"):
        return
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if os.environ.get("AGENT_DASH_URL"):
        import remote as db
    else:
        import db
    c = db.connect()
    db.init(c)
    if kind == "SessionEnd":
        db.heartbeat(c, name, "idle", "session ended")
    elif metrics:
        db.heartbeat(c, name, model=model, metrics=metrics)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001  (a hook must never break the session)
        if os.environ.get("AGENT_TRACKER_DEBUG"):
            print(f"agent-tracker hook: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(0)
