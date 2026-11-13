"""Run state and scheduling, independent of any workflow engine.

A flow is expanded into step *instances*:

    implement            top-level step
    review.x2            replica 2 of a fanout step
    verify.r3            round 3 of a step inside a loop
    review.r3.x2         both

Dependencies are recorded as *group keys*: a top-level step id ("implement"), a loop id
("review-fix"), or a step within a specific round ("verify.r3"). A group is satisfied when
all its instances succeeded or were skipped; it is broken when any of them failed, was
rejected or blocked.

The engine drives a run like this:

    state = RunState.create(run_id, flow_yaml, inputs)
    loop:
        actions = state.plan()            # pure: decides what may start now
        run each StartInstance / AskLoop, writing results back into state
    until plan() returns nothing and nothing is in flight

Everything here is deterministic, so it can run inside Temporal workflow code.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr

from myorch.core.expr import evaluate
from myorch.core.flow import Flow, Loop, Step, load_flow, render_template
from myorch.core.manifest import Manifest, Question
from myorch.core.types import StepResult

InstanceStatus = Literal[
    "pending", "running", "waiting_approval", "waiting_input",
    "succeeded", "failed", "rejected", "blocked", "skipped", "cancelled",
]
OK = frozenset({"succeeded", "skipped"})
BAD = frozenset({"failed", "rejected", "blocked", "cancelled"})
TERMINAL = OK | BAD

LOOP_OPTIONS = ["Run one more round", "Accept the current result and continue with the later steps", "Stop the flow"]
# Offered when a step with on_failed: ask has used up its retries. Any other reply is a hint.
FAILURE_OPTIONS = ["Start over (new session)", "Skip this step and continue with the later ones", "Stop the flow"]
FailureChoice = Literal["retry", "skip", "abort", "hint"]


class Instance(BaseModel):
    id: str
    step_id: str
    group: str  # dependency key this instance belongs to: "<step>" or "<step>.r<round>"
    loop_id: str | None = None
    round: int = 0  # 0: not in a loop
    replica: int = 0  # 0: no fanout
    deps: list[str] = Field(default_factory=list)  # group keys / loop ids
    status: InstanceStatus = "pending"
    attempts: int = 0
    turns: int = 0
    # Attempts made before a change request at a gate sent this step back (request_changes with
    # keep_attempts). `attempts` starts again from 0, as people read it; the engine numbers the
    # attempt's StepKey attempts_before + attempts, so turn logs, sandboxes and sessions of the earlier
    # visits are not reused (key_attempt).
    attempts_before: int = 0
    approvals_needed: int = 0
    approvals: list[str] = Field(default_factory=list)  # distinct approver names
    reminded: bool = False  # the gate's approval_reminder is due (approval.remind_after passed)
    # Failed, then skipped because the step says on_failed: skip (skip_after_failure). Still "skipped"
    # for scheduling; events also report the failure (core/events.py).
    skipped_after_failure: bool = False
    question: Question | None = None
    error: str | None = None
    result: StepResult | None = None
    manifest: Manifest | None = None


class LoopRun(BaseModel):
    id: str
    round: int = 0
    max_rounds: int
    status: Literal["pending", "running", "waiting_input", "succeeded", "failed", "skipped", "cancelled"] = "pending"
    question: Question | None = None
    error: str | None = None


class KnowledgeRef(BaseModel, frozen=True):
    """One knowledge source a run may read, fixed when it starts (myorch/knowledge.py).

    Snapshot kinds (dir, git) carry the version mounted at /sandbox/workspace/knowledge/<name>/;
    an mcp source has no version (its content changes) and carries how agents reach it instead."""

    name: str
    kind: Literal["dir", "git", "mcp"]
    version: str = ""  # sha256:<16> of the snapshot (dir, git)
    index: str = ""  # what to read first (a file), or how to use the tools (mcp)
    url: str = ""  # mcp: the server's address
    provider: str = ""  # mcp: the OpenShell provider that adds the read-only token at the gateway
    token_env: str = ""  # mcp: the provider's placeholder variable (the gateway swaps in the token)
    auth: Literal["bearer", "basic"] = "bearer"  # mcp: how the header is built (tools/myorch-mcp)
    email: str = ""  # mcp, basic: the token owner's email
    tools: list[str] = Field(default_factory=list)  # mcp: the only tools agents may call


class Mandate(BaseModel, frozen=True):
    """What the run was authorised to do, fixed when it starts. Nothing in the run can change it:
    messages, comments and documents are inputs to decisions, not grants of authority."""

    initiator: str
    initiator_aliases: list[str] = Field(default_factory=list)  # same person elsewhere (GitHub login / local user)
    source: str = ""  # where the request came from (e.g. a GitHub comment URL)
    requested_at: str
    request: dict[str, Any]  # the flow inputs as given
    flow: dict[str, str]  # {"name": ..., "sha256": ...} of the expanded flow
    project: dict[str, str] = Field(default_factory=dict)  # {"name": ..., "version": ...} of the pack snapshot
    knowledge: list[KnowledgeRef] = Field(default_factory=list)  # knowledge sources mounted into agent steps
    labels: list[str] = Field(default_factory=list)  # what the run is found by, e.g. its Jira ticket (core/inputs.py)


class ChangeRequest(BaseModel):
    """An approver sent the run back with change requests instead of approving."""

    by: str
    text: str
    at: str
    round: int  # the loop round that handles it (docs/review/round-<round>/approver.json)


class ApprovalRecord(BaseModel):
    """A human decision at a gate, bound to the subject (e.g. commit) it was made on."""

    subject: str | None = None
    approvers: list[str] = Field(default_factory=list)
    rejected_by: str | None = None
    at: str | None = None
    changes: list[ChangeRequest] = Field(default_factory=list)  # change requests before the final decision


class StartInstance(BaseModel):
    kind: Literal["start"] = "start"
    instance_id: str


class AskLoop(BaseModel):
    kind: Literal["ask_loop"] = "ask_loop"
    loop_id: str


Action = StartInstance | AskLoop


def instance_id(step_id: str, round_: int, replica: int) -> str:
    out = step_id
    if round_:
        out += f".r{round_}"
    if replica:
        out += f".x{replica}"
    return out


class RunState(BaseModel):
    run_id: str
    flow_yaml: str
    inputs: dict[str, Any]
    status: Literal["running", "succeeded", "failed", "cancelled"] = "running"
    instances: dict[str, Instance] = Field(default_factory=dict)
    loops: dict[str, LoopRun] = Field(default_factory=dict)
    mandate: Mandate | None = None
    approvals: dict[str, ApprovalRecord] = Field(default_factory=dict)  # step id -> record
    project: dict[str, Any] | None = None  # the project's public config (Project.public()), if any
    # The pack snapshot the run's steps mount. Starts as the mandate's version; a project-apply step
    # of this run moves it to the version it wrote. Other runs keep theirs.
    project_version: str = ""
    resumes: int = 0  # how many times a failed or cancelled run was continued (reopen)

    _flow: Flow | None = PrivateAttr(default=None)

    # ---- construction -------------------------------------------------------------

    @classmethod
    def create(cls, run_id: str, flow_yaml: str, inputs: dict[str, Any], project: dict[str, Any] | None = None) -> RunState:
        flow = load_flow(flow_yaml)
        state = cls(run_id=run_id, flow_yaml=flow_yaml, inputs=flow.resolved_inputs(inputs), project=project)
        for item in flow.steps:
            if isinstance(item, Loop):
                state.loops[item.id] = LoopRun(id=item.id, max_rounds=item.max_rounds)
            else:
                state._add_instances(item, round_=0, loop=None)
        return state

    @property
    def flow(self) -> Flow:
        if self._flow is None:
            self._flow = load_flow(self.flow_yaml)
        return self._flow

    def _add_instances(self, step: Step, round_: int, loop: Loop | None) -> None:
        group = instance_id(step.id, round_, 0)
        if loop is None:
            deps = list(step.needs)
        else:
            inner = {s.id for s in loop.steps}
            deps = [f"{n}.r{round_}" if n in inner else n for n in step.needs]
        replicas = [0] if step.fanout == 1 else list(range(1, step.fanout + 1))
        for r in replicas:
            iid = instance_id(step.id, round_, r)
            self.instances[iid] = Instance(
                id=iid, step_id=step.id, group=group, loop_id=loop.id if loop else None,
                round=round_, replica=r, deps=deps,
            )

    # ---- status helpers ------------------------------------------------------------

    def _group_status(self, key: str) -> Literal["ok", "bad", "pending"]:
        if key in self.loops:
            st = self.loops[key].status
            return "ok" if st in ("succeeded", "skipped") else "bad" if st == "failed" else "pending"
        members = [i for i in self.instances.values() if i.group == key]
        if not members:
            return "pending"
        if any(i.status in BAD for i in members):
            return "bad"
        if all(i.status in OK for i in members):
            return "ok"
        return "pending"

    def _latest(self, step_id: str, round_: int | None = None) -> list[Instance]:
        """Instances of a step: a specific round, or the latest round that exists."""
        cands = [i for i in self.instances.values() if i.step_id == step_id]
        if not cands:
            return []
        r = round_ if round_ is not None else max(i.round for i in cands)
        return [i for i in cands if i.round == r]

    def context(self, instance: Instance | None = None, loop_round: tuple[str, int] | None = None) -> dict:
        """Expression / template context as seen from an instance (or a loop at a round)."""
        loop_id, rnd = (instance.loop_id, instance.round) if instance else loop_round or (None, 0)
        steps: dict[str, Any] = {}
        for step in self.flow.all_steps():
            same_loop = loop_id is not None and self.flow.loop_of(step.id) is not None \
                and self.flow.loop_of(step.id).id == loop_id
            group = self._latest(step.id, rnd if same_loop else None)
            if not group:
                continue
            first = group[0]
            agg = self._group_status(first.group)
            steps[step.id] = {
                "status": {"ok": "succeeded", "bad": "failed", "pending": first.status}[agg]
                if all(i.status in TERMINAL for i in group) else first.status,
                "outputs": (first.manifest.outputs if first.manifest else {}),
            }
        ctx: dict[str, Any] = {
            "inputs": self.inputs,
            "run": {"id": self.run_id, "branch": self.branch()},
            "steps": steps,
            "approvals": {k: v.model_dump() for k, v in self.approvals.items()},
        }
        if loop_id is not None:
            ctx["loop"] = {"round": rnd}
        if instance is not None:
            # Always present so prompts can say "reviewer {{replica}}" even without fanout.
            ctx["replica"] = instance.replica or 1
        if self.project is not None:
            ctx["project"] = self.project
        return ctx

    # ---- planning ------------------------------------------------------------------

    def plan(self) -> list[Action]:
        """Advance everything that can advance without running anything; return what to run."""
        actions: list[Action] = []
        changed = True
        while changed:
            changed = False
            for item in self.flow.steps:
                if isinstance(item, Loop):
                    changed |= self._advance_loop(item, actions)
            for inst in self.instances.values():
                if inst.status != "pending":
                    continue
                deps = [self._group_status(d) for d in inst.deps]
                if any(d == "bad" for d in deps):
                    inst.status, inst.error = "blocked", "an upstream step failed or was rejected"
                    changed = True
                elif all(d == "ok" for d in deps):
                    step = self.flow.step(inst.step_id)
                    if step.when and not evaluate(step.when, self.context(inst)):
                        inst.status = "skipped"
                    else:
                        inst.status = "running"
                        actions.append(StartInstance(instance_id=inst.id))
                    changed = True
        self._update_run_status()
        return actions

    def _advance_loop(self, loop: Loop, actions: list[Action]) -> bool:
        lr = self.loops[loop.id]
        if lr.status == "pending":
            deps = [self._group_status(d) for d in loop.needs]
            if any(d == "bad" for d in deps):
                lr.status, lr.error = "failed", "an upstream step failed or was rejected"
                return True
            if all(d == "ok" for d in deps):
                if loop.when and not evaluate(loop.when, self.context(loop_round=(loop.id, 0))):
                    lr.status = "skipped"
                else:
                    self._start_round(loop, lr)
                return True
            return False
        if lr.status != "running":
            return False
        reflect_id = loop.reflect.id if loop.reflect else None
        body = [i for i in self.instances.values()
                if i.loop_id == loop.id and i.round == lr.round and i.step_id != reflect_id]
        if not all(i.status in TERMINAL for i in body):
            return False
        if any(i.status in BAD for i in body):
            lr.status, lr.error = "failed", f"a step failed in round {lr.round}"
            return True
        if evaluate(loop.until, self.context(loop_round=(loop.id, lr.round))):
            lr.status = "succeeded"
        elif lr.round < lr.max_rounds:
            self._start_round(loop, lr)
        elif loop.on_exhausted == "accept":
            lr.status = "succeeded"
        elif loop.on_exhausted == "fail":
            lr.status, lr.error = "failed", f"the until condition is still not met after {lr.max_rounds} rounds"
        else:
            advice = ""
            if loop.reflect:
                # Reflect first: one agent reads every round and recommends a way forward.
                rid = instance_id(loop.reflect.id, lr.round, 0)
                if rid not in self.instances:
                    self._add_instances(loop.reflect, lr.round, loop)
                    return True
                r = self.instances[rid]
                if r.status not in TERMINAL:
                    return False
                if r.status == "succeeded" and r.manifest is not None:
                    out = r.manifest.outputs
                    rec, why = out.get("recommendation"), out.get("reason") or r.manifest.summary
                    advice = f"\nReflect proposal: {rec}. {why}" if rec else (f"\nReflect: {why}" if why else "")
            lr.status = "waiting_input"
            lr.question = Question(
                question=f"Loop {loop.id} has run {lr.round} rounds and still does not meet: {loop.until}. What should happen?{advice}",
                options=LOOP_OPTIONS,
            )
            actions.append(AskLoop(loop_id=loop.id))
        return True

    def _start_round(self, loop: Loop, lr: LoopRun) -> None:
        lr.round += 1
        lr.status = "running"
        for step in loop.steps:
            self._add_instances(step, lr.round, loop)

    def resolve_loop(self, loop_id: str, reply: str) -> bool:
        """Apply the user's answer to an exhausted loop. Returns False if the reply is not understood."""
        lr = self.loops[loop_id]
        choice = match_option(reply, LOOP_OPTIONS)
        if lr.status != "waiting_input" or choice is None:
            return False
        lr.question = None
        if choice == 0:
            lr.max_rounds += 1
            self._start_round(self.flow.item(loop_id), lr)  # type: ignore[arg-type]
        elif choice == 1:
            lr.status = "succeeded"
        else:
            lr.status, lr.error = "failed", "the user stopped the flow"
        return True

    def change_round(self, iid: str) -> int:
        """The loop round that will handle a change request at this gate (approver.json goes there)."""
        changes = self._changes_config(iid)
        return self.loops[changes.loop].round + 1

    def _changes_config(self, iid: str):
        step = self.step_of(iid)
        changes = step.approval.changes if step.approval else None
        if changes is None:
            raise ValueError(f"{step.id} does not accept change requests (approval.changes is not configured)")
        return changes

    def request_changes(self, iid: str, by: str, text: str, at: str, keep_attempts: bool = False) -> int:
        """An approver sent change requests instead of approving: reopen the loop for up to
        `changes.rounds` more rounds and reset every top-level item after it, this gate included.
        Returns the round that handles the request.

        `keep_attempts`: remember the reset steps' attempts in attempts_before, so their next visit
        does not number its attempts (and so its turn logs, turn-<attempt>-<turn>.log, and its agent
        sessions) from 1 again. Off for runs that started before this existed (the engine decides)."""
        inst = self.instances[iid]
        changes = self._changes_config(iid)
        if inst.status != "waiting_approval":
            raise ValueError(f"{iid} is not waiting for approval (status {inst.status})")
        loop = self.flow.item(changes.loop)
        lr = self.loops[loop.id]
        if lr.status != "succeeded":
            raise ValueError(f"loop {loop.id} is {lr.status} and cannot run again")
        record = self.approvals.get(inst.step_id) or ApprovalRecord()
        record.changes.append(ChangeRequest(by=by, text=text, at=at, round=lr.round + 1))
        self.approvals[inst.step_id] = record
        for item_id in self.flow.downstream(loop.id):
            item = self.flow.item(item_id)
            if isinstance(item, Loop):
                # Keep the round numbers going, so earlier rounds' instances and files stay as they were.
                dlr = self.loops[item_id]
                dlr.max_rounds = dlr.round + item.max_rounds
                dlr.status, dlr.question, dlr.error = "pending", None, None
                continue
            for i in self.instances.values():
                if i.step_id == item_id and i.loop_id is None:
                    if keep_attempts:
                        i.attempts_before += i.attempts
                    i.status, i.attempts, i.turns, i.reminded = "pending", 0, 0, False
                    i.skipped_after_failure = False
                    i.approvals_needed, i.approvals = 0, []
                    i.question = i.error = i.result = i.manifest = None
        lr.max_rounds = lr.round + changes.rounds
        lr.error = None
        self._start_round(loop, lr)  # type: ignore[arg-type]
        return lr.round

    def reopen(self, only: set[str] | None = None) -> list[str]:
        """Continue a failed or cancelled run: the failed and cancelled steps (or only those named,
        by instance or step id) and everything they held back go back to pending; what succeeded
        stays as it is, approvals included. Returns the instances set back to pending.

        A rejected gate is a person's decision and is not reopened. A loop that ran out of rounds
        under on_exhausted: fail has no failed step and stays failed."""
        if self.status not in ("failed", "cancelled"):
            raise ValueError(f"only a failed or cancelled run can be resumed (it is {self.status})")
        retry = [i for i in self.instances.values() if i.status in ("failed", "cancelled")
                 and (only is None or i.id in only or i.step_id in only)]
        if not retry:
            raise ValueError("no step can be re-run" + (f": {', '.join(sorted(only))}" if only else ""))
        retry_ids, reset = {i.id for i in retry}, []
        for inst in self.instances.values():
            if inst.id in retry_ids or inst.status == "blocked" or (only is None and inst.status == "cancelled"):
                inst.status, inst.error, inst.question = "pending", None, None
                reset.append(inst.id)
        for loop in self.flow.steps:
            if not isinstance(loop, Loop):
                continue
            lr = self.loops[loop.id]
            if lr.status not in ("failed", "cancelled"):
                continue
            if lr.round == 0:  # never started: held back by a failed upstream step
                lr.status, lr.error = "pending", None
            elif any(i.loop_id == loop.id and i.round == lr.round and i.status == "pending" for i in self.instances.values()):
                lr.status, lr.error = "running", None
            elif lr.status == "cancelled" and only is None:  # cancelled while asking: ask again
                lr.status, lr.error = "running", None
        self.status = "running"
        self.resumes += 1
        return reset

    def cancel(self) -> None:
        """Mark everything unfinished as cancelled (the engine stopped the run)."""
        for inst in self.instances.values():
            if inst.status not in TERMINAL:
                inst.status = "cancelled"
        for lr in self.loops.values():
            if lr.status in ("pending", "running", "waiting_input"):
                lr.status, lr.question = "cancelled", None
        self.status = "cancelled"

    def failure_summary(self) -> str:
        bad = [f"{i.id}={i.status}" + (f" ({i.error[:120]})" if i.status == "failed" and i.error else "")
               for i in self.instances.values() if i.status in BAD]
        bad += [f"{lr.id}={lr.status}" for lr in self.loops.values() if lr.status == "failed"]
        return ", ".join(bad) or "unknown"

    def _update_run_status(self) -> None:
        if self.status == "cancelled":
            return
        active = any(i.status not in TERMINAL for i in self.instances.values()) or any(
            lr.status in ("pending", "running", "waiting_input") for lr in self.loops.values()
        )
        if active:
            self.status = "running"
            return
        # Reflection only advises; its failure must not fail a run the user chose to continue.
        reflect_ids = {i.reflect.id for i in self.flow.steps if isinstance(i, Loop) and i.reflect}
        ok = all(i.status in OK for i in self.instances.values() if i.step_id not in reflect_ids) and all(
            lr.status in ("succeeded", "skipped") for lr in self.loops.values()
        )
        self.status = "succeeded" if ok else "failed"

    # ---- queries used by the engine ------------------------------------------------

    def step_of(self, iid: str) -> Step:
        return self.flow.step(self.instances[iid].step_id)

    def approvals_required(self, iid: str) -> int:
        inst = self.instances[iid]
        approval = self.flow.step(inst.step_id).approval
        if approval is None:
            return 0
        ctx = self.context(inst)
        if approval.when and not evaluate(approval.when, ctx):
            return 0
        count = approval.count if isinstance(approval.count, int) else evaluate(approval.count, ctx)
        try:
            return max(0, int(count or 0))
        except (TypeError, ValueError):
            return 1  # an unreadable count must not silently skip the human

    def branch(self) -> str:
        """The run's working branch: the project's template (rendered at run time) or myorch/<run_id>.
        A development run that continues another (inputs.from_run) works on that run's branch."""
        template = (self.project or {}).get("branch") or "myorch/{{run.id}}"
        owner = str(self.inputs.get("from_run") or "").strip() or self.run_id
        try:
            return render_template(template, {"run": {"id": owner}, "inputs": self.inputs})
        except ValueError:  # e.g. a review run of a project whose branches carry a ticket number
            return f"myorch/{owner}"

    def approval_subject(self, iid: str) -> str | None:
        approval = self.step_of(iid).approval
        if approval is None or not approval.subject:
            return None
        return render_template(approval.subject, self.context(self.instances[iid]))

    def approval_missing(self, iid: str, approvers: set[str]) -> str | None:
        """None when the given approvers satisfy the gate, else what is still missing."""
        needed = self.approvals_required(iid)
        approval = self.step_of(iid).approval
        missing = []
        if len(approvers) < needed:
            missing.append(f"{needed - len(approvers)} more approval(s) needed")
        initiator = self.mandate.initiator if self.mandate else None
        same_person = {initiator, *(self.mandate.initiator_aliases if self.mandate else [])}
        if needed > 0 and approval is not None and approval.require_initiator and initiator \
                and not (same_person & approvers):
            missing.append(f"the initiator {initiator} must be one of the approvers")
        return "; ".join(missing) or None

    def upstream(self, iid: str) -> dict[str, Manifest]:
        """Manifests the instance should see: its dependencies, plus the previous round of its loop."""
        inst = self.instances[iid]
        keys = list(inst.deps)
        out: dict[str, Manifest] = {}
        for key in keys:
            if key in self.loops:
                loop = self.flow.item(key)
                lr = self.loops[key]
                members = [i for i in self.instances.values() if i.loop_id == loop.id and i.round == lr.round]
            else:
                members = [i for i in self.instances.values() if i.group == key]
            for m in members:
                if m.manifest is not None:
                    out[m.id] = m.manifest
        if inst.loop_id and inst.round > 1:
            for m in self.instances.values():
                if m.loop_id == inst.loop_id and m.round == inst.round - 1 and m.manifest is not None:
                    out[m.id] = m.manifest
        return out

    def mounted_upstream(self, iid: str) -> list[str]:
        return sorted(self.upstream(iid))


