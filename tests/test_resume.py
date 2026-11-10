"""Continuing a failed or cancelled run from its failed steps (RunState.reopen, client.resume_run)."""

from __future__ import annotations

import pytest

from myorch.core.events import derive_events
from myorch.core.run import RunState

from test_run import FLOW, finish, started  # pytest puts tests/ on sys.path

LINE = """
flow: line
steps:
  - {id: a, harness: script, profile: p}
  - {id: b, harness: script, profile: p, needs: [a]}
  - {id: c, harness: script, profile: p, needs: [b]}
  - {id: d, harness: script, profile: p}
"""


def failed_at_b():
    s = RunState.create("r1", LINE, {})
    assert sorted(started(s.plan())) == ["a", "d"]
    finish(s, "a"), finish(s, "d")
    assert started(s.plan()) == ["b"]
    s.instances["b"].status, s.instances["b"].attempts, s.instances["b"].error = "failed", 1, "timed out"
    s.plan()
    assert (s.status, s.instances["c"].status) == ("failed", "blocked")
    return s


def test_the_failed_step_and_what_it_held_back_run_again():
    s = failed_at_b()
    assert sorted(s.reopen()) == ["b", "c"]
    assert (s.status, s.resumes, s.instances["b"].error) == ("running", 1, None)
    assert s.instances["b"].attempts == 1  # the next attempt is number 2 (the workflow counts on)
    assert started(s.plan()) == ["b"]  # a and d keep their results
    finish(s, "b")
    assert started(s.plan()) == ["c"]
    finish(s, "c")
    s.plan()
    assert s.status == "succeeded"
    keys = [e.key for e in derive_events(s, finished=True)]
    assert "finished:1" in keys and "finished" not in keys  # the new end is a new event


def test_only_running_runs_or_unknown_steps_are_refused():
    s = RunState.create("r1", LINE, {})
    s.plan()
    with pytest.raises(ValueError, match="只有失败或取消的运行"):
        s.reopen()
    s = failed_at_b()
    with pytest.raises(ValueError, match="没有可以重新执行的步骤：a"):
        s.reopen({"a"})  # a succeeded
    assert s.status == "failed" and s.resumes == 0  # nothing changed


def test_a_cancelled_run_continues_where_it_stopped():
    s = RunState.create("r1", LINE, {})
    s.plan()
    finish(s, "a")
    s.plan()
    s.cancel()  # b and d were running, c had not started: all three are cancelled
    assert {i: s.instances[i].status for i in "abcd"} == {"a": "succeeded", "b": "cancelled", "c": "cancelled", "d": "cancelled"}
    assert sorted(s.reopen()) == ["b", "c", "d"]
    assert sorted(started(s.plan())) == ["b", "d"]  # c waits for b again


def test_a_rejected_gate_stays_a_decision_and_approvals_are_kept():
    s = RunState.create("r1", FLOW, {"task": "t"})
    s.plan()
    finish(s, "implement", approvers=1)
    s.plan()
    for iid in ("review.r1.x1", "review.r1.x2"):
        finish(s, iid)
    s.plan()
    finish(s, "verify.r1", blocking=0)
    s.plan()  # fix skipped, loop done, the gate opens
    s.instances["human"].status = "rejected"
    s.plan()
    assert s.status == "failed"
    with pytest.raises(ValueError):
        s.reopen()  # rejected, and ship is only blocked


def test_a_loop_that_failed_mid_round_continues_in_the_same_round():
    s = RunState.create("r1", FLOW, {"task": "t"})
    s.plan()
    finish(s, "implement", approvers=1)
    s.plan()
    finish(s, "review.r1.x1")
    s.instances["review.r1.x2"].status = "failed"
    s.plan()
    assert (s.loops["review-fix"].status, s.status) == ("failed", "failed")
    reset = s.reopen({"review"})  # by step id: every failed replica of it
    assert "review.r1.x2" in reset and "review.r1.x1" not in reset
    assert (s.loops["review-fix"].status, s.loops["review-fix"].round) == ("running", 1)
    assert started(s.plan()) == ["review.r1.x2"]
    finish(s, "review.r1.x2")
    assert started(s.plan()) == ["verify.r1"]
