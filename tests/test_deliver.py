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


def publish_step(auto: bool = True) -> dict:
    project = {**PROJECT, "flows": {"development": {"auto_publish": auto}}}
    flow = yaml.safe_load(read_flow_file(FLOWS / "development.yaml", {"task": "x"}, project))
    return next(s for s in flow["steps"] if s.get("id") == "deliver"), next(s for s in flow["steps"] if s.get("id") == "human-review")


def run_published(tmp_path: Path, ws: Path, repo: Path, reviewed: str):
    deliver, _ = publish_step()
    script = deliver["prompt"].replace("{{approvals.human-review.subject}}", "").replace("{{steps.risk-rules.outputs.head_sha}}", reviewed)
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    env = {**os.environ, "MYORCH_PROJECT_BASE": "main", "MYORCH_PROJECT_REPO": "o/r", "MYORCH_BRANCH": "MD-5-run",
           "MYORCH_WORKSPACE": str(ws), "MYORCH_OUT_DIR": str(out), "MYORCH_INPUT_TICKET": "MD-5",
           "GITHUB_TOKEN": "unused", "MYORCH_PROJECT_PR_TEMPLATE": "", "MYORCH_RUN_ID": "development-1"}
    return subprocess.run(["bash", "-c", script], cwd=repo, env=env, capture_output=True, text=True), out / "manifest.json"


def test_auto_publish_skips_approval_and_pushes_the_reviewed_commit_to_base(tmp_path: Path):
    deliver, review = publish_step()
    from myorch.core.expr import evaluate
    ctx = {"inputs": {"at": ""}, "steps": {"develop": {"outputs": {"proceed": True}}}}
    assert evaluate(review["when"], {**ctx, "project": {"flows": {"development": {"auto_publish": True}}}}) is False
    assert evaluate(review["when"], {**ctx, "project": {"flows": {"development": {"auto_publish": False}}}}) is True
    ws, repo, origin = workspace(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    res, manifest = run_published(tmp_path, ws, repo, head)
    assert res.returncode == 0, res.stderr
    assert git(origin, "rev-parse", "refs/heads/main") == head and git(origin, "rev-parse", "refs/heads/MD-5-run") == head
    d = json.loads(manifest.read_text())["outputs"]["delivery"]
    assert d["published"] is True and "Published straight to main without approval" in d["description"]


def test_auto_publish_refuses_a_commit_that_was_not_reviewed(tmp_path: Path):
    ws, repo, origin = workspace(tmp_path)
    reviewed = git(repo, "rev-parse", "HEAD")
    (repo / "A.java").write_text("class A { int y; }\n")
    git(repo, "commit", "-qam", "unreviewed")
    res, _ = run_published(tmp_path, ws, repo, reviewed)
    assert res.returncode == 3 and "HEAD changed" in res.stderr
    assert git(origin, "rev-parse", "refs/heads/main") != git(repo, "rev-parse", "HEAD")


def test_auto_publish_does_not_overwrite_a_base_that_moved(tmp_path: Path):
    ws, repo, origin = workspace(tmp_path)
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(origin), str(other))
    (other / "B.txt").write_text("b\n")
    git(other, "add", "-A")
    git(other, "commit", "-qm", "someone else")
    git(other, "push", "-q", "origin", "main")
    moved = git(origin, "rev-parse", "refs/heads/main")
    res, _ = run_published(tmp_path, ws, repo, git(repo, "rev-parse", "HEAD"))
    assert res.returncode == 4 and "could not fast-forward" in res.stderr
    assert git(origin, "rev-parse", "refs/heads/main") == moved


def test_without_auto_publish_the_approval_gate_stays():
    deliver, review = publish_step(auto=False)
    assert 'export DELIVER_PUBLISH="false"' in deliver["prompt"]
    off = yaml.safe_load(read_flow_file(FLOWS / "development.yaml", {"task": "x"}, PROJECT))
    assert any(s.get("id") == "human-review" and "approval" in s for s in off["steps"])


def test_open_pr_follows_the_project_setting():
    on = {**PROJECT, "flows": {"development": {"open_pr": True}}}
    flow = yaml.safe_load(read_flow_file(FLOWS / "development.yaml", {"task": "x"}, on))
    step = next(s for s in flow["steps"] if s.get("id") == "deliver")
    assert 'export DELIVER_OPEN_PR="true"' in step["prompt"]


