"""ck-development's takeaway (task information and takeaway committed to the repository's myorch/
directory) and ck-learn reading it back: the steps and their guards, the commit script, the risk
exemption, run-trail and run_trail, collect's selection, and the learned-runs ledger in myorch-data."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from myorch import takeaways
from myorch.core.expr import evaluate
from myorch.core.flow import Step, load_flow, render_template
from myorch.core.manifest import Manifest
from myorch.core.run import RunState
from myorch.core.runtrail import run_trail, safe_run_trail
from myorch.core.types import StepKey
from myorch.flowfile import flow_meta, read_flow_file
from myorch.runner.actions import ActionRequest, ActionRunner

ROOT = Path(__file__).resolve().parents[1]
FLOWS = ROOT / "flows"
STEPS = ("takeaway-trail", "takeaway", "takeaway-redact", "takeaway-commit")
PUB = {"name": "mdm", "repo": "o/r", "base": "main", "image": "img", "branch": "b/{{run.id}}", "submodules": [],
       "submodules_list": "", "repo_docs": [], "repo_docs_list": "（无）", "conventions": "", "pr_template": "",
       "e2e": {"image": "", "profile": "dev-read", "acceptance": True}, "approvers": [],
       "jira": {"url": "", "key": "", "auth": "basic", "email": ""}, "identity": {"account": "a", "trigger": "/myorch"},
       "required_inputs": [], "sweep": {"paths": [], "include": [], "exclude": [], "chunk": 20}, "providers": {},
       "flows": {"development": {"takeaway": True}}}
RUN = "ck-development-1a2b3c4d"


def ck_flow():
    return load_flow(read_flow_file(FLOWS / "development.yaml", {"ticket": "1"}, PUB))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


def script_of(step: Step, tmp_path: Path, inputs: dict | None = None) -> str:
    prompt = render_template(step.prompt, {"inputs": inputs or {}, "project": PUB, "run": {"id": RUN}, "steps": {}})
    return prompt.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0].replace("/sandbox/out/manifest.json", str(tmp_path / "manifest.json"))


# ---- the steps --------------------------------------------------------------------------------------

def test_the_takeaway_steps_are_in_development_for_projects_that_ask_for_them():
    for path in sorted(FLOWS.glob("*.yaml")):
        text = path.read_text()
        has = any(f"id: {s}\n" in text for s in STEPS)
        assert has == (flow_meta(path)["flow"] == "development"), path.name
    off = load_flow(read_flow_file(FLOWS / "development.yaml", {"ticket": "1"}, {**PUB, "flows": {}}))
    assert not set(STEPS) & {s.id for s in off.steps}  # flows.development.takeaway is off by default
    flow = ck_flow()
    order = [s.id for s in flow.steps]
    assert order.index("review-fix") < order.index("takeaway-trail") < order.index("takeaway") \
        < order.index("takeaway-redact") < order.index("takeaway-commit") < order.index("risk-rules") < order.index("human-review")
    assert flow.step("risk-rules").needs == ["takeaway-commit"]  # head_sha (the approved commit) includes it
    trail, agent, redact, commit = (flow.step(s) for s in STEPS)
    assert trail.action == "run-trail" and redact.action == "takeaway-redact" and commit.profile == "dev-write"
    assert set(trail.args["clear"].split()) == {"docs/takeaway/task.md", "docs/takeaway/takeaway.md"}
    assert (agent.profile, agent.model, agent.interaction, agent.knowledge) == ("dev-read", "claude-sonnet-5-5", "never", False)
    assert agent.limits.timeout == "10m"
    for s in (trail, agent, redact, commit):
        assert s.on_failed == "skip" and flow.failure_policy(s) == "skip"
    assert "SECRET" not in commit.prompt  # no copy of the rules: takeaway-redact uses knowledge.SECRET_RULES


def ctx(declared, **outputs):
    """What each takeaway step's `when` sees: develop went ahead, the earlier takeaway steps gave their outputs."""
    return {"inputs": declared, "steps": {"develop": {"outputs": {"proceed": True}}, **{k: {"outputs": v} for k, v in outputs.items()}}}


DONE = {"takeaway-trail": {"path": "docs/takeaway/trail.json"}, "takeaway": {"items": 0}, "takeaway-redact": {"files": 2}}


