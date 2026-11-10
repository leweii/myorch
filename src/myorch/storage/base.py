from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from myorch.core.manifest import Manifest, Question
from myorch.core.types import StepKey


class MountSource(BaseModel, frozen=True):
    """Where a directory lives on the shared storage, in driver-neutral terms."""

    volume: str
    subpath: str


def run_dir(run_id: str) -> str:
    return f"runs/{run_id}"


def step_dir(run_id: str, instance_id: str) -> str:
    return f"{run_dir(run_id)}/.myorch/steps/{instance_id}"


def turn_log(attempt: int, turn: int, ext: str = "log") -> str:
    """File name of a turn's log in the step's logs/ (`.jsonl`: the harness's raw events). Named per
    attempt like supervisor-<attempt>-<turn>.log, so a retry never overwrites an earlier attempt. Runs
    from before 2026-11-06 have turn-<turn>.log (only the last attempt's)."""
    return f"turn-{int(attempt)}-{int(turn)}.{ext}"


TURN_LOG = re.compile(r"turn-(?:(\d+)-)?(\d+)\.log")


def turn_log_order(name: str) -> tuple[int, int] | None:
    """(attempt, turn) of a turn log's file name, None for other files. Old names count as attempt 0:
    a resumed old run's new attempts sort after them."""
    m = TURN_LOG.fullmatch(name)
    return (int(m[1] or 0), int(m[2])) if m else None


def project_snapshot_dir(name: str, version: str = "") -> str:
    """projects/<name>/<hash>/: one immutable snapshot per version (version is sha256:<16>).
    projects/<name>/.version names the newest one, which new runs use."""
    base = f"projects/{name}"
    return f"{base}/{version.removeprefix('sha256:')}" if version else base


def shared_dir(project: str, name: str) -> str:
    return f"shared/{project}/{name}"


def job_dir(key: str) -> str:
    return f"jobs/{key}"


def knowledge_dir(name: str, version: str = "") -> str:
    """knowledge/<name>/<hash>/: one immutable snapshot per version (version is sha256:<16>)."""
    base = f"knowledge/{name}"
    return f"{base}/{version.removeprefix('sha256:')}" if version else base


