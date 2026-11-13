import pytest

from myorch.core.expr import evaluate
from myorch.core.flow import load_flow
from myorch.core.manifest import Manifest
from myorch.core.run import AskLoop, RunState, StartInstance, match_option

FLOW = """
flow: dev
inputs: {task: string}
steps:
  - {id: implement, harness: script, profile: p}
  - loop: review-fix
    needs: [implement]
    max_rounds: 2
    until: "steps.verify.outputs.blocking == 0"
    steps:
      - {id: review, harness: script, profile: p, fanout: 2}
      - {id: verify, harness: script, profile: p, needs: [review]}
      - {id: fix, harness: script, profile: p, needs: [verify], when: "steps.verify.outputs.blocking > 0"}
  - id: human
    needs: [review-fix]
    approval: {when: "steps.verify.outputs.blocking == 0", count: "steps.implement.outputs.approvers"}
  - {id: ship, harness: script, profile: p, needs: [human]}
"""


def started(actions):
    return [a.instance_id for a in actions if isinstance(a, StartInstance)]


def finish(state, iid, **outputs):
    inst = state.instances[iid]
    inst.status = "succeeded"
    inst.manifest = Manifest(step_id=iid, outputs=outputs)


def test_loop_rounds_fanout_when_and_dynamic_approval():
    s = RunState.create("r1", FLOW, {"task": "t"})
    assert started(s.plan()) == ["implement"]
    finish(s, "implement", approvers=2)

    # round 1: fanout replicas run in parallel, verify waits for both
    assert started(s.plan()) == ["review.r1.x1", "review.r1.x2"]
    finish(s, "review.r1.x1")
    assert started(s.plan()) == []
    finish(s, "review.r1.x2")
    assert started(s.plan()) == ["verify.r1"]
    finish(s, "verify.r1", blocking=3)
    assert started(s.plan()) == ["fix.r1"]  # when: blocking > 0
    finish(s, "fix.r1")

    # until not met -> round 2; reviewers see round 1 as previous context
    assert started(s.plan()) == ["review.r2.x1", "review.r2.x2"]
    assert {"verify.r1", "fix.r1"} <= set(s.upstream("review.r2.x1"))
    finish(s, "review.r2.x1"), finish(s, "review.r2.x2")
    assert started(s.plan()) == ["verify.r2"]
    finish(s, "verify.r2", blocking=0)
    assert started(s.plan()) == ["human"]  # fix.r2 skipped, loop satisfied
    assert s.instances["fix.r2"].status == "skipped"
    assert s.loops["review-fix"].status == "succeeded"

    # after the loop, steps.verify refers to the last round
    assert s.approvals_required("human") == 2
    finish(s, "human")
    assert started(s.plan()) == ["ship"]
    finish(s, "ship")
    s.plan()
    assert s.status == "succeeded"


def test_exhausted_loop_asks_and_more_rounds_extend():
    s = RunState.create("r1", FLOW, {"task": "t"})
    s.plan(); finish(s, "implement")
    for rnd in (1, 2):
        s.plan()
        finish(s, f"review.r{rnd}.x1"), finish(s, f"review.r{rnd}.x2")
        s.plan(); finish(s, f"verify.r{rnd}", blocking=1)
        s.plan(); finish(s, f"fix.r{rnd}")
    actions = s.plan()
    assert [a for a in actions if isinstance(a, AskLoop)] == [AskLoop(loop_id="review-fix")]
    assert s.loops["review-fix"].status == "waiting_input"
    assert not s.resolve_loop("review-fix", "say something random")  # not an option: still waiting
    assert s.resolve_loop("review-fix", "1")  # one more round
    assert started(s.plan()) == ["review.r3.x1", "review.r3.x2"]


def test_failure_blocks_downstream_and_loop():
    s = RunState.create("r1", FLOW, {"task": "t"})
    s.plan()
    s.instances["implement"].status = "failed"
    s.plan()
    assert s.loops["review-fix"].status == "failed"
    assert s.instances["human"].status == "blocked" and s.instances["ship"].status == "blocked"
    assert s.status == "failed"


def test_accept_and_reject_options():
    assert match_option("2", ["a", "b"]) == 1
    assert match_option("2. b", ["a", "b"]) == 1
    assert match_option("Stop the flow", ["Run one more round", "Stop the flow"]) == 1
    assert match_option("nope", ["a", "b"]) is None


