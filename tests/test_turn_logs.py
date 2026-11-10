"""Turn logs are named per attempt (turn-<attempt>-<turn>.log), a new attempt keeps the earlier
attempts' logs, and every reader finds both the new names and the old turn-<turn>.log of earlier runs."""

import os
import subprocess

import pytest

from myorch.channels.claude_code import ChannelServer
from myorch.core.types import StepKey
from myorch.storage.base import step_dir, turn_log, turn_log_order
from myorch.storage.docker_volume import DockerVolumeStore


@pytest.fixture
def store(tmp_path):
    """A DockerVolumeStore whose helper scripts run on this machine, with tmp_path as the volume."""
    vol = tmp_path / "vol"
    vol.mkdir()
    shims = tmp_path / "shims"
    shims.mkdir()
    (shims / "chown").write_text("#!/bin/sh\nexit 0\n")  # no uid 1000 here
    (shims / "chown").chmod(0o755)
    s = DockerVolumeStore()

    def sh(script, stdin=None):
        res = subprocess.run(["sh", "-c", script.replace("/v/", f"{vol}/")], input=stdin, capture_output=True,
                             text=True, env={**os.environ, "PATH": f"{shims}:{os.environ['PATH']}"})
        if res.returncode:
            raise RuntimeError(res.stderr)
        return res.stdout

    s._sh = sh
    s.root = vol
    return s


def write_logs(store, run, iid, names):
    d = store.root / step_dir(run, iid) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_text(f"<{n}>")
    return d


def test_turn_log_names_and_order():
    assert turn_log(2, 1) == "turn-2-1.log" and turn_log(2, 1, "jsonl") == "turn-2-1.jsonl"
    assert turn_log_order("turn-2-1.log") == (2, 1)
    assert turn_log_order("turn-3.log") == (0, 3)  # an old name
    assert turn_log_order("turn-2-1.jsonl") is None and turn_log_order("supervisor-1-2.log") is None


def test_a_new_attempt_keeps_the_earlier_attempts_logs(store):
    d = write_logs(store, "r", "develop", ["turn-1-1.log", "turn-1-1.jsonl", "turn-1-2.log", "supervisor-1-2.log"])
    out = store.root / step_dir("r", "develop") / "out"
    out.mkdir()
    (out / "manifest.json").write_text("{}")
    store.prepare_step(StepKey(run_id="r", step_id="develop", attempt=2, turn=1))
    assert sorted(p.name for p in d.iterdir()) == ["supervisor-1-2.log", "turn-1-1.jsonl", "turn-1-1.log", "turn-1-2.log"]
    assert list(out.iterdir()) == []  # the record of the attempt is still reset


def test_the_log_tail_is_the_newest_attempt_and_turn(store):
    write_logs(store, "r", "a", ["turn-1.log", "turn-2.log", "turn-1-1.log", "turn-1-2.log", "turn-2-1.log",
                                 "turn-2-1.jsonl", "turn-10-0.jsonl", "supervisor-3-1.log"])
    assert store.read_log_tail("r", "a") == "<turn-2-1.log>"
    assert store.read_log_tail("r", "a", file="turn-1-2.log") == "<turn-1-2.log>"
    assert store.read_log_tail("r", "a", file="turn-9-9.log") == ""  # this turn wrote no log


def test_the_log_tail_of_an_old_run_still_works(store):
    write_logs(store, "r", "a", ["turn-1.log", "turn-2.log", "turn-10.log", "supervisor-1-1.log"])
    assert store.read_log_tail("r", "a") == "<turn-10.log>"
    assert store.read_log_tail("r", "nothing-yet") == ""


def test_a_log_name_cannot_leave_the_logs_dir(store):
    with pytest.raises(ValueError):
        store.read_log_tail("r", "a", file="../out/manifest.json")


class LogsApi:
    def __init__(self, names):
        self.names = names

    async def get(self, endpoint, /, **params):
        if endpoint.endswith("/files"):
            return [f"{params['dir']}/{n}" for n in self.names]
        return {"text": f"<{params['path'].rsplit('/', 1)[-1]}>"}


async def log_tool(tmp_path, names, **args):
    from myorch.channels.claude_code import Subscription

    s = ChannelServer(LogsApi(names), Subscription("me", "mine", []), tmp_path / "cursor.json")
    text = await s.call("log", {"run_id": "r", "instance": "develop", **args})
    return text.split(">", 1)[0] + ">"


async def test_the_log_tool_reads_new_and_old_names(tmp_path):
    names = ["turn-1.log", "turn-1-1.log", "turn-1-1.jsonl", "turn-1-2.log", "turn-2-1.log", "turn-2-1.jsonl",
             "supervisor-1-2.log", "supervisor-2-1.log"]
    assert await log_tool(tmp_path, names) == "<turn-2-1.log>"
    assert await log_tool(tmp_path, names, attempt=1) == "<turn-1-2.log>"
    assert await log_tool(tmp_path, names, attempt=1, turn=1) == "<turn-1-1.log>"
    assert await log_tool(tmp_path, names, which="supervisor") == "<supervisor-2-1.log>"
    old = ["turn-1.log", "turn-2.log", "turn-2.jsonl", "supervisor-1-2.log"]
    assert await log_tool(tmp_path, old) == "<turn-2.log>"
    assert await log_tool(tmp_path, old, turn=1) == "<turn-1.log>"
    with pytest.raises(ValueError, match="没有 turn-"):
        await log_tool(tmp_path, old, turn=3)
