"""The channel relay: what every platform adapter (the console, GitHub, Slack, ...) uses.

The relay only forwards, in three directions, and keeps no run state of its own:

    events    myorch-data/events.db (written by the worker) -> adapters, in seq order
    commands  adapters -> Temporal client (start / reply / approve / reject / request_changes / cancel),
              with the person behind the adapter's account filled in from channels.yaml identities
    content   a run's workspace (docs/, diff, logs, step outputs) -> adapters, read on demand, not copied

Everything about one platform (how to connect, whom to listen to, how to word things, delivery)
belongs to that platform's adapter. Design: docs/notes/2026-11-02-channel-layer.md.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from myorch.channels.model import ChannelsConfig, load_config
from myorch.core.inputs import label_matches
from myorch.engines.temporal import client as tc
from myorch.runs import flow_inputs_of, inherited_labels, label_index, run_labels, started_lookup
from myorch.storage.base import run_dir, step_dir
from myorch.storage.events import EventLog, LoggedEvent, OutputChunk

RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
log = logging.getLogger(__name__)
REQUEST_FILE = "docs/external/request.md"
FOLLOW_INTERVAL = 0.5  # seconds between event-log reads while an adapter follows the log
SEARCH_ENGINE_RUNS = 5000  # a search looks up the engine status of this many newest runs
# The engine status of a run Temporal no longer keeps, from its run_finished event.
ENDED_STATUS = {"succeeded": "COMPLETED", "rejected": "COMPLETED", "failed": "FAILED", "cancelled": "CANCELED"}


def check_run_id(run_id: str) -> str:
    if not RUN_ID.match(run_id or ""):
        raise ValueError(f"invalid run id: {run_id!r}")
    return run_id


def safe_rel(path: str) -> str:
    """A path inside a run's workspace: relative, no `..`."""
    p = (path or "").strip().lstrip("/")
    if not p or any(part in ("..", "") for part in p.split("/")):
        raise ValueError(f"invalid path: {path!r}")
    return p


def request_file(message: str, person: str, via: str) -> str:
    return (f"# 原始请求（来自 {via}，发起人 {person}）\n\n"
            "以下是发起人写的原文。`task` 输入只是一句话的摘要，以这里的原文为准。\n\n"
            f"{message.strip()}\n")


