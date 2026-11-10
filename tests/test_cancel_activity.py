"""A cancel that arrives while a step's activity runs must end the run, even when the activity then
completes normally (engines/temporal/workflow.py CANCEL_PATCH). Runs a throwaway Temporal dev
server from the installed `temporal` binary; skipped without it."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowExecutionStatus
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker
from temporalio.workflow import ActivityCancellationType

from myorch.engines.temporal.workflow import cancel_requested

pytestmark = pytest.mark.skipif(shutil.which("temporal") is None, reason="needs the temporal CLI")


@activity.defn
async def finishes_anyway() -> str:
    """Like an agent turn that was about to end: the cancel request does not stop it."""
    for _ in range(6):
        await asyncio.sleep(0.5)
        try:
            activity.heartbeat()
        except Exception:
            pass
    return "done"


async def step(check: bool) -> None:
    await workflow.execute_activity(finishes_anyway, start_to_close_timeout=timedelta(seconds=60),
                                    heartbeat_timeout=timedelta(seconds=10), retry_policy=RetryPolicy(maximum_attempts=1),
                                    cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED)
    if check and cancel_requested():
        raise asyncio.CancelledError()
    await workflow.wait_condition(lambda: False)  # the step's next wait (e.g. for a reply)


@workflow.defn
class OneStep:
    @workflow.run
    async def run(self, check: bool) -> str:
        task = asyncio.create_task(step(check))
        try:
            await workflow.wait([task])
        except asyncio.CancelledError:  # as MyorchWorkflow.run: cancel the steps, wait for them, record
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        return "finished"


async def status_after_cancel(env: WorkflowEnvironment, check: bool) -> WorkflowExecutionStatus:
    queue = f"q-{uuid.uuid4().hex[:8]}"
    async with Worker(env.client, task_queue=queue, workflows=[OneStep], activities=[finishes_anyway],
                      workflow_runner=UnsandboxedWorkflowRunner()):
        h = await env.client.start_workflow(OneStep.run, check, id=f"w-{uuid.uuid4().hex[:8]}", task_queue=queue)
        await asyncio.sleep(1)  # the activity is running
        await h.cancel()
        for _ in range(40):
            status = (await h.describe()).status
            if status != WorkflowExecutionStatus.RUNNING:
                return status
            await asyncio.sleep(0.25)
        await h.terminate("test cleanup")
        return WorkflowExecutionStatus.RUNNING


async def test_a_cancel_during_an_activity_that_completes_still_cancels_the_run():
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=shutil.which("temporal")) as env:
        assert await status_after_cancel(env, check=False) == WorkflowExecutionStatus.RUNNING  # the bug: it hangs
        assert await status_after_cancel(env, check=True) == WorkflowExecutionStatus.CANCELED


# The other way round (workflow.py CANCEL_RETRY_PATCH): the cancel does stop the activity, the
# workflow gets an ActivityError, and a step with a retry left used to start a fresh attempt, so the
# run went on (2026-11-06: a cancelled develop step started its whole agent turn again).
ATTEMPTS = {"n": 0}


@activity.defn
async def stops_on_cancel() -> str:
    ATTEMPTS["n"] += 1
    while True:  # an agent turn: heartbeats until the cancel reaches it, then CancelledError
        await asyncio.sleep(0.3)
        activity.heartbeat()


async def step_with_retry(check: bool) -> None:
    for _ in range(2):  # retries: 1, as in a flow step
        try:
            await workflow.execute_activity(stops_on_cancel, start_to_close_timeout=timedelta(seconds=120),
                                            heartbeat_timeout=timedelta(seconds=2), retry_policy=RetryPolicy(maximum_attempts=1),
                                            cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED)
            return
        except ActivityError:
            if check and cancel_requested():
                raise asyncio.CancelledError() from None
    await workflow.wait_condition(lambda: False)


@workflow.defn
class RetriedStep:
    @workflow.run
    async def run(self, check: bool) -> str:
        task = asyncio.create_task(step_with_retry(check))
        try:
            await workflow.wait([task])
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        return "finished"


async def cancel_retried(env: WorkflowEnvironment, check: bool) -> tuple[WorkflowExecutionStatus, int]:
    ATTEMPTS["n"] = 0
    queue = f"q-{uuid.uuid4().hex[:8]}"
    async with Worker(env.client, task_queue=queue, workflows=[RetriedStep], activities=[stops_on_cancel],
                      workflow_runner=UnsandboxedWorkflowRunner()):
        h = await env.client.start_workflow(RetriedStep.run, check, id=f"w-{uuid.uuid4().hex[:8]}", task_queue=queue)
        await asyncio.sleep(1)  # the activity is running
        await h.cancel()
        for _ in range(60):
            status = (await h.describe()).status
            if status != WorkflowExecutionStatus.RUNNING:
                return status, ATTEMPTS["n"]
            await asyncio.sleep(0.25)
        await h.terminate("test cleanup")
        return WorkflowExecutionStatus.RUNNING, ATTEMPTS["n"]


async def test_a_cancel_that_stops_the_activity_does_not_retry_the_step():
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=shutil.which("temporal")) as env:
        assert await cancel_retried(env, check=False) == (WorkflowExecutionStatus.RUNNING, 2)   # the bug: a new attempt
        assert await cancel_retried(env, check=True) == (WorkflowExecutionStatus.CANCELED, 1)