def test_replays_skip_the_takeaway():
    flow = ck_flow()
    declared = flow.resolved_inputs({"ticket": "1"})
    normal = ctx(declared, **DONE)
    replay = {**normal, "inputs": {**declared, "at": "0123abcd"}}
    stopped = {**normal, "steps": {**normal["steps"], "develop": {"outputs": {"proceed": False}}}}
    for s in STEPS:
        when = flow.step(s).when
        assert evaluate(when, normal)
        assert not evaluate(when, replay)
        assert not evaluate(when, stopped)


@pytest.mark.parametrize("missing, blocked", [("takeaway-trail", "takeaway"), ("takeaway", "takeaway-redact"),
                                              ("takeaway-redact", "takeaway-commit")])
def test_each_step_waits_for_the_one_before_to_give_its_output(missing, blocked):
    """A skipped step (by when, or after failing) has no outputs: nothing stale or half-written moves on."""
    flow = ck_flow()
    declared = flow.resolved_inputs({"ticket": "1"})
    assert not evaluate(flow.step(blocked).when, ctx(declared, **{**DONE, missing: {}}))
    assert evaluate(flow.step("takeaway-redact").when, ctx(declared, **{**DONE, "takeaway": {"items": "3"}}))  # a string count too


def test_on_failed_skip_is_not_allowed_in_loops():
    with pytest.raises(ValueError, match="not allowed inside a loop"):
        load_flow("flow: f\nsteps:\n  - loop: l\n    until: 'true'\n    steps:\n"
                  "      - {id: a, harness: script, profile: p, prompt: x, on_failed: skip}\n")


def test_on_failed_skip_for_host_actions_only_when_asked():
    flow = load_flow("flow: f\non_failed: ask\nsteps:\n  - {id: a, action: run-trail, on_failed: skip}\n"
                     "  - {id: b, action: run-trail}\n  - {id: c, harness: script, profile: p, prompt: x}\n")
    assert [flow.failure_policy(flow.step(s)) for s in "abc"] == ["skip", "fail", "ask"]


# ---- takeaway-commit -----------------------------------------------------------------------------

def commit_ws(tmp_path: Path, files: dict[str, str]) -> Path:
    ws = tmp_path / "ws"
    repo = ws / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "config", "user.name", "t")
    git(repo, "config", "user.email", "t@t")
    (ws / "docs" / "takeaway").mkdir(parents=True)
    (ws / "docs" / "takeaway" / "trail.json").write_text(json.dumps({"requested_at": "2026-11-09T08:00:00+00:00"}))
    for name, text in files.items():
        (ws / "docs" / "takeaway" / name).write_text(text)
    return ws


def run_commit(tmp_path: Path, ws: Path) -> dict:
    script = script_of(ck_flow().step("takeaway-commit"), tmp_path)
    env = {**os.environ, "MYORCH_WORKSPACE": str(ws), "MYORCH_RUN_ID": RUN, "MYORCH_OUT_DIR": str(tmp_path)}
    subprocess.run([sys.executable, "-c", script], env=env, check=True, capture_output=True)
    return json.loads((tmp_path / "manifest.json").read_text())


def test_takeaway_commit_commits_once(tmp_path: Path):
    ws = commit_ws(tmp_path, {"task.md": "# 任务：MD-1\n", "takeaway.md": "# Takeaway\n"})
    repo = ws / "repo"
    (repo / "a.txt").write_text("someone's work\n")
    git(repo, "add", "a.txt")  # staged, not part of the takeaway commit
    m = run_commit(tmp_path, ws)
    d = f"myorch/2026-11-09-{RUN}"
    assert m["outputs"]["dir"] == d
    assert git(repo, "log", "-1", "--format=%s") == f"Add myorch task and takeaway for {RUN}"
    assert sorted(git(repo, "show", "--format=", "--name-only", "HEAD").split()) == [f"{d}/takeaway.md", f"{d}/task.md"]
    head = git(repo, "rev-parse", "HEAD")
    again = run_commit(tmp_path, ws)  # a retry
    assert "已经提交过" in again["summary"] and git(repo, "rev-parse", "HEAD") == head


def test_takeaway_commit_without_files_commits_nothing(tmp_path: Path):
    ws = commit_ws(tmp_path, {})
    head = git(ws / "repo", "rev-parse", "HEAD")
    m = run_commit(tmp_path, ws)
    assert m["outputs"]["dir"] == "" and git(ws / "repo", "rev-parse", "HEAD") == head


