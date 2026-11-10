import json
from pathlib import Path

import pytest
import yaml

from myorch.core.flow import load_flow
from myorch.core.fragment import FragmentError, expand_flow
from myorch.core.run import RunState
from myorch.project import load_project, resolve_editable, snapshot
from myorch.runner.runner import resolve_providers


class FakeStore:
    def __init__(self):
        self.synced = {}

    def project_sync(self, name, local_dir: Path, version):
        self.synced[name] = {str(p.relative_to(local_dir)): p.read_text() for p in local_dir.rglob("*") if p.is_file()}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "myorch-knowledge"
    t = root / "_templates" / "core-ng"
    (t / "guide").mkdir(parents=True)
    (t / "project.yaml").write_text(yaml.safe_dump({
        "base": "develop", "image": "myorch-dev-core-ng:latest",
        "identity": {"account": "me", "git_name": "Me", "git_email": "me@x", "self_is_operator": True},
        "providers": {"github": "github-me", "artifacts": "azure-artifacts"},
        "repo_docs": ["CLAUDE.md"],
    }))
    (t / "guide" / "gradle.md").write_text("template gradle")
    (t / "guide" / "shared.md").write_text("template version")
    (t / "checks.sh").write_text("template checks")
    (t / "risk-rules.yaml").write_text(yaml.safe_dump({"default": 0, "rules": [{"paths": ["**/*-migration/**"], "approvers": 2}]}))
    (t / "security.md").write_text("通用安全重点")
    (t / "image").mkdir()
    (t / "image" / "Dockerfile").write_text("FROM x")
    p = root / "mdm"
    (p / "guide").mkdir(parents=True)
    (p / "project.yaml").write_text(yaml.safe_dump({
        "extends": "core-ng", "repo": "example-org/master-data-management", "submodules": ["example-org/protos"],
        "branch": "MD-{{inputs.ticket}}-myorch-{{run.id}}", "required_inputs": ["ticket"],
        "providers": {"github": "github-mdm"}, "repo_docs": ["CLAUDE.md", "openspec/specs/"],
    }))
    (p / "guide" / "shared.md").write_text("project version")
    (p / "risk-rules.yaml").write_text(yaml.safe_dump({"rules": [{"paths": ["backend/protos/**"], "approvers": 2}]}))
    (p / "security.md").write_text("mdm 特有的安全重点")
    return root


def test_template_inheritance(root):
    p = load_project(root, "mdm")
    assert p.base == "develop" and p.image == "myorch-dev-core-ng:latest"  # from the template
    assert p.providers == {"github": "github-mdm", "artifacts": "azure-artifacts"}  # deep merge, project wins
    assert p.repo_docs == ["CLAUDE.md", "openspec/specs/"]  # lists are replaced, not merged
    assert p.identity.trigger_word == "/myorch"  # operator's own account: a command, not an @mention


def test_snapshot_overlays_files_and_concatenates_rules(root):
    store = FakeStore()
    n, version = snapshot(store, root, "mdm")
    files = store.synced["mdm"]
    assert files["guide/gradle.md"] == "template gradle"  # template-only file kept
    assert files["guide/shared.md"] == "project version"  # project overrides
    assert files["checks.sh"] == "template checks"
    assert not any(f.startswith("image/") for f in files) and "project.yaml" not in files
    rules = json.loads(files["risk-rules.json"])["rules"]
    assert [r["paths"][0] for r in rules] == ["**/*-migration/**", "backend/protos/**"]
    assert json.loads(files["risk-rules.json"])["default"] == 0  # the project sets none: the template's
    assert files["security.md"].startswith("通用安全重点") and "mdm 特有" in files["security.md"]
    assert json.loads(files["project.json"])["repo"] == "example-org/master-data-management"
    assert version.startswith("sha256:")


def test_flow_project_substitution_and_branch(root):
    public = load_project(root, "mdm").public()
    flow = "flow: f\ninputs: {ticket: string}\nimage: '{{project.image}}'\nsteps:\n" \
           "  - {id: a, harness: script, profile: p, prompt: 'clone {{project.repo}} base {{project.base}} as {{project.identity.git_name}}'}\n"
    data = expand_flow(yaml.safe_load(flow), None, {}, public)
    assert data["image"] == "myorch-dev-core-ng:latest"
    assert data["steps"][0]["prompt"] == "clone example-org/master-data-management base develop as Me"
    with pytest.raises(FragmentError, match="--project"):
        expand_flow(yaml.safe_load(flow), None, {}, None)
    s = RunState.create("development-1", yaml.safe_dump(data), {"ticket": "18700"}, project=public)
    assert s.branch() == "MD-18700-myorch-development-1"
    review = RunState.create("review-1", yaml.safe_dump({**data, "inputs": {}}), {}, project=public)
    assert review.branch() == "myorch/review-1"  # flows without a ticket never create a branch
    assert s.context()["project"]["name"] == "mdm"


