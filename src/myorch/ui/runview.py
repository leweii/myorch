"""A run as the console draws it, folded from the event log and the run's flow snapshot.

The console is an adapter of the channel relay (docs/notes/2026-11-02-channel-layer.md): it reads
a run through the relay's events (myorch-data/events.db) and content (.myorch/flow.yaml), never
through the engine. Runs started before the event-detail patch (event-detail-v1) only have the
lifecycle events; for those the caller falls back to the engine's state during the migration.

`fold()` returns the same shape as the old Temporal-based view (tree, waiting, mandate, status), so
the page draws both alike.
"""

from __future__ import annotations

import re
from typing import Any

from myorch.core.flow import Flow, Loop, Step
from myorch.storage.events import LoggedEvent

IID = re.compile(r"^(?P<step>.+?)(?:\.r(?P<round>\d+))?(?:\.x(?P<replica>\d+))?$")
FLOW_FILE = ".myorch/flow.yaml"

# Engine (Temporal) statuses of a whole run, in the console's words.
ENGINE_STATUS = {"RUNNING": "running", "COMPLETED": "succeeded", "FAILED": "failed", "TERMINATED": "failed",
                 "TIMED_OUT": "failed", "CANCELED": "cancelled", "CONTINUED_AS_NEW": "running"}


def has_detail(events: list[LoggedEvent]) -> bool:
    """The run publishes detail events: its run_started names the flow snapshot."""
    return any(e.kind == "run_started" and e.data.get("flow_file") for e in events)


def exec_kind(step: Step) -> str:
    if step.action:
        return "host"
    if step.harness is None:
        return "approval"
    return "script" if step.harness in ("script", "shell") else "agent"


def step_meta(step: Step) -> dict[str, Any]:
    return {"exec": exec_kind(step), "model": step.model, "profile": step.profile, "when": step.when,
            "fanout": step.fanout, "needs": step.needs, "interaction": step.interaction,
            "approval": bool(step.approval)}


def _blank(iid: str, step: str, loop: str | None, round_: int, replica: int) -> dict[str, Any]:
    return {"id": iid, "step": step, "loop": loop, "status": "pending", "round": round_, "replica": replica,
            "attempts": 0, "turns": 0, "model": None, "exit_code": None, "summary": "", "outputs": {},
            "files": [], "notes": "", "manifest_status": None, "question": None,
            "approvals_needed": 0, "approvals": [], "missing": "", "rejected_by": None, "error": None}