def test_flow_validation_rules():
    with pytest.raises(ValueError, match="loop id"):
        load_flow(FLOW.replace("needs: [review-fix]", "needs: [verify]"))
    with pytest.raises(ValueError, match="without harness"):
        load_flow("flow: f\nsteps: [{id: a}]")
    with pytest.raises(ValueError, match="unexpected character"):
        load_flow("flow: f\nsteps: [{id: a, harness: s, profile: p, when: '1 +'}]")


def test_expressions():
    ctx = {"steps": {"a-b": {"outputs": {"n": 2}}}, "loop": {"round": 3}}
    assert evaluate("steps.a-b.outputs.n > 1 and loop.round >= 3", ctx)
    assert evaluate("steps.missing.outputs.n > 0", ctx) is False
    assert evaluate("max(steps.a-b.outputs.n, 5)", ctx) == 5


def test_loop_when_skips_whole_loop():
    flow = FLOW.replace("    needs: [implement]\n    max_rounds: 2", "    needs: [implement]\n    when: \"steps.implement.outputs.changed\"\n    max_rounds: 2")
    s = RunState.create("r1", flow, {"task": "t"})
    s.plan()
    finish(s, "implement", changed=False, approvers=0)
    actions = s.plan()
    assert s.loops["review-fix"].status == "skipped"
    assert not [i for i in s.instances.values() if i.loop_id]  # no round was created
    assert started(actions) == ["human"]  # downstream treats a skipped loop as done


def test_replica_is_always_in_context():
    s = RunState.create("r1", FLOW.replace("fanout: 2", "fanout: 1"), {"task": "t"})
    s.plan(); finish(s, "implement")
    assert started(s.plan()) == ["review.r1"]
    assert s.context(s.instances["review.r1"])["replica"] == 1
    assert s.context(s.instances["implement"])["replica"] == 1


def test_cancel_marks_unfinished_work():
    s = RunState.create("r1", FLOW, {"task": "t"})
    s.plan(); finish(s, "implement"); s.plan()  # review round 1 running
    s.cancel()
    assert s.status == "cancelled"
    assert s.instances["implement"].status == "succeeded"
    assert s.instances["review.r1.x1"].status == "cancelled" and s.instances["ship"].status == "cancelled"
    assert s.loops["review-fix"].status == "cancelled"
    s.plan()
    assert s.status == "cancelled"  # planning does not revive a cancelled run


def test_approval_needs_initiator_and_records_subject():
    from myorch.core.run import ApprovalRecord, Mandate
    flow = FLOW.replace(
        'approval: {when: "steps.verify.outputs.blocking == 0", count: "steps.implement.outputs.approvers"}',
        'approval: {count: 1, subject: "{{steps.implement.outputs.sha}}"}')
    s = RunState.create("r1", flow, {"task": "t"})
    s.mandate = Mandate(initiator="alice", requested_at="t0", request={"task": "t"}, flow={"name": "dev", "sha256": "x"})
    s.instances["human"].status = "running"
    finish(s, "implement", sha="abc123")
    assert s.approval_subject("human") == "abc123"
    assert "initiator alice" in s.approval_missing("human", {"bob"})
    assert s.approval_missing("human", {"alice"}) is None
    s.approvals["human"] = ApprovalRecord(subject="abc123", approvers=["alice"])
    assert s.context(s.instances["ship"])["approvals"]["human"]["subject"] == "abc123"


REFLECT_FLOW = FLOW.replace(
    "    until: \"steps.verify.outputs.blocking == 0\"\n",
    "    until: \"steps.verify.outputs.blocking == 0\"\n"
    "    reflect: {id: reflect, harness: script, profile: p}\n")


def test_exhausted_loop_reflects_before_asking():
    s = RunState.create("r1", REFLECT_FLOW, {"task": "t"})
    s.plan(); finish(s, "implement")
    for rnd in (1, 2):
        s.plan()
        finish(s, f"review.r{rnd}.x1"), finish(s, f"review.r{rnd}.x2")
        s.plan(); finish(s, f"verify.r{rnd}", blocking=1)
        s.plan(); finish(s, f"fix.r{rnd}")
    actions = s.plan()
    assert started(actions) == ["reflect.r2"] and not [a for a in actions if isinstance(a, AskLoop)]
    assert s.loops["review-fix"].status == "running"
    finish(s, "reflect.r2", recommendation="Stop the flow", reason="All three rounds fixed the same kind of problem; the design itself is wrong")
    actions = s.plan()
    assert [a for a in actions if isinstance(a, AskLoop)] == [AskLoop(loop_id="review-fix")]
    q = s.loops["review-fix"].question.question
    assert "Reflect proposal: Stop the flow" in q and "the design itself is wrong" in q


