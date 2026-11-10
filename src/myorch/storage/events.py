"""The event log: an append-only SQLite table every run writes its events to.

Written by the worker (run events; and in a table of its own, the live output of running steps), read by channels, `myorch watch` and
the console. One machine, one file: myorch-data/events.db. Readers keep the last `seq` they
handled and ask for what came after, so a reader that was down catches up without gaps.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class LoggedEvent(BaseModel):
    seq: int
    run_id: str
    key: str
    kind: str
    step: str = ""
    at: float
    data: dict[str, Any]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  seq    INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  key    TEXT NOT NULL,
  kind   TEXT NOT NULL,
  step   TEXT NOT NULL DEFAULT '',
  at     REAL NOT NULL,
  data   TEXT NOT NULL,
  UNIQUE (run_id, key)
);
CREATE INDEX IF NOT EXISTS events_run ON events (run_id, seq);
CREATE TABLE IF NOT EXISTS output (
  seq    INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  step   TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  turn   INTEGER NOT NULL,
  at     REAL NOT NULL,
  text   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS output_step ON output (run_id, step, seq);
"""


class OutputChunk(BaseModel):
    """Lines a running step wrote to its log, as the worker read them (see EventLog.add_output)."""

    seq: int
    run_id: str
    step: str  # instance id
    attempt: int
    turn: int
    at: float
    text: str


class EventLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(_SCHEMA)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")  # readers do not block the writer
        return db

    def append(self, run_id: str, events: Iterable[tuple[str, str, str, dict[str, Any]]]) -> int:
        """Add (key, kind, step, data) events; keys a run already has are skipped. Returns how many were new."""
        now = time.time()
        with self._db() as db:
            cur = db.executemany(
                "INSERT OR IGNORE INTO events (run_id, key, kind, step, at, data) VALUES (?, ?, ?, ?, ?, ?)",
                [(run_id, key, kind, step, now, json.dumps(data, ensure_ascii=False)) for key, kind, step, data in events],
            )
            return cur.rowcount

    def read(self, after: int = 0, run_id: str | None = None, limit: int = 500) -> list[LoggedEvent]:
        q = "SELECT seq, run_id, key, kind, step, at, data FROM events WHERE seq > ?"
        args: list[Any] = [after]
        if run_id is not None:
            q += " AND run_id = ?"
            args.append(run_id)
        with self._db() as db:
            rows = db.execute(q + " ORDER BY seq LIMIT ?", [*args, limit]).fetchall()
        return [LoggedEvent(seq=r[0], run_id=r[1], key=r[2], kind=r[3], step=r[4], at=r[5], data=json.loads(r[6]))
                for r in rows]

    # ---- live output -------------------------------------------------------------------
    # A separate table: a step can write thousands of lines, and the readers of `events` (run lists,
    # delivery, the watchdog) scan that table often. Only the console's live view reads this one.

    def add_output(self, run_id: str, step: str, attempt: int, turn: int, text: str) -> None:
        with self._db() as db:
            db.execute("INSERT INTO output (run_id, step, attempt, turn, at, text) VALUES (?, ?, ?, ?, ?, ?)",
                       (run_id, step, attempt, turn, time.time(), text))

    def read_output(self, run_id: str, step: str | None = None, after: int = 0, limit: int = 500) -> list[OutputChunk]:
        q = "SELECT seq, run_id, step, attempt, turn, at, text FROM output WHERE run_id = ? AND seq > ?"
        args: list[Any] = [run_id, after]
        if step is not None:
            q += " AND step = ?"
            args.append(step)
        with self._db() as db:
            rows = db.execute(q + " ORDER BY seq LIMIT ?", [*args, limit]).fetchall()
        return [OutputChunk(seq=r[0], run_id=r[1], step=r[2], attempt=r[3], turn=r[4], at=r[5], text=r[6]) for r in rows]

    def prune_output(self, older_than: float) -> int:
        """Drop live output older than `older_than` seconds. The step's log file keeps everything."""
        with self._db() as db:
            return db.execute("DELETE FROM output WHERE at < ?", (time.time() - older_than,)).rowcount

    def last_seq(self) -> int:
        with self._db() as db:
            return db.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]


def in_container() -> bool:
    return Path("/.dockerenv").exists()


class HttpEventLog:
    """Read-only event log through the console (GET /channel/events.json), for processes on the host.

    events.db is SQLite in WAL mode on a host folder that the worker and the console containers also
    open. Their locks and shared memory do not reach the host: when a host process closed its
    connection it believed it was the last one, removed events.db-shm and emptied the WAL, and the
    containers then failed every query with "disk I/O error" until they were restarted (2026-11-09).
    So the host never opens the file; it asks the console."""

    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        self.path = None

    def read(self, after: int = 0, run_id: str | None = None, limit: int = 500) -> list[LoggedEvent]:
        import json
        import urllib.error
        import urllib.parse
        import urllib.request

        q = {"after": str(after), "limit": str(min(limit, 2000))}
        if run_id:
            q["run"] = run_id
        try:
            with urllib.request.urlopen(f"{self.url}/channel/events.json?{urllib.parse.urlencode(q)}", timeout=30) as r:
                return [LoggedEvent.model_validate(e) for e in json.loads(r.read())]
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"读不到事件日志：控制台（{self.url}）没有响应（{e}）。宿主机上的命令通过控制台读事件，"
                               "不直接打开 events.db；先启动控制台，或者在容器里执行这个命令") from e


def open_event_log(data_root: Path, url: str | None = None):
    """The event log for this process: the file itself inside a container (the worker, the console),
    the console's read-only API on the host (see HttpEventLog for why)."""
    import os

    if in_container() or os.environ.get("MYORCH_EVENTS_DIRECT") == "1":
        return EventLog(data_root / "events.db")
    return HttpEventLog(url or os.environ.get("MYORCH_URL", "http://127.0.0.1:8800"))
