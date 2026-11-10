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
    (docs / "implementation.md").write_text("# 实现记录\n\n## 改动\n- A.java：加字段 x\n\n## 假设\n无\n")
    (docs / "review" / "round-1" / "verified.json").write_text(json.dumps([
        {"severity": "Important", "title": "x 没有测试", "file": "A.java", "line": 1, "status": "new"}]))
    (docs / "review" / "round-2" / "verified.json").write_text(json.dumps([
        {"severity": "Important", "title": "x 没有测试", "file": "A.java", "line": 1, "status": "fixed"},
        {"severity": "Nit", "title": "命名", "file": "A.java", "line": 1, "status": "new"}]))
    (docs / "review" / "round-2" / "summary.md").write_text(
        "阻塞问题 0 条。\n\n## 验收标准\n| 标准 | 结论 | 证据 |\n|---|---|---|\n| 有 x | 满足 | ATest |\n\n其他\n")
    (docs / "ci" / "round-2" / "result.json").write_text(json.dumps({"passed": True}))
    (docs / "risk.md").write_text("## 结论\n低风险，1 人审批。\n\n## 影响范围\n小。\n\n## 请审批人重点检查\n- A.java 的 x\n")
    (ws / ".myorch" / "steps" / "analyze-requirement" / "out").mkdir(parents=True)
    (ws / ".myorch" / "steps" / "analyze-requirement" / "out" / "manifest.json").write_text(
        json.dumps({"status": "ok", "outputs": {"title": "Add x to A"}}))
    return ws, repo, origin


def run(tmp_path: Path, ws: Path, repo: Path, approved: str, branch: str = "MD-5-run",
        flow_file: str = "development.yaml"):
    script = deliver_step(flow_file)["prompt"].replace("{{approvals.human-review.subject}}", approved)
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    env = {**os.environ, "MYORCH_PROJECT_BASE": "main", "MYORCH_PROJECT_REPO": "o/r", "MYORCH_BRANCH": branch,
           "MYORCH_WORKSPACE": str(ws), "MYORCH_OUT_DIR": str(out), "MYORCH_INPUT_TICKET": "MD-5",
           "GITHUB_TOKEN": "unused", "MYORCH_PROJECT_PR_TEMPLATE": ""}
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
    assert text.startswith("# Add x to A\n工单：MD-5\n")
    assert "- A.java：加字段 x" in text and "## 假设" not in text
    assert "检查：第 2 轮通过" in text and "| 有 x | 满足 | ATest |" in text and "其他" not in text
    assert "2 轮，阻塞问题 1 条，没解决的 0 条，Nit 1 条" in text
    assert "低风险，1 人审批。" in text and "- A.java 的 x" in text and "影响范围" not in text
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
    assert "不推送" in res.stderr
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
                         ("acceptance", {"passed": False, "failed": ["金额为 0"], "environment": ["截图失败"]})):
        (ws / "docs" / "e2e" / "round-2" / part).mkdir(parents=True)
        (ws / "docs" / "e2e" / "round-2" / part / "result.json").write_text(json.dumps(result))
    (ws / ".myorch" / "steps" / "risk-rules" / "out").mkdir(parents=True)
    (ws / ".myorch" / "steps" / "risk-rules" / "out" / "manifest.json").write_text(json.dumps(
        {"outputs": {"unreviewed_commits": [{"sha": "a" * 40, "subject": "fix(e2e): 金额为 0"}]}}))
    res, manifest = run(tmp_path, ws, repo, git(repo, "rev-parse", "HEAD"))
    assert res.returncode == 0, res.stderr
    text = json.loads(manifest.read_text())["outputs"]["delivery"]["description"]
    assert "- 端到端：第 2 轮，冒烟用例通过，验收用例失败：金额为 0（测试环境的问题，没有测成：截图失败）" in text
    assert "review 之后的提交 1 个（没有经过 review）：fix(e2e): 金额为 0" in text


def test_delivery_says_when_there_is_no_e2e(tmp_path: Path):
    ws, repo, origin = workspace(tmp_path)
    res, manifest = run(tmp_path, ws, repo, git(repo, "rev-parse", "HEAD"))
    assert "- 端到端：项目没有端到端测试" in json.loads(manifest.read_text())["outputs"]["delivery"]["description"]


def test_the_title_comes_from_develop(tmp_path: Path):
    """develop writes the title (the analysis used to: analyze-requirement is the fallback for old runs)."""
    ws, repo, origin = workspace(tmp_path)
    (ws / ".myorch" / "steps" / "develop" / "out").mkdir(parents=True)
    (ws / ".myorch" / "steps" / "develop" / "out" / "manifest.json").write_text(
        json.dumps({"status": "ok", "outputs": {"title": "feat: add x"}}))
    res, manifest = run(tmp_path, ws, repo, git(repo, "rev-parse", "HEAD"))
    assert res.returncode == 0, res.stderr
    assert json.loads(manifest.read_text())["outputs"]["delivery"]["description"].startswith("# feat: add x\n")
