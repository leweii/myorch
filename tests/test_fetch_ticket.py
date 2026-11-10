"""development's fetch-ticket step: the script, run against a local fake Jira (REST API v2)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from myorch.core.flow import load_flow, render_template
from myorch.flowfile import read_flow_file

ROOT = Path(__file__).resolve().parents[1]
ISSUE = {"key": "MD-18700", "fields": {
    "summary": "主数据导出加上供应商编码", "description": "导出 CSV 时加一列 vendor_code。", "issuetype": {"name": "Story"},
    "status": {"name": "To Do"}, "priority": {"name": "High"}, "labels": ["export"], "components": [{"name": "md-service"}],
    "reporter": {"displayName": "PM"}, "assignee": None, "subtasks": [], "issuelinks": [], "attachment": [{"filename": "sample.csv"}],
    "comment": {"comments": [{"author": {"displayName": "Dev"}, "created": "2026-11-01T10:00:00.000+0000", "body": "旧数据怎么办？"}]}}}


class Fake(BaseHTTPRequestHandler):
    seen: list[tuple[str, str]] = []

    def do_GET(self):  # noqa: N802
        Fake.seen.append((self.path, self.headers.get("Authorization", "")))
        ok = self.path.startswith("/rest/api/2/issue/MD-18700?")
        self.send_response(200 if ok else 404)
        self.end_headers()
        self.wfile.write(json.dumps(ISSUE if ok else {"errorMessages": ["nope"]}).encode())

    def log_message(self, *a):
        pass


def test_the_ticket_is_written_for_the_analysis(tmp_path: Path):
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    project = {"name": "mdm", "repo": "o/r", "base": "develop", "image": "img", "branch": "b",
               "e2e": {"image": "", "profile": "dev-read", "acceptance": True},
               "jira": {"url": f"http://127.0.0.1:{srv.server_port}", "key": "MD", "auth": "basic", "email": "me@x"}}
    inputs = {"task": "t", "ticket": "18700", "from_run": ""}
    flow = load_flow(read_flow_file(ROOT / "flows" / "development.yaml", inputs, project))
    step = next(s for s in flow.all_steps() if s.id == "fetch-ticket")
    prompt = render_template(step.prompt, {"inputs": inputs, "project": project, "run": {"id": "r"}, "steps": {}})
    script = prompt.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0].replace("/sandbox/out/manifest.json", str(tmp_path / "manifest.json"))
    env = {**os.environ, "MYORCH_WORKSPACE": str(tmp_path), "MYORCH_INPUT_TICKET": "18700", "JIRA_TOKEN": "placeholder"}
    subprocess.run([sys.executable, "-c", script], env=env, check=True)
    srv.shutdown()
    md = (tmp_path / "docs" / "external" / "ticket.md").read_text()
    assert "# MD-18700 主数据导出加上供应商编码" in md and "vendor_code" in md and "旧数据怎么办" in md and "sample.csv" in md
    assert Fake.seen[0][1].startswith("Basic ")  # email:placeholder, swapped at the gateway
    assert json.loads((tmp_path / "manifest.json").read_text())["outputs"]["key"] == "MD-18700"


# Replay (ticket_as_of): the ticket as it was when the work started. Later edits, comments, links and
# subtasks would describe the solution.
LATER = {"key": "MD-18701", "fields": {
    "summary": "导出加供应商编码（已完成）", "description": "最终方案：在 VendorExporter 里加列。", "issuetype": {"name": "Story"},
    "status": {"name": "Done"}, "priority": {"name": "High"}, "labels": [], "components": [],
    "reporter": {"displayName": "PM"}, "assignee": {"displayName": "Dev"},
    "subtasks": [{"key": "MD-18702", "fields": {"summary": "改 VendorExporter"}}],
    "issuelinks": [{"type": {"outward": "relates to"}, "outwardIssue": {"key": "MD-1", "fields": {"summary": "早就有的"}}},
                   {"type": {"outward": "is fixed by"}, "outwardIssue": {"key": "MD-9", "fields": {"summary": "后来的修复"}}}],
    "attachment": [{"filename": "old.csv", "created": "2026-09-01T00:00:00.000+0000"},
                   {"filename": "after.png", "created": "2026-10-05T00:00:00.000+0000"}],
    "comment": {"comments": [{"author": {"displayName": "PM"}, "created": "2026-09-02T10:00:00.000+0000", "body": "旧数据不用回填"},
                             {"author": {"displayName": "Dev"}, "created": "2026-10-03T10:00:00.000+0000", "body": "PR #77 已合并"}]}},
    "changelog": {"total": 2, "histories": [
        {"created": "2026-10-02T09:00:00.000+0000", "items": [
            {"field": "description", "fromString": "导出 CSV 时加一列 vendor_code。", "toString": "中间版本"},
            {"field": "status", "fromString": "To Do", "toString": "In Progress"},
            {"field": "Link", "to": "MD-9", "toString": "This issue is fixed by MD-9"}]},
        {"created": "2026-10-04T09:00:00.000+0000", "items": [
            {"field": "description", "fromString": "中间版本", "toString": "最终方案：在 VendorExporter 里加列。"},
            {"field": "summary", "fromString": "导出加供应商编码", "toString": "导出加供应商编码（已完成）"}]}]}}


class FakeLater(BaseHTTPRequestHandler):
    seen: list[str] = []

    def do_GET(self):  # noqa: N802
        FakeLater.seen.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(LATER).encode())

    def log_message(self, *a):
        pass


def test_a_replay_reads_the_ticket_as_it_was(tmp_path: Path):
    srv = HTTPServer(("127.0.0.1", 0), FakeLater)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    project = {"name": "mdm", "repo": "o/r", "base": "develop", "image": "img", "branch": "b",
               "e2e": {"image": "", "profile": "dev-read", "acceptance": True},
               "jira": {"url": f"http://127.0.0.1:{srv.server_port}", "key": "MD", "auth": "basic", "email": "me@x"}}
    inputs = {"task": "", "ticket": "18701", "from_run": "", "at": "abc", "ticket_as_of": "2026-10-01T00:00:00Z"}
    flow = load_flow(read_flow_file(ROOT / "flows" / "development.yaml", inputs, project))
    step = next(s for s in flow.all_steps() if s.id == "fetch-ticket")
    prompt = render_template(step.prompt, {"inputs": inputs, "project": project, "run": {"id": "r"}, "steps": {}})
    script = prompt.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0].replace("/sandbox/out/manifest.json", str(tmp_path / "manifest.json"))
    env = {**os.environ, "MYORCH_WORKSPACE": str(tmp_path), "MYORCH_INPUT_TICKET": "18701", "JIRA_TOKEN": "x",
           "MYORCH_INPUT_TICKET_AS_OF": inputs["ticket_as_of"]}
    subprocess.run([sys.executable, "-c", script], env=env, check=True)
    srv.shutdown()
    md = (tmp_path / "docs" / "external" / "ticket.md").read_text()
    assert "expand=changelog" in FakeLater.seen[0]
    assert "# MD-18701 导出加供应商编码\n" in md and "导出 CSV 时加一列 vendor_code。" in md   # rolled back
    assert "最终方案" not in md and "中间版本" not in md and "已完成" not in md
    assert "状态：To Do" in md and "Done" not in md                                          # status rolled back
    assert "旧数据不用回填" in md and "PR #77" not in md                                     # later comment dropped
    assert "MD-1 早就有的" in md and "MD-9" not in md                                        # later link dropped
    assert "old.csv" in md and "after.png" not in md and "MD-18702" not in md


def test_comments_can_be_left_out(tmp_path: Path):
    srv = HTTPServer(("127.0.0.1", 0), FakeLater)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    project = {"name": "mdm", "repo": "o/r", "base": "develop", "image": "img", "branch": "b",
               "e2e": {"image": "", "profile": "dev-read", "acceptance": True},
               "jira": {"url": f"http://127.0.0.1:{srv.server_port}", "key": "MD", "auth": "basic", "email": "me@x"}}
    inputs = {"task": "", "ticket": "18701", "from_run": "", "at": "abc", "ticket_as_of": "2026-10-01T00:00:00Z",
              "ticket_comments": "none"}
    flow = load_flow(read_flow_file(ROOT / "flows" / "development.yaml", inputs, project))
    step = next(s for s in flow.all_steps() if s.id == "fetch-ticket")
    prompt = render_template(step.prompt, {"inputs": inputs, "project": project, "run": {"id": "r"}, "steps": {}})
    script = prompt.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0].replace("/sandbox/out/manifest.json", str(tmp_path / "manifest.json"))
    env = {**os.environ, "MYORCH_WORKSPACE": str(tmp_path), "MYORCH_INPUT_TICKET": "18701", "JIRA_TOKEN": "x",
           "MYORCH_INPUT_TICKET_AS_OF": inputs["ticket_as_of"], "MYORCH_INPUT_TICKET_COMMENTS": "none"}
    subprocess.run([sys.executable, "-c", script], env=env, check=True)
    srv.shutdown()
    md = (tmp_path / "docs" / "external" / "ticket.md").read_text()
    assert "## 评论" not in md and "旧数据不用回填" not in md and "导出 CSV 时加一列 vendor_code。" in md


PNG = b"\x89PNG\r\n\x1a\nfake"
WITH_IMAGES = {"key": "MD-18703", "fields": {
    "summary": "截图里的报错", "description": "见截图 !err.png|width=300!", "issuetype": {"name": "Bug"},
    "status": {"name": "To Do"}, "priority": {"name": "High"}, "labels": [], "components": [], "reporter": {"displayName": "QA"},
    "assignee": None, "subtasks": [], "issuelinks": [], "comment": {"comments": []},
    "attachment": [{"id": "101", "filename": "err.png", "mimeType": "image/png", "size": len(PNG), "created": "2026-09-01T00:00:00.000+0000"},
                   {"id": "102", "filename": "later.png", "mimeType": "image/png", "size": len(PNG), "created": "2026-10-05T00:00:00.000+0000"},
                   {"id": "103", "filename": "data.csv", "mimeType": "text/csv", "size": 10, "created": "2026-09-01T00:00:00.000+0000"}]},
    "changelog": {"total": 0, "histories": []}}


class FakeImages(BaseHTTPRequestHandler):
    seen: list[str] = []

    def do_GET(self):  # noqa: N802
        FakeImages.seen.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(PNG if self.path.startswith("/rest/api/2/attachment/content/") else json.dumps(WITH_IMAGES).encode())

    def log_message(self, *a):
        pass


def test_screenshots_before_the_cut_are_downloaded(tmp_path: Path):
    srv = HTTPServer(("127.0.0.1", 0), FakeImages)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    project = {"name": "mdm", "repo": "o/r", "base": "develop", "image": "img", "branch": "b",
               "e2e": {"image": "", "profile": "dev-read", "acceptance": True},
               "jira": {"url": f"http://127.0.0.1:{srv.server_port}", "key": "MD", "auth": "basic", "email": "me@x"}}
    inputs = {"task": "", "ticket": "18703", "from_run": "", "at": "abc", "ticket_as_of": "2026-10-01T00:00:00Z", "ticket_comments": "all"}
    flow = load_flow(read_flow_file(ROOT / "flows" / "development.yaml", inputs, project))
    step = next(s for s in flow.all_steps() if s.id == "fetch-ticket")
    prompt = render_template(step.prompt, {"inputs": inputs, "project": project, "run": {"id": "r"}, "steps": {}})
    script = prompt.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0].replace("/sandbox/out/manifest.json", str(tmp_path / "manifest.json"))
    env = {**os.environ, "MYORCH_WORKSPACE": str(tmp_path), "MYORCH_INPUT_TICKET": "18703", "JIRA_TOKEN": "x",
           "MYORCH_INPUT_TICKET_AS_OF": inputs["ticket_as_of"]}
    subprocess.run([sys.executable, "-c", script], env=env, check=True)
    srv.shutdown()
    md = (tmp_path / "docs" / "external" / "ticket.md").read_text()
    assert (tmp_path / "docs/external/attachments/101-err.png").read_bytes() == PNG
    assert "err.png → docs/external/attachments/101-err.png" in md and "data.csv（没有下载）" in md
    assert "later.png" not in md and not any("/102" in p for p in FakeImages.seen)   # after the cut: not even fetched
    assert any(p.startswith("/rest/api/2/attachment/content/101?redirect=false") for p in FakeImages.seen)
