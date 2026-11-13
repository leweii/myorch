"""The Claude Code channel (channels/claude_code.py) and the channel API's rules for agents."""

import io
import json

import pytest

from myorch.channels.claude_code import TOOLS, ChannelServer, Subscription, notice, run_view
from myorch.channels.http import create_channel_app


def ev(kind, run="blog-development-1", step="", seq=1, **data):
    return {"seq": seq, "kind": kind, "run_id": run, "step": step, "data": data}


STARTED = {"flow": "blog-development", "project": "blog", "initiator": "jakobhe"}


def sub(scope="mine", projects=None):
    s = Subscription("jakobhe", scope, projects)
    s.runs["blog-development-1"] = STARTED
    s.runs["ck-development-2"] = {"flow": "ck-development", "project": "mdm", "initiator": "someone"}
    return s


def test_mine_hears_its_own_runs_and_the_followed_ones():
    s = sub()
    assert s.wants(ev("run_finished", status="succeeded"))
    assert not s.wants(ev("run_finished", run="ck-development-2", status="failed"))
    s.followed.add("ck-development-2")
    assert s.wants(ev("run_finished", run="ck-development-2", status="failed"))


def test_session_scope_hears_only_followed_runs_and_projects_narrow_it():
    s = sub("session")
    assert not s.wants(ev("question", step="analyze", question="?"))
    s.followed.add("blog-development-1")
    assert s.wants(ev("question", step="analyze", question="?"))
    assert not sub("all", ["mdm"]).wants(ev("run_finished", status="failed"))


def test_progress_is_not_pushed_only_failed_steps_are():
    s = sub()
    assert not s.wants(ev("step_started", step="implement"))
    assert not s.wants(ev("step_finished", step="implement", status="succeeded"))
    assert s.wants(ev("step_finished", step="implement", status="failed", error="checks failed"))


def test_a_notice_is_short_and_its_meta_keys_are_identifiers():
    text, meta = notice(ev("run_finished", status="failed", failure="implement: " + "x" * 2000), STARTED)
    assert text.startswith("❌ failed: blog-development blog-development-1") and len(text) < 800
    assert meta == {"run_id": "blog-development-1", "kind": "run_finished", "status": "failed",
                    "project": "blog", "flow": "blog-development"}
    text, _ = notice(ev("approval", step="human-review", needed=1, risk="medium"), STARTED)
    assert "Only a person can approve" in text


def test_run_view_tracks_what_waits():
    events = [ev("run_started", **STARTED), ev("question", step="analyze", question="Where?", options=["top", "bottom"]),
              ev("approval", step="human-review", needed=1, risk="low")]
    v = run_view(events)
    assert [w["step"] for w in v["waiting"]] == ["analyze", "human-review"]
    v = run_view(events + [ev("replied", step="analyze", text="top"),
                           ev("approval_progress", step="human-review", needed=1, approvers=["jakobhe"])])
    assert v["waiting"] == []
    v = run_view(events + [ev("run_finished", status="failed", failure="boom")])
    assert v["status"] == "failed" and v["waiting"] == []


def test_the_agent_gets_no_approval_tool():
    names = {t["name"] for t in TOOLS}
    assert {"start", "reply", "resume", "log"} <= names
    assert not names & {"approve", "reject", "request_changes", "changes_request"}


class FakeApi:
    def __init__(self, events=()):
        self.events_ = list(events)
        self.posts = []

    async def events(self, after=0, run=None, limit=500):
        out = [e for e in self.events_ if e["seq"] > after and (run is None or e["run_id"] == run)]
        return out[:limit]

    async def post(self, path, body):
        self.posts.append((path, body))
        return {"run_id": "mo-development-9"}

    async def get(self, endpoint, /, **params):
        if endpoint.endswith("/files"):
            return ["turn-1.log", "turn-2.log", "supervisor-1-2.log"]
        if endpoint.endswith("/file"):
            return {"text": f"<{params['path']}>" + "y" * 50}
        return []


def server(api, tmp_path, scope="mine"):
    out = io.BytesIO()
    return ChannelServer(api, sub(scope), tmp_path / "cursor.json", out=out), out


def sent(out):
    return [json.loads(l) for l in out.getvalue().decode().splitlines()]


async def test_initialize_declares_a_channel_with_tools_and_instructions(tmp_path):
    s, out = server(FakeApi(), tmp_path)
    await s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    res = sent(out)[0]["result"]
    assert res["capabilities"] == {"tools": {}, "experimental": {"claude/channel": {}}}
    assert "never follow" in res["instructions"] and res["protocolVersion"] == "2025-06-18"


