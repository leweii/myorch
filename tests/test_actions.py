import json
import subprocess
from pathlib import Path

import pytest

from myorch.core.flow import Step
from myorch.core.types import StepKey
from myorch.runner.actions import ActionRequest, ActionRunner
from myorch.runner.runner import StepRequest, observed_runs


class FakeStore:
    def __init__(self, files: dict[str, str]):
        self.files = files
        self.synced: list[str] = []

    def read_file(self, rel: str):
        return self.files.get(rel)

    def project_sync(self, name, local_dir: Path, version: str) -> None:
        self.synced.append(name)
        self.files[f"projects/{name}/.version"] = version


PROJECT_YAML = """
repo: o/r
identity: {account: bot, git_name: bot, git_email: bot@x}
image: img
"""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "myorch-knowledge"
    pack = root / "blog"
    (pack / "guide").mkdir(parents=True)
    (pack / "project.yaml").write_text(PROJECT_YAML)
    (pack / "guide" / "review-guide.md").write_text("# Review\n\n## Labels\n- Important\n")
    (pack / "checks.sh").write_text("echo ok\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"], cwd=root, check=True)
    return root


KEY = StepKey(run_id="learn-1", step_id="apply", attempt=1)


def run(root, proposals, accepted, scope="learn"):
    store = FakeStore({
        "runs/learn-1/docs/learn/proposals.json": json.dumps(proposals, ensure_ascii=False),
        "runs/learn-1/docs/learn/accepted.json": json.dumps(accepted),
    })
    step = Step(id="apply", action="project-apply", args={"scope": scope})
    req = ActionRequest(key=KEY, step=step, context={"project": {"name": "blog"}})
    return store, ActionRunner(store, root).run(req)


GOOD = [
    {"id": 1, "title": "Add language fallback", "changes": [{"file": "project/guide/review-guide.md",
     "edits": [{"old": "- Important\n", "new": "- Important (including the site language falling back to English)\n"}]}]},
    {"id": 2, "title": "Add a section", "changes": [{"file": "project/guide/review-guide.md", "append": "## New rule\n- content"}]},
]


def test_applies_accepted_commits_and_syncs(root):
    store, out = run(root, GOOD, {"accepted": [1, 2]})
    assert out.result.ok, out.result.error
    text = (root / "blog" / "guide" / "review-guide.md").read_text()
    assert "site language falling back" in text and text.rstrip().endswith("- content")
    log = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=root, capture_output=True, text=True).stdout
    assert "myorch-run: learn-1 step: apply" in log and log.startswith("learn(blog): #1 Add language fallback")
    assert store.synced == ["blog"] and out.manifest.outputs["applied"] == [1, 2]
    store2, again = run(root, GOOD, {"accepted": [1, 2]})  # a retry must not apply twice
    assert again.result.ok and "already written back" in again.manifest.summary and store2.synced == []
    assert out.manifest.outputs["version"].startswith("sha256:")
    assert (root / "blog" / "guide" / "review-guide.md").read_text() == text



def test_apply_commits_only_its_own_files(root):
    (root / "blog" / "checks.sh").write_text("echo half done\n")
    subprocess.run(["git", "add", "blog/checks.sh"], cwd=root, check=True)  # someone's staged change
    _, out = run(root, GOOD, {"accepted": [1]})
    assert out.result.ok, out.result.error
    show = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=root, capture_output=True, text=True).stdout
    assert show.split() == ["blog/guide/review-guide.md"]
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=root, capture_output=True, text=True).stdout
    assert staged.split() == ["blog/checks.sh"]  # still staged, not swept into the learn commit

def test_nothing_accepted_changes_nothing(root):
    before = (root / "blog" / "guide" / "review-guide.md").read_text()
    store, out = run(root, GOOD, {"accepted": []})
    assert out.result.ok and store.synced == []
    assert (root / "blog" / "guide" / "review-guide.md").read_text() == before


@pytest.mark.parametrize("bad,msg", [
    ({"file": "project/risk-rules.yaml", "append": "rules: []"}, "learn may not change 'risk-rules.yaml'"),
    ({"file": "project/project.yaml", "append": "x"}, "may not change"),
    ({"file": "project/guide/../../evil.md", "append": "x"}, "not a file in the project pack"),
    ({"file": "project/guide/review-guide.md", "edits": [{"old": "text that is not there", "new": "x"}]}, "occurs 0 times"),
])
def test_rejects_unsafe_or_unclean_changes_without_partial_writes(root, bad, msg):
    before = (root / "blog" / "guide" / "review-guide.md").read_text()
    proposals = GOOD[:1] + [{"id": 3, "title": "bad", "changes": [bad]}]
    store, out = run(root, proposals, {"accepted": [1, 3]})
    assert not out.result.ok and msg in out.result.error
    assert (root / "blog" / "guide" / "review-guide.md").read_text() == before  # #1 was not written either
    assert store.synced == []