class Relay:
    def __init__(self, client, store, events: EventLog, config_dir: Path, kb_root: Path,
                 cfg: ChannelsConfig | None = None) -> None:
        self.client = client
        self.store = store
        self.events_log = events
        self.config_dir = config_dir
        self.kb_root = kb_root
        self.cfg = cfg or load_config(config_dir / "channels.yaml")

    # ---- who ---------------------------------------------------------------------------

    def person(self, via: str, account: str) -> str:
        """The person behind an account on a platform (channels.yaml identities; else the account)."""
        if account in self.cfg.identities:
            return account
        return self.cfg.person(via, account)

    # ---- events: myorch -> adapters ---------------------------------------------------

    def events(self, after: int = 0, run_id: str | None = None, limit: int = 500) -> list[LoggedEvent]:
        return self.events_log.read(after=after, run_id=check_run_id(run_id) if run_id else None, limit=limit)

    async def follow(self, after: int = 0, run_id: str | None = None) -> AsyncIterator[LoggedEvent]:
        """Every event after `after`, as the worker writes them. A reader that reconnects with the last
        seq it saw continues without gaps or repeats."""
        while True:
            batch = await asyncio.to_thread(self.events, after, run_id)
            for e in batch:
                after = e.seq
                yield e
            if not batch:
                await asyncio.sleep(FOLLOW_INTERVAL)

    # ---- live output of running steps -------------------------------------------------

    def output(self, run_id: str, step: str | None = None, after: int = 0, limit: int = 500) -> list[OutputChunk]:
        return self.events_log.read_output(check_run_id(run_id), step or None, after, limit)

    async def follow_output(self, run_id: str, step: str | None = None, after: int = 0) -> AsyncIterator[OutputChunk]:
        """A run's (or one step's) live output after `after`, as the worker writes it."""
        while True:
            batch = await asyncio.to_thread(self.output, run_id, step, after)
            for c in batch:
                after = c.seq
                yield c
            if not batch:
                await asyncio.sleep(FOLLOW_INTERVAL)

    async def runs(self, limit: int = 60, label: str | None = None) -> list[dict[str, Any]]:
        """Recent runs: what each run_started event said, with the engine's status and the run's labels
        (myorch/runs.py). The status comes from Temporal because a run terminated from outside (temporal
        workflow terminate) never writes run_finished.
        `label`: only runs with this label (any case), or whose id contains it. The whole log is searched
        before `limit` applies; a run Temporal no longer keeps (older than its retention) is found too,
        with the status of its run_finished event."""
        started, ended = await asyncio.to_thread(self._started_events)
        labels = label_index(started, flow_inputs_of(self.config_dir / "flows", self.kb_root))

        def row(rid: str, status: str) -> dict[str, Any]:
            return {"run_id": rid, "status": status, **started.get(rid, {}), "labels": labels.get(rid, [])}

        if not (label or "").strip():
            return [row(rid, status) for rid, status in await tc.list_runs(self.client, limit)]
        live = await tc.list_runs(self.client, SEARCH_ENGINE_RUNS)
        known = {rid for rid, _ in live}
        hits = [row(rid, status) for rid, status in live if label_matches(label, rid, labels.get(rid, []))]
        hits += [row(rid, ENDED_STATUS.get(ended.get(rid, ""), "UNKNOWN")) for rid in reversed(started)
                 if rid not in known and label_matches(label, rid, labels[rid])]
        return hits[:limit]

    def run_labels(self, run_id: str) -> list[str]:
        """One run's labels, from its run_started and those of the runs its inputs name."""
        lookup = started_lookup(self.events_log)
        started = lookup(check_run_id(run_id))
        if started is None:
            return []
        return run_labels(run_id, started, lookup, flow_inputs_of(self.config_dir / "flows", self.kb_root))

    def _started_events(self) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
        """run id -> run_started data (oldest first), and run id -> the status of its last run_finished."""
        started: dict[str, dict[str, Any]] = {}
        ended: dict[str, str] = {}
        after = 0
        while True:
            batch = self.events_log.read(after=after, limit=2000)
            if not batch:
                return started, ended
            for e in batch:
                if e.kind == "run_started":
                    started.setdefault(e.run_id, e.data)
                elif e.kind == "run_finished":
                    ended[e.run_id] = str(e.data.get("status") or "")
                elif e.kind == "run_resumed":
                    ended.pop(e.run_id, None)
            after = batch[-1].seq

    # ---- commands: adapters -> myorch -------------------------------------------------

    def flow_path(self, flow: str) -> Path:
        name = flow.removeprefix("flows/").removesuffix(".yaml")
        if "/" in name or not name:
            raise ValueError(f"unknown flow {flow!r}")
        p = self.config_dir / "flows" / f"{name}.yaml"
        if not p.is_file():
            raise ValueError(f"unknown flow {flow!r}")
        return p

    async def start(self, flow: str, project: str | None, inputs: dict[str, Any], message: str, *,
                    via: str, account: str, source: str = "", knowledge: list[str] | None = None,
                    labels: list[str] | None = None) -> str:
        """Start a run. The message is the request: saved verbatim in the run, and it fills the flow's
        message input (core/inputs.py) unless the caller gave one.
        `knowledge`: the knowledge sources to mount (None: the project's defaults), fixed to their
        newest snapshots now (myorch/knowledge.py).
        `labels`: what the run is found by, besides the tickets in its inputs and the labels of the runs
        its inputs name (added here and in tc.start_run)."""
        from myorch import knowledge as kb

        from myorch.cli import (_auto_runs, _continue_files, _continue_from, _flow_doc, _flow_inputs, _learn_files,
                                _load_project, _normalize_inputs, _project_public, _run_records)
        from myorch.core import inputs as fi
        from myorch.flowfile import flow_meta, read_flow_file

        person = self.person(via, account)
        path = self.flow_path(flow)
        meta = flow_meta(path)
        if meta["projects"] and project not in meta["projects"]:
            raise ValueError(f"流程 {meta['flow']} 只给项目 {', '.join(meta['projects'])} 用")
        inputs = {str(k): str(v) for k, v in (inputs or {}).items() if str(v).strip()}
        declared, text = _flow_inputs(path, _project_public(self.kb_root, project)), (message or "").strip()
        _normalize_inputs(declared, inputs)
        fi.fill_from_message(declared, inputs, text)
        if inputs.get("runs") == "auto" or ("runs" in declared and "runs" not in inputs):
            inputs["runs"] = _auto_runs(await _run_records(self.client, self.store), project, meta["role"])
        if any(inputs.get(n) for n in fi.run_inputs(declared)):
            await asyncio.to_thread(_continue_from, self.events_log, inputs, project, declared)
        fi.check(declared, inputs, _flow_doc(path).get("require_one_of"))
        pub = _load_project(self.kb_root, project, inputs, path)
        flow_yaml = read_flow_file(path, inputs, pub)
        files = {REQUEST_FILE: request_file(text, person, via)} if text else {}
        if meta["role"] == "learn":
            files.update(await asyncio.to_thread(_learn_files, self.events_log, inputs, Path(self.events_log.path).parent,
                                                 project))
        if any(inputs.get(n) for n in fi.run_inputs(declared)):  # continuing a run: what people said to it
            files.update(await asyncio.to_thread(_continue_files, self.events_log, inputs, declared,
                                                 Path(self.events_log.path).parent))
        for note in await asyncio.to_thread(kb.refresh, self.store, self.kb_root, project, knowledge):
            log.info("%s", note)  # a git source that could not be refreshed runs on its last snapshot
        refs = await asyncio.to_thread(kb.resolve, self.store, self.kb_root, project, knowledge)
        labels = [*(labels or []), *await asyncio.to_thread(inherited_labels, self.events_log, inputs,
                                                             flow_inputs_of(self.config_dir / "flows", self.kb_root))]
        return await tc.start_run(self.client, flow_yaml, inputs, person, self.cfg.aliases(person), pub,
                                  source=source or via, files=files, knowledge=refs, labels=labels)

    async def reply(self, run_id: str, step: str, text: str, *, via: str, account: str) -> None:
        if not (text or "").strip():
            raise ValueError("回复不能为空。")
        await tc.reply(self.client, check_run_id(run_id), step, text)

    async def approve(self, run_id: str, step: str, *, via: str, account: str) -> None:
        await tc.approve(self.client, check_run_id(run_id), step, self.person(via, account))

    async def reject(self, run_id: str, step: str, *, via: str, account: str) -> None:
        await tc.reject(self.client, check_run_id(run_id), step, self.person(via, account))

    async def request_changes(self, run_id: str, step: str, text: str, *, via: str, account: str) -> None:
        await tc.request_changes(self.client, check_run_id(run_id), step, self.person(via, account), text)

    async def cancel(self, run_id: str, *, via: str, account: str) -> None:
        await tc.cancel(self.client, check_run_id(run_id))

    async def resume(self, run_id: str, steps: list[str] | None, *, via: str, account: str) -> list[str]:
        """Continue a failed or cancelled run from its failed steps (all of them, or `steps`)."""
        return await tc.resume_run(self.client, check_run_id(run_id), steps, self.person(via, account))

    # ---- content: myorch -> adapters --------------------------------------------------

    async def list_files(self, run_id: str, rel: str = "docs", depth: int = 4) -> list[str]:
        base = f"{run_dir(check_run_id(run_id))}/{safe_rel(rel)}"
        return await asyncio.to_thread(self.store.list_files, base, depth)

    async def read_file(self, run_id: str, rel: str) -> str | None:
        return await asyncio.to_thread(self.store.read_file, f"{run_dir(check_run_id(run_id))}/{safe_rel(rel)}")

    async def read_bytes(self, run_id: str, rel: str, max_bytes: int) -> bytes | None:
        return await asyncio.to_thread(self.store.read_bytes, f"{run_dir(check_run_id(run_id))}/{safe_rel(rel)}", max_bytes)

    async def step_files(self, run_id: str, instance: str, log_bytes: int = 30000) -> dict[str, Any]:
        rid = check_run_id(run_id)
        manifest, outputs, log = await asyncio.gather(
            asyncio.to_thread(self.store.read_manifest, rid, instance),
            asyncio.to_thread(self.store.list_outputs, rid, instance),
            asyncio.to_thread(self.store.read_log_tail, rid, instance, log_bytes))
        return {"instance": instance, "dir": step_dir(rid, instance),
                "manifest": manifest.model_dump() if manifest else None, "files": outputs, "log": log}

    async def changes(self, run_id: str, base: str) -> dict[str, str]:
        """The run's change against origin/<base> and its review material (path -> text)."""
        return await asyncio.to_thread(self.store.review_bundle, check_run_id(run_id), base)
