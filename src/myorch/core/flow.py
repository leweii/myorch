from __future__ import annotations

import re
from collections.abc import Callable
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    Discriminator,
    Field,
    Tag,
    field_validator,
    model_validator,
)

from myorch.core.expr import compile_expr
from myorch.core.fragment import expand_flow
from myorch.core.types import check_id

_DURATION = re.compile(r"^(\d+)([smhd])$")
_TEMPLATE = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")


def parse_duration(value: str) -> int:
    """'45m' -> 2700 seconds."""
    m = _DURATION.match(value)
    if not m:
        raise ValueError(f"invalid duration {value!r}, expected e.g. 30s, 45m, 2h, 7d")
    n, unit = int(m.group(1)), m.group(2)
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def _check_expr(v: str | None) -> str | None:
    if v is not None:
        compile_expr(v)
    return v


class Limits(BaseModel):
    timeout: str = "30m"
    retries: int = Field(default=0, ge=0, le=5)  # extra attempts after the first failure
    input_timeout: str = "24h"  # how long to wait for a user reply when the agent asks

    @field_validator("timeout", "input_timeout")
    @classmethod
    def _valid(cls, v: str) -> str:
        parse_duration(v)
        return v

    @property
    def timeout_seconds(self) -> int:
        return parse_duration(self.timeout)

    @property
    def input_timeout_seconds(self) -> int:
        return parse_duration(self.input_timeout)


class ApprovalChanges(BaseModel):
    """Where a change request at the gate sends the run: back into `loop`, for up to `rounds` more rounds."""

    loop: str
    rounds: int = Field(default=2, ge=1, le=5)


class Approval(BaseModel):
    """Human approval before a step runs.

    when:    expression; approval is needed only if it is true (default: always)
    count:   how many distinct people must approve; an int or an expression (0 = skip)
    subject: what is being approved, rendered when the gate opens (e.g. a commit SHA:
             "{{steps.risk-rules.outputs.head_sha}}"); later steps can check it is unchanged
             via {{approvals.<step>.subject}}
    require_initiator: the person who started the run must be one of the approvers
    changes: an approver may send change requests instead of approving: the run goes back into
             this loop, and every top-level item after the loop (including this gate) runs again
    remind_after: publish one approval_reminder event when nobody has decided after this long
    timeout: end the gate as "expired" (the run fails) when nobody has decided after this long
    """

    when: str | None = None
    count: int | str = 1
    subject: str | None = None
    require_initiator: bool = True
    changes: ApprovalChanges | None = None
    remind_after: str | None = None
    timeout: str | None = None

    @field_validator("remind_after", "timeout")
    @classmethod
    def _duration(cls, v: str | None) -> str | None:
        if v is not None:
            parse_duration(v)
        return v

    @field_validator("when")
    @classmethod
    def _when(cls, v: str | None) -> str | None:
        return _check_expr(v)

    @field_validator("count")
    @classmethod
    def _count(cls, v: int | str) -> int | str:
        if isinstance(v, str):
            compile_expr(v)
        elif v < 0:
            raise ValueError("approval count must be >= 0")
        return v


