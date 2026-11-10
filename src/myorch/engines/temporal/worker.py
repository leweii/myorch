from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from myorch.engines.temporal import TASK_QUEUE
from myorch.engines.temporal.activities import Activities
from myorch.engines.temporal.replay import start_replay
from myorch.engines.temporal.schedules import LaunchActivities, ScheduledLaunch
from myorch.engines.temporal.workflow import FlowWorkflow
from myorch.runner.actions import ActionRunner
from myorch.runner.harnesses import default_harnesses
from myorch.runner.runner import StepRunner
from myorch.storage.docker_volume import DockerVolumeStore
from myorch.storage.events import EventLog


OUTPUT_KEEP = 7 * 86400  # seconds


async def connect(address: str) -> Client:
    return await Client.connect(address, data_converter=pydantic_data_converter)


async def run_worker(address: str, config_dir: Path, volume: str, projects_root: Path,
                     events_path: Path | None = None) -> None:
    client = await connect(address)
    store = DockerVolumeStore(volume)
    events = EventLog(events_path or config_dir.parent / "myorch-data" / "events.db")
    events.prune_output(OUTPUT_KEEP)  # live output is only for watching; the log files keep everything
    runner = StepRunner(store, default_harnesses(), config_dir, projects_root=projects_root, output=events)
    loop = asyncio.get_running_loop()

    def launch(req: dict) -> str:  # host actions run in the thread pool; starting a run needs the event loop
        coro = start_replay(client, config_dir, projects_root, req["case"], req["depth"], req["by"], req["source"],
                            run_id=req.get("run_id"))
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=120)

    data_root = (events_path or config_dir.parent / "myorch-data" / "events.db").parent
    actions = ActionRunner(store, projects_root, config_dir=config_dir, data_root=data_root, launch=launch)
    activities = Activities(store, runner, actions, events, data_root=data_root)
    launches = LaunchActivities(client, store, config_dir, projects_root, events)  # schedules (schedules.py)
    with ThreadPoolExecutor(max_workers=16) as executor:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[FlowWorkflow, ScheduledLaunch],
            activities=[*activities.all(), *launches.all()],
            activity_executor=executor,
        )
        print(f"myorch worker listening on {address}, task queue {TASK_QUEUE!r}")
        await worker.run()
