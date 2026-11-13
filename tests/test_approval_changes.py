"""Change requests at an approval gate: the run goes back into a loop, the steps after it run again."""

import pytest

from myorch.core.events import derive_events
from myorch.core.flow import load_flow, parse_duration
from myorch.core.manifest import Manifest
from myorch.core.run import RunState, StartInstance

FLOW = """
flow: dev
inputs: {task: string}
steps:
  - {id: implement, harness: script, profile: p}
  - loop: review-fix
    needs: [implement]
    max_rounds: 3
    until: "steps.verify.outputs.blocking == 0"
    steps:
      - {id: verify, harness: script, profile: p}
      - {id: fix, harness: script, profile: p, needs: [verify], when: "steps.verify.outputs.blocking > 0"}
  - loop: e2e
    needs: [review-fix]
    max_rounds: 2
    until: "steps.smoke.outputs.passed"
    steps:
      - {id: smoke, harness: script, profile: p}
  - {id: risk, harness: script, profile: p, needs: [e2e]}
  - id: human
    needs: [risk]
    approval:
      count: 1
      subject: "{{steps.risk.outputs.head}}"
      require_initiator: false
      changes: {loop: review-fix, rounds: 2}
      remind_after: 24h
      timeout: 7d
  - {id: ship, harness: script, profile: p, needs: [human]}
"""


def started(actions):
    return [a.instance_id for a in actions if isinstance(a, StartInstance)]


def finish(state, iid, **outputs):
    inst = state.instances[iid]
    inst.status = "succeeded"
    inst.manifest = Manifest(step_id=iid, outputs=outputs)


def at_gate() -> RunState:
    s = RunState.create("r1", FLOW, {"task": "t"})
    assert started(s.plan()) == ["implement"]
    finish(s, "implement")
    assert started(s.plan()) == ["verify.r1"]
    finish(s, "verify.r1", blocking=0)
    assert started(s.plan()) == ["smoke.r1"]
    finish(s, "smoke.r1", passed=True)
    assert started(s.plan()) == ["risk"]
    finish(s, "risk", head="aaa")
    assert started(s.plan()) == ["human"]
    s.instances["human"].status, s.instances["human"].approvals_needed = "waiting_approval", 1  # what the engine does
    return s


def test_changes_reopen_the_loop_and_reset_everything_after_it():
    s = at_gate()
    assert s.change_round("human") == 2
    assert s.request_changes("human", "ann", "reject an amount of 0 too", "2026-11-02T10:00:00") == 2

    lr = s.loops["review-fix"]
    assert lr.status == "running" and lr.round == 2 and lr.max_rounds == 3  # round 1 + 2 more
    e2e = s.loops["e2e"]
    assert e2e.status == "pending" and e2e.round == 1 and e2e.max_rounds == 3  # keeps numbering: next is r2
    assert s.instances["risk"].status == "pending" and s.instances["risk"].manifest is None
    assert s.instances["human"].status == "pending" and s.instances["human"].approvals_needed == 0
    assert s.approvals["human"].changes[0].by == "ann" and s.approvals["human"].changes[0].round == 2
    assert s.instances["verify.r1"].status == "succeeded"  # history stays

    assert started(s.plan()) == ["verify.r2"]
    finish(s, "verify.r2", blocking=1)
    assert started(s.plan()) == ["fix.r2"]
    finish(s, "fix.r2")
    assert started(s.plan()) == ["verify.r3"]
    finish(s, "verify.r3", blocking=0)
    assert started(s.plan()) == ["smoke.r2"]
    finish(s, "smoke.r2", passed=True)
    assert started(s.plan()) == ["risk"]
    finish(s, "risk", head="bbb")
    assert started(s.plan()) == ["human"]
    assert s.approval_subject("human") == "bbb"  # the new commit is what gets approved


def test_changes_only_while_waiting_and_only_where_configured():
    s = at_gate()
    s.instances["human"].status = "running"
    with pytest.raises(ValueError, match="is not waiting for approval"):
        s.request_changes("human", "ann", "x", "t")
    with pytest.raises(ValueError, match="approval.changes"):
        s.request_changes("risk", "ann", "x", "t")


def test_events_carry_actions_reminder_and_a_new_key_per_visit():
    s = at_gate()
    ev = {e.key: e for e in derive_events(s)}
    assert ev["approve:human"].data["actions"] == ["approve", "reject", "changes"]
    assert "remind:human" not in ev
    s.instances["human"].reminded = True
    assert derive_events(s)[-1].kind == "approval_reminder"

    s.request_changes("human", "ann", "x", "t")
    for iid, out in (("verify.r2", {"blocking": 0}), ("smoke.r2", {"passed": True}), ("risk", {"head": "b"})):
        s.plan()
        finish(s, iid, **out)
    assert started(s.plan()) == ["human"]
    s.instances["human"].status = "waiting_approval"
    keys = [e.key for e in derive_events(s)]
    assert "approve:human:1" in keys and "remind:human:1" not in keys  # reminder flag was reset


def test_flow_checks_the_changes_loop_and_durations():
    assert parse_duration("7d") == 7 * 86400
    with pytest.raises(ValueError, match="not a top-level loop"):
        load_flow(FLOW.replace("loop: review-fix, rounds", "loop: risk, rounds"))
    with pytest.raises(ValueError, match="must run before"):
        load_flow(FLOW.replace("{id: ship, harness: script, profile: p, needs: [human]}",
                               "{id: ship, harness: script, profile: p, needs: [human]}\n"
                               "  - id: late\n    needs: [ship]\n    approval: {changes: {loop: e2e}}\n"
                               "  - loop: after\n    needs: [late]\n    until: 'true'\n"
                               "    steps: [{id: a, harness: script, profile: p}]").replace(
                                   "changes: {loop: e2e}", "changes: {loop: after}"))
    with pytest.raises(ValueError, match="invalid duration"):
        load_flow(FLOW.replace("timeout: 7d", "timeout: 1w"))


def test_failed_gate_reason_reaches_the_failure_summary():
    s = at_gate()
    s.instances["human"].status, s.instances["human"].error = "failed", "approval timed out: nobody handled it within 7d"
    s.plan()
    assert s.status == "failed" and "approval timed out" in s.failure_summary()
