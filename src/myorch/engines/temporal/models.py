from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from myorch.core.events import RunEvent
from myorch.core.run import KnowledgeRef, RunState


class FlowInput(BaseModel):
    flow_yaml: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    initiator: str = "unknown"  # who started the run; recorded in the mandate
    initiator_aliases: list[str] = Field(default_factory=list)
    source: str = ""
    project: dict[str, Any] | None = None  # Project.public() of the project the run belongs to
    files: dict[str, str] = Field(default_factory=dict)  # written into the workspace at start (e.g. the original request)
    knowledge: list[KnowledgeRef] = Field(default_factory=list)  # fixed at start (myorch/knowledge.py resolve)
    labels: list[str] = Field(default_factory=list)  # what the run is found by (core/inputs.py start_labels)
    # Continue a failed or cancelled run (client.resume_run): its last state, and which steps to run
    # again (instance or step ids; empty: every failed and cancelled step).
    resume: RunState | None = None
    resume_steps: list[str] = Field(default_factory=list)
    resumed_by: str = ""


class InitRunInput(BaseModel):
    run_id: str
    flow_yaml: str
    project: str | None = None


class SeedRepoInput(BaseModel):
    run_id: str
    project: str
    repo: str  # owner/name on GitHub
    base: str


class RunFileInput(BaseModel):
    run_id: str
    rel_path: str
    text: str


class CollectInput(BaseModel):
    run_id: str
    instance_id: str


class ReplyInput(BaseModel):
    step_id: str  # instance id, or a loop id for an exhausted loop
    text: str


class ApproveInput(BaseModel):
    step_id: str
    approver: str


class ChangesInput(BaseModel):
    """An approver sends the run back with change requests instead of approving."""

    step_id: str
    approver: str
    text: str


class PublishInput(BaseModel):
    run_id: str
    events: list[RunEvent]


class LaunchInput(BaseModel):
    """What a schedule starts each time it fires (engines/temporal/schedules.py)."""

    name: str  # the schedule's name
    flow: str  # flows/<name>.yaml, relative to the config dir
    project: str | None = None
    inputs: dict[str, str] = Field(default_factory=dict)
    knowledge: list[str] | None = None  # None: the project's defaults at launch time
    initiator: str  # the person who created the schedule: the runs' initiator
    skip_if_running: bool = True  # do not start while this schedule's previous run is still running

