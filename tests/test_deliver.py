"""lib/<prefix>-deliver.yaml: push only the approved commit, write docs/delivery.md, hand `delivery` to channels.
The full description (e2e, risk, unreviewed commits) with mo-deliver; the short one of the blog at the end."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from myorch.core.events import derive_events
from myorch.core.manifest import Manifest
from myorch.core.run import RunState
from myorch.flowfile import read_flow_file

FLOWS = Path(__file__).resolve().parents[1] / "flows"
PROJECT = {"name": "p", "repo": "o/r", "base": "main", "image": "img", "branch": "b", "identity": {}, "providers": {},
           "e2e": {"image": "", "profile": "dev-read", "acceptance": True}, "jira": {"url": "", "key": "", "auth": "basic", "email": ""}}


def deliver_step(flow_file: str = "development.yaml") -> dict:
    flow = yaml.safe_load(read_flow_file(FLOWS / flow_file, {"task": "x"}, PROJECT))
    return next(s for s in flow["steps"] if s.get("id") == "deliver")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


def workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    ws = tmp_path / "ws"
    repo = ws / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "A.java").write_text("class A {}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "main")
    git(repo, "checkout", "-qb", "MD-5-run")
    (repo / "A.java").write_text("class A { int x; }\n")
    git(repo, "commit", "-qam", "change")
    docs = ws / "docs"
    (docs / "review" / "round-1").mkdir(parents=True)
    (docs / "review" / "round-2").mkdir(parents=True)
    (docs / "ci" / "round-2").mkdir(parents=True)
    (docs / "implementation.md").write_text("# Implementation record\n\n## Changes\n- A.java: add field x\n\n## Assumptions\nNone\n")
    (docs / "review" / "round-1" / "verified.json").write_text(json.dumps([
        {"severity": "Important", "title": "x has no test", "file": "A.java", "line": 1, "status": "new"}]))
    (docs / "review" / "round-2" / "verified.json").write_text(json.dumps([
        {"severity": "Important", "title": "x has no test", "file": "A.java", "line": 1, "status": "fixed"},
        {"severity": "Nit", "title": "naming", "file": "A.java", "line": 1, "status": "new"}]))
    (docs / "review" / "round-2" / "summary.md").write_text(
        "0 blocking findings.\n\n## Acceptance criteria\n| Criterion | Verdict | Evidence |\n|---|---|---|\n| has x | met | ATest |\n\nOther\n")
    (docs / "ci" / "round-2" / "result.json").write_text(json.dumps({"passed": True}))
    (docs / "risk.md").write_text("## Verdict\nLow risk, 1 approver.\n\n## Impact\nSmall.\n\n## For the approvers to check\n- x in A.java\n")
    (ws / ".myorch" / "steps" / "analyze-requirement" / "out").mkdir(parents=True)
    (ws / ".myorch" / "steps" / "analyze-requirement" / "out" / "manifest.json").write_text(
        json.dumps({"status": "ok", "outputs": {"title": "Add x to A"}}))
    return ws, repo, origin


def run(tmp_path: Path, ws: Path, repo: Path, approved: str, branch: str = "MD-5-run",
        flow_file: str = "development.yaml", open_pr: bool = False, env_extra: dict | None = None):
    script = deliver_step(flow_file)["prompt"].replace("{{approvals.human-review.subject}}", approved)
    assert 'export DELIVER_OPEN_PR="false"' in script  # projects without flows.development.open_pr
    if open_pr:
        script = script.replace('export DELIVER_OPEN_PR="false"', 'export DELIVER_OPEN_PR="true"')
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    env = {**os.environ, "MYORCH_PROJECT_BASE": "main", "MYORCH_PROJECT_REPO": "o/r", "MYORCH_BRANCH": branch,
           "MYORCH_WORKSPACE": str(ws), "MYORCH_OUT_DIR": str(out), "MYORCH_INPUT_TICKET": "MD-5",
           "GITHUB_TOKEN": "unused", "MYORCH_PROJECT_PR_TEMPLATE": "", "MYORCH_RUN_ID": "development-1",
           **(env_extra or {})}
    res = subprocess.run(["bash", "-c", script], cwd=repo, env=env, capture_output=True, text=True)
    return res, out / "manifest.json"


def test_pushes_the_approved_commit_and_describes_it(tmp_path: Path):
    ws, repo, origin = workspace(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    res, manifest = run(tmp_path, ws, repo, head)
    assert res.returncode == 0, res.stderr
    assert git(origin, "rev-parse", "refs/heads/MD-5-run") == head
    d = json.loads(manifest.read_text())["outputs"]["delivery"]
    assert d["commit"] == head and len(d["commit"]) == 40
    assert (d["branch"], d["base"], d["title"], d["ticket"], d["repo"]) == ("MD-5-run", "main", "Add x to A", "MD-5", "o/r")
    text = d["description"]
    assert text == (ws / "docs" / "delivery.md").read_text()
    assert text.startswith("# Add x to A\nTicket: MD-5\n")
    assert "- A.java: add field x" in text and "## Assumptions" not in text
    assert "Checks: passed in round 2" in text and "| has x | met | ATest |" in text and "Other" not in text
    assert "2 rounds, 1 blocking findings, 0 unresolved, 1 Nits" in text
    assert "Low risk, 1 approver." in text and "- x in A.java" in text and "Impact" not in text
    assert f"main..{head[:12]}" in text and d["pr_template"] == ""


@pytest.mark.parametrize("case", ["moved", "base"])
def test_refuses_a_moved_branch_or_the_base_branch(tmp_path: Path, case: str):
    ws, repo, origin = workspace(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    if case == "moved":
        (repo / "A.java").write_text("class A { int y; }\n")
        git(repo, "commit", "-qam", "after approval")
    res, manifest = run(tmp_path, ws, repo, head, branch="main" if case == "base" else "MD-5-run")
    assert res.returncode == 3 and not manifest.exists()
    assert "Not pushing" in res.stderr
    assert git(origin, "branch", "--list", "MD-5-run") == ""


def test_run_finished_carries_the_delivery():
    flow = read_flow_file(FLOWS / "development.yaml", {"task": "x"}, PROJECT)
    s = RunState.create("r", flow, {"task": "x"}, project=PROJECT)
    for inst in s.instances.values():
        inst.status = "skipped"
    for lr in s.loops.values():
        lr.status = "skipped"
    delivery = {"branch": "b", "base": "main", "commit": "a" * 40, "title": "t", "description": "# t\n"}
    s.instances["deliver"].status = "succeeded"
    s.instances["deliver"].manifest = Manifest(step_id="deliver", outputs={"delivery": delivery})
    s.plan()
    finished = next(e for e in derive_events(s, finished=True) if e.kind == "run_finished")
    assert finished.data["status"] == "succeeded" and finished.data["delivery"] == delivery


def test_delivery_lists_e2e_results_and_unreviewed_commits(tmp_path: Path):
    ws, repo, origin = workspace(tmp_path)
    for part, result in (("smoke", {"passed": True, "failed": []}),
                         ("acceptance", {"passed": False, "failed": ["amount is 0"], "environment": ["screenshot failed"]})):
        (ws / "docs" / "e2e" / "round-2" / part).mkdir(parents=True)
        (ws / "docs" / "e2e" / "round-2" / part / "result.json").write_text(json.dumps(result))
    (ws / ".myorch" / "steps" / "risk-rules" / "out").mkdir(parents=True)
    (ws / ".myorch" / "steps" / "risk-rules" / "out" / "manifest.json").write_text(json.dumps(
        {"outputs": {"unreviewed_commits": [{"sha": "a" * 40, "subject": "fix(e2e): amount is 0"}]}}))
    res, manifest = run(tmp_path, ws, repo, git(repo, "rev-parse", "HEAD"))
    assert res.returncode == 0, res.stderr
    text = json.loads(manifest.read_text())["outputs"]["delivery"]["description"]
    assert "- End-to-end: round 2, smoke tests passed, acceptance tests failed: amount is 0 (test environment problem, not tested: screenshot failed)" in text
    assert "1 commits after the review (not reviewed): fix(e2e): amount is 0" in text


def test_delivery_says_when_there_is_no_e2e(tmp_path: Path):
    ws, repo, origin = workspace(tmp_path)
    res, manifest = run(tmp_path, ws, repo, git(repo, "rev-parse", "HEAD"))
    assert "- End-to-end: the project has no end-to-end tests" in json.loads(manifest.read_text())["outputs"]["delivery"]["description"]


def test_the_title_comes_from_develop(tmp_path: Path):
    """develop writes the title (the analysis used to: analyze-requirement is the fallback for old runs)."""
    ws, repo, origin = workspace(tmp_path)
    (ws / ".myorch" / "steps" / "develop" / "out").mkdir(parents=True)
    (ws / ".myorch" / "steps" / "develop" / "out" / "manifest.json").write_text(
        json.dumps({"status": "ok", "outputs": {"title": "feat: add x"}}))
    res, manifest = run(tmp_path, ws, repo, git(repo, "rev-parse", "HEAD"))
    assert res.returncode == 0, res.stderr
    assert json.loads(manifest.read_text())["outputs"]["delivery"]["description"].startswith("# feat: add x\n")


def fake_gh(tmp_path: Path, existing: list, fail: bool = False) -> tuple[dict, Path]:
    """A gh on PATH that answers `gh api` like GitHub and records each call (args, stdin) in calls.jsonl."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    calls = tmp_path / "calls.jsonl"
    (tmp_path / "existing.json").write_text(json.dumps(existing))
    script = """#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
body = sys.stdin.read() if "--input" in args else ""
open(CALLS, "a").write(json.dumps({"args": args, "body": body}) + "\\n")
if FAIL:
    sys.stderr.write("HTTP 403: Resource not accessible by personal access token")
    sys.exit(1)
if "POST" in args:
    print(json.dumps({"html_url": "https://github.com/o/r/pull/42", "number": 42}))
else:
    print(open(EXISTING).read())
"""
    script = script.replace("CALLS", repr(str(calls))).replace("FAIL", repr(fail)) \
                   .replace("EXISTING", repr(str(tmp_path / "existing.json")))
    (bin_dir / "gh").write_text(script)
    (bin_dir / "gh").chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}"}, calls


