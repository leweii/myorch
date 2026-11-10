"""What happened in one run so far, from its state: for development's takeaway step
(docs/takeaway/trail.json, written by the run-trail host action).

The takeaway agent reads this instead of the event log, which lives on the host. Built inside the
workflow, only for a run-trail step, so it is pure and deterministic: every value comes from RunState
and the replies the run accepted. Manifests are written by agents, so nothing here trusts their shape:
safe_run_trail never raises (a workflow task must not fail over a strange manifest).

myorch/trail.py is the same idea across many runs, from the event log, for learn.
"""

from __future__ import annotations

from typing import Any

from myorch.core.flow import Loop
from myorch.core.run import RunState, failure_reason

TEXT = 1500  # per summary, error or answer: enough to learn from, short enough for a prompt


def _cut(v: Any, n: int = TEXT) -> str:
    s = v if isinstance(v, str) else ("" if v is None else repr(v))
    return s if len(s) <= n else s[:n] + "…"


def _small(outputs: Any) -> dict[str, Any]:
    """A step's outputs, long values cut (an output is a flag or a count, rarely a document)."""
    if not isinstance(outputs, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in list(outputs.items())[:40]:
        out[_cut(k, 80)] = v if isinstance(v, (bool, int, float)) or v is None else _cut(v, 300)
    return out


def _outputs(inst) -> dict[str, Any]:
    m = inst.manifest
    return m.outputs if m is not None and isinstance(m.outputs, dict) else {}


def run_trail(state: RunState, replies: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The run's course: each step's status, summary, failure and attempts; the questions and answers;
    each loop round's blocking count and CI result; approvals so far.

    `replies`: the answers the run accepted, as {"step", "question", "text", "at"} (the workflow's
    `replied` events)."""
    flow = state.flow
    steps = []
    for inst in state.instances.values():
        if inst.status == "pending":
            continue  # not reached yet (the steps after the takeaway)
        m = inst.manifest
        item: dict[str, Any] = {"id": inst.id, "status": inst.status, "attempts": inst.attempts, "turns": inst.turns}
        if inst.loop_id:
            item["loop"], item["round"] = inst.loop_id, inst.round
        if m is not None and m.summary:
            item["summary"] = _cut(m.summary, 600)
        if inst.status in ("failed", "blocked", "rejected") or (inst.status == "skipped" and inst.error):
            item["error"] = _cut(inst.error or failure_reason(inst))
        if _outputs(inst):
            item["outputs"] = _small(_outputs(inst))
        if m is not None and m.notes:
            item["notes"] = _cut(m.notes, 600)
        steps.append(item)

    loops = []
    for item in flow.steps:
        if not isinstance(item, Loop) or item.id not in state.loops:
            continue
        lr = state.loops[item.id]
        rounds = []
        for rnd in range(1, lr.round + 1):
            members = [i for i in state.instances.values() if i.loop_id == item.id and i.round == rnd]
            outs = [_outputs(i) for i in members]
            r: dict[str, Any] = {"round": rnd, "steps": {i.id: i.status for i in members}}
            blocking = next((o["blocking"] for o in outs if "blocking" in o), None)
            if blocking is not None:
                r["blocking"] = blocking if isinstance(blocking, (int, float)) else _cut(blocking, 40)
            ci = next((o for o in outs if "passed" in o), None)  # the CI step: passed / failed / skipped
            if ci is not None:
                passed, failed = ci.get("passed"), ci.get("failed")
                r["ci"] = {"passed": passed if isinstance(passed, bool) else _cut(passed, 40),
                           "failed": [_cut(x, 200) for x in failed[:20]] if isinstance(failed, list) else []}
            rounds.append(r)
        loops.append({"id": item.id, "status": lr.status, "rounds": lr.round, "max_rounds": lr.max_rounds,
                      "until": item.until, "error": lr.error, "per_round": rounds})

    qa = [{"step": _cut(r.get("step"), 80), "question": _cut(r.get("question")), "answer": _cut(r.get("text")),
           "at": _cut(r.get("at"), 40)} for r in replies or [] if isinstance(r, dict)]
    approvals = {k: {"approvers": list(v.approvers), "rejected_by": v.rejected_by,
                     "changes": [{"by": c.by, "text": _cut(c.text), "round": c.round} for c in v.changes]}
                 for k, v in state.approvals.items()}
    m = state.mandate
    return {
        "run": state.run_id,
        "flow": flow.flow,
        "role": flow.kind or "",
        "project": (state.project or {}).get("name", ""),
        "branch": state.branch(),
        "inputs": {k: _cut(v, 300) for k, v in state.inputs.items()},
        "requested_at": m.requested_at if m else "",
        "failed_so_far": [s["id"] for s in steps if s["status"] == "failed"],
        "steps": steps,
        "loops": loops,
        "qa": qa,
        "approvals": approvals,
    }


def safe_run_trail(state: RunState, replies: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """run_trail, or a minimal trail when something in the state is not what it expects."""
    try:
        return run_trail(state, replies)
    except Exception as e:  # noqa: BLE001 - never fail the workflow task over a trail
        return {"run": state.run_id, "flow": getattr(state.flow, "flow", ""),
                "error": f"trail incomplete: {type(e).__name__}: {_cut(str(e), 300)}", "steps": [], "loops": [], "qa": []}