# ---- risk-rules: the takeaway commit is not an unreviewed change -----------------------------------

def unreviewed_after(tmp_path: Path, commits: list[dict[str, str]]) -> list[str]:
    ws = tmp_path / "ws"
    repo = ws / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "svc.java").write_text("class A {}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    (repo / "svc.java").write_text("class A { int x; }\n")
    git(repo, "commit", "-qam", "change")
    (ws / "project").mkdir()
    (ws / "project" / "risk-rules.json").write_text(json.dumps({"default": 0, "rules": []}))
    (ws / "project" / "project.json").write_text(json.dumps({"approvers": ["a"]}))
    (ws / "docs" / "ci" / "round-1").mkdir(parents=True)
    (ws / "docs" / "ci" / "round-1" / "result.json").write_text(json.dumps({"passed": True, "failed": []}))
    (ws / "docs" / "ci" / "round-1" / "head.txt").write_text(git(repo, "rev-parse", "HEAD"))
    for i, files in enumerate(commits):
        if "__merge__" in files:  # a side branch with these files, merged with an extra edit in the merge itself
            git(repo, "checkout", "-q", "-b", f"side{i}", "HEAD~1")
            for rel, text in files["__merge__"].items():
                (repo / rel).parent.mkdir(parents=True, exist_ok=True)
                (repo / rel).write_text(text)
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", f"side {i}")
            git(repo, "checkout", "-q", "-")
            git(repo, "merge", "-q", "--no-ff", "--no-commit", f"side{i}")
            files = files["__in_merge__"]
        for rel, text in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text)
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", f"commit {i}")
    out = tmp_path / "out"
    out.mkdir()
    prompt = ck_flow().step("risk-rules").prompt
    env = {**os.environ, "MYORCH_PROJECT_BASE": "main", "MYORCH_WORKSPACE": str(ws), "MYORCH_OUT_DIR": str(out)}
    subprocess.run(["bash", "-c", prompt], cwd=repo, env=env, check=True, capture_output=True)
    m = json.loads((out / "manifest.json").read_text())
    return [c["subject"] for c in m["outputs"]["unreviewed_commits"]]


def test_risk_rules_does_not_count_the_takeaway_commit(tmp_path: Path):
    md = f"myorch/2026-11-09-{RUN}"
    assert unreviewed_after(tmp_path / "1", [{f"{md}/task.md": "x", f"{md}/takeaway.md": "y"}]) == []
    assert unreviewed_after(tmp_path / "2", [{f"{md}/task.md": "x", "svc.java": "class B {}\n"}]) == ["commit 0"]
    assert unreviewed_after(tmp_path / "3", [{"myorch/run.sh": "echo"}]) == ["commit 0"]  # not markdown
    assert unreviewed_after(tmp_path / "4", [{"docs/notes.md": "x"}]) == ["commit 0"]  # not under myorch/
    assert unreviewed_after(tmp_path / "5", [{f"{md}/task.md": "x"}, {"svc.java": "class C {}\n"}]) == ["commit 1"]


def test_a_merge_commit_is_never_exempt(tmp_path: Path):
    md = f"myorch/2026-11-09-{RUN}"
    # The merge's own file list shows only the markdown it edits; what it brings in does not show.
    got = unreviewed_after(tmp_path, [{"__merge__": {"svc/Other.java": "class O {}\n"}, "__in_merge__": {f"{md}/task.md": "x"}}])
    assert "commit 0" in got


# ---- run_trail and run-trail -------------------------------------------------------------------

FLOW = """
flow: f
steps:
  - {id: develop, harness: script, profile: p, prompt: x}
  - loop: rv
    needs: [develop]
    max_rounds: 2
    until: "steps.verify.outputs.blocking == 0"
    steps:
      - {id: ci, harness: script, profile: p, prompt: x}
      - {id: verify, harness: script, profile: p, prompt: x, needs: [ci]}
  - {id: trail, action: run-trail, needs: [rv]}
"""


def advance(state: RunState, iid: str, status: str = "succeeded", **outputs) -> None:
    state.plan()
    inst = state.instances[iid]
    inst.status, inst.manifest = status, Manifest(step_id=iid, summary=f"{iid} {status}", outputs=outputs)