def media_repo(tmp_path: Path, ws: Path) -> Path:
    """A media repository (project.media) as checkout-branch leaves it: a clone of its base in <ws>/media."""
    origin = tmp_path / "media.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "master", str(origin))
    media = ws / "media"
    git(tmp_path, "clone", "-q", str(origin), str(media))
    git(media, "config", "user.name", "myorch")  # checkout-branch sets the identity in the clone
    git(media, "config", "user.email", "myorch@example.com")
    (media / "projects").mkdir()
    (media / "projects" / "old.png").write_bytes(b"old")
    (media / "2020-post").mkdir()
    (media / "2020-post" / "a.png").write_bytes(b"post")
    git(media, "add", "-A")
    git(media, "commit", "-qm", "base")
    git(media, "push", "-q", "origin", "HEAD:master")
    return origin


MEDIA_ENV = {"MYORCH_PROJECT_MEDIA_REPO": "o/pic", "MYORCH_PROJECT_MEDIA_BASE": "master", "MYORCH_PROJECT_MEDIA_PATHS": "projects/"}


def test_media_files_are_pushed_before_the_branch(tmp_path: Path):
    ws, repo, origin = workspace(tmp_path)
    morigin = media_repo(tmp_path, ws)
    (ws / "media" / "projects" / "sound-pin").mkdir()
    (ws / "media" / "projects" / "sound-pin" / "menu.png").write_bytes(b"png")
    res, manifest = run(tmp_path, ws, repo, git(repo, "rev-parse", "HEAD"), env_extra=MEDIA_ENV)
    assert res.returncode == 0, res.stderr
    assert git(morigin, "show", "master:projects/sound-pin/menu.png") == "png"
    assert git(origin, "rev-parse", "refs/heads/MD-5-run") == git(repo, "rev-parse", "HEAD")
    d = json.loads(manifest.read_text())["outputs"]["delivery"]
    assert d["media"] == {"repo": "o/pic", "files": ["projects/sound-pin/menu.png"]}
    assert "Also pushed to o/pic (master), before the branch: projects/sound-pin/menu.png" in d["description"]


def test_no_media_changes_push_nothing_there(tmp_path: Path):
    ws, repo, _ = workspace(tmp_path)
    morigin = media_repo(tmp_path, ws)
    before = git(morigin, "rev-parse", "master")
    res, manifest = run(tmp_path, ws, repo, git(repo, "rev-parse", "HEAD"), env_extra=MEDIA_ENV)
    assert res.returncode == 0, res.stderr
    assert git(morigin, "rev-parse", "master") == before
    assert json.loads(manifest.read_text())["outputs"]["delivery"]["media"] is None


@pytest.mark.parametrize("change", ["delete", "outside"])
def test_media_deletions_and_files_outside_paths_are_refused(tmp_path: Path, change: str):
    ws, repo, origin = workspace(tmp_path)
    morigin = media_repo(tmp_path, ws)
    before = git(morigin, "rev-parse", "master")
    if change == "delete":
        (ws / "media" / "projects" / "old.png").unlink()
    else:
        (ws / "media" / "2020-post" / "b.png").write_bytes(b"x")
    res, _ = run(tmp_path, ws, repo, git(repo, "rev-parse", "HEAD"), env_extra=MEDIA_ENV)
    assert res.returncode != 0
    assert ("deletes projects/old.png" if change == "delete" else "2020-post/b.png is outside projects/") in res.stderr
    assert git(morigin, "rev-parse", "master") == before
    assert subprocess.run(["git", "rev-parse", "--verify", "-q", "refs/heads/MD-5-run"], cwd=origin).returncode != 0


def upstream(tmp_path: Path, morigin: Path, files: dict[str, bytes]) -> str:
    """Another run pushes `files` to the media base after this run cloned it (development-897f1a4b)."""
    other = tmp_path / "other-media"
    if not other.exists():
        git(tmp_path, "clone", "-q", str(morigin), str(other))
    for name, data in files.items():
        (other / name).parent.mkdir(parents=True, exist_ok=True)
        (other / name).write_bytes(data)
    git(other, "add", "-A")
    git(other, "commit", "-qm", "another run")
    git(other, "push", "-q", "origin", "HEAD:master")
    return git(morigin, "rev-parse", "master")


