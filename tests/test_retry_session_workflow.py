"""FlowWorkflow end to end on a throwaway Temporal dev server, with fake storage and a fake runner:
an automatic retry after a platform interruption continues the interrupted session
(RETRY_SESSION_PATCH), later turns of that attempt get the session turn 1 used, a change request keeps
counting attempts (CHANGES_ATTEMPTS_PATCH), and runs recorded without either patch still replay.

Uses the installed `temporal` CLI (like test_cancel_activity.py). Without it, MYORCH_TEMPORAL_DOWNLOAD=1 lets
the SDK download the dev server; otherwise skipped, so the default test run needs no network."""

from __future__ import annotations

import os
import asyncio
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

import myorch.engines.temporal.workflow as wfmod
from myorch.core.types import StepResult
from myorch.engines.temporal.activities import Activities
from myorch.core.manifest import Manifest, Question
from myorch.engines.temporal.models import ApproveInput, ChangesInput, FlowInput, ReplyInput
from myorch.engines.temporal.workflow import CHANGES_ATTEMPTS_PATCH, RETRY_SESSION_PATCH, FlowWorkflow

FLOW = """
flow: t
steps:
  - id: develop
    harness: claude-code
    profile: p
    prompt: do it
    interaction: allowed
    limits: {retries: 2}
"""

# A gate that can send the run back into a loop; `risk` (after the loop) is reset by a change request.
CHANGES_FLOW = """
flow: c
steps:
  - {id: implement, harness: claude-code, profile: p, prompt: x}
  - loop: review
    needs: [implement]
    max_rounds: 1
    until: "steps.verify.outputs.blocking == 0"
    steps:
      - {id: verify, harness: claude-code, profile: p, prompt: v}
  - {id: risk, harness: claude-code, profile: p, prompt: r, needs: [review], limits: {retries: 1}}
  - id: human
    needs: [risk]
    approval: {count: 1, require_initiator: false, changes: {loop: review, rounds: 1}}
"""


class Store:
    def __init__(self, questions=()):
        self.questions = list(questions)  # what each collect_manifest finds as the agent's question, in order

    def init_run(self, run_id, flow_yaml, project):
        return None

    def write_run_file(self, run_id, rel_path, text):
        pass

    def prepare_step(self, key):
        pass

    def read_manifest(self, run_id, iid):
        return Manifest(step_id=iid, outputs={"blocking": 0})

    def read_question(self, run_id, iid):
        return self.questions.pop(0) if self.questions else None

    def list_outputs(self, run_id, iid):
        return []


class Runner:
    """Plays back results per step (OK once a step's list is used up), records the requests, and reports
    the session like StepRunner: the one it was given, else the one it continues, else its own."""

    def __init__(self, results=(), **per_step):
        self.results = {"develop": list(results), **{k: list(v) for k, v in per_step.items()}}
        self.requests = []

    async def run(self, req, ctx):
        self.requests.append(req)
        queue = self.results.get(req.step.id) or []
        exit_code, error, stopped = queue.pop(0) if queue else OK
        session = req.session_id or f"sess-{req.continues or req.key.attempt}"
        return StepResult(step_id=req.key.step_id, ok=exit_code == 0, exit_code=exit_code, sandbox="mo-x", error=error,
                          stopped=stopped, session_id=session)

    async def cleanup(self, key):
        pass


LOST = (None, "activity Heartbeat timeout", "lost")  # the platform stopped the attempt
TIMEOUT = (None, "timed out after 30m", "timeout")  # the step's own limit
CRASHED = (1, None, None)  # the harness ran and exited 1
OK = (0, None, None)


@pytest.fixture
async def client():
    path = shutil.which("temporal")
    if not path and os.environ.get("MYORCH_TEMPORAL_DOWNLOAD") != "1":
        pytest.skip("needs the temporal CLI (or MYORCH_TEMPORAL_DOWNLOAD=1)")
    try:
        env = await (WorkflowEnvironment.start_local(dev_server_existing_path=path, data_converter=pydantic_data_converter)
                     if path else WorkflowEnvironment.start_local(data_converter=pydantic_data_converter))
    except Exception as e:  # noqa: BLE001 - no CLI and no download
        pytest.skip(f"no Temporal dev server: {e}")
    async with env:
        yield env.client


async def wait_for(h, pred):
    for _ in range(200):
        state = await h.query(FlowWorkflow.status)
        if state is not None and pred(state):
            return state
        await asyncio.sleep(0.05)
    raise AssertionError("the run never got there")


async def run_flow(client: Client, runner: Runner, flow: str = FLOW, store: Store | None = None, drive=None):
    queue = f"q-{uuid.uuid4().hex[:8]}"
    acts = Activities(store or Store(), runner, actions=None)
    with ThreadPoolExecutor(max_workers=4) as executor:
        async with Worker(client, task_queue=queue, workflows=[FlowWorkflow], activities=acts.all(),
                          activity_executor=executor, workflow_runner=UnsandboxedWorkflowRunner()):
            h = await client.start_workflow(FlowWorkflow.run, FlowInput(flow_yaml=flow),
                                            id=f"t-{uuid.uuid4().hex[:8]}", task_queue=queue)
            if drive is not None:
                await drive(h)
            state = await h.result()
            history = await h.fetch_history()
    return state, history


async def replay(history):
    await Replayer(workflows=[FlowWorkflow], data_converter=pydantic_data_converter,
                   workflow_runner=UnsandboxedWorkflowRunner()).replay_workflow(history)


