from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import timedelta
from typing import Literal

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.workflow import ActivityCancellationType

with workflow.unsafe.imports_passed_through():
    from myorch.core.run import (
        LOOP_OPTIONS, ApprovalRecord, AskLoop, Mandate, RunState, StartInstance, continued_session, failure_question,
        failure_reason, key_attempt, match_option, parse_failure_reply, skip_after_failure,
    )
    from myorch.core.events import RunEvent, derive_events
    from myorch.core.runtrail import safe_run_trail
    from myorch.core.manifest import escalate
    from myorch.core.types import StepKey, StepResult
    from myorch.engines.temporal.activities import Activities
    from myorch.core.flow import Approval, parse_duration
    from myorch.engines.temporal.models import (
        ApproveInput,
        ChangesInput,
        CollectInput,
        FlowInput,
        InitRunInput,
        PublishInput,
        ReplyInput,
        RunFileInput,
        SeedRepoInput,
    )
    from myorch.runner.actions import ActionRequest
    from myorch.runner.runner import StepRequest

# Storage helpers are short; transient docker hiccups get retried by Temporal.
_QUICK = dict(
    start_to_close_timeout=timedelta(minutes=2),
    retry_policy=RetryPolicy(maximum_attempts=3),
)
_LOOP_DECISION_TIMEOUT = timedelta(hours=24)


EVENT_LOG_PATCH = "event-log-v1"  # workflow.patched marker: runs that publish to the event log
# workflow.patched marker: gates that accept change requests and have a reminder and a timeout.
# Runs already waiting at a gate when this shipped keep the old wait (approve or reject only).
APPROVAL_PATCH = "approval-v2"
# workflow.patched marker: runs that publish detail events (step_started, step_finished,
# approval_progress, loop_round, replied) so channels can draw a run from the event log alone.
EVENT_DETAIL_PATCH = "event-detail-v1"
# workflow.patched marker: runs that record their knowledge sources in the mandate and mount
# them into steps (docs/notes/2026-11-03-knowledge-mounts.md). Older runs mount none.
KNOWLEDGE_PATCH = "knowledge-v1"
# workflow.patched marker: a cancel that arrives while a step's activity is running is kept, even
# when the activity then completes normally (WAIT_CANCELLATION_COMPLETED hands its result back and
# the CancelledError is lost; the step would go on, e.g. wait 24h for a reply, and the run hang).
CANCEL_PATCH = "cancel-after-activity-v1"
# workflow.patched marker: when the cancel does stop the step's activity, the workflow sees an
# ActivityError, which used to count as a failed attempt: the step was retried (a fresh sandbox, the
# whole agent turn again) and the cancel was lost until that attempt ended (2026-11-06,
# ck-development-0208126b). Now a cancelled run stops there instead of retrying.
CANCEL_RETRY_PATCH = "cancel-no-retry-v1"
# workflow.patched marker: an automatic retry after a platform interruption (timeout, lost or cancelled
# activity, sandbox failure) continues the interrupted agent session, and the agent is told to look at
# what it left in the repo first. It used to start a fresh session on the half-edited repo, unaware
# the changes were its own (2026-11-06, ck-development-56059024). Older runs retry as before.
RETRY_SESSION_PATCH = "retry-continues-session-v1"
# workflow.patched marker: a change request at a gate keeps counting the attempts of the steps it
# sends back (Instance.attempts_before), so their next visit does not reuse attempt 1's turn logs,
# sandbox names and session ids. Older runs number them from 1 again.
CHANGES_ATTEMPTS_PATCH = "changes-keep-attempts-v1"
# workflow.patched marker: new runs first fill repo/ from a clone on the worker's machine
# (myorch/localrepo.py), so sync-master only fetches what is new from GitHub.
SEED_PATCH = "seed-repo-v1"
# Copying a large clone into the volume takes about a minute; the activity never fails the run.
_SEED = dict(
    start_to_close_timeout=timedelta(minutes=15),
    retry_policy=RetryPolicy(maximum_attempts=1),
)


def cancel_requested() -> bool:
    """Has this task been cancelled, even if the CancelledError was swallowed on the way?"""
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