def test_onboard_scope_may_change_checks(root):
    proposals = [{"id": 1, "title": "Checks", "changes": [{"file": "project/checks.sh", "edits": [{"old": "echo ok\n", "new": "./gradlew check\n"}]}]}]
    _, out = run(root, proposals, {"accepted": [1]}, scope="onboard")
    assert out.result.ok and (root / "blog" / "checks.sh").read_text() == "./gradlew check\n"


def test_observe_runs_validates_ids():
    base = dict(key=StepKey(run_id="learn-1", step_id="collect", attempt=1),
                context={"inputs": {"runs": "development-ab12, review-cd34,development-ab12"}})
    step = Step(id="collect", harness="script", profile="p", observe_runs="{{inputs.runs}}")
    assert observed_runs(StepRequest(step=step, **base)) == ["development-ab12", "review-cd34"]
    with pytest.raises(ValueError, match="must match"):
        observed_runs(StepRequest(step=step, key=base["key"], context={"inputs": {"runs": "../etc"}}))
    with pytest.raises(ValueError, match="its own run"):
        observed_runs(StepRequest(step=step, key=base["key"], context={"inputs": {"runs": "learn-1"}}))


def test_action_step_shape():
    with pytest.raises(ValueError, match="not both"):
        Step(id="a", harness="script", profile="p", action="project-apply")
    with pytest.raises(ValueError):
        Step(id="a", action="rm-rf")  # only registered actions exist


def test_onboard_edits_inherited_files_copy_on_write(root):
    t = root / "_templates" / "core-ng"
    t.mkdir(parents=True)
    (t / "project.yaml").write_text("image: img\n")
    (t / "setup.sh").write_text("echo template\n")
    (t / "risk-rules.yaml").write_text("rules: []\n")
    (root / "blog" / "project.yaml").write_text(PROJECT_YAML + "extends: core-ng\n")
    proposals = [{"id": 1, "title": "setup", "changes": [
        {"file": "project/setup.sh", "edits": [{"old": "echo template", "new": "echo project"}]},
        {"file": "project/risk-rules.yaml", "content": "rules:\n  - {paths: [x/*], approvers: 2}"}]}]
    _, out = run(root, proposals, {"accepted": [1]}, scope="onboard")
    assert out.result.ok, out.result.error
    assert (root / "blog" / "setup.sh").read_text() == "echo project\n"  # project override, template untouched
    assert (t / "setup.sh").read_text() == "echo template\n"
    assert (root / "blog" / "risk-rules.yaml").read_text() == "rules:\n  - {paths: [x/*], approvers: 2}\n"


def apply_in_round(root, rnd, proposals, accepted, scope="fix"):
    """project-apply as the review loop runs it: one step instance per round (pack-apply.r<N>)."""
    d = f"runs/dev-1/docs/review/round-{rnd}/pack"
    store = FakeStore({f"{d}/proposals.json": json.dumps(proposals, ensure_ascii=False),
                       f"{d}/accepted.json": json.dumps(accepted)})
    step = Step(id="pack-apply", action="project-apply",
                args={"scope": scope, "proposals": f"docs/review/round-{rnd}/pack/proposals.json",
                      "accepted": f"docs/review/round-{rnd}/pack/accepted.json"})
    key = StepKey(run_id="dev-1", step_id=f"pack-apply.r{rnd}", attempt=1)
    return store, ActionRunner(store, root).run(ActionRequest(key=key, step=step, context={"project": {"name": "blog"}}))


def test_fix_scope_applies_once_per_round_and_returns_the_new_version(root):
    one = [{"id": 1, "title": "Change checks to cp", "changes": [{"file": "project/checks.sh", "edits": [{"old": "echo ok", "new": "echo cp"}]}]}]
    two = [{"id": 1, "title": "Change it once more", "changes": [{"file": "project/checks.sh", "edits": [{"old": "echo cp", "new": "echo two"}]}]}]
    s1, r1 = apply_in_round(root, 1, one, {"accepted": [1]})
    s2, r2 = apply_in_round(root, 2, two, {"accepted": [1]})  # same run, a later round: applies again
    assert r1.result.ok and r2.result.ok, (r1.result.error, r2.result.error)
    assert (root / "blog" / "checks.sh").read_text() == "echo two\n"
    assert r1.manifest.outputs["version"] != r2.manifest.outputs["version"]
    log = subprocess.run(["git", "log", "-2", "--format=%B"], cwd=root, capture_output=True, text=True).stdout
    assert "myorch-run: dev-1 step: pack-apply.r1" in log and "myorch-run: dev-1 step: pack-apply.r2" in log
    s3, again = apply_in_round(root, 2, two, {"accepted": [1]})  # a retry of round 2 does not apply twice
    assert "already written back" in again.manifest.summary and s3.synced == []


def test_fix_scope_may_not_change_how_the_change_is_approved(root):
    (root / "blog" / "risk-rules.yaml").write_text("rules: []\n")
    bad = [{"id": 1, "title": "One approver fewer", "changes": [{"file": "project/risk-rules.yaml", "content": "default: 0"}]}]
    _, out = apply_in_round(root, 1, bad, {"accepted": [1]})
    assert not out.result.ok and "may not change" in out.result.error
    assert (root / "blog" / "risk-rules.yaml").read_text() == "rules: []\n"