def gh_calls(calls: Path) -> list[dict]:
    return [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []


def test_open_pr_opens_the_pull_request_after_pushing(tmp_path: Path):
    ws, repo, origin = workspace(tmp_path)
    tpl = tmp_path / "pull_request_template.md"
    tpl.write_text("## Checklist\n- [ ] tested\n")
    env, calls = fake_gh(tmp_path, existing=[])
    head = git(repo, "rev-parse", "HEAD")
    res, manifest = run(tmp_path, ws, repo, head, open_pr=True, env_extra={**env, "MYORCH_PROJECT_PR_TEMPLATE": str(tpl)})
    assert res.returncode == 0, res.stderr
    assert git(origin, "rev-parse", "refs/heads/MD-5-run") == head
    m = json.loads(manifest.read_text())
    assert m["outputs"]["pr_url"] == "https://github.com/o/r/pull/42" and "opened pull request" in m["summary"]
    lookup, create = gh_calls(calls)
    assert lookup["args"] == ["api", "repos/o/r/pulls?state=open&head=o:MD-5-run&per_page=1"]
    assert create["args"][:3] == ["api", "repos/o/r/pulls", "--method"] and "POST" in create["args"]
    body = json.loads(create["body"])
    assert (body["title"], body["head"], body["base"]) == ("Add x to A", "MD-5-run", "main")
    assert body["body"].startswith("# Add x to A\nTicket: MD-5\n") and "## Checklist" in body["body"]
    assert body["body"].rstrip().endswith("<!-- myorch-pr run=development-1 -->")


def test_open_pr_reuses_the_branchs_open_pull_request(tmp_path: Path):
    ws, repo, origin = workspace(tmp_path)
    env, calls = fake_gh(tmp_path, existing=[{"html_url": "https://github.com/o/r/pull/7", "number": 7}])
    res, manifest = run(tmp_path, ws, repo, git(repo, "rev-parse", "HEAD"), open_pr=True, env_extra=env)
    assert res.returncode == 0, res.stderr
    assert json.loads(manifest.read_text())["outputs"]["pr_url"] == "https://github.com/o/r/pull/7"
    assert not any("POST" in c["args"] for c in gh_calls(calls))


def test_open_pr_failure_fails_the_step_after_the_push(tmp_path: Path):
    ws, repo, origin = workspace(tmp_path)
    env, calls = fake_gh(tmp_path, existing=[], fail=True)
    head = git(repo, "rev-parse", "HEAD")
    res, manifest = run(tmp_path, ws, repo, head, open_pr=True, env_extra=env)
    assert res.returncode != 0 and not manifest.exists()
    assert "opening the pull request failed" in res.stderr and "403" in res.stderr
    assert git(origin, "rev-parse", "refs/heads/MD-5-run") == head  # the push stands; a retry reuses it


def test_without_open_pr_no_platform_api_is_called(tmp_path: Path):
    ws, repo, origin = workspace(tmp_path)
    env, calls = fake_gh(tmp_path, existing=[])
    res, manifest = run(tmp_path, ws, repo, git(repo, "rev-parse", "HEAD"), env_extra=env)
    assert res.returncode == 0, res.stderr
    assert json.loads(manifest.read_text())["outputs"]["pr_url"] == "" and gh_calls(calls) == []


def test_open_pr_follows_the_project_setting():
    on = {**PROJECT, "flows": {"development": {"open_pr": True}}}
    flow = yaml.safe_load(read_flow_file(FLOWS / "development.yaml", {"task": "x"}, on))
    step = next(s for s in flow["steps"] if s.get("id") == "deliver")
    assert 'export DELIVER_OPEN_PR="true"' in step["prompt"]
