"""ck-development in replay mode (`at`, for the development eval): the code as it was at a commit,
with every later commit unreachable and no origin remote; never an approval, never a delivery."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from myorch.core.expr import truthy
from myorch.core.flow import load_flow, render_template
from myorch.flowfile import read_flow_file

ROOT = Path(__file__).resolve().parents[1]
PROJECT = {"name": "mdm", "repo": "o/r", "base": "master", "image": "img", "branch": "b",
           "e2e": {"image": "", "profile": "dev-read", "acceptance": True},
           "jira": {"url": "", "key": "MD", "auth": "basic", "email": ""}}


def _flow(**inputs):
    inputs = {"task": "", "ticket": "1", "from_run": "", "at": "", "ticket_as_of": "", **inputs}
    return inputs, load_flow(read_flow_file(ROOT / "flows" / "development.yaml", inputs, PROJECT))


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name)
    _git(repo, "add", name)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", name)
    return _git(repo, "rev-parse", "HEAD")


def _checkout(tmp_path: Path, **inputs) -> tuple[subprocess.CompletedProcess, Path, dict]:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "master")
    shas = {"a": _commit(origin, "a"), "b": _commit(origin, "b"), "c": _commit(origin, "c")}
    _git(origin, "checkout", "-qb", "feature")
    shas["d"] = _commit(origin, "d")
    _git(origin, "checkout", "-q", "master")
    ws = tmp_path / "ws"
    (ws / "memory").mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(ws / "repo")], check=True)
    _git(ws / "repo", "fetch", "-q", "origin", "master")  # leaves FETCH_HEAD, like sync-master
    inputs = {k: shas.get(v, v) for k, v in inputs.items()}
    rendered, flow = _flow(**inputs)
    step = next(s for s in flow.all_steps() if s.id == "checkout-branch")
    script = render_template(step.prompt, {"inputs": rendered, "project": PROJECT, "run": {"id": "r"}, "steps": {}})
    script = script.replace("/sandbox/out/manifest.json", str(tmp_path / "manifest.json"))
    script = script.replace("/tmp/replay-cut.sh", str(tmp_path / "replay-cut.sh"))
    env = {**os.environ, "MYORCH_WORKSPACE": str(ws), "MYORCH_BRANCH": "MD-1-myorch-r", "MYORCH_PROJECT_BASE": "master",
           "MYORCH_PROJECT_SUBMODULES": "", "MYORCH_GIT_NAME": "myorch", "MYORCH_GIT_EMAIL": "m@x",
           "MYORCH_INPUT_AT": inputs.get("at", ""), "MYORCH_INPUT_FROM_RUN": inputs.get("from_run", ""), "GITHUB_TOKEN": "x"}
    r = subprocess.run(["bash", "-c", script], cwd=ws / "repo", env=env, capture_output=True, text=True)
    return r, ws / "repo", shas


def test_a_replay_starts_at_the_commit_with_the_future_removed(tmp_path: Path):
    r, repo, shas = _checkout(tmp_path, at="a")
    assert r.returncode == 0, r.stderr
    assert _git(repo, "rev-parse", "HEAD") == shas["a"]
    assert _git(repo, "branch", "--show-current") == "MD-1-myorch-r"
    assert _git(repo, "remote") == ""                                   # deliver's `push origin` has nowhere to go
    assert _git(repo, "rev-parse", "origin/master") == shas["a"]        # later diffs compare with the base
    assert _git(repo, "rev-list", "--count", "--all") == "1"
    for later in ("b", "c", "d"):                                      # not on any ref, and not in the object store
        assert subprocess.run(["git", "-C", str(repo), "cat-file", "-e", shas[later]], capture_output=True).returncode != 0
    assert not (repo / ".git" / "FETCH_HEAD").exists()
    summary = json.loads((tmp_path / "manifest.json").read_text())["summary"]
    assert "重放" in summary and shas["a"][:12] in summary


def test_replay_and_continue_do_not_mix(tmp_path: Path):
    r, _, _ = _checkout(tmp_path, at="a", from_run="development-1")
    assert r.returncode == 3 and "不能同时给" in r.stderr


@pytest.mark.parametrize("step", ["human-review", "deliver"])
def test_a_replay_never_asks_for_approval_and_never_delivers(step: str):
    """Evaluated the way the engine does (core/run.py: evaluate(step.when, context)): `when` is NOT
    template-rendered, so a `{{inputs.x}}` inside it stays a literal string. The first version of this
    test rendered it first and passed while every normal run skipped approval and delivery (2026-11-09)."""
    for at, runs in (("abc", False), ("", True)):
        inputs, flow = _flow(at=at)
        s = next(x for x in flow.all_steps() if x.id == step)
        ctx = {"inputs": inputs, "project": PROJECT, "run": {"id": "r"},
               "steps": {"develop": {"outputs": {"proceed": True}}}}
        assert truthy(s.when, ctx) is runs


def test_no_when_expression_uses_an_inputs_template():
    """`when` sees inputs as `inputs.x`; `'{{inputs.x}}'` is a literal that never changes."""
    import re

    flows_dir = Path(__file__).resolve().parent.parent / "flows"
    bad = [f"{f.relative_to(flows_dir)}:{i}" for f in flows_dir.rglob("*.yaml")
           for i, line in enumerate(f.read_text().splitlines(), 1)
           if re.search(r"^\s*(when|until):.*\{\{\s*(inputs|steps|run)\.", line)]
    assert bad == []