def test_provider_roles():
    project = {"providers": {"github": "github-mdm", "artifacts": None}}
    assert resolve_providers(["github", "artifacts", "claude"], project) == ["github-mdm", "claude"]
    assert resolve_providers(["github"], None) == ["github"]  # no project: role is the instance name
    assert resolve_providers(["github", "artifacts"], {"providers": {"github": "github-bot"}}) == ["github-bot"]


def test_editable_scopes(root):
    assert resolve_editable(root, "mdm", "project/guide/x.md", "learn").name == "x.md"
    with pytest.raises(ValueError, match="learn may not change"):
        resolve_editable(root, "mdm", "project/risk-rules.yaml", "learn")
    assert resolve_editable(root, "mdm", "project/checks.sh", "learn").name == "checks.sh"  # learn may add checks (a person adopts them)
    assert resolve_editable(root, "mdm", "project/risk-rules.yaml", "onboard").name == "risk-rules.yaml"


def test_project_shared_dirs_join_the_steps():
    from myorch.core.flow import Step
    from myorch.core.types import StepKey
    from myorch.runner.runner import StepRequest, shared_dirs

    req = StepRequest(key=StepKey(run_id="r", step_id="s", attempt=1), project="mdm",
                      step=Step(id="s", harness="script", profile="p", shared=["notes"]),
                      context={"project": {"name": "mdm", "shared": ["gradle", "notes"]}})
    assert shared_dirs(req) == ["notes", "gradle"]


def test_github_provider_profile_scopes_to_the_project(root):
    from myorch.project import github_provider_profile

    prof = yaml.safe_load(github_provider_profile(load_project(root, "mdm")))
    git = [r["allow"] for r in prof["endpoints"][0]["rules"]]
    assert {"method": "POST", "path": "/example-org/master-data-management.git/git-receive-pack"} in git
    assert {"method": "POST", "path": "/example-org/protos.git/git-upload-pack"} in git
    assert not any(r["path"] == "/example-org/protos.git/git-receive-pack" for r in git)  # submodules: read only
    assert prof["id"] == "github-mdm"


def test_github_provider_profile_read_only(root):
    from myorch.project import github_provider_profile

    prof = yaml.safe_load(github_provider_profile(load_project(root, "mdm"), read_only=True))
    rules = [r["allow"] for e in prof["endpoints"] for r in e["rules"]]
    assert all(r["method"] == "GET" or r["path"].endswith("git-upload-pack") for r in rules)


def test_select_for_learn_takes_runs_since_the_last_learn():
    from myorch.runs import RunRecord, select_for_learn

    def r(i, flow, t, project="mdm", status="COMPLETED"):
        return RunRecord(run_id=f"{flow}-{i}", flow=flow, project=project, requested_at=f"2026-11-0{t}T00:00:00+00:00", status=status)

    records = [r(1, "development", 1), r(2, "review", 2), r(3, "learn", 3), r(4, "development", 4),
               r(5, "development", 5, status="CANCELED"), r(6, "onboard", 5), r(7, "development", 6, project="blog"),
               r(8, "learn", 7, status="FAILED"), r(9, "review", 8, status="FAILED")]
    picked, last = select_for_learn(records, "mdm")
    assert last.run_id == "learn-3"  # a failed learn does not count as having learned
    assert [p.run_id for p in picked] == ["development-4", "review-9"]  # after learn-3, finished, same project
    picked, last = select_for_learn(records[:2], "mdm")
    assert last is None and len(picked) == 2


def test_review_page_shows_request_diff_findings_and_risk():
    from myorch.review_page import build

    files = {
        ".myorch/mandate.json": json.dumps({"initiator": "leweii", "flow": {"name": "development"}, "project": {"name": "mdm"},
                                            "source": "https://github.com/o/r/issues/2", "request": {"task": "t"}}),
        ".myorch/steps/analyze-requirement/out/manifest.json": json.dumps({"outputs": {"title": "Reject blank id"}}),
        ".myorch/steps/risk-rules/out/manifest.json": json.dumps({"summary": "medium，需要 1 人审批"}),
        "docs/requirements.md": "## 目标\n- 空 id 报错",
        "docs/review/round-1/verified.json": json.dumps([{"severity": "Nit", "file": "A.java", "line": 3, "title": "<b>x</b>", "status": "new"}]),
        "docs/ci/round-1/result.json": json.dumps({"passed": True, "environment": ["T.conf()"]}),
        "git/diff": " A.java | 1 +\n\ndiff --git a/A.java b/A.java\n@@ -1 +1,2 @@\n+if (id == null) throw\n-old",
        "git/log": "805ef7d Jakob He Reject blank id",
    }
    page = build("development-1", files, waiting="human-review：还需要 1 人批准", approve_hint="<p>cmd</p>")
    assert "<h1>Reject blank id</h1>" in page and "<h2>目标</h2>" in page
    assert '<span class="add">+if (id == null) throw</span>' in page and '<span class="del">-old</span>' in page
    assert "&lt;b&gt;x&lt;/b&gt;" in page  # agent-written text is escaped
    assert "环境失败（不计入）：T.conf()" in page and "还需要 1 人批准" in page and "issues/2" in page
    assert "端到端测试" not in page  # no e2e results: no section
    files["docs/e2e/round-1/smoke/result.json"] = json.dumps({"passed": False, "failed": ["上一轮的用例XYZ"]})
    files["docs/e2e/round-2/smoke/result.json"] = json.dumps({"passed": False, "failed": ["首页<列出>文章"]})
    files["docs/e2e/round-2/smoke/report.md"] = "| 首页列出文章 | FAIL |"
    files["docs/e2e/round-2/acceptance/result.json"] = json.dumps({"passed": True, "failed": []})
    files["docs/e2e/round-2/acceptance.md"] = "| 默认主题 | 通过 |"
    page = build("development-1", files)
    assert "端到端测试（2 轮）" in page and "冒烟用例，第 2 轮" in page and "未通过：首页&lt;列出&gt;文章" in page
    assert "验收用例，第 2 轮：<span class=\"ok\">通过" in page and "验收标准对照" in page and "上一轮的用例XYZ" not in page


