"""Seed a run's repo/ from a clone on this machine, so sync-master does not clone everything from GitHub.

Each machine lists its own clones in <data>/local-repos.yaml (default ../myorch-data/local-repos.yaml):

    mdm: ~/Github/master-data-management

Only the clone's refs/remotes/origin/<base> is copied (storage seed_repo): local branches, commits,
uncommitted changes, config and hooks stay behind. sync-master then fetches origin/<base> from GitHub
and resets to it. Without an entry, or when the clone does not fit, nothing happens and sync-master
clones from GitHub as before. A seed never fails the run.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_FILE = "local-repos.yaml"


@dataclass
class Seed:
    git_dir: Path | None  # the clone's git dir to copy from; None: skip
    reason: str  # why it was skipped, or which clone is used


def configured_clone(data_root: Path, project: str) -> Path | None:
    p = data_root / CONFIG_FILE
    if not p.is_file():
        return None
    entry = (yaml.safe_load(p.read_text()) or {}).get(project)
    return Path(str(entry)).expanduser() if entry else None


def _git(path: Path, *args: str) -> str | None:
    res = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)
    return res.stdout.strip() if res.returncode == 0 else None


def _repo_of(url: str) -> str:
    """owner/name from an https or ssh GitHub URL."""
    m = re.search(r"github\.com[:/]+(.+?)(?:\.git)?/?$", url.strip())
    return m.group(1).lower() if m else ""


def find_seed(data_root: Path, project: str, repo: str, base: str) -> Seed:
    """The local clone to seed from, after checking that it is a clone of `repo` with origin/<base>."""
    path = configured_clone(data_root, project)
    if path is None:
        return Seed(None, f"{data_root / CONFIG_FILE} 里没有 {project}")
    if not path.is_dir():
        return Seed(None, f"本机克隆不存在：{path}")
    git_dir = _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not git_dir:
        return Seed(None, f"不是 git 仓库：{path}")
    url = _git(path, "remote", "get-url", "origin") or ""
    if _repo_of(url) != repo.lower():
        return Seed(None, f"{path} 的 origin 是 {url or '（无）'}，不是 {repo}")
    if not _git(path, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{base}"):
        return Seed(None, f"{path} 没有 origin/{base}")
    return Seed(Path(git_dir), str(path))