def fold(flow: Flow, events: list[LoggedEvent], engine_status: str | None = None) -> dict[str, Any]:
    """The run from its events, in seq order. `engine_status` (RUNNING, TERMINATED, ...) settles the
    run's status when no run_finished was written (a run terminated from outside)."""
    from myorch.ui.service import stages

    loop_of = {s.id: item.id for item in flow.steps if isinstance(item, Loop) for s in item.all_steps()}
    loops: dict[str, dict[str, Any]] = {
        item.id: {"status": "pending", "round": 0, "max_rounds": item.max_rounds, "error": None, "question": None}
        for item in flow.steps if isinstance(item, Loop)}
    insts: dict[str, dict[str, Any]] = {}
    started: dict[str, Any] = {}
    finished: dict[str, Any] | None = None
    said: list[dict[str, Any]] = []  # replies and change requests, from the engine (any channel, the CLI too)

    def inst(iid: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        if iid not in insts:
            d, m = data or {}, IID.match(iid)
            step = d.get("step") or (m.group("step") if m else iid)
            round_ = int(d.get("round") if d.get("round") is not None else (m.group("round") or 0) if m else 0)
            replica = int(d.get("replica") if d.get("replica") is not None else (m.group("replica") or 0) if m else 0)
            insts[iid] = _blank(iid, step, d.get("loop") or loop_of.get(step), round_, replica)
        return insts[iid]

    for e in events:
        d = e.data or {}
        if e.kind == "run_started":
            started = d
        elif e.kind == "step_started":
            i = inst(e.step, d)
            if d.get("attempt", i["attempts"]) != i["attempts"]:  # a new attempt: the last one's result is gone
                i.update(summary="", exit_code=None, manifest_status=None, outputs={}, notes="")
            i.update(status="running", attempts=d.get("attempt", i["attempts"]), turns=d.get("turn", i["turns"]),
                     model=d.get("model") or i["model"], question=None, error=None)
        elif e.kind == "step_finished":
            i = inst(e.step, d)
            i.update({k: d[k] for k in ("status", "attempts", "turns", "summary", "notes", "outputs", "manifest_status",
                                        "error", "exit_code") if k in d})
            i["model"] = d.get("model") or i["model"]
            i["question"] = None
        elif e.kind == "question":
            q = {"question": d.get("question", ""), "options": list(d.get("options") or [])}
            if e.step in loops:
                loops[e.step].update(status="waiting_input", question=q)
            else:
                inst(e.step).update(status="waiting_input", question=q)
        elif e.kind == "approval":
            inst(e.step).update(status="waiting_approval", approvals_needed=d.get("needed", 0), missing=d.get("missing", ""))
        elif e.kind == "approval_progress":
            inst(e.step).update(approvals_needed=d.get("needed", 0), approvals=list(d.get("approvers") or []),
                                rejected_by=d.get("rejected_by"), missing=d.get("missing", ""))
        elif e.kind == "loop_round" and d.get("loop") in loops:
            loops[d["loop"]].update(round=d.get("round", 0), max_rounds=d.get("max_rounds", loops[d["loop"]]["max_rounds"]),
                                    status=d.get("status", "running"), error=d.get("error"), question=d.get("question"))
        elif e.kind == "replied":
            said.append({"kind": "reply", "step": e.step, "text": d.get("text", ""), "question": d.get("question", ""),
                         "options": [], "at": d.get("at"), "by": None})
        elif e.kind == "changes_requested":
            said.append({"kind": "changes", "step": e.step, "text": d.get("text", ""), "by": d.get("by"), "at": d.get("at")})
        elif e.kind == "run_finished":
            finished = d
        elif e.kind == "run_resumed":  # continued from its failed steps (RunState.reopen), from any channel
            finished = None
            said.append({"kind": "resume", "step": "", "text": ", ".join(d.get("steps") or []), "by": d.get("by"),
                         "at": d.get("at")})
            for iid in d.get("steps") or []:  # unless a later attempt already shows (older logs wrote it late)
                if inst(iid)["status"] in ("failed", "cancelled", "blocked", "waiting_input", "waiting_approval"):
                    insts[iid].update(status="pending", error=None, question=None)

    status = (finished or {}).get("status") or ENGINE_STATUS.get(engine_status or "RUNNING", "running")
    if finished is None and status != "running":  # stopped from outside: nothing is still moving
        for i in insts.values():
            if i["status"] in ("running", "waiting_input", "waiting_approval"):
                i["status"] = "cancelled"

    # Only agent steps use a model. Runs from before the fix logged the flow's model on script steps too.
    kind_of = {s.id: exec_kind(s) for item in flow.steps
               for s in (item.all_steps() if isinstance(item, Loop) else [item])}
    for i in insts.values():
        if kind_of.get(i["step"], "agent") != "agent":
            i["model"] = None

    tree: list[dict[str, Any]] = []
    for item in flow.steps:
        if isinstance(item, Loop):
            lp = loops[item.id]
            rounds: dict[int, list] = {}
            for i in insts.values():
                if i["loop"] == item.id:
                    rounds.setdefault(i["round"], []).append(i)
            tree.append({"kind": "loop", "id": item.id, "status": lp["status"], "round": lp["round"],
                         "max_rounds": lp["max_rounds"], "until": item.until, "error": lp["error"],
                         "needs": item.needs, "when": item.when, "question": lp["question"],
                         "steps": [{"id": s.id, **step_meta(s)} for s in item.steps],
                         "stages": stages([(s.id, s.needs) for s in item.steps]),
                         "rounds": [{"round": k, "instances": v} for k, v in sorted(rounds.items())]})
        else:
            tree.append({"kind": "step", "id": item.id, "instances": [i for i in insts.values() if i["step"] == item.id and not i["loop"]],
                         **step_meta(item)})
    top = stages([(t["id"], t["needs"]) for t in tree])
    for t in tree:
        t["stage"] = top[t["id"]]

    waiting: list[dict[str, Any]] = []
    if status == "running":
        for i in insts.values():
            if i["status"] == "waiting_input" and i["question"]:
                waiting.append({"step": i["id"], "kind": "question", "text": i["question"]["question"],
                                "options": i["question"]["options"]})
            elif i["status"] == "waiting_approval":
                waiting.append({"step": i["id"], "kind": "approval",
                                "text": i["missing"] or f"{i['approvals_needed']} approval(s) needed", "approvals": i["approvals"]})
        for lid, lp in loops.items():
            if lp["status"] == "waiting_input" and lp["question"]:
                waiting.append({"step": lid, "kind": "loop", "text": lp["question"]["question"],
                                "options": lp["question"]["options"]})
    mandate = {k: started.get(k) for k in ("initiator", "initiator_aliases", "requested_at", "source")} if started else None
    return {"flow": started.get("flow") or flow.flow, "role": started.get("role") or flow.kind, "status": status, "inputs": started.get("inputs") or {},
            "project": started.get("project") or None, "mandate": mandate, "tree": tree, "waiting": waiting,
            "said": said, "finished": finished, "source": "events"}


def progress(view: dict[str, Any]) -> dict[str, Any]:
    insts = [i for t in view["tree"] for i in (t["instances"] if t["kind"] == "step" else [x for r in t["rounds"] for x in r["instances"]])]
    done = sum(1 for i in insts if i["status"] in ("succeeded", "skipped"))
    current = [i["id"] for i in insts if i["status"] in ("running", "waiting_input", "waiting_approval")]
    return {"done": done, "total": len(insts), "current": current}