def failure_reason(inst: Instance) -> str:
    """One line on why the last turn of an instance failed."""
    r, m = inst.result, inst.manifest
    if r is not None and r.error:
        return r.error
    if m is not None and m.status == "failed":
        return m.summary or "the agent declared this step failed"
    if r is not None and r.exit_code not in (0, None):
        return f"exit code {r.exit_code}"
    return "unknown reason"


def skip_after_failure(inst: Instance, reason: str) -> None:
    """A step with on_failed: skip failed for good: record why and treat it as skipped, so the run goes
    on. Its outputs are dropped: later steps must not act on a half-done result (a `when` on one of
    its outputs is then false)."""
    inst.status, inst.skipped_after_failure = "skipped", True
    inst.error = f"skipped after failure (on_failed: skip): {reason}"[:2000]
    if inst.manifest is not None:
        inst.manifest = inst.manifest.model_copy(update={"outputs": {}})


def key_attempt(inst: Instance, attempt: int) -> int:
    """The StepKey attempt of the instance's attempt number `attempt` (see Instance.attempts_before)."""
    return inst.attempts_before + attempt


def interrupted_by_platform(inst: Instance) -> bool:
    """Did the instance's last turn stop for a platform reason: an activity that was cancelled or lost
    its worker (heartbeat timeout), a sandbox that could not be created or ended without an exit code?

    Not when the harness exited with a code, or the agent said `failed`: then the attempt ran to its
    end and its result stands. Not when the step ran into its own limits.timeout (stopped="timeout"):
    a slow or stuck agent is not told it was not its fault, nor resumed with all that context. A
    result without `stopped` (from a runner before 2026-11-06) counts as an interruption."""
    r, m = inst.result, inst.manifest
    if r is None or r.ok or r.exit_code is not None or r.stopped in ("timeout", "harness"):
        return False
    return m is None or m.status != "failed"