@workflow.defn
class FlowWorkflow:
    """Thin driver: core.run.RunState decides what runs; this class runs it on Temporal."""

    def __init__(self) -> None:
        self._state: RunState | None = None
        self._approvals: dict[str, set[str]] = {}
        self._rejected: dict[str, str] = {}  # instance id -> who rejected
        self._changes: dict[str, tuple[str, str]] = {}  # instance id -> (approver, change requests)
        self._replies: dict[str, str] = {}
        self._published: set[str] = set()  # event keys already in the event log
        self._finishing = False
        self._detail = False  # set in run(): publish detail events (EVENT_DETAIL_PATCH)
        self._said: list[RunEvent] = []  # what people told the run (replies), as events
        self._sessions: dict[str, str] = {}  # instance id -> session of the current continuing attempt

    @workflow.run
    async def run(self, inp: FlowInput) -> RunState:
        run_id = workflow.info().workflow_id
        self._detail = workflow.patched(EVENT_DETAIL_PATCH)
        if inp.resume is not None:
            # Continue a failed or cancelled run (client.resume_run): same id, same workspace and
            # mandate; the failed steps and what they held back run again, the rest keeps its results.
            state = inp.resume
            self._state = state
            reset = state.reopen(set(inp.resume_steps) if inp.resume_steps else None)
            self._said.append(RunEvent(key=f"resumed:{state.resumes}", kind="run_resumed", run_id=run_id, data={
                "steps": reset, "by": inp.resumed_by, "resume": state.resumes,
                "at": workflow.now().isoformat(timespec="seconds")}))
        else:
            state = RunState.create(run_id, inp.flow_yaml, inp.inputs, project=inp.project)
            self._state = state
            project_name = (inp.project or {}).get("name")
            project_version = await workflow.execute_activity_method(
                Activities.init_run, InitRunInput(run_id=run_id, flow_yaml=inp.flow_yaml, project=project_name), **_QUICK
            )
            state.mandate = Mandate(
                initiator=inp.initiator,
                initiator_aliases=list(inp.initiator_aliases),
                source=inp.source,
                requested_at=workflow.now().isoformat(timespec="seconds"),
                request=dict(state.inputs),
                flow={"name": state.flow.flow, "role": state.flow.kind or "",
                      "sha256": hashlib.sha256(inp.flow_yaml.encode()).hexdigest()},
                project={"name": project_name, "version": project_version or ""} if project_name else {},
                knowledge=list(inp.knowledge) if workflow.patched(KNOWLEDGE_PATCH) else [],
                # Data only (it goes into mandate.json and run_started): no new command, so no patch marker.
                labels=list(inp.labels),
            )
            state.project_version = project_version or ""
            await workflow.execute_activity_method(
                Activities.write_run_file,
                RunFileInput(run_id=run_id, rel_path=".myorch/mandate.json", text=state.mandate.model_dump_json(indent=2)),
                **_QUICK,
            )
            project = inp.project or {}
            if project.get("repo") and workflow.patched(SEED_PATCH):
                try:
                    seeded = await workflow.execute_activity_method(
                        Activities.seed_repo,
                        SeedRepoInput(run_id=run_id, project=project_name, repo=project["repo"],
                                      base=project.get("base") or "main"),
                        **_SEED)
                except ActivityError as e:  # e.g. a timeout: sync-master clones from GitHub instead
                    seeded = f"skipped: {e}"
                workflow.logger.info("seed_repo %s: %s", run_id, seeded)
            for rel_path, text in inp.files.items():  # e.g. docs/external/request.md: the request as written
                await workflow.execute_activity_method(
                    Activities.write_run_file, RunFileInput(run_id=run_id, rel_path=rel_path, text=text), **_QUICK)

        publisher = asyncio.create_task(self._publisher())
        tasks: dict[str, asyncio.Task[None]] = {}
        try:
            while True:
                for action in state.plan():
                    if isinstance(action, StartInstance):
                        tasks[action.instance_id] = asyncio.create_task(self._run_instance(action.instance_id))
                    elif isinstance(action, AskLoop):
                        tasks[action.loop_id] = asyncio.create_task(self._ask_loop(action.loop_id))
                if not tasks:
                    break
                await workflow.wait(list(tasks.values()), return_when=asyncio.FIRST_COMPLETED)
                for key in [k for k, t in tasks.items() if t.done()]:
                    tasks.pop(key).result()
        except asyncio.CancelledError:
            # Let running steps finish their cancellation (they delete their sandboxes), then
            # record the run as cancelled so queries on the closed workflow say so.
            for t in tasks.values():
                t.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            state.cancel()
            await self._finish(publisher)
            raise
        state.plan()  # settle the final status
        await self._finish(publisher)
        if state.status == "failed":
            # Fail the Temporal workflow too, so both views agree. The state stays queryable.
            raise ApplicationError(f"run failed: {state.failure_summary()}", non_retryable=True)
        return state

    # ---- events --------------------------------------------------------------------

    def _new_events(self, finished: bool = False) -> list:
        assert self._state is not None
        # A resume goes first: it must reach the log before the steps it sets running again.
        head = [e for e in self._said if e.kind == "run_resumed"]
        events = head + derive_events(self._state, finished, detail=self._detail) + [e for e in self._said if e not in head]
        return [e for e in events if e.key not in self._published]

    async def _publish(self, finished: bool = False) -> None:
        events = self._new_events(finished)
        if not events:
            return
        # Runs started before the event log existed have no publish_events in their history; without
        # this guard their replay (every status query) fails with a nondeterminism error.
        if not workflow.patched(EVENT_LOG_PATCH):
            self._published.update(e.key for e in events)  # nothing to publish to: do not ask again
            return
        assert self._state is not None
        await workflow.execute_activity_method(
            Activities.publish_events, PublishInput(run_id=self._state.run_id, events=events), **_QUICK)
        self._published.update(e.key for e in events)

    async def _publisher(self) -> None:
        """Publish events as the state produces them (a question, an approval, a PR, ...)."""
        while not self._finishing:
            await workflow.wait_condition(lambda: self._finishing or bool(self._new_events()))
            if not self._finishing:
                await self._publish()

    async def _finish(self, publisher: asyncio.Task[None]) -> None:
        """Stop the publisher and publish what is left, including the run's end."""
        self._finishing = True
        try:
            await publisher
        except (asyncio.CancelledError, ActivityError):
            pass
        try:
            await self._publish(finished=True)
        except ActivityError as e:  # the run's outcome must not depend on the event log
            workflow.logger.warning(f"could not publish the final events: {e.cause or e}")

    # ---- one instance --------------------------------------------------------------

    async def _run_instance(self, iid: str) -> None:
        assert self._state is not None
        state = self._state
        inst = state.instances[iid]
        step = state.step_of(iid)

        if step.approval is not None:
            # The subject (e.g. the commit SHA) is fixed when the gate opens, before anyone approves.
            # Change requests from earlier visits to this gate stay in the record.
            previous = state.approvals.get(step.id)
            record = ApprovalRecord(subject=state.approval_subject(iid),
                                    changes=list(previous.changes) if previous else [])
            needed = state.approvals_required(iid)
            if needed > 0:
                inst.status, inst.approvals_needed = "waiting_approval", needed

                def decided() -> bool:
                    return (iid in self._rejected or iid in self._changes
                            or state.approval_missing(iid, self._approvals.get(iid, set())) is None)

                if workflow.patched(APPROVAL_PATCH):
                    if not await self._wait_gate(iid, step.approval, decided):
                        inst.status, inst.error = "failed", f"approval timed out: nobody handled it within {step.approval.timeout}"
                        return
                else:
                    await workflow.wait_condition(decided)
                if iid not in self._rejected and iid in self._changes:
                    await self._send_back(iid)
                    return
                inst.approvals = sorted(self._approvals.get(iid, set()))
                record.approvers = inst.approvals
                if iid in self._rejected:
                    record.rejected_by = self._rejected[iid]
                    record.at = workflow.now().isoformat(timespec="seconds")
                    state.approvals[step.id] = record
                    inst.status = "rejected"
                    return
            record.at = workflow.now().isoformat(timespec="seconds")
            state.approvals[step.id] = record
        inst.status = "running"
        if step.action is not None:  # built-in host action, runs once (actions are idempotent)
            inst.attempts = 1
            trail = None
            if step.action == "run-trail":  # the run's course, which only the workflow knows in full
                trail = safe_run_trail(state, [{**e.data, "step": e.step} for e in self._said if e.kind == "replied"])
            try:
                outcome = await workflow.execute_activity_method(
                    Activities.run_action,
                    ActionRequest(key=StepKey(run_id=state.run_id, step_id=iid, attempt=1), step=step,
                                  context=state.context(inst), trail=trail),
                    start_to_close_timeout=timedelta(seconds=step.limits.timeout_seconds),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
            except ActivityError as e:
                if state.flow.failure_policy(step) != "skip" or cancel_requested():
                    raise
                skip_after_failure(inst, str(e.cause or e))
                return
            inst.result, inst.manifest = outcome.result, outcome.manifest
            inst.status = "succeeded" if outcome.result.ok else "failed"
            if not outcome.result.ok and state.flow.failure_policy(step) == "skip":
                skip_after_failure(inst, failure_reason(inst))
            if outcome.result.ok and step.action == "project-apply" and outcome.manifest.outputs.get("version"):
                state.project_version = str(outcome.manifest.outputs["version"])  # later steps see the new pack
            return
        if step.harness is None:  # pure gate
            inst.status = "succeeded"
            return

        # Retries are driven here, not by Temporal's activity retry, so that every attempt
        # gets a fresh record, session and sandbox name. Once they are used up, a step with
        # on_failed: ask lets the user decide; a hint continues the failed attempt's session.
        # A continued run (RunState.reopen) goes on counting: a new sandbox, session and event keys.
        # An automatic retry after a platform interruption continues the interrupted session
        # (RETRY_SESSION_PATCH): `continues` is the first interrupted attempt (a StepKey attempt).
        attempt, turn, reply, failure = inst.attempts + 1, 1, None, None
        continues: int | None = None
        interrupted: str | None = None
        retries_left = step.limits.retries
        while True:
            inst.attempts, inst.error, inst.question = attempt, None, None
            if turn == 1:
                self._sessions.pop(iid, None)
            try:
                outcome = await self._run_attempt(iid, attempt, turn, reply, failure, continues, interrupted)
            except ActivityError as e:  # prepare_step or collect_manifest gave up (run_step's own errors are caught)
                if state.flow.failure_policy(step) != "skip" or cancel_requested():
                    raise
                skip_after_failure(inst, f"platform error: {e.cause or e}")
                return
            if outcome == "succeeded":
                inst.status = "succeeded"
                return
            if outcome == "fatal":
                break
            if retries_left > 0:
                retries_left -= 1
                continues = continued_session(inst, key_attempt(inst, attempt), continues)
                if continues is not None and not workflow.patched(RETRY_SESSION_PATCH):
                    continues = None
                interrupted = failure_reason(inst) if continues is not None else None
                attempt, turn, reply, failure = attempt + 1, 1, None, None
                continue
            if state.flow.failure_policy(step) != "ask":
                break  # fail, or skip (below)
            failure = failure_reason(inst)
            inst.status = "waiting_input"
            inst.question = failure_question(state.run_id, inst, agent=step.harness not in ("script", "shell"))
            try:
                await workflow.wait_condition(
                    lambda: iid in self._replies, timeout=timedelta(seconds=step.limits.input_timeout_seconds)
                )
            except asyncio.TimeoutError:
                inst.error = f"timed out ({step.limits.input_timeout}) waiting for the user to decide after the step failed: {failure}"
                break
            choice, hint = parse_failure_reply(self._replies.pop(iid))
            inst.status, inst.question = "running", None
            if choice == "skip":
                inst.status, inst.error = "skipped", f"skipped by the user after failure: {failure}"
                return
            if choice == "abort":
                inst.error = f"stopped by the user after failure: {failure}"
                break
            if choice == "retry":  # "Start over (new session)": what the person chose
                attempt, turn, reply, failure = attempt + 1, 1, None, None
                continues = interrupted = None
            else:  # hint: the next turn of the same attempt, so the session remembers what was tried
                turn, reply = inst.turns + 1, hint
        if state.flow.failure_policy(step) == "skip":
            # A nice-to-have step (on_failed: skip): the failure is recorded, the run goes on as if skipped.
            skip_after_failure(inst, inst.error or failure_reason(inst))
            return
        inst.status = "failed"

    async def _wait_gate(self, iid: str, approval: Approval, decided) -> bool:
        """Wait for a decision at a gate. Marks the reminder as due after approval.remind_after.
        Returns False when approval.timeout passes without a decision."""
        inst = self._state.instances[iid]  # type: ignore[union-attr]
        opened = workflow.now()
        if approval.remind_after and not inst.reminded:
            try:
                await workflow.wait_condition(decided, timeout=timedelta(seconds=parse_duration(approval.remind_after)))
                return True
            except asyncio.TimeoutError:
                inst.reminded = True  # derive_events publishes approval_reminder once
        if not approval.timeout:
            await workflow.wait_condition(decided)
            return True
        left = parse_duration(approval.timeout) - (workflow.now() - opened).total_seconds()
        try:
            await workflow.wait_condition(decided, timeout=timedelta(seconds=max(left, 1)))
            return True
        except asyncio.TimeoutError:
            return False

    async def _send_back(self, iid: str) -> None:
        """Change requests instead of an approval: write them where the loop's verify step reads
        them (docs/review/round-<next>/approver.json, in the reviewer format), then reopen the loop."""
        assert self._state is not None
        state = self._state
        by, text = self._changes.pop(iid)
        self._approvals.pop(iid, None)
        self._rejected.pop(iid, None)
        rnd = state.change_round(iid)
        first = text.strip().splitlines()[0] if text.strip() else text
        at = workflow.now().isoformat(timespec="seconds")
        finding = {"severity": "Important", "class": "acceptance", "title": f"Approver: {first[:100]}",
                   "locations": [{"file": "-", "line": 0}], "evidence": text,
                   "detail": f"change request from {by} at {at}"}
        await workflow.execute_activity_method(
            Activities.write_run_file,
            RunFileInput(run_id=state.run_id, rel_path=f"docs/review/round-{rnd}/approver.json",
                         text=json.dumps([finding], ensure_ascii=False, indent=2)),
            **_QUICK,
        )
        state.request_changes(iid, by, text, at, keep_attempts=workflow.patched(CHANGES_ATTEMPTS_PATCH))

    async def _run_attempt(
        self, iid: str, attempt: int, turn: int = 1, reply: str | None = None, failure: str | None = None,
        continues: int | None = None, interrupted: str | None = None,
    ) -> Literal["succeeded", "retry", "fatal"]:
        """Run turns until the agent finishes, fails, or asks and the user does not answer.
        Starts at `turn`: later than 1 when the user sent a hint after a failed turn.
        `continues`, `interrupted`: this attempt retries one the platform stopped (StepRequest)."""
        assert self._state is not None
        state = self._state
        inst = state.instances[iid]
        step = state.step_of(iid)
        base = StepRequest(
            key=StepKey(run_id=state.run_id, step_id=iid, attempt=key_attempt(inst, attempt)),
            step=step,
            context=state.context(inst),
            upstream=state.upstream(iid),
            project=(state.project or {}).get("name"),
            project_version=state.project_version or ((state.mandate.project or {}).get("version", "") if state.mandate else ""),
            default_image=state.flow.image,
            default_model=state.flow.model,
            knowledge=list(state.mandate.knowledge) if state.mandate else [],
            continues=continues,
            interrupted=interrupted,
        )
        while True:
            inst.turns = turn
            req = base.model_copy(update={"key": base.key.model_copy(update={"turn": turn}), "reply": reply,
                                          "failure": failure, "session_id": self._sessions.get(iid)})
            failure = None  # only the first turn after a failure retries it
            inst.result = await self._run_turn(req)
            if continues is not None and inst.result.session_id and iid not in self._sessions:
                # A continuing attempt may go on in an earlier attempt's session: later turns use what
                # turn 1 chose instead of looking again (only activity inputs change).
                self._sessions[iid] = inst.result.session_id
            inst.manifest = escalate(
                await workflow.execute_activity_method(
                    Activities.collect_manifest, CollectInput(run_id=state.run_id, instance_id=iid), **_QUICK
                ),
                step.escalate_when,
            )
            if not inst.result.ok or inst.manifest.status == "failed":
                return "retry"
            if inst.manifest.status != "needs_input":
                return "succeeded"

            # The agent stopped to ask the user.
            inst.question = inst.manifest.question
            if step.interaction == "never":
                # Retrying would most likely ask again, so this is final.
                inst.error = "the agent asked a question in a step with interaction=never; treated as a failure"
                return "fatal"
            inst.status = "waiting_input"
            try:
                await workflow.wait_condition(
                    lambda: iid in self._replies, timeout=timedelta(seconds=step.limits.input_timeout_seconds)
                )
            except asyncio.TimeoutError:
                inst.error = f"timed out waiting for the user's reply ({step.limits.input_timeout})"
                return "fatal"
            reply = self._replies.pop(iid)
            inst.status, inst.question = "running", None
            turn += 1

    async def _run_turn(self, req: StepRequest) -> StepResult:
        await workflow.execute_activity_method(Activities.prepare_step, req.key, **_QUICK)
        try:
            result = await workflow.execute_activity_method(
                Activities.run_step,
                req,
                start_to_close_timeout=timedelta(seconds=req.step.limits.timeout_seconds + 120),
                heartbeat_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=1),
                cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
            if workflow.patched(CANCEL_PATCH) and cancel_requested():
                raise asyncio.CancelledError()  # the run was cancelled while the step ran: stop here
            return result
        except ActivityError as e:
            # Worker crash, heartbeat timeout, ...: make sure the sandbox is gone.
            await workflow.execute_activity_method(Activities.cleanup_step, req.key, **_QUICK)
            if workflow.patched(CANCEL_RETRY_PATCH) and cancel_requested():
                raise asyncio.CancelledError() from None  # the activity stopped because the run was cancelled: no retry
            return StepResult(step_id=req.key.step_id, ok=False, exit_code=None, sandbox="", error=str(e.cause or e),
                              stopped="lost")

    # ---- exhausted loops -----------------------------------------------------------

    async def _ask_loop(self, loop_id: str) -> None:
        assert self._state is not None
        try:
            await workflow.wait_condition(lambda: loop_id in self._replies, timeout=_LOOP_DECISION_TIMEOUT)
        except asyncio.TimeoutError:
            lr = self._state.loops[loop_id]
            lr.status, lr.error, lr.question = "failed", "timed out waiting for the user to decide", None
            return
        self._state.resolve_loop(loop_id, self._replies.pop(loop_id))

    # ---- queries and signals -------------------------------------------------------

    @workflow.query
    def status(self) -> RunState | None:
        return self._state

    @workflow.signal
    def approve(self, inp: ApproveInput) -> None:
        inst = self._state.instances.get(inp.step_id) if self._state else None
        if inst is not None and inst.status == "waiting_approval":
            self._approvals.setdefault(inp.step_id, set()).add(inp.approver)
            inst.approvals = sorted(self._approvals[inp.step_id])  # visible to status while waiting

    @workflow.signal
    def reject(self, inp: ApproveInput) -> None:
        inst = self._state.instances.get(inp.step_id) if self._state else None
        if inst is not None and inst.status == "waiting_approval":
            self._rejected[inp.step_id] = inp.approver

    @workflow.signal
    def request_changes(self, inp: ChangesInput) -> None:
        # Only while the gate waits, only at gates that configure approval.changes, first request wins.
        inst = self._state.instances.get(inp.step_id) if self._state else None
        if inst is None or inst.status != "waiting_approval" or inp.step_id in self._changes or not inp.text.strip():
            return
        approval = self._state.step_of(inp.step_id).approval  # type: ignore[union-attr]
        if approval is not None and approval.changes is not None:
            self._changes[inp.step_id] = (inp.approver, inp.text)

    def _record_reply(self, step_id: str, text: str, question: str) -> None:
        """A reply the run accepted, as an event (detail runs only): replies sent from any channel or
        the CLI then show up in every conversation view."""
        if not self._detail or self._state is None:
            return
        n = sum(1 for e in self._said if e.step == step_id) + 1
        self._said.append(RunEvent(key=f"reply:{step_id}:{n}", kind="replied", run_id=self._state.run_id, step=step_id,
                                   data={"text": text, "question": question,
                                         "at": workflow.now().isoformat(timespec="seconds")}))

    @workflow.signal
    def reply(self, inp: ReplyInput) -> None:
        # Only accept a reply for something that is actually waiting, so stale replies cannot
        # leak into a later question.
        if self._state is None:
            return
        inst = self._state.instances.get(inp.step_id)
        if inst is not None and inst.status == "waiting_input":
            self._replies[inp.step_id] = inp.text
            self._record_reply(inp.step_id, inp.text, inst.question.question if inst.question else "")
            return
        lr = self._state.loops.get(inp.step_id)
        if lr is not None and lr.status == "waiting_input" and match_option(inp.text, LOOP_OPTIONS) is not None:
            self._replies[inp.step_id] = inp.text
            self._record_reply(inp.step_id, inp.text, lr.question.question if lr.question else "")
