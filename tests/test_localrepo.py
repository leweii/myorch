"""Which local clone a run's repo/ is seeded from (myorch/localrepo.py)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from myorch.localrepo import find_seed


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def clone(tmp: Path, url: str = "git@github.com:example-org/master-data-management.git") -> Path:
    c = tmp / "clone"
    c.mkdir()
    git(c, "init", "-q")
    git(c, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
    git(c, "remote", "add", "origin", url)
    git(c, "update-ref", "refs/remotes/origin/master", "HEAD")
    return c


def config(tmp: Path, text: str) -> Path:
    data = tmp / "data"
    data.mkdir()
    (data / "local-repos.yaml").write_text(text)
    return data


def test_a_matching_clone_is_used(tmp_path: Path):
    c = clone(tmp_path)
    s = find_seed(config(tmp_path, f"mdm: {c}\n"), "mdm", "example-org/master-data-management", "master")
    assert s.git_dir == (c / ".git").resolve() and s.reason == str(c)


def test_no_entry_or_no_file_skips(tmp_path: Path):
    assert find_seed(tmp_path, "mdm", "example-org/master-data-management", "master").git_dir is None
    data = config(tmp_path, "blog: /nowhere\n")
    assert find_seed(data, "mdm", "example-org/master-data-management", "master").git_dir is None


def test_a_clone_that_does_not_fit_skips(tmp_path: Path):
    c = clone(tmp_path, url="https://github.com/someone/other.git")
    data = config(tmp_path, f"mdm: {c}\nother: {tmp_path / 'missing'}\n")
    other = find_seed(data, "mdm", "example-org/master-data-management", "master")
    assert other.git_dir is None and "someone/other" in other.reason
    assert "does not exist" in find_seed(data, "other", "x/y", "master").reason


def test_a_clone_without_the_base_skips(tmp_path: Path):
    c = clone(tmp_path)
    s = find_seed(config(tmp_path, f"mdm: {c}\n"), "mdm", "example-org/master-data-management", "main")
    assert s.git_dir is None and "origin/main" in s.reason
