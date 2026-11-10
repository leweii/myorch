"""Schedules: start a flow on a cron or interval, through Temporal Schedules.

A schedule does not start the flow itself. It starts ScheduledLaunch, a short workflow whose one
activity starts the run the same way the console does (channels/relay.py start): the flow file is
expanded and the knowledge sources resolved when the schedule fires, not when it was created, so
edits to flows/ and new snapshots reach the next scheduled run. The run's initiator is the person
who created the schedule; its source is `schedule:<name>`.

While the schedule's previous run is still running, the launch is skipped (skip_if_running).
"""

from __future__ import annotations

import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

from temporalio import activity, workflow
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
)
from temporalio.common import RetryPolicy

from myorch.engines.temporal import TASK_QUEUE
from myorch.engines.temporal.models import LaunchInput

PREFIX = "myorch-"  # schedule ids: myorch-<name>
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
EVERY = re.compile(r"^(\d+)([smhd])$")


def schedule_id(name: str) -> str:
    if not NAME.match(name):
        raise ValueError(f"schedule name {name!r}: lowercase letters, digits and '-', at most 41 characters")
    return PREFIX + name


def local_time_zone() -> str:
    """The machine's IANA time zone (cron times are local), e.g. Asia/Shanghai."""
    tz = os.environ.get("TZ", "")
    if "/" in tz:
        return tz
    try:
        link = os.readlink("/etc/localtime")
        return link.split("zoneinfo/", 1)[1]
    except (OSError, IndexError):
        return "UTC"


def parse_every(every: str) -> timedelta:
    m = EVERY.match(every.strip())
    if not m:
        raise ValueError(f"--every {every!r}: use e.g. 30s, 10m, 6h, 1d")
    units = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
    return timedelta(**{units[m.group(2)]: int(m.group(1))})


class LaunchActivities:
    """The launch itself, in the worker: it has the config dir, the knowledge root and a client."""

    def __init__(self, client: Client, store, config_dir: Path, kb_root: Path, events) -> None:
        self.client, self.store, self.config_dir, self.kb_root, self.events = client, store, config_dir, kb_root, events

    @activity.defn
    async def launch_scheduled(self, inp: LaunchInput) -> str:
        """The run id started, or "" when skipped because the previous run still runs."""
        from myorch.channels.relay import Relay

        relay = Relay(self.client, self.store, self.events, self.config_dir, self.kb_root)
        if inp.skip_if_running:
            source = f"schedule:{inp.name}"
            mine = {e.run_id for e in relay.events(0, None, 100000) if e.kind == "run_started" and e.data.get("source") == source}
            for rid, status in await self._statuses():
                if rid in mine and status == "RUNNING":
                    activity.logger.info("schedule %s: %s still running, skipped", inp.name, rid)
                    return ""
        return await relay.start(inp.flow, inp.project, dict(inp.inputs), "", via="schedule", account=inp.initiator,
                                 source=f"schedule:{inp.name}", knowledge=inp.knowledge)

    async def _statuses(self) -> list[tuple[str, str]]:
        from myorch.engines.temporal.client import list_runs

        return await list_runs(self.client, 500)

    def all(self) -> list:
        return [self.launch_scheduled]


@workflow.defn
class ScheduledLaunch:
    @workflow.run
    async def run(self, inp: LaunchInput) -> str:
        return await workflow.execute_activity(
            "launch_scheduled", inp, start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=10)),
        )


async def create(client: Client, inp: LaunchInput, *, cron: str | None = None, every: str | None = None,
                 paused: bool = False, note: str = "") -> None:
    if bool(cron) == bool(every):
        raise ValueError("give exactly one of --cron and --every")
    spec = (ScheduleSpec(cron_expressions=[cron], time_zone_name=local_time_zone()) if cron
            else ScheduleSpec(intervals=[ScheduleIntervalSpec(every=parse_every(every or ""))]))
    sid = schedule_id(inp.name)
    await client.create_schedule(sid, Schedule(
        action=ScheduleActionStartWorkflow(ScheduledLaunch.run, inp, id=f"launch-{inp.name}", task_queue=TASK_QUEUE),
        spec=spec,
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
        # The server turns cron into calendar specs; the note keeps what the person wrote, for `list`.
        state=ScheduleState(paused=paused, note=note or (f"cron {cron} ({local_time_zone()})" if cron else f"every {every}")),
    ))


async def list_schedules(client: Client) -> list[dict[str, Any]]:
    out = []
    async for s in await client.list_schedules():
        if not s.id.startswith(PREFIX):
            continue
        d = await client.get_schedule_handle(s.id).describe()
        act, spec, info = d.schedule.action, d.schedule.spec, d.info
        inp = None
        if isinstance(act, ScheduleActionStartWorkflow) and act.args:
            arg = act.args[0]
            if isinstance(arg, LaunchInput):
                inp = arg
            else:  # describe() returns the encoded payloads
                try:
                    inp = (await client.data_converter.decode([arg], [LaunchInput]))[0]
                except Exception:
                    inp = None
        when = d.schedule.state.note or (", ".join(f"every {i.every}" for i in spec.intervals) if spec.intervals else "?")
        out.append({
            "name": s.id.removeprefix(PREFIX), "when": when, "paused": d.schedule.state.paused,
            "flow": inp.flow if inp else "?", "project": inp.project if inp else None,
            "inputs": inp.inputs if inp else {}, "initiator": inp.initiator if inp else "",
            "next": [t.isoformat(timespec="minutes") for t in info.next_action_times[:3]],
            "recent": [{"at": a.started_at.isoformat(timespec="minutes") if a.started_at else "",
                        "launch": getattr(a.action, "workflow_id", "")} for a in info.recent_actions[-3:]],
        })
    return out


async def pause(client: Client, name: str, paused: bool) -> None:
    h = client.get_schedule_handle(schedule_id(name))
    await (h.pause() if paused else h.unpause())


async def delete(client: Client, name: str) -> None:
    await client.get_schedule_handle(schedule_id(name)).delete()


async def trigger(client: Client, name: str) -> None:
    await client.get_schedule_handle(schedule_id(name)).trigger()