class Step(BaseModel):
    id: str
    # None (and no action) makes a pure gate: nothing runs, the step only collects approvals.
    harness: str | None = None
    # A built-in host action instead of a sandboxed harness (see runner/actions.py). Only
    # registered, deterministic actions exist; they never run agent-written code.
    action: Literal["project-apply", "postmortem-replay", "run-trail", "takeaway-redact", "takeaways-learned"] | None = None
    args: dict[str, Any] = Field(default_factory=dict)  # action arguments (templates allowed)
    # Comma-separated run ids (templates allowed) whose workspaces are mounted read-only at
    # /sandbox/workspace/observed/<run_id>/, for steps that learn from past runs.
    observe_runs: str | None = None
    profile: str | None = None
    # Sandbox image for this step only (e.g. a browser image for end-to-end tests). Overrides the
    # profile's and the flow's image; empty or None: use those.
    image: str | None = None
    prompt: str = ""
    needs: list[str] = Field(default_factory=list)
    when: str | None = None  # skip the step (status "skipped") unless this is true
    fanout: int = Field(default=1, ge=1, le=8)  # run N independent replicas in parallel
    outputs: dict[str, str] = Field(default_factory=dict)  # name -> description, asked of the agent
    model: str | None = None  # model for agent harnesses; overrides the profile's and the flow's
    shared: list[str] = Field(default_factory=list)  # project-wide dirs mounted rw at workspace/shared/<name>
    # The run's knowledge sources (Mandate.knowledge, docs/notes/2026-11-03-knowledge-mounts.md):
    # None mounts them for agent steps only; true also for scripts; false never.
    knowledge: bool | None = None
    approval: Approval | None = None
    # never: the agent must not stop to ask; it assumes and records assumptions in notes.
    # allowed: the agent may call myorch-ask, which pauses the step until the user replies.
    interaction: Literal["never", "allowed"] = "never"
    # Expression over this step's own manifest outputs (e.g. "outputs.requires_design_decision").
    # When true and the agent finished without asking, the turn is turned into a question for
    # the user anyway (from outputs.question / outputs.design_question, else the summary).
    escalate_when: str | None = None
    # When the step still fails after its retries: fail the run, or ask the user (retry /
    # retry with a hint in the same session / skip / abort). None: the flow's default.
    # skip: record the failure and go on as if the step were skipped (the run neither stops nor fails);
    # for steps whose result is nice to have, e.g. development's takeaway. Host actions too.
    on_failed: Literal["fail", "ask", "skip"] | None = None
    limits: Limits = Field(default_factory=Limits)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        return check_id(v, "step id")

    @field_validator("approval", mode="before")
    @classmethod
    def _approval_shorthand(cls, v: Any) -> Any:
        if v in (None, "none"):
            return None
        if v == "required":
            return {}
        return v

    @field_validator("when")
    @classmethod
    def _when(cls, v: str | None) -> str | None:
        return _check_expr(v)

    @field_validator("escalate_when")
    @classmethod
    def _escalate(cls, v: str | None) -> str | None:
        return _check_expr(v)

    @model_validator(mode="after")
    def _shape(self) -> Step:
        if self.escalate_when and self.interaction != "allowed":
            raise ValueError(f"step {self.id!r}: escalate_when needs interaction: allowed")
        if self.action is not None:
            if self.harness is not None:
                raise ValueError(f"step {self.id!r}: set either harness or action, not both")
        elif self.harness is None:
            if self.approval is None:
                raise ValueError(f"step {self.id!r}: a step without harness or action must have an approval")
        elif self.profile is None:
            raise ValueError(f"step {self.id!r}: profile is required when harness is set")
        return self


class Loop(BaseModel):
    """Repeat a group of steps in rounds until `until` holds, at most `max_rounds` times.

    Inside the body, `needs` may name body steps (same round) or steps outside the loop.
    Steps after the loop depend on the loop id and see the last round's outputs.
    """

    loop: str
    steps: list[Step]
    needs: list[str] = Field(default_factory=list)
    when: str | None = None  # skip the whole loop (status "skipped") unless this is true
    max_rounds: int = Field(default=3, ge=1, le=10)
    until: str
    # What to do when max_rounds is reached and `until` still fails:
    # ask: pause and ask the user (one more round / accept as is / abort); fail; accept.
    on_exhausted: Literal["ask", "fail", "accept"] = "ask"
    # Run before asking, when on_exhausted is "ask": reads all rounds and recommends what to do.
    # Its outputs.recommendation / outputs.reason (or summary) are added to the question.
    reflect: Step | None = None

    @field_validator("loop")
    @classmethod
    def _id(cls, v: str) -> str:
        return check_id(v, "loop id")

    @field_validator("until")
    @classmethod
    def _until(cls, v: str) -> str:
        return _check_expr(v) or v

    @field_validator("when")
    @classmethod
    def _loop_when(cls, v: str | None) -> str | None:
        return _check_expr(v)

    @property
    def id(self) -> str:
        return self.loop

    def all_steps(self) -> list[Step]:
        return [*self.steps, *([self.reflect] if self.reflect else [])]


