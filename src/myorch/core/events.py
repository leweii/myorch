"""What a run tells the outside world, derived from its state.

The workflow publishes each event once (by key) to the event log; channels and `myorch watch`
read the log. Events are facts in a channel-neutral form; how they are worded for GitHub, Slack
or a terminal is the channel's business.

Attention principle: a run speaks when it starts, when someone must act (a question, an
approval, a reminder for an approval nobody handled), when it opens a pull request, and when
it ends. Progress in between is streamed separately and is not an event here.

Detail events (`detail=True`, runs started after the event-detail patch): step_started,
step_finished, approval_progress and loop_round. With them a channel adapter (the console)
builds the whole run view from the event log alone, without asking the engine. Each one is
keyed by what changed, so a change publishes a new event and a repeat publishes nothing.
Design: docs/notes/2026-11-02-channel-layer.md.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from myorch.core.flow import Loop
from myorch.core.run import RunState

EventKind = Literal["run_started", "question", "approval", "approval_reminder", "pr_opened", "run_finished",
                    "step_started", "step_finished", "approval_progress", "loop_round",
                    "replied", "changes_requested", "run_resumed"]

FINISHED = ("succeeded", "failed", "rejected", "skipped", "blocked", "cancelled")
SUMMARY_CHARS = 4000  # an event is a notice, not the document: the full text stays in the workspace

PR_URL = re.compile(r"https://github\.com/([^/]+/[^/]+)/pull/(\d+)")


class RunEvent(BaseModel):
    key: str  # stable within a run: the same fact is published once
    kind: EventKind
    run_id: str
    step: str = ""  # instance or loop id it concerns
    data: dict[str, Any] = Field(default_factory=dict)


def _outputs(state: RunState, key: str, where=lambda o: True) -> list[Any]:
    return [i.manifest.outputs.get(key) for i in state.instances.values()
            if i.manifest is not None and i.manifest.outputs.get(key) and where(i.manifest.outputs)]


def _loop_def(state: RunState, loop_id: str) -> Loop | None:
    return next((s for s in state.flow.steps if isinstance(s, Loop) and s.loop == loop_id), None)


def _started_data(state: RunState) -> dict[str, Any]:
    """run_started, detail form: what the run is, so an adapter can draw it without the engine.
    The expanded flow itself is in the workspace (.myorch/flow.yaml), read through the channel."""
    m = state.mandate
    return {"flow": state.flow.flow, "role": state.flow.kind or "", "project": (state.project or {}).get("name", ""),
            "inputs": dict(state.inputs), "flow_file": ".myorch/flow.yaml", "mandate_file": ".myorch/mandate.json",
            "initiator": m.initiator if m else "", "initiator_aliases": list(m.initiator_aliases) if m else [],
            "requested_at": m.requested_at if m else "", "source": m.source if m else "",
            "labels": list(m.labels) if m else []}


def _detail_events(state: RunState) -> list[RunEvent]:
    rid, out = state.run_id, []
    for inst in state.instances.values():
        where = {"step": inst.step_id, "loop": inst.loop_id or "", "round": inst.round, "replica": inst.replica}
        if inst.attempts >= 1:
            out.append(RunEvent(key=f"step:{inst.id}:{inst.attempts}.{inst.turns}", kind="step_started", run_id=rid,
                                step=inst.id, data={**where, "attempt": inst.attempts, "turn": inst.turns,
                                                    "model": _model(state, inst.id)}))
        if inst.status in FINISHED:
            m, r = inst.manifest, inst.result
            # A step with on_failed: skip that failed: report the failure first (watchers and learn look for
            # failed steps), then the skip that lets the run go on.
            for status in (["failed"] if inst.skipped_after_failure and inst.status == "skipped" else []) + [inst.status]:
                out.append(RunEvent(key=f"done:{inst.id}:{inst.attempts}.{inst.turns}:{status}", kind="step_finished",
                                    run_id=rid, step=inst.id, data={
                                        **where, "status": status, "attempts": inst.attempts, "turns": inst.turns,
                                        "summary": (m.summary if m else "")[:SUMMARY_CHARS],
                                        "notes": (m.notes if m else "")[:SUMMARY_CHARS],
                                        "outputs": dict(m.outputs) if m else {},
                                        "manifest_status": m.status if m else "",
                                        "error": inst.error or (r.error if r else None),
                                        "exit_code": r.exit_code if r else None,
                                        "model": r.model if r else None}))
        record = state.approvals.get(inst.step_id)
        if inst.approvals or (record is not None and record.rejected_by):
            rejected = record.rejected_by if record is not None else None
            visit = f":{len(record.changes)}" if record is not None and record.changes else ""
            out.append(RunEvent(key=f"apv:{inst.id}{visit}:{','.join(inst.approvals)}:{rejected or ''}",
                                kind="approval_progress", run_id=rid, step=inst.id, data={
                                    "needed": inst.approvals_needed, "approvers": list(inst.approvals),
                                    "rejected_by": rejected,
                                    "missing": state.approval_missing(inst.id, set(inst.approvals)) or ""}))
        if record is not None:
            for n, c in enumerate(record.changes, 1):
                out.append(RunEvent(key=f"changes:{inst.step_id}:{n}", kind="changes_requested", run_id=rid,
                                    step=inst.step_id, data={"by": c.by, "text": c.text, "at": c.at, "round": c.round}))
    for lr in state.loops.values():
        if lr.round >= 1 or lr.status != "pending":
            loop = _loop_def(state, lr.id)
            data: dict[str, Any] = {"loop": lr.id, "round": lr.round, "max_rounds": lr.max_rounds,
                                    "status": lr.status, "until": loop.until if loop else "", "error": lr.error}
            if lr.status == "waiting_input" and lr.question is not None:
                data["question"] = {"question": lr.question.question, "options": list(lr.question.options)}
            out.append(RunEvent(key=f"round:{lr.id}:{lr.round}:{lr.status}", kind="loop_round", run_id=rid,
                                step=lr.id, data=data))
    return out


def _model(state: RunState, iid: str) -> str | None:
    step = state.step_of(iid)
    # Only agent steps use a model; script steps, approvals and host actions have none (like the runner).
    if getattr(step, "action", None) or getattr(step, "harness", None) in (None, "script", "shell"):
        return None
    return getattr(step, "model", None) or getattr(state.flow, "model", None)


def derive_events(state: RunState, finished: bool = False, detail: bool = False) -> list[RunEvent]:
    """Every event the run has produced so far, in a stable order. Publishers drop known keys."""
    rid = state.run_id
    started = _started_data(state) if detail else {"flow": state.flow.flow, "project": (state.project or {}).get("name", "")}
    out = [RunEvent(key="started", kind="run_started", run_id=rid, data=started)]
    for inst in state.instances.values():
        if inst.status == "waiting_input" and inst.question is not None:
            # Attempt and turn: a later attempt (or a failure after a reply) asks anew.
            at = f"{inst.turns}" if inst.attempts <= 1 else f"{inst.attempts}.{inst.turns}"
            out.append(RunEvent(key=f"ask:{inst.id}:{at}", kind="question", run_id=rid, step=inst.id,
                                data={"question": inst.question.question, "options": list(inst.question.options)}))
        if inst.status == "waiting_approval":
            risk = next((i.manifest.summary for i in state.instances.values()
                         if i.step_id == "risk-rules" and i.manifest is not None), "")
            approval = state.step_of(inst.id).approval
            record = state.approvals.get(inst.step_id)
            # Each visit to the gate (after change requests) is a new decision with its own key.
            visit = f":{len(record.changes)}" if record is not None and record.changes else ""
            actions = ["approve", "reject"] + (["changes"] if approval is not None and approval.changes else [])
            out.append(RunEvent(key=f"approve:{inst.id}{visit}", kind="approval", run_id=rid, step=inst.id,
                                data={"needed": inst.approvals_needed, "risk": risk, "actions": actions,
                                      "missing": state.approval_missing(inst.id, set(inst.approvals)) or ""}))
            if inst.reminded:
                out.append(RunEvent(key=f"remind:{inst.id}{visit}", kind="approval_reminder", run_id=rid,
                                    step=inst.id, data={"needed": inst.approvals_needed,
                                                        "approved_by": list(inst.approvals),
                                                        "waited": approval.remind_after if approval else ""}))
    for lr in state.loops.values():
        if lr.status == "waiting_input" and lr.question is not None:
            out.append(RunEvent(key=f"loop:{lr.id}:{lr.round}", kind="question", run_id=rid, step=lr.id,
                                data={"question": lr.question.question, "options": list(lr.question.options)}))
    if detail:
        out.extend(_detail_events(state))
    for url in dict.fromkeys(str(u) for u in _outputs(state, "pr_url")):
        m = PR_URL.search(url)
        if m:
            out.append(RunEvent(key=f"pr:{m.group(2)}", kind="pr_opened", run_id=rid,
                                data={"url": url, "repo": m.group(1), "number": int(m.group(2))}))
    if finished:
        data: dict[str, Any] = {"status": state.status}
        if state.status == "succeeded":
            data["pr_url"] = str(next(iter(_outputs(state, "pr_url")), ""))
            data["review_url"] = str(next(iter(_outputs(state, "review_url")), ""))
            data["stopped"] = next((i.manifest.summary for i in state.instances.values()
                                    if i.manifest is not None and i.manifest.outputs.get("proceed") is False), "")
            # What a channel needs to hand the change over (lib/deliver.yaml); see
            # docs/notes/2026-11-02-delivery-interface.md.
            delivery = next((d for d in _outputs(state, "delivery") if isinstance(d, dict)), None)
            if delivery is not None:
                data["delivery"] = delivery
        elif state.status != "cancelled":
            data["failure"] = state.failure_summary()
        # A continued run (RunState.reopen) ends again: a new key, so the new end is published.
        key = "finished" if state.resumes == 0 else f"finished:{state.resumes}"
        out.append(RunEvent(key=key, kind="run_finished", run_id=rid, data=data))
    return out
