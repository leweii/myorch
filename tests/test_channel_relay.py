import asyncio
from pathlib import Path

import pytest
import json

from myorch.channels import relay as relay_mod
from myorch.channels.http import create_channel_app
from myorch.channels.model import ChannelsConfig
from myorch.channels.relay import Relay
from myorch.storage.events import EventLog

FLOW = """
flow: ask
inputs:
  task: {type: message, from_message: first_line}
steps:
  - {id: a, harness: script, profile: p, prompt: "echo {{inputs.task}}"}
"""


class FakeStore:
    def __init__(self):
        self.files = {"runs/r1/docs/requirements.md": "# 需求"}

    def read_file(self, rel):
        return self.files.get(rel)

    def list_files(self, base, max_depth=3):
        return [k.removeprefix(base + "/") for k in self.files if k.startswith(base + "/")]

    def read_manifest(self, rid, iid):
        return None

    def list_outputs(self, rid, iid):
        return ["manifest.json"]

    def read_log_tail(self, rid, iid, max_bytes=4000):
        return "log"

    def review_bundle(self, rid, base):
        return {"git/diff": f"diff against {base}"}


@pytest.fixture
def calls(monkeypatch):
    seen = []

    async def record(name, *a, **kw):
        seen.append((name, a[1:], kw))  # drop the client
        return "ask-1" if name == "start_run" else None

    for name in ("start_run", "reply", "approve", "reject", "request_changes", "cancel"):
        monkeypatch.setattr(relay_mod.tc, name, lambda *a, _n=name, **kw: record(_n, *a, **kw))

    async def list_runs(client, limit=20):
        return [("r1", "RUNNING"), ("r2", "TERMINATED")]

    monkeypatch.setattr(relay_mod.tc, "list_runs", list_runs)
    return seen


@pytest.fixture
def relay(tmp_path: Path):
    (tmp_path / "flows").mkdir()
    (tmp_path / "flows" / "ask.yaml").write_text(FLOW)
    log = EventLog(tmp_path / "events.db")
    log.append("r1", [("started", "run_started", "", {"flow": "ask", "project": "blog"}),
                      ("ask:a:1", "question", "a", {"question": "?", "options": []})])
    cfg = ChannelsConfig(identities={"jakobhe": {"github": "leweii"}})
    return Relay(object(), FakeStore(), log, tmp_path, tmp_path / "kb", cfg)


def test_person_comes_from_identities(relay):
    assert relay.person("web", "jakobhe") == "jakobhe"
    assert relay.person("github", "leweii") == "jakobhe"
    assert relay.person("github", "stranger") == "stranger"


async def test_commands_are_forwarded_with_the_person(relay, calls):
    await relay.approve("r1", "human-review", via="github", account="leweii")
    await relay.request_changes("r1", "human-review", "改一下", via="web", account="jakobhe")
    await relay.reply("r1", "a", "B", via="web", account="jakobhe")
    assert calls[0] == ("approve", ("r1", "human-review", "jakobhe"), {})
    assert calls[1] == ("request_changes", ("r1", "human-review", "jakobhe", "改一下"), {})
    with pytest.raises(ValueError):
        await relay.reply("r1", "a", "  ", via="web", account="jakobhe")
    with pytest.raises(ValueError):
        await relay.cancel("../etc", via="web", account="jakobhe")


async def test_start_saves_the_request_and_fills_task(relay, calls):
    rid = await relay.start("ask", None, {}, "加一个按钮\n细节在这里", via="web", account="jakobhe")
    assert rid == "ask-1"
    name, args, kw = calls[0]
    assert name == "start_run" and args[1] == {"task": "加一个按钮"} and args[2] == "jakobhe"
    assert "细节在这里" in kw["files"]["docs/external/request.md"] and kw["source"] == "web"
    with pytest.raises(ValueError):
        await relay.start("../x", None, {}, "", via="web", account="jakobhe")


async def test_runs_take_the_status_from_the_engine(relay, calls):
    runs = await relay.runs()
    assert runs[0] == {"run_id": "r1", "status": "RUNNING", "flow": "ask", "project": "blog", "labels": []}
    assert runs[1] == {"run_id": "r2", "status": "TERMINATED", "labels": []}  # terminated from outside: no events


TICKET_FLOW = """
flow: dev
inputs:
  ticket: {type: ticket, prefix: MD-}
  from_run: {type: run}
  task: {type: message}
steps:
  - {id: a, harness: script, profile: p, prompt: "echo"}
"""


