"""`myorch worker` takes the event log from the data dir, not from next to the code."""

import asyncio
import sys

from myorch import cli
from myorch.engines.temporal import worker


def test_the_worker_writes_its_events_in_the_data_dir(tmp_path, monkeypatch):
    """In a container the data dir (/data) is not next to the code (/app): the worker must not guess it."""
    seen = {}

    async def fake_run_worker(address, config_dir, volume, projects_root, events_path=None):
        seen["events_path"] = events_path

    monkeypatch.setattr(worker, "run_worker", fake_run_worker)
    monkeypatch.setattr(sys, "argv", ["myorch", "--data", str(tmp_path), "--knowledge", str(tmp_path), "worker"])
    monkeypatch.setattr(cli.asyncio, "run", lambda coro: asyncio.new_event_loop().run_until_complete(coro))
    try:
        cli.main()
    except SystemExit:
        pass
    assert seen["events_path"] == tmp_path.resolve() / "events.db"