def test_prompt_slots_come_from_template_then_project(root):
    (root / "_templates" / "core-ng" / "prompts").mkdir()
    (root / "_templates" / "core-ng" / "prompts" / "review.md").write_text("## core-ng\n- 接口模块\n")
    (root / "mdm" / "prompts").mkdir()
    (root / "mdm" / "prompts" / "review.md").write_text("## mdm\n- 主数据\n")
    (root / "mdm" / "prompts" / "develop.md").write_text("  \n")  # blank: nothing
    p = load_project(root, "mdm")
    assert p.prompts["review"] == "## core-ng\n- 接口模块\n\n## mdm\n- 主数据"
    assert p.prompts["develop"] == "" and set(p.prompts) == {"develop", "review", "verify", "fix", "risk"}
    assert p.public()["prompts"]["review"].startswith("## core-ng")
    (root / "mdm" / "prompts" / "reviw.md").write_text("typo")
    with pytest.raises(ValueError, match="not a prompt slot"):
        load_project(root, "mdm")


def test_flow_settings_default_and_validate(root):
    p = load_project(root, "mdm")
    d = p.flows.development
    assert (d.reviewers, d.max_rounds, d.security, d.on_exhausted, d.takeaway, d.incident) == (3, 3, True, "ask", False, False)
    own = yaml.safe_load((root / "mdm" / "project.yaml").read_text())
    (root / "mdm" / "project.yaml").write_text(yaml.safe_dump({**own, "flows": {"development": {"reviewers": 1, "takeaway": True}}}))
    d = load_project(root, "mdm").flows.development
    assert d.reviewers == 1 and d.takeaway and d.max_rounds == 3  # the rest keep their defaults
    (root / "mdm" / "project.yaml").write_text(yaml.safe_dump({**own, "flows": {"development": {"reviewers": 0}}}))
    with pytest.raises(ValueError):
        load_project(root, "mdm")


SHAPED = """
flow: f
inputs:
  ticket: {if: "project.jira.url", type: ticket, prefix: "{{project.jira.key}}-", default: ""}
  task: {type: message, required: "not project.jira.url"}
steps:
  - {id: a, harness: script, profile: p, prompt: "echo"}
  - {id: b, if: "project.on", harness: script, profile: p, prompt: "echo", needs: [a]}
  - {id: c, if: "project.on", harness: script, profile: p, prompt: "echo", needs: [b]}
  - {id: d, harness: script, profile: p, prompt: "echo", needs: [c]}
"""


def test_project_if_drops_steps_and_their_dependents_wait_for_what_they_waited_for():
    on = expand_flow(yaml.safe_load(SHAPED), None, {}, {"on": True, "jira": {"url": "https://j", "key": "MD"}})
    assert [s["id"] for s in on["steps"]] == ["a", "b", "c", "d"] and on["steps"][3]["needs"] == ["c"]
    assert on["inputs"]["ticket"] == {"type": "ticket", "prefix": "MD-", "default": ""}
    assert on["inputs"]["task"]["required"] is False  # with a Jira, the ticket is the request
    off = expand_flow(yaml.safe_load(SHAPED), None, {}, {"on": False, "jira": {"url": "", "key": ""}})
    assert [s["id"] for s in off["steps"]] == ["a", "d"] and off["steps"][1]["needs"] == ["a"]  # not []: d still waits
    assert off["inputs"]["ticket"] == {"type": "hidden", "default": ""}  # still declared: {{inputs.ticket}} renders
    assert off["inputs"]["task"]["required"] is True
    load_flow(yaml.safe_dump(off))
    with pytest.raises(FragmentError, match="--project"):
        expand_flow(yaml.safe_load(SHAPED), None, {}, None)