async def test_runs_carry_labels_and_a_search_looks_past_the_limit(relay, calls, monkeypatch, tmp_path):
    (tmp_path / "flows" / "dev.yaml").write_text(TICKET_FLOW)
    log = relay.events_log
    # An old development run: run_started has inputs but no labels (before labels existed).
    log.append("dev-1f29e33a", [("started", "run_started", "", {"flow": "dev", "inputs": {"ticket": "18700"}}),
                                ("finished", "run_finished", "", {"status": "succeeded"})])
    # A helper run started from the CLI names it by id; another lists tickets in a string.
    log.append("eval-hidden-tests-aa", [("started", "run_started", "", {
        "flow": "eval-hidden-tests", "inputs": {"run": "dev-1f29e33a", "base": "abc"}})])
    log.append("eval-fetch-tickets-bb", [("started", "run_started", "", {
        "flow": "eval-fetch-tickets", "inputs": {"tickets": "MD-1,md-2, MD-18700"}, "labels": ["batch2"]})])
    seen = []

    async def list_runs(client, limit=20):
        seen.append(limit)
        live = [("eval-fetch-tickets-bb", "COMPLETED"), ("eval-hidden-tests-aa", "COMPLETED"), ("r1", "RUNNING")]
        return live[:limit]  # dev-1f29e33a: older than Temporal's retention

    monkeypatch.setattr(relay_mod.tc, "list_runs", list_runs)
    rows = {r["run_id"]: r for r in await relay.runs(limit=10)}
    assert rows["dev-1f29e33a"]["status"] == "COMPLETED"  # gone from Temporal: listed from the event log
    assert [r["run_id"] for r in await relay.runs(limit=3)] == ["eval-fetch-tickets-bb", "eval-hidden-tests-aa", "r1"]
    assert rows["eval-hidden-tests-aa"]["labels"] == ["MD-18700"]  # inherited from the run it names
    assert rows["eval-fetch-tickets-bb"]["labels"] == ["batch2", "MD-1", "MD-18700"]  # md-2: not upper case

    found = await relay.runs(limit=10, label="md-18700")
    assert [r["run_id"] for r in found] == ["eval-fetch-tickets-bb", "eval-hidden-tests-aa", "dev-1f29e33a"]
    assert found[-1]["status"] == "COMPLETED" and found[-1]["labels"] == ["MD-18700"]  # from its run_finished
    assert seen[-1] > 10  # the engine statuses of every run, not only the newest `limit`
    assert [r["run_id"] for r in await relay.runs(limit=1, label="18700")] == ["eval-fetch-tickets-bb"]
    assert [r["run_id"] for r in await relay.runs(label="1f29e33a")] == ["dev-1f29e33a"]  # part of the run id
    assert await relay.runs(label="MD-9") == []
    assert relay.run_labels("eval-hidden-tests-aa") == ["MD-18700"]
    assert relay.run_labels("nope") == []


async def test_start_adds_the_labels_of_the_run_it_continues(relay, calls, tmp_path):
    (tmp_path / "flows" / "dev.yaml").write_text(TICKET_FLOW)
    relay.events_log.append("dev-1f29e33a", [("started", "run_started", "", {"flow": "dev", "inputs": {"ticket": "18700"}})])
    relay.events_log.append("dev-1f29e33a", [("finished", "run_finished", "", {"status": "succeeded"})])
    await relay.start("ask", None, {"from": "dev-1f29e33a"}, "看一下", via="claude-code", account="jakobhe",
                      labels=["eval"])
    assert calls[0][2]["labels"] == ["eval", "MD-18700"]


async def test_follow_continues_after_the_last_seq(relay):
    first = relay.events()
    assert [e.key for e in first] == ["started", "ask:a:1"]
    it = relay.follow(after=first[0].seq).__aiter__()
    assert (await asyncio.wait_for(it.__anext__(), 2)).key == "ask:a:1"
    relay.events_log.append("r1", [("finished", "run_finished", "", {"status": "succeeded"})])
    assert (await asyncio.wait_for(it.__anext__(), 2)).key == "finished"


async def test_content_is_read_from_the_workspace(relay):
    assert await relay.read_file("r1", "docs/requirements.md") == "# 需求"
    assert await relay.list_files("r1") == ["requirements.md"]
    with pytest.raises(ValueError):
        await relay.read_file("r1", "../../etc/passwd")
    assert (await relay.changes("r1", "main"))["git/diff"] == "diff against main"


async def call(app, method, path, host="127.0.0.1:8800", body=None, headers=None, query=""):
    """One request to an ASGI app, without an HTTP client library."""
    sent, out = False, {"status": 0, "body": b""}
    raw = json.dumps(body).encode() if body is not None else b""

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")

    hdrs = [(b"host", host.encode())] + [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    await app({"type": "http", "method": method, "path": path, "raw_path": path.encode(), "root_path": "",
               "query_string": query.encode(), "headers": hdrs, "scheme": "http", "server": ("127.0.0.1", 8800),
               "client": ("127.0.0.1", 1), "http_version": "1.1"}, receive, send)
    out["json"] = json.loads(out["body"]) if out["body"][:1] in (b"{", b"[") else None
    return out


async def test_http_passes_the_label_search_and_start_labels(relay, calls, monkeypatch):
    got = {}

    async def runs(limit=60, label=None):
        got.update(limit=limit, label=label)
        return []

    monkeypatch.setattr(relay, "runs", runs)
    app = create_channel_app(relay, 8800)
    assert (await call(app, "GET", "/runs", query="q=MD-1&limit=5"))["json"] == []
    assert got == {"limit": 5, "label": "MD-1"}
    await call(app, "GET", "/runs", query="label=MD-2")
    assert got["label"] == "MD-2"
    r = await call(app, "POST", "/runs", body={"flow": "ask", "message": "x", "labels": "MD-3, x"},
                   headers={"X-Myorch-UI": "1"})
    assert r["status"] == 200 and calls[-1][2]["labels"] == ["MD-3", " x"]


async def test_http_guards_and_routes(relay, calls):
    app = create_channel_app(relay, 8800, user="jakobhe")
    ui = {"X-Myorch-UI": "1"}
    assert (await call(app, "GET", "/runs", host="evil.example"))["status"] == 403
    assert (await call(app, "POST", "/runs/r1/approve", body={"step": "hr"}))["status"] == 403  # no header
    r = await call(app, "POST", "/runs/r1/approve", body={"step": "hr"}, headers=ui)
    assert r["status"] == 200 and calls[-1] == ("approve", ("r1", "hr", "jakobhe"), {})
    assert (await call(app, "POST", "/runs/r1/nope", body={}, headers=ui))["status"] == 404
    assert [e["key"] for e in (await call(app, "GET", "/events.json"))["json"]] == ["started", "ask:a:1"]
    f = await call(app, "GET", "/runs/r1/file", query="path=docs/requirements.md")
    assert f["json"]["text"] == "# 需求"
    assert (await call(app, "GET", "/runs/r1/changes"))["status"] == 400  # base is required
