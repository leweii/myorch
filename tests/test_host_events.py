"""Processes on the host read the event log through the console, never the SQLite file the containers
use (storage/events.py HttpEventLog: a host connection once removed events.db-shm under them)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from myorch.storage import events as ev


def test_the_host_gets_the_console_reader(monkeypatch, tmp_path):
    monkeypatch.setattr(ev, "in_container", lambda: False)
    monkeypatch.delenv("MYORCH_EVENTS_DIRECT", raising=False)
    assert isinstance(ev.open_event_log(tmp_path), ev.HttpEventLog)
    assert not (tmp_path / "events.db").exists()  # the file was never opened
    monkeypatch.setattr(ev, "in_container", lambda: True)
    assert isinstance(ev.open_event_log(tmp_path), ev.EventLog)


def test_the_console_reader_returns_logged_events():
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.update(parse_qs(urlparse(self.path).query))
            body = json.dumps([{"seq": 7, "run_id": "ck-development-11111111", "key": "r1", "kind": "replied", "step": "develop",
                                "at": 1.0, "data": {"text": "1A"}}]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        log = ev.HttpEventLog(f"http://127.0.0.1:{srv.server_port}")
        [e] = log.read(run_id="ck-development-11111111", limit=5000)
        assert (e.seq, e.kind, e.data["text"]) == (7, "replied", "1A")
        assert seen["run"] == ["ck-development-11111111"] and seen["limit"] == ["2000"]
    finally:
        srv.shutdown()
