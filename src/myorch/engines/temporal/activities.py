from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from temporalio import activity

from myorch.core.manifest import Manifest, resolve_outcome
from myorch.core.types import StepKey, StepResult
from myorch.engines.temporal.models import CollectInput, InitRunInput, PublishInput, RunFileInput, SeedRepoInput
from myorch.runner.actions import ActionOutcome, ActionRequest, ActionRunner
from myorch.runner.runner import StepRequest, StepRunner
from myorch.storage.base import ArtifactStore
from myorch.storage.events import EventLog


class TemporalRunContext:
    def heartbeat(self, detail: Mapping[str, Any] | None = None) -> None:
        activity.heartbeat(dict(detail or {}))

    def is_cancelled(self) -> bool:
        return activity.is_cancelled()


class Activities:
    """Each activity only converts arguments and forwards to the core library."""

    def __init__(self, store: ArtifactStore, runner: StepRunner, actions: ActionRunner,
                 events: EventLog | None = None, data_root: Path | None = None) -> None:
        self.store = store
        self.data_root = data_root  # this machine's settings (local-repos.yaml)
        self.runner = runner
        self.actions = actions
        self.events = events

    @activity.defn
    def init_run(self, inp: InitRunInput) -> str | None:
        return self.store.init_run(inp.run_id, inp.flow_yaml, inp.project)

    @activity.defn
    def seed_repo(self, inp: SeedRepoInput) -> str:
        """Fill repo/ from a clone on this machine (myorch/localrepo.py). Never fails the run: when
        there is no usable clone, or the copy fails, sync-master clones from GitHub as before."""
        from myorch.localrepo import find_seed

        if self.data_root is None:
            return "skipped: no data dir"
        seed = find_seed(self.data_root, inp.project, inp.repo, inp.base)
        if seed.git_dir is None:
            return f"skipped: {seed.reason}"
        activity.heartbeat()
        try:
            sha = self.store.seed_repo(inp.run_id, seed.git_dir, inp.repo, inp.base)
        except RuntimeError as e:
            activity.logger.warning("seed_repo %s from %s failed: %s", inp.run_id, seed.reason, e)
            return f"skipped: copy failed ({e})"
        return f"seeded from {seed.reason} @ {sha}" if sha else "skipped: repo/ already has a repository"

    @activity.defn
    def write_run_file(self, inp: RunFileInput) -> None:
        self.store.write_run_file(inp.run_id, inp.rel_path, inp.text)

    @activity.defn
    def prepare_step(self, key: StepKey) -> None:
        self.store.prepare_step(key)

    @activity.defn
    async def run_step(self, req: StepRequest) -> StepResult:
        return await self.runner.run(req, TemporalRunContext())

    @activity.defn
    def collect_manifest(self, inp: CollectInput) -> Manifest:
        """How the turn ended: ok / failed / needs_input / manifest_missing."""
        return resolve_outcome(
            inp.instance_id,
            self.store.read_manifest(inp.run_id, inp.instance_id),
            self.store.read_question(inp.run_id, inp.instance_id),
            self.store.list_outputs(inp.run_id, inp.instance_id),
        )

    @activity.defn
    def run_action(self, req: ActionRequest) -> ActionOutcome:
        return self.actions.run(req)

    @activity.defn
    async def cleanup_step(self, key: StepKey) -> None:
        await self.runner.cleanup(key)

    @activity.defn
    def publish_events(self, inp: PublishInput) -> int:
        """Append the run's new events to the event log (keys already there are skipped)."""
        if self.events is None:
            return 0
        return self.events.append(inp.run_id, [(e.key, e.kind, e.step, e.data) for e in inp.events])

    def all(self) -> list:
        return [self.init_run, self.seed_repo, self.write_run_file, self.prepare_step, self.run_step, self.collect_manifest,
                self.run_action, self.cleanup_step, self.publish_events]
