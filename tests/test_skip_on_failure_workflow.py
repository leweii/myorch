"""on_failed: skip on the real workflow (FlowWorkflow on a throwaway Temporal dev server, fake storage,
runner and host actions): a failing nice-to-have step, agent, script or host action, is recorded and
skipped, and the run goes on and succeeds; run-trail gets the run's course from the workflow, and a
broken trail does not stop the workflow. Skipped without a Temporal dev server, like
test_retry_session_workflow.py."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import WorkflowFailureError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

import myorch.core.runtrail as rt
from myorch.core.manifest import Manifest
from myorch.core.types import StepResult
from myorch.engines.temporal.activities import Activities
from myorch.engines.temporal.models import FlowInput
from myorch.engines.temporal.workflow import FlowWorkflow
from myorch.runner.actions import ActionOutcome
from test_retry_session_workflow import CRASHED, Runner, Store, client, replay  # noqa: F401  (pytest puts tests/ on sys.path)

FLOW = """
flow: f
on_failed: ask
steps:
  - {id: develop, harness: claude-code, profile: p, prompt: x}
  - {id: takeaway-trail, action: run-trail, needs: [develop], on_failed: skip}
  - {id: takeaway, harness: claude-code, profile: p, prompt: x, needs: [takeaway-trail], on_failed: skip, limits: {retries: 1}}
  - {id: takeaway-commit, harness: script, profile: p, prompt: x, needs: [takeaway], on_failed: skip}
  - {id: risk-rules, harness: script, profile: p, prompt: x, needs: [takeaway-commit]}
"""


class Actions:
    """Host actions: records what they got; fails (or raises) when told to."""

    def __init__(self, ok=True, raises=False):
        self.ok, self.raises, self.requests = ok, raises, []

    def run(self, req):
        self.requests.append(req)
        if self.raises:
            raise RuntimeError("worker lost its disk")
        return ActionOutcome(result=StepResult(step_id=req.key.step_id, ok=self.ok, exit_code=0 if self.ok else 1,
                                               sandbox="(host action)", error=None if self.ok else "no workspace"),
                             manifest=Manifest(step_id=req.key.step_id, status="ok" if self.ok else "failed", summary="x"))


async def run_flow(client, runner, actions, flow=FLOW):
    queue = f"q-{uuid.uuid4().hex[:8]}"
    acts = Activities(Store(), runner, actions=actions)
    with ThreadPoolExecutor(max_workers=4) as executor:
        async with Worker(client, task_queue=queue, workflows=[FlowWorkflow], activities=acts.all(),
                          activity_executor=executor, workflow_runner=UnsandboxedWorkflowRunner()):
            h = await client.start_workflow(FlowWorkflow.run, FlowInput(flow_yaml=flow),
                                            id=f"t-{uuid.uuid4().hex[:8]}", task_queue=queue)
            try:
                await h.result()
            except WorkflowFailureError:
                pass
            state = await h.query(FlowWorkflow.status)
            history = await h.fetch_history()
    return state, history


def ran(runner) -> list[str]:
    return [r.key.step_id for r in runner.requests]


async def test_the_takeaway_steps_run_and_the_trail_comes_from_the_workflow(client):
    runner, actions = Runner(), Actions()
    state, history = await run_flow(client, runner, actions)
    assert state.status == "succeeded" and ran(runner) == ["develop", "takeaway", "takeaway-commit", "risk-rules"]
    trail = actions.requests[0].trail
    assert trail["run"] == state.run_id and [s["id"] for s in trail["steps"]][0] == "develop"
    await replay(history)


async def test_failing_takeaway_steps_are_skipped_and_the_run_goes_on(client):
    runner, actions = Runner(takeaway=[CRASHED, CRASHED], **{"takeaway-commit": [CRASHED]}), Actions(ok=False)
    state, history = await run_flow(client, runner, actions)
    assert state.status == "succeeded"
    for s in ("takeaway-trail", "takeaway", "takeaway-commit"):
        assert state.instances[s].status == "skipped" and "on_failed: skip" in state.instances[s].error
    assert state.instances["takeaway"].attempts == 2  # its retry first; never asks (the flow says on_failed: ask)
    assert state.instances["risk-rules"].status == "succeeded"
    await replay(history)


async def test_a_host_action_that_raises_is_skipped_too(client):
    runner, actions = Runner(), Actions(raises=True)
    state, history = await run_flow(client, runner, actions)
    assert state.status == "succeeded" and state.instances["takeaway-trail"].status == "skipped"
    await replay(history)


async def test_a_broken_trail_does_not_stop_the_workflow(client, monkeypatch):
    def broken(state, replies):
        raise TypeError("odd manifest")

    monkeypatch.setattr(rt, "run_trail", broken)  # safe_run_trail calls it
    runner, actions = Runner(), Actions()
    state, _ = await run_flow(client, runner, actions)
    assert state.status == "succeeded"
    assert actions.requests[0].trail["error"].startswith("trail incomplete")


async def test_without_skip_a_failing_step_still_fails_the_run(client):
    flow = FLOW.replace(", on_failed: skip}", "}").replace("on_failed: ask\n", "")
    runner, actions = Runner(**{"takeaway-commit": [CRASHED]}), Actions()
    state, _ = await run_flow(client, runner, actions, flow)
    assert state.status == "failed" and state.instances["risk-rules"].status == "blocked"


class BrokenPrepare(Store):
    """prepare_step fails for one step every time (the volume, say): an infrastructure failure."""

    def prepare_step(self, key):
        if key.step_id == "takeaway":
            raise RuntimeError("docker volume busy")


async def test_an_infrastructure_failure_of_a_skip_step_is_skipped_too(client):
    queue = f"q-{uuid.uuid4().hex[:8]}"
    runner, actions = Runner(), Actions()
    acts = Activities(BrokenPrepare(), runner, actions=actions)
    with ThreadPoolExecutor(max_workers=4) as executor:
        async with Worker(client, task_queue=queue, workflows=[FlowWorkflow], activities=acts.all(),
                          activity_executor=executor, workflow_runner=UnsandboxedWorkflowRunner()):
            h = await client.start_workflow(FlowWorkflow.run, FlowInput(flow_yaml=FLOW),
                                            id=f"t-{uuid.uuid4().hex[:8]}", task_queue=queue)
            state = await h.result()
            history = await h.fetch_history()
    takeaway = state.instances["takeaway"]
    assert state.status == "succeeded" and takeaway.status == "skipped" and takeaway.skipped_after_failure
    assert "platform error" in takeaway.error
    assert "takeaway" not in ran(runner) and state.instances["risk-rules"].status == "succeeded"
    await replay(history)


async def test_skipped_failures_are_marked_for_events(client):
    runner, actions = Runner(takeaway=[CRASHED, CRASHED]), Actions()
    state, _ = await run_flow(client, runner, actions)
    from myorch.core.events import derive_events

    failed = [e.step for e in derive_events(state, detail=True) if e.kind == "step_finished" and e.data["status"] == "failed"]
    assert failed == ["takeaway"]
