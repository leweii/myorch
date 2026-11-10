import json
import os
import subprocess
from pathlib import Path

import yaml

from myorch.flowfile import read_flow_file

FLOWS = Path(__file__).resolve().parents[1] / "flows"


def risk_rules_script() -> str:
    flow = yaml.safe_load(read_flow_file(FLOWS / "development.yaml", {"task": "x"}, {
        "name": "p", "repo": "o/r", "base": "main", "image": "img", "e2e": {"image": "", "profile": "dev-read", "acceptance": True}, "jira": {"url": "", "key": "", "auth": "basic", "email": ""}, "branch": "b", "identity": {}, "providers": {}}))
    return next(s for s in flow["steps"] if s.get("id") == "risk-rules")["prompt"]


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True, capture_output=True)


def run(tmp_path: Path, rules: dict, approvers: list[str], verified: list | None = None, ci_passed: bool = True,
        e2e: dict | None = None, after_review: int | None = None, files: dict | None = None) -> dict:
    """after_review: record the reviewed HEAD (docs/ci/round-1/head.txt), then add that many commits.
    files: extra workspace files, e.g. {"docs/e2e/round-1/rebuttal.md": "..."}."""
    ws = tmp_path / "ws"
    repo = ws / "repo"
    (repo / "svc").mkdir(parents=True)
    (repo / "db").mkdir()
    (repo / "svc" / "Order.java").write_text("class Order {\n    String id;\n}\n")
    (repo / "db" / "V1__init.sql").write_text("CREATE TABLE orders (id INT);\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    (repo / "svc" / "Order.java").write_text('class Order {\n    @Column(name = "id")\n    String id;\n}\n')
    (repo / "db" / "V2__drop.sql").write_text("drop table legacy;\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "change")
    (ws / "project").mkdir()
    (ws / "project" / "risk-rules.json").write_text(json.dumps(rules))
    (ws / "project" / "project.json").write_text(json.dumps({"approvers": approvers}))
    (ws / "docs" / "review" / "round-1").mkdir(parents=True)
    (ws / "docs" / "review" / "round-1" / "verified.json").write_text(json.dumps(verified or []))
    (ws / "docs" / "ci" / "round-1").mkdir(parents=True)
    (ws / "docs" / "ci" / "round-1" / "result.json").write_text(json.dumps({"passed": ci_passed, "failed": [] if ci_passed else ["gradle"]}))
    if after_review is not None:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout
        (ws / "docs" / "ci" / "round-1" / "head.txt").write_text(head)
        for i in range(after_review):
            (repo / "svc" / f"Fix{i}.java").write_text(f"class Fix{i} {{}}\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", f"fix(e2e): case {i}")
    for rel, text in (files or {}).items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text)
    for rel, result in (e2e or {}).items():  # e.g. {"round-2/smoke": {...}}
        (ws / "docs" / "e2e" / rel).mkdir(parents=True, exist_ok=True)
        (ws / "docs" / "e2e" / rel / "result.json").write_text(json.dumps(result))
    out = tmp_path / "out"
    out.mkdir()
    env = {**os.environ, "MYORCH_PROJECT_BASE": "main", "MYORCH_WORKSPACE": str(ws), "MYORCH_OUT_DIR": str(out)}
    subprocess.run(["bash", "-c", risk_rules_script()], cwd=repo, env=env, check=True, capture_output=True)
    return json.loads((out / "manifest.json").read_text())


def test_changed_with_text_matches_added_or_removed_lines(tmp_path: Path):
    rules = {"default": 0, "rules": [
        {"changed_with_text": {"paths": ["*.java"], "pattern": "@(Table|Column)\\b"}, "approvers": 2, "reason": "实体字段"},
        {"changed_with_text": {"paths": ["*.sql"], "pattern": "(?i)\\bDROP\\b"}, "approvers": 1, "reason": "破坏性语句"}]}
    m = run(tmp_path, rules, approvers=[])
    assert m["outputs"]["approvers"] == 2 and m["outputs"]["available"] == 99
    assert "实体字段：svc/Order.java" in m["summary"] and "破坏性语句：db/V2__drop.sql" in m["summary"]


def test_open_findings_and_failed_checks_need_a_person(tmp_path: Path):
    verified = [{"severity": "Important", "status": "open", "title": "空 id 没校验"},
                {"severity": "Important", "status": "fixed", "title": "已修"},
                {"severity": "Nit", "status": "new", "title": "命名"}]
    m = run(tmp_path, {"default": 0, "rules": []}, approvers=[], verified=verified, ci_passed=False)
    o = m["outputs"]
    assert o["approvers"] == 1 and o["open_findings"] == 1 and o["ci_passed"] is False
    assert "1 条没解决的 Important 问题：空 id 没校验" in m["summary"] and "检查失败：gradle" in m["summary"]


def test_more_approvers_than_registered_people_are_capped(tmp_path: Path):
    rules = {"default": 0, "rules": [{"paths": ["svc/*"], "approvers": 2, "reason": "服务"}]}
    m = run(tmp_path, rules, approvers=["jakobhe"])
    assert m["outputs"]["approvers"] == 2 and m["outputs"]["available"] == 1
    assert "规则要求 2 人，项目只登记了 1 个审批人，按 1 人审批" in m["summary"]


def test_e2e_failures_in_the_last_round_need_a_person(tmp_path: Path):
    ok = {"passed": True, "failed": []}
    m = run(tmp_path / "a", {"default": 0, "rules": []}, approvers=[], e2e={
        "round-1/smoke": {"passed": False, "failed": ["旧失败"]}, "round-1/acceptance": ok,
        "round-2/smoke": ok, "round-2/acceptance": {"passed": False, "failed": ["默认主题是 mono"]}})
    assert m["outputs"]["approvers"] == 1 and m["outputs"]["e2e_passed"] is False
    assert "端到端测试失败：默认主题是 mono" in m["summary"] and "旧失败" not in m["summary"]
    # Fixed in the last round, or no e2e block at all: no extra approver.
    m = run(tmp_path / "b", {"default": 0, "rules": []}, approvers=[], e2e={"round-1/smoke": ok, "round-1/acceptance": ok})
    assert m["outputs"]["approvers"] == 0 and m["outputs"]["e2e_passed"] is True
    assert run(tmp_path / "c", {"default": 0, "rules": []}, approvers=[])["outputs"]["e2e_passed"] is True
    # Acceptance off: only smoke ran; that is enough. A part that ran without a result counts as failed.
    assert run(tmp_path / "d", {"default": 0, "rules": []}, approvers=[], e2e={"round-1/smoke": ok})["outputs"]["e2e_passed"] is True


NO_RULES = {"default": 0, "rules": []}


def test_commits_after_the_last_review_round_need_a_person(tmp_path: Path):
    m = run(tmp_path, NO_RULES, approvers=["a"], after_review=2)
    assert m["outputs"]["approvers"] == 1
    assert [c["subject"] for c in m["outputs"]["unreviewed_commits"]] == ["fix(e2e): case 0", "fix(e2e): case 1"]
    assert "2 个提交没有经过 review" in m["summary"]


def test_nothing_after_review_and_no_head_record_add_nothing(tmp_path: Path):
    assert run(tmp_path, NO_RULES, approvers=["a"], after_review=0)["outputs"]["unreviewed_commits"] == []
    assert run(tmp_path / "b", NO_RULES, approvers=["a"])["outputs"]["approvers"] == 0  # older runs: no head.txt


def test_e2e_environment_problems_are_not_code_failures(tmp_path: Path):
    m = run(tmp_path, NO_RULES, approvers=["a"],
            e2e={"round-1/smoke": {"passed": False, "failed": [], "environment": ["Chromium 没有启动"]}})
    assert m["outputs"]["approvers"] == 1 and m["outputs"]["e2e_passed"] is False
    assert "没有测成" in m["summary"] and "端到端测试失败" not in m["summary"]


def test_e2e_rebuttals_for_a_person_are_named(tmp_path: Path):
    m = run(tmp_path, NO_RULES, approvers=["a"],
            e2e={"round-1/smoke": {"passed": False, "failed": ["首页列表"]}},
            files={"docs/e2e/round-1/rebuttal.md": "- 首页列表：和改动无关：首页模板这次没有改。\n- 其他：用例写错了\n"})
    assert m["outputs"]["approvers"] == 1
    assert "1 条要人判断的说明" in m["summary"] and "首页模板这次没有改" in m["summary"]