def test_run_trail_tells_how_the_run_went_so_far():
    state = RunState.create(RUN, FLOW, {})
    advance(state, "develop")
    advance(state, "ci.r1", passed=False, failed=["unit"])
    advance(state, "verify.r1", blocking=2)
    advance(state, "ci.r2", passed=True, failed=[])
    advance(state, "verify.r2", blocking=0)
    state.plan()
    t = run_trail(state, [{"step": "develop", "question": "红还是蓝？", "text": "红", "at": "2026-11-09T10:00:00"}])
    assert [s["id"] for s in t["steps"]] == ["develop", "trail", "ci.r1", "verify.r1", "ci.r2", "verify.r2"]
    assert t["steps"][1]["status"] == "running"  # the trail step itself; later steps are not reached yet
    rounds = t["loops"][0]["per_round"]
    assert rounds[0] == {"round": 1, "steps": {"ci.r1": "succeeded", "verify.r1": "succeeded"}, "blocking": 2,
                         "ci": {"passed": False, "failed": ["unit"]}}
    assert t["qa"] == [{"step": "develop", "question": "红还是蓝？", "answer": "红", "at": "2026-11-09T10:00:00"}]


def test_run_trail_survives_odd_manifests():
    state = RunState.create(RUN, FLOW, {})
    advance(state, "develop", blocking={"x": 1}, huge="y" * 5000)
    advance(state, "ci.r1", passed="yes", failed="unit, lint")  # agents write what they like
    advance(state, "verify.r1", blocking=[1, 2])
    t = run_trail(state, [{"step": "x", "question": None, "text": 3}, "not a dict"])
    r1 = t["loops"][0]["per_round"][0]
    assert r1["ci"] == {"passed": "yes", "failed": []} and r1["blocking"] == "[1, 2]"
    assert len(t["steps"][0]["outputs"]["huge"]) < 400 and len(t["qa"]) == 1
    state.instances["develop"].manifest = None
    state.loops["rv"].round = 5  # inconsistent: rounds that have no instances
    assert safe_run_trail(state)["run"] == RUN
    broken = RunState.create(RUN, FLOW, {})
    broken.approvals = {"x": object()}  # type: ignore[dict-item]
    assert safe_run_trail(broken)["error"].startswith("trail incomplete")


class Store:
    def __init__(self, files: dict[str, str] | None = None):
        self.files = dict(files or {})

    def read_file(self, rel: str):
        return self.files.get(rel)

    def write_run_file(self, run_id, rel_path, text):
        self.files[f"runs/{run_id}/{rel_path}"] = text


def test_run_trail_action_adds_what_the_host_knows(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MYORCH_COMMIT", "0123456789abcdef")
    store = Store({f"runs/{RUN}/.myorch/mandate.json": json.dumps({"project": {"name": "mdm", "version": "sha256:abc"}})})
    req = ActionRequest(key=StepKey(run_id=RUN, step_id="takeaway-trail", attempt=1),
                        step=Step(id="takeaway-trail", action="run-trail", args={"path": "docs/takeaway/trail.json"}),
                        context={}, trail={"run": RUN, "steps": [{"id": "ci", "status": "failed"}], "failed_so_far": ["ci"], "qa": []})
    out = ActionRunner(store, tmp_path).run(req)
    assert out.result.ok and "失败过：ci" in out.manifest.summary
    t = json.loads(store.files[f"runs/{RUN}/docs/takeaway/trail.json"])
    assert (t["myorch_commit"], t["pack_version"]) == ("0123456789ab", "sha256:abc")
    assert not ActionRunner(store, tmp_path).run(req.model_copy(update={"trail": None})).result.ok
    # Host actions write their manifest into out/: the takeaway agent mounts takeaway-trail's out/ as
    # /sandbox/in/takeaway-trail, and a missing directory failed its sandbox (2026-11-09).
    m = json.loads(store.files[f"runs/{RUN}/.myorch/steps/takeaway-trail/out/manifest.json"])
    assert m["step_id"] == "takeaway-trail" and m["status"] == "failed"  # the last run (no trail) failed


# ---- ck-learn: unlearned takeaways from the base branch, within a budget -----------------------------

def learn_ws(tmp_path: Path, entries: dict[str, int], learned: list[str], extra: dict[str, str] | None = None) -> Path:
    """entries: <date>-<run_id> -> size of its takeaway.md, committed on origin/main under myorch/.
    extra: more files committed with them (path in the repo -> text)."""
    ws = tmp_path / "ws"
    repo = ws / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "README").write_text("r\n")
    for name, size in entries.items():
        (repo / "myorch" / name).mkdir(parents=True)
        (repo / "myorch" / name / "task.md").write_text(f"# 任务 {name}\n")
        (repo / "myorch" / name / "takeaway.md").write_text(
            f"# Takeaway\n## 遇到的问题\n- [环境] {name} 的问题\n## 缺了什么知识\n- [项目知识] {'x' * size}\n")
    (repo / "myorch" / "README.md").write_text("not an entry\n")
    for rel, text in (extra or {}).items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "merged")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    (ws / "docs" / "learn").mkdir(parents=True)
    (ws / "docs" / "learn" / "takeaways-learned.json").write_text(json.dumps(learned))
    return ws


