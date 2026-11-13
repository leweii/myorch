"""tools/myorch-mcp: only the registered tools of an MCP knowledge server reach the agent."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

WRAPPER = Path(__file__).resolve().parents[1] / "tools" / "myorch-mcp"
TOOLS = ["getJiraIssue", "searchJiraIssuesUsingJql", "createJiraIssue", "editJiraIssue"]


class FakeMcp(BaseHTTPRequestHandler):
    auth: list[str] = []

    def do_POST(self):  # noqa: N802
        FakeMcp.auth.append(self.headers.get("Authorization", ""))
        msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/broken":
            self.send_response(500); self.end_headers(); return
        if "id" not in msg:  # a notification
            self.send_response(202); self.end_headers(); return
        if msg["method"] == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake"}}
        elif msg["method"] == "tools/call":
            key = msg["params"]["arguments"].get("issueIdOrKey")
            result = {"content": [{"type": "text", "text": f"{key}: Sync to ERP"}], "isError": key != "MD-1"}
        elif msg["method"] == "tools/list" and not (msg.get("params") or {}).get("cursor"):
            result = {"tools": [{"name": t} for t in TOOLS[:2]], "nextCursor": "p2"}
        else:
            result = {"tools": [{"name": t} for t in TOOLS[2:]]}
        body = f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result})}\n\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Mcp-Session-Id", "s1")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_unregistered_tools_are_denied_and_an_unreachable_server_is_denied_whole(tmp_path):
    srv = HTTPServer(("127.0.0.1", 0), FakeMcp)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    spec = {"servers": [
        {"name": "jira", "url": f"{base}/mcp", "token_env": "JIRA_TOKEN", "auth": "basic", "email": "me@x",
         "tools": ["getJiraIssue", "searchJiraIssuesUsingJql", "notThere"]},
        {"name": "wiki", "url": f"{base}/broken", "token_env": "WIKI_TOKEN", "auth": "bearer", "email": "", "tools": ["search"]}]}
    (tmp_path / "spec.json").write_text(json.dumps(spec))
    show = "import json,sys; print(json.dumps(sys.argv[1:]))"
    r = subprocess.run([sys.executable, str(WRAPPER), str(tmp_path / "spec.json"), "--", sys.executable, "-c", show, "-p", "hi"],
                       capture_output=True, text=True, env={"JIRA_TOKEN": "ph-jira", "WIKI_TOKEN": "ph-wiki", "PATH": "/usr/bin:/bin"})
    srv.shutdown()
    args = json.loads(r.stdout)
    assert args[:2] == ["-p", "hi"] and "--strict-mcp-config" in args
    denied = args[args.index("--disallowedTools") + 1].split(",")
    assert sorted(denied) == ["mcp__jira__createJiraIssue", "mcp__jira__editJiraIssue", "mcp__wiki"]
    assert FakeMcp.auth[0] == "Basic " + base64.b64encode(b"me@x:ph-jira").decode()
    cfg = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())["mcpServers"]
    assert cfg["wiki"]["headers"]["Authorization"] == "Bearer ph-wiki"
    assert "notThere" in r.stderr and "cannot read the tool list" in r.stderr


def run_check(tmp_path, tools, *call, env=None):
    srv = HTTPServer(("127.0.0.1", 0), FakeMcp)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    spec = {"servers": [{"name": "jira", "url": f"http://127.0.0.1:{srv.server_port}/mcp", "token_env": "JIRA_TOKEN",
                         "auth": "basic", "email": "me@x", "tools": tools}]}
    (tmp_path / "spec.json").write_text(json.dumps(spec))
    r = subprocess.run([sys.executable, str(WRAPPER), "--check", str(tmp_path / "spec.json"), *call], capture_output=True, text=True,
                       env={"JIRA_TOKEN": "ph-jira", "PATH": "/usr/bin:/bin"} if env is None else env)
    srv.shutdown()
    return r


def test_check_reports_the_tools_and_makes_one_call(tmp_path):
    r = run_check(tmp_path, ["getJiraIssue", "searchJiraIssuesUsingJql"], "--call", "getJiraIssue", '{"issueIdOrKey": "MD-1"}')
    assert r.returncode == 0, r.stdout
    assert "registered and available: getJiraIssue, searchJiraIssuesUsingJql" in r.stdout
    assert "on the server but not registered (disabled at run time): createJiraIssue, editJiraIssue" in r.stdout
    assert "MD-1: Sync to ERP" in r.stdout and r.stdout.strip().endswith("RESULT: ok")


def test_check_fails_on_a_missing_tool_a_failed_call_or_no_token(tmp_path):
    assert "registered but not on the server: notThere" in run_check(tmp_path, ["getJiraIssue", "notThere"]).stdout
    r = run_check(tmp_path, ["getJiraIssue"], "--call", "getJiraIssue", '{"issueIdOrKey": "MD-2"}')
    assert r.returncode == 1 and "call getJiraIssue failed" in r.stdout
    r = run_check(tmp_path, ["getJiraIssue"], "--call", "createJiraIssue", "{}")
    assert r.returncode == 1 and "is not registered" in r.stdout  # only registered tools are ever called
    r = run_check(tmp_path, ["getJiraIssue"], env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 1 and "no JIRA_TOKEN" in r.stdout
