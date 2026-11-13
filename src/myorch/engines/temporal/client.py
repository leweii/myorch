from __future__ import annotations

import secrets
from typing import Any

from temporalio.client import Client, WorkflowFailureError

from myorch.core.flow import load_flow
from myorch.core.inputs import start_labels
from myorch.core.run import RunState
from myorch.engines.temporal import TASK_QUEUE
from myorch.engines.temporal.models import ApproveInput, ChangesInput, FlowInput, ReplyInput
from myorch.engines.temporal.workflow import FlowWorkflow


async def start_run(client: Client, flow_yaml: str, inputs: dict[str, Any], initiator: str = "unknown",
                    initiator_aliases: list[str] | None = None, project: dict[str, Any] | None = None,
                    run_id: str | None = None, source: str = "", files: dict[str, str] | None = None,
                    knowledge: list | None = None, labels: list[str] | None = None) -> str:
    """Start a run. A given run_id makes the start idempotent: a second start with the same id
    raises WorkflowAlreadyStartedError (channels derive it from the message).
    `labels`: the caller's own (e.g. --label MD-18700); the tickets in the inputs are added here."""
    flow = load_flow(flow_yaml)  # validate before submitting
    flow.check_inputs(inputs)
    labels = start_labels(flow.inputs, inputs, labels or [])
    run_id = run_id or f"{flow.flow}-{secrets.token_hex(4)}"
    await client.start_workflow(
        FlowWorkflow.run,
        FlowInput(flow_yaml=flow_yaml, inputs=inputs, initiator=initiator, initiator_aliases=initiator_aliases or [],
                  project=project, source=source, files=files or {}, knowledge=list(knowledge or []),
                  labels=labels),
        id=run_id,
        task_queue=TASK_QUEUE,
    )
    return run_id


async def get_status(client: Client, run_id: str) -> RunState | None:
    return await client.get_workflow_handle(run_id).query(FlowWorkflow.status)


async def wait_result(client: Client, run_id: str) -> RunState | None:
    """Final state of a run. Failed and cancelled runs end the workflow with an error, so their
    state is read back through the status query instead."""
    try:
        return await client.get_workflow_handle(run_id, result_type=RunState).result()
    except WorkflowFailureError:
        return await get_status(client, run_id)


async def list_runs(client: Client, limit: int = 20) -> list[tuple[str, str]]:
    """Flow runs only, newest first. A continued run (resume_run) has several executions under one
    id; only the newest counts."""
    out, seen = [], set()
    async for wf in client.list_workflows(f"TaskQueue = '{TASK_QUEUE}' AND WorkflowType = 'FlowWorkflow'"):
        if wf.id in seen:
            continue
        seen.add(wf.id)
        out.append((wf.id, wf.status.name if wf.status else "UNKNOWN"))
        if len(out) >= limit:
            break
    return out


async def resume_run(client: Client, run_id: str, steps: list[str] | None = None, by: str = "") -> list[str]:
    """Continue a failed or cancelled run from its failed steps: a new execution with the same id,
    the same workspace and mandate, starting from the last state (RunState.reopen). Returns the
    instances that run again. `steps`: only these (instance or step ids); empty: every failed one."""
    from temporalio.common import WorkflowIDReusePolicy

    desc = await client.get_workflow_handle(run_id).describe()
    engine = desc.status.name if desc.status else "UNKNOWN"
    if engine == "RUNNING":
        raise ValueError("The run has not ended yet. For a step that is waiting on you, reply in the conversation.")
    state = await get_status(client, run_id)
    if state is None:
        raise ValueError(f"cannot read the state of run {run_id}")
    if state.status == "running":  # terminated from outside: nothing recorded the end
        state.cancel()
    trial = state.model_copy(deep=True)
    reset = trial.reopen(set(steps) if steps else None)  # check before starting: raises ValueError
    m = state.mandate
    await client.start_workflow(
        FlowWorkflow.run,
        FlowInput(flow_yaml=state.flow_yaml, inputs=state.inputs, initiator=m.initiator if m else "unknown",
                  initiator_aliases=list(m.initiator_aliases) if m else [], source=m.source if m else "",
                  project=state.project, knowledge=list(m.knowledge) if m else [],
                  labels=list(m.labels) if m else [], resume=state, resume_steps=list(steps or []), resumed_by=by),
        id=run_id, task_queue=TASK_QUEUE, id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
    )
    return reset


async def _waiting(client: Client, run_id: str, step_id: str, statuses: set[str]) -> None:
    state = await get_status(client, run_id)
    if state is None:
        raise ValueError(f"run {run_id!r} has not started yet")
    inst = state.instances.get(step_id)
    loop = state.loops.get(step_id)
    status = inst.status if inst else loop.status if loop else None
    if status not in statuses:
        raise ValueError(f"{step_id!r} is not waiting for this (status: {status or 'unknown'})")


async def approve(client: Client, run_id: str, step_id: str, approver: str) -> None:
    await _waiting(client, run_id, step_id, {"waiting_approval"})
    await client.get_workflow_handle(run_id).signal(FlowWorkflow.approve, ApproveInput(step_id=step_id, approver=approver))


async def reject(client: Client, run_id: str, step_id: str, approver: str) -> None:
    await _waiting(client, run_id, step_id, {"waiting_approval"})
    await client.get_workflow_handle(run_id).signal(FlowWorkflow.reject, ApproveInput(step_id=step_id, approver=approver))


async def request_changes(client: Client, run_id: str, step_id: str, approver: str, text: str) -> None:
    """Send the run back from an approval gate with change requests (the gate needs approval.changes)."""
    if not text.strip():
        raise ValueError("the change request is empty")
    await _waiting(client, run_id, step_id, {"waiting_approval"})
    state = await get_status(client, run_id)
    step = state.step_of(step_id) if state else None
    if step is None or step.approval is None or step.approval.changes is None:
        raise ValueError(f"{step_id!r} does not accept change requests (approval.changes is not configured): approve or reject it")
    await client.get_workflow_handle(run_id).signal(
        FlowWorkflow.request_changes, ChangesInput(step_id=step_id, approver=approver, text=text))


async def reply(client: Client, run_id: str, step_id: str, text: str) -> None:
    await _waiting(client, run_id, step_id, {"waiting_input"})
    await client.get_workflow_handle(run_id).signal(FlowWorkflow.reply, ReplyInput(step_id=step_id, text=text))


async def cancel(client: Client, run_id: str) -> None:
    await client.get_workflow_handle(run_id).cancel()