def test_failed_reflection_still_asks():
    s = RunState.create("r1", REFLECT_FLOW.replace("max_rounds: 2", "max_rounds: 1"), {"task": "t"})
    s.plan(); finish(s, "implement"); s.plan()
    finish(s, "review.r1.x1"), finish(s, "review.r1.x2")
    s.plan(); finish(s, "verify.r1", blocking=1)
    s.plan(); finish(s, "fix.r1")
    s.plan()
    s.instances["reflect.r1"].status = "failed"
    s.plan()
    assert s.loops["review-fix"].status == "waiting_input"
    assert "Reflect" not in s.loops["review-fix"].question.question


def test_failed_reflection_does_not_fail_an_accepted_run():
    s = RunState.create("r1", REFLECT_FLOW.replace("max_rounds: 2", "max_rounds: 1"), {"task": "t"})
    s.plan(); finish(s, "implement"); s.plan()
    finish(s, "review.r1.x1"), finish(s, "review.r1.x2")
    s.plan(); finish(s, "verify.r1", blocking=1)
    s.plan(); finish(s, "fix.r1")
    s.plan(); s.instances["reflect.r1"].status = "failed"; s.plan()
    assert s.resolve_loop("review-fix", "2")  # accept and continue
    for iid in ("human", "ship"):
        assert started(s.plan()) == [iid]
        finish(s, iid)
    s.plan()
    assert s.status == "succeeded"


def test_initiator_aliases_count_as_the_initiator():
    from myorch.core.run import Mandate
    flow = FLOW.replace(
        'approval: {when: "steps.verify.outputs.blocking == 0", count: "steps.implement.outputs.approvers"}',
        'approval: {count: 1}')
    s = RunState.create("r1", flow, {"task": "t"})
    s.mandate = Mandate(initiator="leweii", initiator_aliases=["jakobhe"], requested_at="t0", request={}, flow={"name": "d", "sha256": "x"})
    assert s.approval_missing("human", {"jakobhe"}) is None  # approving from the CLI as the same person
    assert "initiator leweii" in s.approval_missing("human", {"bob"})


def test_failure_escalation_question_and_replies():
    from myorch.core.run import failure_question, failure_reason, parse_failure_reply
    from myorch.core.types import StepResult

    flow = load_flow("flow: f\non_failed: ask\nsteps:\n"
                     "  - {id: a, harness: claude-code, profile: p}\n"
                     "  - {id: b, harness: script, profile: p, on_failed: fail}\n"
                     "  - {id: c, action: project-apply}\n")
    assert [flow.failure_policy(flow.step(s)) for s in "abc"] == ["ask", "fail", "fail"]  # actions never ask
    assert load_flow("flow: f\nsteps:\n  - {id: a, harness: script, profile: p}\n").failure_policy(
        load_flow("flow: f\nsteps:\n  - {id: a, harness: script, profile: p}\n").step("a")) == "fail"

    s = RunState.create("r1", FLOW, {"task": "t"})
    inst = s.instances["implement"]
    inst.attempts = 2
    inst.result = StepResult(step_id="implement", ok=False, exit_code=None, sandbox="x", error="timed out after 30m")
    assert failure_reason(inst) == "timed out after 30m"
    inst.result = StepResult(step_id="implement", ok=True, exit_code=0, sandbox="x")
    inst.manifest = Manifest(step_id="implement", status="failed", summary="the tests do not pass")
    assert failure_reason(inst) == "the tests do not pass"

    q = failure_question("r1", inst, agent=True)
    assert "attempt 2" in q.question and "the tests do not pass" in q.question and "myorch logs r1 implement" in q.question
    assert "hint" in q.question and len(q.options) == 3
    assert "hint" not in failure_question("r1", inst, agent=False).question

    assert parse_failure_reply("1") == ("retry", "")
    assert parse_failure_reply("2. Skip this step") == ("skip", "")
    assert parse_failure_reply("Stop the flow") == ("abort", "")
    assert parse_failure_reply("don't change build.gradle yet, look at the test data") == ("hint", "don't change build.gradle yet, look at the test data")
    assert parse_failure_reply("Skip the tests and commit")[0] == "hint"  # only an exact option text picks it