def _item_kind(v: Any) -> str:
    if isinstance(v, dict):
        return "loop" if "loop" in v else "step"
    return "loop" if isinstance(v, Loop) else "step"


Item = Annotated[Annotated[Step, Tag("step")] | Annotated[Loop, Tag("loop")], Discriminator(_item_kind)]


ROLES = ("development", "review", "postmortem", "learn", "sweep", "eval", "onboard")


def role_of(name: str, declared: str | None = None) -> str | None:
    """What a flow does, whatever its name: the declared role, else the name itself (old runs:
    development) or the part after a project prefix (ck-development)."""
    if declared:
        return declared
    if name in ROLES:
        return name
    tail = name.partition("-")[2]
    return tail if tail in ROLES else None


class Flow(BaseModel):
    flow: str
    # What the flow does (code decides by role, never by name: development and the older ck-development
    # are both development) and which projects may run it (empty: any).
    role: Literal["development", "review", "postmortem", "learn", "sweep", "eval", "onboard"] | None = None
    projects: list[str] = Field(default_factory=list)
    label: str | None = None  # the console's task name; default: the role's (core/inputs.py ROLE_INFO)
    hint: str | None = None   # the console's one-line description; default: the role's
    # name -> "string", or a spec (core/inputs.py): the console draws the form from it, and the CLI
    # and relay check it. A spec's default goes into `defaults`.
    inputs: dict[str, str | dict[str, Any]] = Field(default_factory=dict)
    require_one_of: list[list[str]] = Field(default_factory=list)  # at least one input of each group
    defaults: dict[str, str] = Field(default_factory=dict)  # default values for inputs
    image: str | None = None  # default sandbox image for steps whose profile has none
    model: str | None = None  # default model for agent steps that set none (nor their profile)
    on_failed: Literal["fail", "ask"] = "fail"  # default for steps that set none (see Step.on_failed)
    steps: list[Item]

    @model_validator(mode="before")
    @classmethod
    def _input_defaults(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("inputs"), dict):
            spec_defaults = {k: str(v["default"]) for k, v in data["inputs"].items() if isinstance(v, dict) and "default" in v}
            if spec_defaults:
                data = {**data, "defaults": {**spec_defaults, **(data.get("defaults") or {})}}
        return data

    @property
    def kind(self) -> str | None:
        return role_of(self.flow, self.role)

    def check_project(self, project: str | None) -> None:
        if self.projects and project not in self.projects:
            raise ValueError(f"流程 {self.flow} 只给项目 {', '.join(self.projects)} 用，不能用于 {project or '（没有项目）'}")

    @model_validator(mode="after")
    def _validate_graph(self) -> Flow:
        top_ids = [i.id for i in self.steps]
        body_ids = [s.id for i in self.steps if isinstance(i, Loop) for s in i.all_steps()]
        ids = top_ids + body_ids
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"duplicate step/loop ids: {sorted(dup)}")
        top, body = set(top_ids), set(body_ids)

        for item in self.steps:
            bad = set(item.needs) - top
            if bad:
                hint = " (depend on the loop id instead of steps inside it)" if bad & body else ""
                raise ValueError(f"{item.id!r} needs unknown steps: {sorted(bad)}{hint}")
            if isinstance(item, Loop):
                inner = {s.id for s in item.steps}
                for s in item.all_steps():
                    if s.on_failed == "skip":  # a skipped member would count as done for `until`
                        raise ValueError(f"loop step {s.id!r}: on_failed: skip is not allowed inside a loop")
                for s in item.steps:
                    bad = set(s.needs) - inner - top
                    if bad:
                        raise ValueError(f"loop step {s.id!r} needs unknown steps: {sorted(bad)}")
                    if item.id in s.needs:
                        raise ValueError(f"loop step {s.id!r} cannot depend on its own loop")
                _check_acyclic({s.id: [n for n in s.needs if n in inner] for s in item.steps}, item.id)

        _check_acyclic({i.id: list(i.needs) for i in self.steps}, "flow")
        for item in self.steps:
            changes = item.approval.changes if isinstance(item, Step) and item.approval else None
            if changes is None:
                continue
            if not isinstance(next((i for i in self.steps if i.id == changes.loop), None), Loop):
                raise ValueError(f"{item.id!r}: approval.changes.loop {changes.loop!r} is not a top-level loop")
            if item.id not in self.downstream(changes.loop):
                raise ValueError(f"{item.id!r}: approval.changes.loop {changes.loop!r} must run before this step")
        return self

    def downstream(self, item_id: str) -> list[str]:
        """Top-level items that depend on `item_id`, directly or not, in flow order."""
        found = {item_id}
        changed = True
        while changed:
            changed = False
            for i in self.steps:
                if i.id not in found and found & set(i.needs):
                    found.add(i.id)
                    changed = True
        return [i.id for i in self.steps if i.id in found and i.id != item_id]

    def item(self, item_id: str) -> Item:
        for i in self.steps:
            if i.id == item_id:
                return i
        raise KeyError(item_id)

    def step(self, step_id: str) -> Step:
        for s in self.all_steps():
            if s.id == step_id:
                return s
        raise KeyError(step_id)

    def failure_policy(self, step: Step) -> Literal["fail", "ask", "skip"]:
        # Built-in actions are deterministic host code: a human cannot steer them, so they just fail
        # (or are skipped, when the step says so).
        if step.action is not None:
            return "skip" if step.on_failed == "skip" else "fail"
        return step.on_failed or self.on_failed

    def loop_of(self, step_id: str) -> Loop | None:
        for i in self.steps:
            if isinstance(i, Loop) and any(s.id == step_id for s in i.all_steps()):
                return i
        return None

    def all_steps(self) -> list[Step]:
        out: list[Step] = []
        for i in self.steps:
            out.extend(i.all_steps() if isinstance(i, Loop) else [i])
        return out

    def resolved_inputs(self, inputs: dict[str, Any]) -> dict[str, Any]:
        merged = {**self.defaults, **inputs}
        missing = set(self.inputs) - set(merged)
        if missing:
            raise ValueError(f"missing flow inputs: {sorted(missing)}")
        return merged

    def check_inputs(self, inputs: dict[str, Any]) -> None:
        self.resolved_inputs(inputs)