def run_collect(tmp_path: Path, ws: Path, token: str = "") -> dict:
    pub = {"name": "mdm", "image": "img", "repo": "o/r"}
    flow = load_flow(read_flow_file(FLOWS / "learn.yaml", {"runs": ""}, pub))
    assert flow.step("takeaways-learned").needs == ["apply"]
    script = script_of(flow.step("collect"), tmp_path, {"runs": ""})
    env = {**os.environ, "MYORCH_WORKSPACE": str(ws), "MYORCH_INPUT_RUNS": "", "MYORCH_PROJECT_REPO": "o/r",
           "MYORCH_PROJECT_BASE": "main", "GITHUB_TOKEN": token, "GIT_TERMINAL_PROMPT": "0",
           # Offline: a clone of github.com goes to a path that does not exist and fails at once.
           "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"url.file://{tmp_path}/nowhere/.insteadOf",
           "GIT_CONFIG_VALUE_0": "https://github.com/"}
    subprocess.run([sys.executable, "-c", script], env=env, check=True, capture_output=True)
    return json.loads((tmp_path / "manifest.json").read_text())


def test_collect_takes_unlearned_takeaways_oldest_first(tmp_path: Path):
    ws = learn_ws(tmp_path, {"2026-11-03-ck-development-cccc": 10, "2026-11-01-ck-development-aaaa": 10,
                             "2026-11-02-ck-development-bbbb": 10}, learned=["ck-development-aaaa"])
    m = run_collect(tmp_path, ws)
    assert (m["outputs"]["takeaways"], m["outputs"]["takeaways_left"]) == (2, 0)
    picked = json.loads((ws / "docs" / "learn" / "takeaways.json").read_text())["picked"]
    assert [p["run"] for p in picked] == ["ck-development-bbbb", "ck-development-cccc"]
    assert (ws / "docs" / "learn" / "takeaways" / "2026-11-02-ck-development-bbbb" / "task.md").is_file()
    assert "2026-11-02-ck-development-bbbb 的问题" in (ws / "docs" / "learn" / "signals.md").read_text()


def test_collect_keeps_to_its_budget_and_leaves_the_rest(tmp_path: Path):
    # Each takeaway.md is cut to 60 000 characters (about 60 KB here); the budget is 300 KB per learn run.
    ws = learn_ws(tmp_path, {f"2026-11-0{i}-ck-development-r{i}": 70_000 for i in range(1, 8)}, learned=[])
    m = run_collect(tmp_path, ws)
    assert (m["outputs"]["takeaways"], m["outputs"]["takeaways_left"]) == (4, 3)
    picked = json.loads((ws / "docs" / "learn" / "takeaways.json").read_text())["picked"]
    assert [p["run"] for p in picked] == [f"ck-development-r{i}" for i in range(1, 5)]


def test_collect_without_takeaways_or_a_repo(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "repo").mkdir(parents=True)  # no clone, and no network for one: noted, not fatal
    m = run_collect(tmp_path, ws)
    assert m["outputs"]["takeaways"] == 0 and m["status"] == "ok"


