"""OpenShell CLI calls have a time limit: a hung call is killed, and a status poll that times out is
"unknown, ask again" (2026-11-06, ck-development-56059024: a hung `sandbox get` cost a step)."""

import asyncio
import os

import pytest

import myorch.runner.openshell as osh
from myorch.runner.openshell import OpenShellCLI, OpenShellTimeout


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def gone(pid: int) -> bool:
    for _ in range(50):  # a killed child is reaped by asyncio shortly after
        if not alive(pid):
            return True
        await asyncio.sleep(0.02)
    return False


async def test_a_call_that_hangs_is_killed_at_its_timeout(tmp_path):
    pidfile = tmp_path / "pid"
    with pytest.raises(OpenShellTimeout, match="did not return within 0.3s"):
        await OpenShellCLI("sh")._run(["-c", f"echo $$ > {pidfile}; exec sleep 30"], 0.3)
    assert await gone(int(pidfile.read_text()))


async def test_a_cancelled_call_kills_its_process(tmp_path):
    pidfile = tmp_path / "pid"
    task = asyncio.create_task(OpenShellCLI("sh")._run(["-c", f"echo $$ > {pidfile}; exec sleep 30"], 60))
    while not pidfile.exists() or not pidfile.read_text().strip():
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await gone(int(pidfile.read_text()))


def hanging_binary(tmp_path):
    script = tmp_path / "openshell"
    script.write_text("#!/bin/sh\nexec sleep 30\n")
    script.chmod(0o755)
    return str(script)


async def test_a_status_call_that_times_out_is_unknown_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(osh, "STATUS_TIMEOUT", 0.2)
    assert await OpenShellCLI(hanging_binary(tmp_path)).status("mo-x") is None


async def test_delete_gives_up_on_a_call_that_hangs(tmp_path, monkeypatch):
    monkeypatch.setattr(osh, "DELETE_TIMEOUT", 0.2)
    await OpenShellCLI(hanging_binary(tmp_path)).delete("mo-x")  # logged, not raised: cleanup goes on


async def test_create_that_hangs_raises_an_openshell_error(tmp_path, monkeypatch):
    monkeypatch.setattr(osh, "CREATE_TIMEOUT", 0.2)
    with pytest.raises(osh.OpenShellError):
        await OpenShellCLI(hanging_binary(tmp_path)).create_detached("mo-x", "img", ["true"], [], [])


def printing_binary(tmp_path, text):
    script = tmp_path / "openshell"
    (tmp_path / "out.txt").write_text(text)
    script.write_text(f"#!/bin/sh\ncat {tmp_path / 'out.txt'}\n")
    script.chmod(0o755)
    return str(script)


@pytest.mark.parametrize("out", [
    "",                                   # exit 0, nothing printed
    "upstream connect error or disconnect/reset before headers",  # a proxy's error page
    '{"phase": "Ready", "exit_co',        # cut off
    '["Ready"]',                          # JSON, but not an object
    '{"phase": "Completed", "exit_code": "n/a"}',
])
async def test_a_status_that_cannot_be_parsed_is_unknown_not_an_error(tmp_path, out):
    assert await OpenShellCLI(printing_binary(tmp_path, out)).status("mo-x") is None


async def test_a_status_is_parsed(tmp_path):
    s = await OpenShellCLI(printing_binary(tmp_path, '{"phase": "Completed", "exit_code": 3}')).status("mo-x")
    assert s.phase == "Completed" and s.exit_code == 3 and s.finished
    s = await OpenShellCLI(printing_binary(tmp_path, '{"phase": "Provisioning"}')).status("mo-x")
    assert s.phase == "Provisioning" and s.exit_code is None and not s.finished


async def test_listing_sandboxes_by_label(tmp_path, monkeypatch):
    script = tmp_path / "openshell"
    script.write_text(f'#!/bin/sh\necho "$@" > {tmp_path / "args"}\nprintf "mo-a-1-1\\n\\nmo-a-2-1\\n"\n')
    script.chmod(0o755)
    names = await OpenShellCLI(str(script)).list_names({"myorch.run": "r1", "myorch.step": "develop"})
    assert names == ["mo-a-1-1", "mo-a-2-1"]
    assert (tmp_path / "args").read_text().split() == ["sandbox", "list", "--selector",
                                                      "myorch.run=r1,myorch.step=develop", "--names"]
    failing = tmp_path / "failing"
    failing.write_text("#!/bin/sh\nexit 1\n")
    failing.chmod(0o755)
    assert await OpenShellCLI(str(failing)).list_names({"a": "b"}) is None  # unknown, not "none left"
    monkeypatch.setattr(osh, "STATUS_TIMEOUT", 0.2)
    assert await OpenShellCLI(hanging_binary(tmp_path)).list_names({"a": "b"}) is None