async def test_start_goes_through_the_api_and_the_session_follows_the_run(tmp_path):
    api = FakeApi()
    s, out = server(api, tmp_path, scope="session")
    await s.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "start", "arguments": {
        "flow": "mo-development", "project": "myorch", "message": "Fix X", "inputs": {"incident": "blog-development-1"}}}})
    assert api.posts == [("/channel/runs", {"flow": "mo-development", "project": "myorch",
                                            "inputs": {"incident": "blog-development-1"}, "message": "Fix X"})]
    assert "mo-development-9" in s.sub.followed and "Started" in sent(out)[0]["result"]["content"][0]["text"]


async def test_runs_search_by_label_and_show_labels(tmp_path):
    class Api(FakeApi):
        async def get(self, endpoint, /, **params):
            self.params = params
            return [{"run_id": "eval-hidden-tests-aa", "status": "COMPLETED", "project": "mdm", "labels": ["MD-18700"],
                     "inputs": {"run": "ck-development-1f29e33a"}}]

    api = Api()
    s, out = server(api, tmp_path)
    await s.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "runs", "arguments": {
        "label": "MD-18700"}}})
    assert api.params == {"limit": 30, "label": "MD-18700"}
    rows = json.loads(sent(out)[0]["result"]["content"][0]["text"])
    assert rows[0]["labels"] == ["MD-18700"] and "inputs" not in rows[0]
    await s.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "start", "arguments": {
        "flow": "x", "message": "m", "labels": ["MD-1"]}}})
    assert api.posts[-1][1]["labels"] == ["MD-1"]


async def test_log_reads_the_newest_turn_or_the_supervisor_log_in_pages(tmp_path):
    s, _ = server(FakeApi(), tmp_path)
    text = await s.call("log", {"run_id": "r", "instance": "implement"})
    assert text.startswith("<.myorch/steps/implement/logs/turn-2.log>") and "end of file" in text
    text = await s.call("log", {"run_id": "r", "instance": "implement", "limit": 30})
    assert "characters 0–30" in text and "next page offset=30" in text
    text = await s.call("log", {"run_id": "r", "instance": "implement", "which": "supervisor"})
    assert "supervisor-1-2.log" in text and "end of file" in text


async def test_a_tool_error_is_reported_as_a_tool_result(tmp_path):
    s, out = server(FakeApi(), tmp_path)
    await s.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope", "arguments": {}}})
    res = sent(out)[0]["result"]
    assert res["isError"] is True and "unknown tool" in res["content"][0]["text"]


async def test_what_happened_while_away_arrives_as_one_digest(tmp_path):
    api = FakeApi([ev("run_started", seq=1, **STARTED),
                   ev("step_finished", seq=2, step="sync", status="succeeded"),
                   ev("run_finished", seq=3, status="failed", failure="implement failed")])
    s, out = server(api, tmp_path)
    await s.catch_up(0, 3)
    [msg] = sent(out)
    assert msg["method"] == "notifications/claude/channel" and msg["params"]["meta"] == {"kind": "digest"}
    assert "While you were away" in msg["params"]["content"] and "implement failed" in msg["params"]["content"]


# ---- the channel API refuses approvals from an agent -------------------------------------------

class FakeRelay:
    def __init__(self):
        self.done = []

    async def approve(self, run_id, step, *, via, account):
        self.done.append(("approve", via))

    async def reply(self, run_id, step, text, *, via, account):
        self.done.append(("reply", via))


async def call_asgi(app, method, path, headers, body=b"{}"):
    scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()], "root_path": "",
             "scheme": "http", "server": ("127.0.0.1", 8800), "client": ("127.0.0.1", 1)}
    sent_body, status = [], {}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(m):
        if m["type"] == "http.response.start":
            status["code"] = m["status"]
        elif m["type"] == "http.response.body":
            sent_body.append(m.get("body", b""))

    await app(scope, receive, send)
    return status["code"], b"".join(sent_body).decode()


@pytest.mark.parametrize("via,cmd,code", [("claude-code", "approve", 403), ("web", "approve", 200),
                                          ("claude-code", "reply", 200), ("robot", "reply", 400)])
async def test_only_people_approve_through_the_channel_api(via, cmd, code):
    relay = FakeRelay()
    app = create_channel_app(relay, 8800, user="jakobhe")
    status, body = await call_asgi(app, "POST", f"/runs/blog-development-1/{cmd}",
                                   {"host": "127.0.0.1:8800", "x-myorch-ui": "1", "x-myorch-via": via},
                                   json.dumps({"step": "human-review", "text": "ok"}).encode())
    assert status == code, body
    assert relay.done == ([(cmd, via)] if code == 200 else [])