def sessions(runner: Runner) -> list[tuple[int, int, int | None, str | None]]:
    return [(r.key.attempt, r.key.turn, r.continues, r.interrupted) for r in runner.requests]


async def test_retries_after_interruptions_continue_the_session(client):
    runner = Runner([LOST, LOST, OK])
    state, history = await run_flow(client, runner)
    assert state.instances["develop"].status == "succeeded"
    hb = "activity Heartbeat timeout"
    assert sessions(runner) == [(1, 1, None, None), (2, 1, 1, hb), (3, 1, 1, hb)]
    await replay(history)


async def test_a_retry_after_the_harness_failed_or_its_own_timeout_starts_fresh(client):
    runner = Runner([CRASHED, LOST, OK])
    await run_flow(client, runner)
    assert sessions(runner) == [(1, 1, None, None), (2, 1, None, None), (3, 1, 2, "activity Heartbeat timeout")]
    runner = Runner([TIMEOUT, LOST, OK])
    await run_flow(client, runner)
    assert sessions(runner) == [(1, 1, None, None), (2, 1, None, None), (3, 1, 2, "activity Heartbeat timeout")]


async def test_later_turns_of_a_continuing_attempt_use_the_session_turn_one_reported(client):
    runner = Runner([LOST, OK, OK])
    store = Store([None, Question(question="红色还是蓝色？", options=["红", "蓝"])])  # attempt 2 asks on turn 1

    async def answer(h):
        await wait_for(h, lambda s: s.instances["develop"].status == "waiting_input")
        await h.signal(FlowWorkflow.reply, ReplyInput(step_id="develop", text="红"))

    state, history = await run_flow(client, runner, store=store, drive=answer)
    assert state.instances["develop"].status == "succeeded"
    assert [(r.key.attempt, r.key.turn, r.session_id) for r in runner.requests] == [
        (1, 1, None), (2, 1, None), (2, 2, "sess-1")]
    await replay(history)


async def send_back_once(h):
    """Request changes at the gate, then approve the second visit."""
    await wait_for(h, lambda s: s.instances["human"].status == "waiting_approval")
    await h.signal(FlowWorkflow.request_changes, ChangesInput(step_id="human", approver="ann", text="再改改"))
    def back_at_gate(s):
        record = s.approvals.get("human")
        return s.instances["human"].status == "waiting_approval" and record is not None and len(record.changes) == 1

    await wait_for(h, back_at_gate)
    await h.signal(FlowWorkflow.approve, ApproveInput(step_id="human", approver="ann"))


def risk_attempts(runner: Runner) -> list[int]:
    return [r.key.attempt for r in runner.requests if r.step.id == "risk"]


async def test_a_change_request_keeps_counting_attempts(client):
    runner = Runner(risk=[CRASHED, OK])  # risk needs 2 attempts on its first visit
    state, history = await run_flow(client, runner, CHANGES_FLOW, drive=send_back_once)
    assert state.status == "succeeded"
    assert risk_attempts(runner) == [1, 2, 3]  # the second visit is attempt 3 in logs and sessions
    risk = state.instances["risk"]
    assert (risk.attempts, risk.attempts_before) == (1, 2)  # people see attempt 1 of this visit
    await replay(history)


class WithoutPatch:
    """The workflow module's `workflow` as the code was before a patch: never patched, no marker."""

    def __init__(self, real, patch_id):
        self.real, self.patch_id = real, patch_id

    def __getattr__(self, name):
        return getattr(self.real, name)

    def patched(self, patch_id):
        return False if patch_id == self.patch_id else self.real.patched(patch_id)


async def test_a_run_recorded_before_the_retry_patch_replays_the_old_way(client, monkeypatch):
    monkeypatch.setattr(wfmod, "workflow", WithoutPatch(wfmod.workflow, RETRY_SESSION_PATCH))
    runner = Runner([LOST, OK])
    _, history = await run_flow(client, runner)
    assert sessions(runner) == [(1, 1, None, None), (2, 1, None, None)]  # old behaviour: a fresh session
    monkeypatch.undo()
    await replay(history)


async def test_a_run_recorded_before_the_changes_patch_replays_the_old_way(client, monkeypatch):
    monkeypatch.setattr(wfmod, "workflow", WithoutPatch(wfmod.workflow, CHANGES_ATTEMPTS_PATCH))
    runner = Runner(risk=[CRASHED, OK])
    _, history = await run_flow(client, runner, CHANGES_FLOW, drive=send_back_once)
    assert risk_attempts(runner) == [1, 2, 1]  # old behaviour: numbered from 1 again
    monkeypatch.undo()
    await replay(history)


async def test_labels_go_into_the_mandate_without_new_commands(client):
    """Labels are data on the Mandate: a labelled run replays, and so does one whose input has none
    (FlowInput as written before labels existed)."""
    queue = f"q-{uuid.uuid4().hex[:8]}"
    acts = Activities(Store(), Runner(), actions=None)
    with ThreadPoolExecutor(max_workers=4) as executor:
        async with Worker(client, task_queue=queue, workflows=[FlowWorkflow], activities=acts.all(),
                          activity_executor=executor, workflow_runner=UnsandboxedWorkflowRunner()):
            for inp, want in ((FlowInput(flow_yaml=FLOW, labels=["MD-18700"]), ["MD-18700"]),
                              ({"flow_yaml": FLOW}, [])):
                h = await client.start_workflow(FlowWorkflow.run, inp, id=f"t-{uuid.uuid4().hex[:8]}", task_queue=queue)
                state = await h.result()
                assert state.mandate.labels == want
                await replay(await h.fetch_history())
