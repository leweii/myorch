from pathlib import Path

from myorch.core.events import derive_events
from myorch.core.manifest import Manifest, Question
from myorch.core.run import RunState
from myorch.storage.events import EventLog

FLOW = """
flow: development
steps:
  - {id: analyze, harness: script, profile: p, interaction: allowed}
  - {id: risk-rules, harness: script, profile: p, needs: [analyze]}
  - {id: open-draft-pr, harness: script, profile: p, needs: [risk-rules]}
  - {id: human-review, needs: [open-draft-pr], approval: {count: 1}}
  - {id: push-and-pr, harness: script, profile: p, needs: [human-review]}
"""


def test_events_follow_the_state_and_have_stable_keys():
    s = RunState.create("development-1", FLOW, {}, project={"name": "mdm"})
    s.plan()
    assert [(e.key, e.kind) for e in derive_events(s)] == [("started", "run_started")]
    a = s.instances["analyze"]
    a.status, a.turns, a.attempts, a.question = "waiting_input", 1, 1, Question(question="Where should it go?", options=["A", "B"])
    ask = derive_events(s)[1]
    assert ask.key == "ask:analyze:1" and ask.data == {"question": "Where should it go?", "options": ["A", "B"]}
    a.status, a.question = "succeeded", None
    s.instances["risk-rules"].manifest = Manifest(step_id="risk-rules", summary="medium, needs 1 approval")
    s.instances["open-draft-pr"].manifest = Manifest(
        step_id="open-draft-pr", outputs={"pr_url": "https://github.com/o/r/pull/5", "draft": True})
    hr = s.instances["human-review"]
    hr.status, hr.approvals_needed = "waiting_approval", 1
    by_key = {e.key: e for e in derive_events(s)}
    assert by_key["approve:human-review"].data == {"needed": 1, "risk": "medium, needs 1 approval",
                                                   "actions": ["approve", "reject"], "missing": "1 more approval(s) needed"}
    assert by_key["pr:5"].data == {"url": "https://github.com/o/r/pull/5", "repo": "o/r", "number": 5}
    for i in s.instances.values():
        i.status = "succeeded"
    s.status = "succeeded"
    done = derive_events(s, finished=True)[-1]
    assert done.key == "finished" and done.data["status"] == "succeeded" and done.data["pr_url"].endswith("/pull/5")


def test_event_log_appends_once_and_reads_in_order(tmp_path: Path):
    log = EventLog(tmp_path / "events.db")
    assert log.append("r1", [("started", "run_started", "", {}), ("ask:a:1", "question", "a", {"question": "?"})]) == 2
    assert log.append("r1", [("started", "run_started", "", {})]) == 0  # a replayed publish is ignored
    log.append("r2", [("started", "run_started", "", {})])
    all_ = log.read()
    assert [(e.run_id, e.key) for e in all_] == [("r1", "started"), ("r1", "ask:a:1"), ("r2", "started")]
    assert [e.key for e in log.read(after=all_[0].seq)] == ["ask:a:1", "started"]
    assert [e.key for e in log.read(run_id="r1")] == ["started", "ask:a:1"]
    assert log.last_seq() == all_[-1].seq


LOOP_FLOW = """
flow: development
steps:
  - {id: implement, harness: script, profile: p}
  - loop: review-fix
    needs: [implement]
    max_rounds: 2
    until: "steps.verify.outputs.blocking == 0"
    steps:
      - {id: verify, harness: script, profile: p}
  - {id: human-review, needs: [review-fix], approval: {count: 2}}
"""


def test_detail_events_only_when_asked():
    s = RunState.create("development-2", LOOP_FLOW, {}, project={"name": "blog"})
    s.plan()
    s.instances["implement"].attempts = 1
    assert {e.kind for e in derive_events(s)} == {"run_started"}  # old runs: no detail events
    kinds = {e.kind for e in derive_events(s, detail=True)}
    assert {"run_started", "step_started"} <= kinds