def test_the_learned_ledger_lives_in_myorch_data(tmp_path: Path):
    from myorch.cli import _learn_files

    data = tmp_path / "myorch-data"
    assert _learn_files(None, {"runs": ""}, data, "mdm") == {takeaways.LEARNED_FILE: "[]"}
    store = Store({f"runs/ck-learn-1/{takeaways.PICKED_FILE}": json.dumps({"picked": [{"run": "ck-development-aaaa"}]})})
    req = ActionRequest(key=StepKey(run_id="ck-learn-1", step_id="takeaways-learned", attempt=1),
                        step=Step(id="takeaways-learned", action="takeaways-learned"), context={"project": {"name": "mdm"}})
    runner = ActionRunner(store, tmp_path / "kb", data_root=data)
    out = runner.run(req)
    assert out.result.ok and out.manifest.outputs == {"recorded": 1, "read": 1}
    assert runner.run(req).manifest.outputs["recorded"] == 0  # a retry records nothing new
    assert takeaways.learned(data, "mdm") == {"ck-development-aaaa": "ck-learn-1"}
    assert json.loads(_learn_files(None, {"runs": ""}, data, "mdm")[takeaways.LEARNED_FILE]) == ["ck-development-aaaa"]
    assert takeaways.ledger(data, "mdm") == data / "learn" / "mdm" / "takeaways.json"


def test_the_ledger_skips_what_is_not_a_run_id(tmp_path: Path):
    assert takeaways.record(tmp_path, "mdm", ["../x", "a/b", "Ck-Upper", "ck-development-ok1"], "ck-learn-1") == ["ck-development-ok1"]
    assert list(takeaways.learned(tmp_path, "mdm")) == ["ck-development-ok1"]


def test_collect_and_the_ledger_use_the_same_run_id_pattern():
    pub = {"name": "mdm", "image": "img", "repo": "o/r"}
    collect = load_flow(read_flow_file(FLOWS / "learn.yaml", {"runs": ""}, pub)).step("collect").prompt
    assert f"-({takeaways.RUN_ID})$" in collect
    assert f"^{takeaways.RUN_ID}$" in ck_flow().step("takeaway-commit").prompt