class ArtifactStore(Protocol):
    """Layout on the shared volume.

    tools/                                   helper commands, mounted read-only at /sandbox/bin
    projects/<name>/<hash>/                  project pack snapshots, one per version, mounted read-only at
                                             workspace/project; projects/<name>/.version is the newest
    shared/<project>/<name>/                 state shared across runs of a project (caches, rotation)
    jobs/<key>/                              one-off sandbox jobs (channels)
    runs/<run_id>/                           the run's workspace, mounted at /sandbox/workspace
        repo/                                code, shared by every step of the run
        docs/                                documents the agents produce
        memory/MEMORY.md                     notes shared across steps of the run
        .claude/<instance>/                  per-instance harness config and sessions
        .myorch/flow.yaml                    flow snapshot
        .myorch/steps/<instance>/out/        manifest.json, .myorch/question.json
        .myorch/steps/<instance>/logs/       turn-<attempt>-<turn>.log/.jsonl, supervisor-<attempt>-<turn>.log

    Runs never share anything except the read-only project snapshots and the shared dirs a step
    explicitly asks for. A run mounts the snapshot version it uses (RunState.project_version):
    syncing a pack does not change what a running run sees, unless the run itself wrote the pack
    back (project-apply) and moved on to the new version.
    """

    def workspace_source(self, run_id: str) -> MountSource: ...

    def repo_source(self, run_id: str) -> MountSource: ...

    def out_source(self, run_id: str, instance_id: str) -> MountSource: ...

    def logs_source(self, run_id: str, instance_id: str) -> MountSource: ...

    def project_source(self, name: str, version: str = "") -> MountSource:
        """The snapshot of `version`. When it is not stored (pruned, or synced before snapshots had
        versions), the newest one; with no versioned snapshot at all, projects/<name>/ itself."""
        ...

    def project_sync(self, name: str, local_dir: Path, version: str) -> None:
        """Store a snapshot of a local directory as projects/<name>/<version>/ (immutable once
        written) and make it the newest. Old versions are dropped once no run can still use them."""
        ...

    def shared_source(self, project: str, name: str) -> MountSource: ...

    def ensure_shared(self, project: str, name: str) -> None:
        """Create shared/<project>/<name>/ owned by the sandbox user."""
        ...

    def tools_source(self) -> MountSource: ...

    def init_run(self, run_id: str, flow_yaml: str, project: str | None) -> str | None:
        """Create the workspace skeleton and store the flow snapshot.

        Returns the version of the project snapshot the run will use (for the mandate).
        """
        ...

    def write_run_file(self, run_id: str, rel_path: str, text: str) -> None:
        """Write a file into the run's workspace (e.g. .myorch/mandate.json)."""
        ...

    def seed_repo(self, run_id: str, git_dir: Path, repo: str, base: str) -> str:
        """Fill the run's repo/ from a local clone's origin/<base> (myorch/localrepo.py).
        Returns the copied sha, or "" when repo/ already holds a repository."""
        ...

    def prepare_step(self, key: StepKey) -> None:
        """Make the instance's dirs exist and be writable by the sandbox user.

        turn 1 (new attempt): empty its out/. logs/ and the harness session dir are kept: earlier
        attempts' logs are the record of what was tried (file names carry the attempt), and their
        sessions (each attempt has its own session id) are what a retry after a platform
        interruption continues.
        later turns: keep everything, only drop the previous turn's question.
        The shared workspace (repo/, docs/, memory/) is never reset.
        """
        ...

    def session_files(self, run_id: str, instance_id: str, names: list[str]) -> set[str]:
        """Which of the file names exist anywhere under the instance's harness session dir
        (.claude/<instance>/), e.g. <session id>.jsonl transcripts."""
        ...

    def set_aside_session_file(self, run_id: str, instance_id: str, name: str) -> None:
        """Rename every file or directory called `name` under the instance's session dir to
        <name>.replaced-<time>, kept for the record: its session id is about to be created anew."""
        ...

    def knowledge_source(self, name: str, version: str) -> MountSource: ...

    def knowledge_sync(self, name: str, version: str, local_dir: Path, keep: int = 5) -> None:
        """Store a snapshot of a local directory as knowledge/<name>/<version>/ (immutable once
        written), then drop all but the newest `keep` versions."""
        ...

    def knowledge_adopt_job(self, key: str, name: str, version: str, keep: int = 5) -> None:
        """Move jobs/<key>/out/tree/ (a snapshot built in a sandbox job) to knowledge/<name>/<version>/."""
        ...

    def knowledge_versions(self, name: str) -> list[dict]:
        """Stored snapshots, newest first: {version, synced_at, files, bytes}."""
        ...

    def job_source(self, key: str) -> MountSource: ...

    def prepare_job(self, key: str, files: dict[str, str]) -> None:
        """Fresh jobs/<key>/ with in/<files> and an empty out/, writable by the sandbox user."""
        ...

    def read_job_result(self, key: str) -> str | None: ...

    def read_manifest(self, run_id: str, instance_id: str) -> Manifest | None: ...

    def read_question(self, run_id: str, instance_id: str) -> Question | None: ...

    def list_outputs(self, run_id: str, instance_id: str) -> list[str]: ...

    def read_log_tail(self, run_id: str, instance_id: str, max_bytes: int = 4000, file: str = "") -> str:
        """Tail of the log file `file` (a turn_log name), by default of the latest turn's log (highest
        attempt, then turn; old turn-<turn>.log names count as attempt 0). "" when there is none."""
        ...

    def follow_log(self, run_id: str, instance_id: str, file: str, name: str, max_seconds: int,
                   idle: float = 0.5) -> AsyncIterator[str]:
        """The log file `file` (a turn_log name) as it grows (async generator). Yields "" when idle for
        `idle` seconds. `name` names the follower (a container, for the docker store)."""
        ...

    def run_records(self) -> list[tuple[str, str, str]]:
        """(run_id, mandate JSON, push-and-pr manifest JSON or "") for every run on the volume."""
        ...

    def review_bundle(self, run_id: str, base: str) -> dict[str, str]:
        """Docs, Mandate, step manifests and the branch diff of a run (path -> text), for review."""
        ...