def refuse_pushes(morigin: Path) -> None:
    """The remote refuses every push, as GitHub does when the token cannot push (403)."""
    hook = morigin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'remote: Permission denied (403)' >&2\nexit 1\n")
    hook.chmod(0o755)


def no_branch(origin: Path) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "-q", "refs/heads/MD-5-run"], cwd=origin).returncode != 0


def test_media_push_rebases_onto_a_base_that_moved(tmp_path: Path):
    ws, repo, origin = workspace(tmp_path)
    morigin = media_repo(tmp_path, ws)
    moved = upstream(tmp_path, morigin, {"projects/other/x.png": b"x", "2020-post/a.png": b"changed"})
    (ws / "media" / "projects" / "sound-pin").mkdir()
    (ws / "media" / "projects" / "sound-pin" / "menu.png").write_bytes(b"png")
    res, manifest = run(tmp_path, ws, repo, git(repo, "rev-parse", "HEAD"), env_extra=MEDIA_ENV)
    assert res.returncode == 0, res.stderr
    assert git(morigin, "rev-parse", "master~1") == moved  # on top of the other run's commit: no force, no merge
    assert git(morigin, "show", "master:projects/sound-pin/menu.png") == "png"
    assert git(morigin, "show", "master:projects/other/x.png") == "x"
    assert git(morigin, "show", "master:2020-post/a.png") == "changed"
    assert git(origin, "rev-parse", "refs/heads/MD-5-run") == git(repo, "rev-parse", "HEAD")
    assert json.loads(manifest.read_text())["outputs"]["delivery"]["media"]["files"] == ["projects/sound-pin/menu.png"]


def test_media_conflict_with_the_moved_base_pushes_nothing(tmp_path: Path):
    ws, repo, origin = workspace(tmp_path)
    morigin = media_repo(tmp_path, ws)
    cloned = git(ws / "media", "rev-parse", "HEAD")
    moved = upstream(tmp_path, morigin, {"projects/old.png": b"theirs"})
    (ws / "media" / "projects" / "old.png").write_bytes(b"ours")
    res, _ = run(tmp_path, ws, repo, git(repo, "rev-parse", "HEAD"), env_extra=MEDIA_ENV)
    assert res.returncode == 4
    assert "changed a file this run also changed" in res.stderr and "Nothing was pushed" in res.stderr
    assert git(morigin, "rev-parse", "master") == moved
    assert no_branch(origin)
    gitdir = ws / "media" / ".git"
    assert not (gitdir / "rebase-merge").exists() and not (gitdir / "rebase-apply").exists()
    assert git(ws / "media", "rev-parse", "HEAD") == cloned  # as before: the run's change is staged, not committed
    assert git(ws / "media", "diff", "--cached", "--name-only") == "projects/old.png"
    assert (ws / "media" / "projects" / "old.png").read_bytes() == b"ours"


def test_media_push_refused_without_a_moved_base_does_not_rebase(tmp_path: Path):
    ws, repo, origin = workspace(tmp_path)
    morigin = media_repo(tmp_path, ws)
    before = git(morigin, "rev-parse", "master")
    refuse_pushes(morigin)
    (ws / "media" / "projects" / "new.png").write_bytes(b"png")
    res, _ = run(tmp_path, ws, repo, git(repo, "rev-parse", "HEAD"), env_extra=MEDIA_ENV)
    assert res.returncode == 4
    assert "(the token cannot push there). Nothing was pushed" in res.stderr
    assert git(morigin, "rev-parse", "master") == before
    assert no_branch(origin)


def test_media_push_refused_again_after_the_rebase(tmp_path: Path):
    ws, repo, origin = workspace(tmp_path)
    morigin = media_repo(tmp_path, ws)
    moved = upstream(tmp_path, morigin, {"projects/other/x.png": b"x"})
    refuse_pushes(morigin)
    (ws / "media" / "projects" / "new.png").write_bytes(b"png")
    res, _ = run(tmp_path, ws, repo, git(repo, "rev-parse", "HEAD"), env_extra=MEDIA_ENV)
    assert res.returncode == 4
    assert "it moved again" in res.stderr and "Nothing was pushed" in res.stderr
    assert git(morigin, "rev-parse", "master") == moved
    assert no_branch(origin)
    assert git(ws / "media", "rev-parse", "HEAD") == moved  # the retry commits again on top of the fetched base
    assert git(ws / "media", "diff", "--cached", "--name-only") == "projects/new.png"


