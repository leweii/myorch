import json
import os
import subprocess
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True, capture_output=True)


def workspace(tmp_path: Path, passed: bool) -> Path:
    ws = tmp_path / "ws"
    repo, project = ws / "repo", ws / "project"
    repo.mkdir(parents=True)
    project.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "base.txt").write_text("x")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")  # stands in for the fetched base
    git(repo, "checkout", "-qb", "work")
    (repo / "committed.txt").write_text("1")
    git(repo, "add", "committed.txt")
    git(repo, "commit", "-qm", "work")
    (repo / "staged.txt").write_text("2")
    git(repo, "add", "staged.txt")
    (repo / "base.txt").write_text("changed, not staged")
    (repo / "new.sql").write_text("untracked")
    (project / "checks.sh").write_text(
        'cp "$CHECKS_DIR/changed.txt" "$CHECKS_DIR/report.md"\n'
        f'echo \'{{"passed": {"true" if passed else "false"}, "failed": [], "skipped": []}}\' > "$CHECKS_DIR/result.json"\n')
    return ws


def run_checks(ws: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "MYORCH_WORKSPACE": str(ws), "MYORCH_PROJECT_BASE": "main"}
    return subprocess.run([str(TOOLS / "myorch-checks"), "--out", str(tmp_path / "out")], env=env, capture_output=True, text=True)


def test_myorch_checks_sees_committed_staged_unstaged_and_new_files(tmp_path: Path):
    ws = workspace(tmp_path, passed=True)
    res = run_checks(ws, tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    changed = (tmp_path / "out" / "changed.txt").read_text().split()
    assert changed == ["base.txt", "committed.txt", "new.sql", "staged.txt"]
    assert "new.sql" in res.stdout  # the report is printed


def test_myorch_checks_fails_when_the_checks_fail(tmp_path: Path):
    assert run_checks(workspace(tmp_path, passed=False), tmp_path).returncode == 1


def test_myorch_ask_only_where_the_step_may_ask(tmp_path: Path):
    env = {**os.environ, "MYORCH_OUT_DIR": str(tmp_path)}
    res = subprocess.run([str(TOOLS / "myorch-ask"), "Keep it compatible?"], env={**env, "MYORCH_INTERACTION": "never"},
                         capture_output=True, text=True)
    assert res.returncode != 0 and "may not ask the user" in res.stderr and not (tmp_path / ".myorch").exists()
    res = subprocess.run([str(TOOLS / "myorch-ask"), "Keep it compatible?", "--option", "Yes"],
                         env={**env, "MYORCH_INTERACTION": "allowed"}, capture_output=True, text=True)
    assert res.returncode == 0
    assert json.loads((tmp_path / ".myorch" / "question.json").read_text())["options"] == ["Yes"]


def test_myorch_stream_renders_claude_events(tmp_path: Path):
    events = [
        {"type": "system", "subtype": "hook_started"},
        {"type": "system", "subtype": "init", "model": "claude-haiku-5-5"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Look at the files first."},
                                                      {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "a\nb\nc\nd\ne"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True,
                                                  "content": [{"type": "text", "text": "no such file"}]}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}},
        {"type": "result", "subtype": "success", "result": "Done.", "num_turns": 3, "duration_ms": 4200,
         "total_cost_usd": 0.0123},
    ]
    stdin = "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\nnot json\n"
    raw = tmp_path / "raw.jsonl"
    r = subprocess.run([str(TOOLS / "myorch-stream"), str(raw)], input=stdin, capture_output=True, text=True, check=True)
    assert r.stdout.splitlines() == [
        "· session start (claude-haiku-5-5)",
        "Look at the files first.",
        "→ Bash: ls -la",
        "  ← a", "  ← b", "  ← c", "  ← … (5 lines)",
        "  ✗ no such file",
        "Done.",
        "═══ result ═══",  # the final answer was the last text shown: not repeated
        "· success, 3 turns, 4s, $0.01",
        "not json",
    ]
    assert raw.read_text() == stdin
