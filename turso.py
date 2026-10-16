"""Tiny Turso / libSQL client over the Hrana HTTP protocol (standard library only).

Exposes just enough of the sqlite3 connection API for db.py: execute() returning
a cursor with fetchone/fetchall/iteration/lastrowid, rows that work with
row["col"], row[0] and dict(row), and sqlite3 exception types.

Statements run on one server-side stream (tracked by its baton), so
BEGIN IMMEDIATE ... COMMIT behaves like a local transaction.
"""
import base64
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request


class Row(tuple):
    def __new__(cls, cols, values):
        r = super().__new__(cls, values)
        r._cols = cols
        return r

    def keys(self):
        return list(self._cols)

    def __getitem__(self, key):
        if isinstance(key, str):
            return tuple.__getitem__(self, self._cols.index(key))
        return tuple.__getitem__(self, key)


class Cursor:
    def __init__(self, result):
        cols = [c["name"] for c in result.get("cols", [])]
        self._rows = [Row(cols, [_decode(v) for v in row]) for row in result.get("rows", [])]
        rid = result.get("last_insert_rowid")
        self.lastrowid = int(rid) if rid is not None else None
        self.rowcount = result.get("affected_row_count", -1)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


def _encode(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    if isinstance(v, (bytes, bytearray)):
        return {"type": "blob", "base64": base64.b64encode(v).decode().rstrip("=")}
    return {"type": "text", "value": str(v)}


def _decode(v):
    t = v["type"]
    if t == "null":
        return None
    if t == "integer":
        return int(v["value"])
    if t == "float":
        return float(v["value"])
    if t == "blob":
        b = v["base64"]
        return base64.b64decode(b + "=" * (-len(b) % 4))
    return v["value"]


class Connection:
    row_factory = None

    def __init__(self, url, token=""):
        if url.startswith("libsql://"):
            url = "https://" + url[len("libsql://"):]
        self.url = url.rstrip("/")
        self.token = token
        self.baton = None
        self.in_tx = False

    def _post(self, requests):
        body = {"baton": self.baton, "requests": requests}
        req = urllib.request.Request(self.url + "/v2/pipeline", data=json.dumps(body).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            raise sqlite3.OperationalError(f"turso HTTP {e.code}: {text[:300]}") from None
        self.baton = data.get("baton")
        if data.get("base_url"):
            self.url = data["base_url"].rstrip("/")
        return data["results"]

    def _run(self, sql, args):
        stmt = {"sql": sql, "args": [_encode(a) for a in args]}
        reqs = [{"type": "execute", "stmt": stmt}]
        if self.baton is None:
            # New server stream: enable FK cascades for this connection first.
            reqs.insert(0, {"type": "execute", "stmt": {"sql": "PRAGMA foreign_keys=ON"}})
        res = self._post(reqs)[-1]
        if res["type"] == "error":
            err = res["error"]
            msg = err.get("message", "")
            code = err.get("code", "") or ""
            if "CONSTRAINT" in code or "constraint failed" in msg:
                raise sqlite3.IntegrityError(msg)
            raise sqlite3.OperationalError(f"{code}: {msg}")
        return res["response"]["result"]

    def execute(self, sql, params=()):
        head = sql.lstrip()[:16].upper()
        if head.startswith("PRAGMA") and any(k in sql.lower() for k in ("journal_mode", "synchronous", "busy_timeout")):
            return Cursor({})  # local-file tuning pragmas don't apply to a remote server
        if head.startswith("BEGIN"):
            return self._begin(sql)
        try:
            result = self._run(sql, params)
        except sqlite3.OperationalError as e:
            # An idle stream may expire between serverless invocations. Outside a
            # transaction it is safe to open a new stream and retry once.
            expired = "STREAM_EXPIRED" in str(e) or "baton" in str(e).lower()
            if self.in_tx or not expired:
                raise
            self.baton = None
            result = self._run(sql, params)
        if head.startswith(("COMMIT", "ROLLBACK", "END")):
            self.in_tx = False
        return Cursor(result)

    def _begin(self, sql):
        """Concurrent writers queue here: retry while another stream holds the write lock."""
        delay, deadline = 0.02, time.time() + 30
        while True:
            try:
                self._run(sql, ())
                self.in_tx = True
                return Cursor({})
            except sqlite3.OperationalError as e:
                s = str(e)
                if ("BUSY" in s or "locked" in s) and time.time() < deadline:
                    time.sleep(delay)
                    delay = min(delay * 2, 0.5)
                    continue
                if "STREAM_EXPIRED" in s or "baton" in s.lower():
                    self.baton = None
                    continue
                raise

    def close(self):
        if self.baton is not None:
            try:
                self._post([{"type": "close"}])
            except Exception:  # noqa: BLE001
                pass
            self.baton = None


def connect():
    return Connection(os.environ["TURSO_DATABASE_URL"], os.environ.get("TURSO_AUTH_TOKEN", ""))