@pytest.mark.parametrize("moved", [False, True])
def test_media_in_a_sparse_blobless_clone_of_a_private_repository(tmp_path: Path, moved: bool):
    """checkout-branch clones the media repository with --filter=blob:none and only its paths checked out, so the
    root .gitignore is a missing blob that `git add` fetches on demand. The repository is private: like GitHub,
    the remote here answers only git calls that carry the token (development-8f05459f failed with exit 128).
    moved: another run pushed after the clone, so the fetch and the rebase need the token too (development-897f1a4b)."""
    ws, repo, origin = workspace(tmp_path)
    morigin = tmp_path / "media.git"
    seed = tmp_path / "seed"
    git(tmp_path, "init", "-q", "--bare", "-b", "master", str(morigin))
    git(morigin, "config", "uploadpack.allowFilter", "true")
    git(morigin, "config", "uploadpack.allowAnySHA1InWant", "true")
    git(tmp_path, "init", "-q", "-b", "master", str(seed))
    (seed / ".gitignore").write_text("/.idea\n.DS_Store\n")
    (seed / "projects").mkdir()
    (seed / "projects" / "old.png").write_bytes(b"old")
    (seed / "2020-post").mkdir()
    (seed / "2020-post" / "a.png").write_bytes(b"post")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "base")
    git(seed, "push", "-q", str(morigin), "HEAD:master")
    serve = tmp_path / "serve"  # the remote: refuses a call without an Authorization header, as GitHub does
    serve.write_text('#!/bin/sh\nunset GIT_DIR\ncase "$GIT_CONFIG_PARAMETERS" in *"Authorization: Basic"*) exec git "$1" "$2" ;; esac\n'
                     'echo "fatal: could not read Username for \'https://github.com\'" >&2; exit 128\n')
    serve.chmod(0o755)
    media, auth = ws / "media", ["-c", "protocol.ext.allow=always", "-c", "http.extraHeader=Authorization: Basic eA=="]
    git(tmp_path, *auth, "clone", "-q", "--depth", "1", "--filter=blob:none", "--no-checkout", "--branch", "master",
        f"ext::{serve} %s {morigin}", str(media))
    git(media, "config", "protocol.ext.allow", "always")
    git(media, "config", "user.name", "myorch")
    git(media, "config", "user.email", "myorch@example.com")
    git(media, "sparse-checkout", "set", "--no-cone", "/projects/")
    git(media, *auth, "checkout", "-q")
    assert not (media / ".gitignore").exists()
    if moved:
        (seed / ".gitignore").write_text("/.idea\n.DS_Store\n*.tmp\n")
        (seed / "projects" / "other.png").write_bytes(b"other")
        git(seed, "add", "-A")
        git(seed, "commit", "-qm", "another run")
        git(seed, "push", "-q", str(morigin), "HEAD:master")
    (media / "projects" / "sound-pin").mkdir()
    (media / "projects" / "sound-pin" / "menu.png").write_bytes(b"png")
    res, manifest = run(tmp_path, ws, repo, git(repo, "rev-parse", "HEAD"), env_extra=MEDIA_ENV)
    assert res.returncode == 0, res.stderr
    assert git(morigin, "show", "master:projects/sound-pin/menu.png") == "png"
    if moved:
        assert git(morigin, "rev-parse", "master~1") == git(seed, "rev-parse", "HEAD")
        assert git(morigin, "show", "master:projects/other.png") == "other"
        assert git(morigin, "show", "master:.gitignore") == "/.idea\n.DS_Store\n*.tmp"
    else:
        assert git(morigin, "show", "master:.gitignore") == "/.idea\n.DS_Store"  # the commit kept the files outside the paths
    assert json.loads(manifest.read_text())["outputs"]["delivery"]["media"]["files"] == ["projects/sound-pin/menu.png"]