def continued_session(inst: Instance, failed_key_attempt: int, chain: int | None) -> int | None:
    """Which earlier attempts the retry after `failed_key_attempt` continues (as StepKey attempts),
    or None for a fresh start. A retry after a platform interruption continues the interrupted
    agent session instead of starting a new one on a half-edited repo (2026-11-06,
    ck-development-56059024). `chain` is what the failed attempt itself continued: after several
    interruptions in a row the retry still looks back to the first of them. The runner picks the
    newest session among attempts chain..failed_key_attempt that still exists."""
    if not interrupted_by_platform(inst):
        return None
    return chain if chain is not None else failed_key_attempt


def failure_question(run_id: str, inst: Instance, agent: bool) -> Question:
    """What to ask when a step has failed and used up its retries. `agent`: the step runs an
    agent (a hint can steer it), not a script."""
    reason = failure_reason(inst)
    if len(reason) > 300:
        reason = reason[:300] + "…"
    text = (f"Step {inst.id} failed (attempt {inst.attempts}): {reason}\n"
            f"Log: `myorch logs {run_id} {inst.id}`\nReply with a number to choose what to do")
    if agent:
        text += ". Or write a hint (for example \"don't change X yet, look at Y\"), and the agent tries again in the same session, following the hint"
    return Question(question=text + ".", options=list(FAILURE_OPTIONS))


def parse_failure_reply(reply: str) -> tuple[FailureChoice, str]:
    """A number or the exact option text picks an option; anything else is a hint."""
    text = reply.strip()
    head = text.split(".")[0].split("\u3001")[0].strip()  # "2. ..." or "2\u3001..." (ideographic comma)
    idx = int(head) - 1 if head.isdigit() and 1 <= int(head) <= len(FAILURE_OPTIONS) else (
        FAILURE_OPTIONS.index(text) if text in FAILURE_OPTIONS else None)
    if idx is None:
        return ("hint", text) if text else ("retry", "")
    return (["retry", "skip", "abort"][idx], "")  # type: ignore[return-value]


def match_option(reply: str, options: list[str]) -> int | None:
    """'2', '2. xxx' or the option text itself -> index; None if nothing matches."""
    text = reply.strip()
    head = text.split(".")[0].split("\u3001")[0].strip()  # "2. ..." or "2\u3001..." (ideographic comma)
    if head.isdigit() and 1 <= int(head) <= len(options):
        return int(head) - 1
    for i, opt in enumerate(options):
        if text == opt or text.startswith(opt) or opt.startswith(text) and len(text) >= 2:
            return i
    return None