def test_the_ledger_merges_concurrent_learns(tmp_path: Path):
    import threading

    threads = [threading.Thread(target=takeaways.record, args=(tmp_path, "mdm", [f"ck-development-r{i}"], f"ck-learn-{i}"))
               for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(takeaways.learned(tmp_path, "mdm")) == sorted(f"ck-development-r{i}" for i in range(8))


def test_collect_skips_unreadable_and_badly_named_entries(tmp_path: Path):
    ws = learn_ws(tmp_path, {"2026-11-01-ck-development-good": 10}, learned=[], extra={
        "myorch/2026-11-02-ck-development-notakeaway/task.md": "# 任务\n",          # no takeaway.md
        "myorch/2026-11-03-ck-development-empty/takeaway.md": "\n",                  # empty
        "myorch/2026-11-04-CK-Upper_Case/takeaway.md": "# Takeaway\n",              # not a run id
    })
    m = run_collect(tmp_path, ws)
    data = json.loads((ws / "docs" / "learn" / "takeaways.json").read_text())
    assert [p["run"] for p in data["picked"]] == ["ck-development-good"]  # only this one gets recorded as learned
    assert data["unreadable"] == ["ck-development-notakeaway", "ck-development-empty"]
    assert "2 条读不到" in m["summary"] and m["outputs"]["takeaways"] == 1


def test_collect_notes_never_carry_the_token(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "repo").mkdir(parents=True)
    token = "ghs_" + "s" * 36
    m = run_collect(tmp_path, ws, token=token)
    note = json.loads((ws / "docs" / "learn" / "takeaways.json").read_text())["note"]
    assert note.startswith("读不到仓库的 myorch/（git 退出码")
    import base64
    for leak in (token, base64.b64encode(f"x-access-token:{token}".encode()).decode(), "Authorization", "extraHeader"):
        assert leak not in note and leak not in m["summary"]


def test_takeaway_redact_uses_the_shared_rules(tmp_path: Path):
    pat = "abcdefghijklmnopqrstuvwxyz234567abcdefghijklmnopqrst"  # an Azure DevOps PAT (old format)
    store = Store({f"runs/{RUN}/docs/takeaway/task.md": "# 任务\nclone https://bob:hunter22@dev.azure.com/x\n",
                   f"runs/{RUN}/docs/takeaway/takeaway.md": f"- [环境] Authorization: Bearer abcdefghijklmnop1234\n- pat {pat}\n"
                                                             f"- commit {'0123456789abcdef' * 2}\n"})
    step = Step(id="takeaway-redact", action="takeaway-redact", args={"files": "docs/takeaway/task.md docs/takeaway/takeaway.md"})
    out = ActionRunner(store, tmp_path).run(ActionRequest(key=StepKey(run_id=RUN, step_id="takeaway-redact", attempt=1),
                                                          step=step, context={}))
    assert out.result.ok and out.manifest.outputs == {"files": 2, "redacted": 3}
    text = store.files[f"runs/{RUN}/docs/takeaway/task.md"] + store.files[f"runs/{RUN}/docs/takeaway/takeaway.md"]
    assert "hunter22" not in text and "abcdefghijklmnop1234" not in text and pat not in text
    assert "0123456789abcdef" * 2 in text  # a hash is not a secret
    empty = Store({f"runs/{RUN}/docs/takeaway/task.md": "", f"runs/{RUN}/docs/takeaway/takeaway.md": "  \n"})
    assert not ActionRunner(empty, tmp_path).run(ActionRequest(key=StepKey(run_id=RUN, step_id="takeaway-redact", attempt=1),
                                                                step=step, context={})).result.ok


def test_run_trail_drops_secrets_and_clears_the_agents_files(tmp_path: Path):
    store = Store({f"runs/{RUN}/docs/takeaway/takeaway.md": "an earlier visit's text"})
    step = Step(id="takeaway-trail", action="run-trail",
                args={"path": "docs/takeaway/trail.json", "clear": "docs/takeaway/task.md docs/takeaway/takeaway.md"})
    trail = {"run": RUN, "steps": [{"id": "ci", "status": "failed",
                                    "error": "git push https://x-access-token:ghs_abcdefabcdef@github.com/o/r\nexit 128"}], "qa": []}
    out = ActionRunner(store, tmp_path).run(ActionRequest(key=StepKey(run_id=RUN, step_id="takeaway-trail", attempt=1),
                                                          step=step, context={}, trail=trail))
    assert out.result.ok and out.manifest.outputs["redacted"] == 1
    t = json.loads(store.files[f"runs/{RUN}/docs/takeaway/trail.json"])
    assert "ghs_abcdef" not in t["steps"][0]["error"] and t["steps"][0]["error"].endswith("exit 128")
    assert store.files[f"runs/{RUN}/docs/takeaway/takeaway.md"] == "" and store.files[f"runs/{RUN}/docs/takeaway/task.md"] == ""


def test_a_step_skipped_after_failing_reports_the_failure():
    from myorch.core.events import derive_events
    from myorch.core.run import skip_after_failure

    state = RunState.create(RUN, "flow: f\nsteps:\n  - {id: a, harness: script, profile: p, prompt: x, on_failed: skip}\n", {})
    state.plan()
    inst = state.instances["a"]
    inst.attempts, inst.turns = 1, 1
    inst.manifest = Manifest(step_id="a", status="failed", summary="boom", outputs={"items": 2})
    skip_after_failure(inst, "boom")
    assert inst.manifest.outputs == {} and state.context(inst)["steps"]["a"]["outputs"] == {}
    done = [(e.key, e.data["status"]) for e in derive_events(state, detail=True) if e.kind == "step_finished"]
    assert done == [("done:a:1.1:failed", "failed"), ("done:a:1.1:skipped", "skipped")]
    state.plan()
    assert state.status == "succeeded"


@pytest.mark.parametrize("line, secret", [
    ("Authorization: Bearer abcdefghijklmnop1234", True),
    ("-H 'authorization: basic eDp5enp6enp6enp6eno='", True),
    ('curl -H "Authorization: Bearer $GITHUB_TOKEN"', False),
    ("Authorization: Bearer <token>", False),
    ("https://user:pass@example.com/x", True),
    ("see https://github.com/o/r and http://localhost:8800/", False),
    ("abcdefghijklmnopqrstuvwxyz234567abcdefghijklmnopqrst", True),
    ("0123456789abcdef" * 3 + "0123", False),  # 52 hex characters: a hash, not a PAT
    ("sha256:" + "a1" * 32, False),
])
def test_the_shared_secret_rules(line, secret):
    from myorch.knowledge import scan_text

    assert bool(scan_text(line)) is secret
