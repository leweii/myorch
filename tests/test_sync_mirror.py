"""ck-sync-base keeps a bare mirror in the project's shared dir and clones through it (dissociated)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from myorch.core.flow import load_flow, render_template
from myorch.flowfile import read_flow_file

ROOT = Path(__file__).resolve().parents[1]
PROJECT = {"name": "mdm", "repo": "o/r", "base": "master", "image": "img", "branch": "b",
           "e2e": {"image": "", "profile": "dev-read", "acceptance": True},
           "jira": {"url": "", "key": "MD", "auth": "basic", "email": ""}}


def _git(repo: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name)
    _git(repo, "add", name)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", name)
    return _git(repo, "rev-parse", "HEAD")


def _sync(tmp: Path, origin: Path, run: str) -> subprocess.CompletedProcess:
    inputs = {"task": "", "ticket": "1", "from_run": "", "at": "", "ticket_as_of": "", "ticket_comments": "all"}
    flow = load_flow(read_flow_file(ROOT / "flows" / "development.yaml", inputs, PROJECT))
    step = next(s for s in flow.all_steps() if s.id == "sync-master")
    assert "git-mirror" in step.shared
    script = render_template(step.prompt, {"inputs": inputs, "project": PROJECT, "run": {"id": run}, "steps": {}})
    script = (script.replace("https://github.com/$MYORCH_PROJECT_REPO.git", str(origin))
              .replace("base64 -w0", "base64").replace("/sandbox/out/manifest.json", str(tmp / f"{run}.json")))
    ws = tmp / run
    for d in ("repo", "memory"):
        (ws / d).mkdir(parents=True)
    (ws / "shared").symlink_to(tmp / "shared")  # the shared dir outlives runs
    shim = tmp / "bin"                                  # macOS has no flock; the sandbox image does
    if not shutil.which("flock") and not shim.exists():
        shim.mkdir()
        (shim / "flock").write_text("#!/bin/sh\nexit 0\n")
        (shim / "flock").chmod(0o755)
    env = {**os.environ, "PATH": f"{shim}:{os.environ['PATH']}", "MYORCH_WORKSPACE": str(ws), "MYORCH_PROJECT_REPO": "o/r", "MYORCH_PROJECT_BASE": "master",
           "MYORCH_PROJECT_SUBMODULES": "", "GITHUB_TOKEN": "x"}
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)


def test_runs_clone_through_a_shared_mirror(tmp_path: Path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "master")
    first = _commit(origin, "a")
    (tmp_path / "shared" / "git-mirror").mkdir(parents=True)
    r = _sync(tmp_path, origin, "r1")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "shared" / "git-mirror" / "repo.git" / "objects").is_dir()
    second = _commit(origin, "b")                       # the base moved on: the mirror fetches it
    r = _sync(tmp_path, origin, "r2")
    assert r.returncode == 0, r.stderr
    repo = tmp_path / "r2" / "repo"
    assert _git(repo, "rev-parse", "HEAD") == second and _git(repo, "rev-list", "--count", "HEAD") == "2"
    assert not (repo / ".git" / "objects" / "info" / "alternates").exists()   # dissociated: owns its objects
    assert _git(tmp_path / "shared" / "git-mirror" / "repo.git", "rev-parse", "master") == second
    assert first


def test_no_shared_dir_falls_back_to_a_plain_clone(tmp_path: Path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "master")
    _commit(origin, "a")
    (tmp_path / "shared").mkdir()                       # shared mounted, but no git-mirror dir
    r = _sync(tmp_path, origin, "r1")
    assert r.returncode == 0, r.stderr
    assert _git(tmp_path / "r1" / "repo", "rev-list", "--count", "HEAD") == "1"