def _check_acyclic(graph: dict[str, list[str]], where: str) -> None:
    indeg = {n: len(d) for n, d in graph.items()}
    children: dict[str, list[str]] = {n: [] for n in graph}
    for n, deps in graph.items():
        for d in deps:
            children[d].append(n)
    queue = [n for n, d in indeg.items() if d == 0]
    seen = 0
    while queue:
        cur = queue.pop()
        seen += 1
        for c in children[cur]:
            indeg[c] -= 1
            if indeg[c] == 0:
                queue.append(c)
    if seen != len(graph):
        raise ValueError(f"{where} has a dependency cycle")


def load_flow(text: str, resolve: Callable[[str], str] | None = None) -> Flow:
    """Parse and validate a flow. `resolve` reads fragment files for `use:` entries."""
    return Flow.model_validate(expand_flow(yaml.safe_load(text), resolve))



def render_template(template: str, ctx: dict[str, Any]) -> str:
    """Replace {{inputs.x}}, {{loop.round}}, {{run.id}}, {{run.branch}}, {{replica}} ..."""

    def sub(m: re.Match[str]) -> str:
        cur: Any = ctx
        for part in m.group(1).split("."):
            if not isinstance(cur, dict) or part not in cur:
                raise ValueError(f"template references unknown value {m.group(1)!r}")
            cur = cur[part]
        return str(cur)

    return _TEMPLATE.sub(sub, template)