def test_detail_events_follow_steps_loops_and_approvals():
    s = RunState.create("development-3", LOOP_FLOW, {"task": "x"}, project={"name": "blog"})
    s.plan()
    started = derive_events(s, detail=True)[0]
    assert started.data["flow_file"] == ".myorch/flow.yaml" and started.data["project"] == "blog"

    imp = s.instances["implement"]
    imp.attempts, imp.turns, imp.status = 1, 1, "running"
    keys = {e.key: e for e in derive_events(s, detail=True)}
    assert keys["step:implement:1.1"].data["attempt"] == 1
    assert not any(k.startswith("done:implement") for k in keys)

    imp.status = "succeeded"
    imp.manifest = Manifest(step_id="implement", summary="done", outputs={"files": 3}, notes="n")
    keys = {e.key: e for e in derive_events(s, detail=True)}
    fin = keys["done:implement:1.1:succeeded"]
    assert fin.kind == "step_finished" and fin.data["summary"] == "done" and fin.data["outputs"] == {"files": 3}

    s.plan()  # starts the loop
    rounds = [e for e in derive_events(s, detail=True) if e.kind == "loop_round"]
    assert rounds and rounds[0].data["loop"] == "review-fix" and rounds[0].data["max_rounds"] == 2
    assert rounds[0].data["until"] == "steps.verify.outputs.blocking == 0"

    hr = s.instances["human-review"]
    hr.status, hr.approvals_needed, hr.approvals = "waiting_approval", 2, ["jakobhe"]
    prog = [e for e in derive_events(s, detail=True) if e.kind == "approval_progress"]
    assert prog[0].data["approvers"] == ["jakobhe"] and prog[0].data["needed"] == 2
    assert prog[0].data["missing"]  # the rule text comes from core/run.py, not from the channel
    hr.approvals = ["jakobhe", "alice"]
    assert {e.key for e in derive_events(s, detail=True) if e.kind == "approval_progress"} != {prog[0].key}


def test_skipped_steps_are_reported_as_finished():
    s = RunState.create("development-4", LOOP_FLOW, {}, project={"name": "blog"})
    s.instances["implement"].status = "skipped"
    fin = [e for e in derive_events(s, detail=True) if e.kind == "step_finished"]
    assert fin and fin[0].data["status"] == "skipped"


def test_live_output_has_its_own_table(tmp_path: Path):
    log = EventLog(tmp_path / "events.db")
    log.append("r1", [("started", "run_started", "", {})])
    log.add_output("r1", "implement", 1, 1, "→ Bash: ls\n")
    log.add_output("r1", "review.r1.x1", 1, 1, "x\n")
    log.add_output("r2", "implement", 1, 1, "other run\n")
    assert [e.kind for e in log.read()] == ["run_started"]  # readers of events do not see output
    mine = log.read_output("r1", "implement")
    assert [c.text for c in mine] == ["→ Bash: ls\n"] and mine[0].attempt == 1
    assert len(log.read_output("r1")) == 2
    assert log.read_output("r1", after=log.read_output("r1")[-1].seq) == []
    assert log.prune_output(older_than=-1) == 3 and log.read_output("r1") == []


def test_step_started_has_a_model_only_for_agent_steps():
    flow = """
flow: development
model: claude-sonnet-5-5
steps:
  - {id: sync, harness: script, profile: p}
  - {id: analyze, harness: claude-code, profile: p, needs: [sync]}
  - {id: design, harness: claude-code, profile: p, model: claude-opus-5-5, needs: [analyze]}
"""
    s = RunState.create("development-1", flow, {}, project={"name": "mdm"})
    s.plan()
    for i in s.instances.values():
        i.attempts = 1
    models = {e.step: e.data["model"] for e in derive_events(s, detail=True) if e.kind == "step_started"}
    assert models == {"sync": None, "analyze": "claude-sonnet-5-5", "design": "claude-opus-5-5"}
