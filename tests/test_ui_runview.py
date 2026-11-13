"""The console reads runs through the channel relay: a run folded from events (ui/runview.py), and
commands and content going through the relay (ui/service.py)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from myorch.core.flow import load_flow
from myorch.storage.events import LoggedEvent
from myorch.ui import runview
from myorch.ui.service import Service

FLOW = """\
flow: demo
inputs: {task: string}
steps:
  - id: analyze
    harness: claude-code
    profile: read-only
    interaction: allowed
    prompt: analyze
  - loop: review
    needs: [analyze]
    until: "steps.verify.outputs.ok"
    max_rounds: 2
    steps:
      - {id: check, harness: script, profile: read-only, prompt: "true", fanout: 2}
      - {id: verify, harness: script, profile: read-only, prompt: "true", needs: [check]}
  - id: gate
    needs: [review]
    approval: {count: 1}
"""


def events(*items):
    return [LoggedEvent(seq=n, run_id="r1", key=f"k{n}", kind=k, step=s, at=0.0, data=d)
            for n, (k, s, d) in enumerate(items, 1)]


STARTED = ("run_started", "", {"flow": "demo", "project": "blog", "inputs": {"task": "x"}, "flow_file": ".myorch/flow.yaml",
                               "initiator": "jakobhe", "initiator_aliases": [], "requested_at": "2026-11-02T10:00:00", "source": "web"})


def test_fold_draws_the_run_from_events():
    f = load_flow(FLOW)
    evs = events(
        STARTED,
        ("step_started", "analyze", {"step": "analyze", "loop": "", "round": 0, "replica": 0, "attempt": 1, "turn": 1, "model": "opus"}),
        ("question", "analyze", {"question": "Full or partial?", "options": ["Full", "Partial"]}),
        ("replied", "analyze", {"text": "Full", "question": "Full or partial?", "at": "t"}),
        ("step_started", "analyze", {"step": "analyze", "loop": "", "round": 0, "replica": 0, "attempt": 1, "turn": 2, "model": "opus"}),
        ("step_finished", "analyze", {"step": "analyze", "loop": "", "round": 0, "replica": 0, "status": "succeeded",
                                      "summary": "done", "outputs": {"proceed": True}, "attempts": 1, "turns": 2}),
        ("loop_round", "review", {"loop": "review", "round": 1, "max_rounds": 2, "status": "running", "until": "u"}),
        ("step_started", "check.r1.x1", {"step": "check", "loop": "review", "round": 1, "replica": 1, "attempt": 1, "turn": 1}),
        ("step_finished", "check.r1.x2", {"step": "check", "loop": "review", "round": 1, "replica": 2, "status": "succeeded"}),
        ("approval", "gate", {"needed": 1, "missing": "needs 1 more approval"}),
    )
    v = runview.fold(f, evs, "RUNNING")
    assert (v["status"], v["project"], v["mandate"]["initiator"]) == ("running", "blog", "jakobhe")
    analyze, review, gate = v["tree"]
    assert [t["stage"] for t in v["tree"]] == [0, 1, 2]
    (a,) = analyze["instances"]
    assert (a["status"], a["turns"], a["summary"], a["question"]) == ("succeeded", 2, "done", None)
    assert (review["round"], review["status"]) == (1, "running")
    (rnd,) = review["rounds"]
    assert sorted((i["id"], i["status"]) for i in rnd["instances"]) == [("check.r1.x1", "running"), ("check.r1.x2", "succeeded")]
    assert gate["instances"][0]["status"] == "waiting_approval"
    assert v["waiting"] == [{"step": "gate", "kind": "approval", "text": "needs 1 more approval", "approvals": []}]
    assert v["said"] == [{"kind": "reply", "step": "analyze", "text": "Full", "question": "Full or partial?",
                          "options": [], "at": "t", "by": None}]
    assert runview.progress(v) == {"done": 2, "total": 4, "current": ["check.r1.x1", "gate"]}


def test_a_run_terminated_from_outside_is_not_left_running():
    f = load_flow(FLOW)
    evs = events(STARTED, ("step_started", "analyze", {"step": "analyze", "attempt": 1, "turn": 1}))
    v = runview.fold(f, evs, "TERMINATED")
    assert v["status"] == "failed"
    assert v["tree"][0]["instances"][0]["status"] == "cancelled"
    assert v["waiting"] == []


def test_lifecycle_only_runs_have_no_detail():
    assert not runview.has_detail(events(("run_started", "", {"flow": "demo", "project": ""})))
    assert runview.has_detail(events(STARTED))


class FakeRelay:
    """Records what the console asks the relay; serves one run."""

    def __init__(self, evs):
        self.evs, self.calls = evs, []
        self.client = self.store = None

    def events(self, after, run_id, limit):
        return [e for e in self.evs if e.seq > after][:limit]

    async def runs(self, limit):
        return [{"run_id": "r1", "status": "RUNNING", **STARTED[2]}]

    async def read_file(self, run_id, rel):
        return {".myorch/flow.yaml": FLOW, "docs/external/request.md": "# Original request (from web)\n\nNote\n\nAdd a feature to the blog"}.get(rel)

    async def reply(self, *a, **kw):
        self.calls.append(("reply", a, kw))

    async def approve(self, *a, **kw):
        self.calls.append(("approve", a, kw))


def test_the_console_reads_and_writes_through_the_relay(tmp_path: Path):
    relay = FakeRelay(events(
        STARTED,
        ("step_started", "analyze", {"step": "analyze", "attempt": 1, "turn": 1}),
        ("question", "analyze", {"question": "Q?", "options": ["a"]}),
    ))
    svc = Service(relay, tmp_path, tmp_path, "jakobhe", log_dir=tmp_path / "log")

    async def go():
        listing = await svc.runs()
        assert [w["step"] for w in listing["inbox"]] == ["analyze"]
        d = await svc.run_detail("r1")
        conv = await svc.conversation("r1", d)
        assert conv["items"][0] == {"kind": "request", "by": "jakobhe", "at": "2026-11-02T10:00:00", "text": "Add a feature to the blog"}
        assert conv["target"]["mode"] == "reply" and conv["target"]["step"] == "analyze"
        await svc.reply("r1", "analyze", "a")
        await svc.approve("r1", "gate", "Looked at it")
        return d

    d = asyncio.run(go())
    assert d["source"] == "events"
    assert relay.calls == [("reply", ("r1", "analyze", "a"), {"via": "web", "account": "jakobhe"}),
                           ("approve", ("r1", "gate"), {"via": "web", "account": "jakobhe"})]


def test_a_label_search_lists_the_hits_and_keeps_the_inbox(tmp_path: Path):
    class R(FakeRelay):
        async def runs(self, limit, label=None):
            if label:
                self.searched = (limit, label)
                return [{"run_id": "eval-x", "status": "COMPLETED", "flow": "eval-x", "project": "mdm",
                         "initiator": "jakobhe", "labels": ["MD-18700"]}]
            return [{"run_id": "r1", "status": "RUNNING", **STARTED[2], "labels": []}]

        def run_labels(self, run_id):
            return ["MD-1"] if run_id == "old" else []

    relay = R(events(STARTED, ("question", "analyze", {"question": "Q?", "options": []})))
    svc = Service(relay, tmp_path, tmp_path, "jakobhe")

    async def go():
        found = await svc.runs(label="md-18700")
        assert [r["run_id"] for r in found["runs"]] == ["eval-x"] and found["runs"][0]["labels"] == ["MD-18700"]
        assert relay.searched[1] == "md-18700"
        assert [w["run_id"] for w in found["inbox"]] == ["r1"]  # a search does not hide what waits
        assert await svc.run_labels("r1") == [] and await svc.run_labels("old") == ["MD-1"]

    asyncio.run(go())


def test_the_step_page_shows_what_the_agent_read_and_queried(tmp_path: Path):
    import json as _json

    transcript = "\n".join(_json.dumps(r) for r in [
        {"timestamp": "t1", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Read", "input": {"file_path": "/sandbox/workspace/knowledge/notes/README.md"}},
            {"type": "tool_use", "id": "b", "name": "Read", "input": {"file_path": "/sandbox/workspace/repo/x.java"}},
            {"type": "tool_use", "id": "c", "name": "mcp__wiki__search", "input": {"query": "refund"}}]}},
        {"message": {"content": [{"type": "tool_result", "tool_use_id": "c", "content": [{"type": "text", "text": "3 pages"}]}]}},
    ])

    class R(FakeRelay):
        async def list_files(self, run_id, rel, depth):
            return ["projects/-sandbox-workspace/s1.jsonl"]

        async def read_file(self, run_id, rel):
            return transcript if rel.endswith(".jsonl") else await super().read_file(run_id, rel)

    svc = Service(R(events(STARTED)), tmp_path, tmp_path, "jakobhe")
    d = asyncio.run(svc.step_knowledge_use("r1", "implement"))
    assert [r["target"] for r in d["reads"]] == ["/sandbox/workspace/knowledge/notes/README.md"]
    assert [(q["tool"], q["input"], q["result"]) for q in d["queries"]] == [("mcp__wiki__search", {"query": "refund"}, "3 pages")]


def test_a_resumed_run_runs_again_from_its_failed_step():
    flow = load_flow(FLOW)
    failed = [STARTED, ("step_started", "analyze", {"attempt": 1}),
              ("step_finished", "analyze", {"status": "failed", "attempts": 1, "error": "timed out", "summary": "old", "exit_code": 3}),
              ("run_finished", "", {"status": "failed"})]
    v = runview.fold(flow, events(*failed, ("run_resumed", "", {"steps": ["analyze"], "resume": 1}),
                                  ("step_started", "analyze", {"attempt": 2})))
    assert (v["status"], v["finished"], v["tree"][0]["instances"][0]["status"]) == ("running", None, "running")
    assert (v["tree"][0]["instances"][0]["summary"], v["tree"][0]["instances"][0]["exit_code"]) == ("", None)
    assert v["tree"][0]["instances"][0]["error"] is None and v["said"][0]["kind"] == "resume"
    # older logs wrote the resume after the step had started again: the step still shows as running
    v = runview.fold(flow, events(*failed, ("step_started", "analyze", {"attempt": 2}),
                                  ("run_resumed", "", {"steps": ["analyze"], "resume": 1})))
    assert (v["status"], v["tree"][0]["instances"][0]["status"]) == ("running", "running")


def test_fold_drops_the_model_logged_on_script_steps():
    # Runs from before the fix logged the flow's model on script steps too.
    f = load_flow(FLOW)
    evs = events(
        STARTED,
        ("step_started", "analyze", {"step": "analyze", "loop": "", "round": 0, "replica": 0, "attempt": 1, "turn": 1, "model": "opus"}),
        ("step_started", "check.r1.x1", {"step": "check", "loop": "review", "round": 1, "replica": 1, "attempt": 1, "turn": 1,
                                         "model": "sonnet"}),
    )
    v = runview.fold(f, evs, "RUNNING")
    analyze, review, _ = v["tree"]
    assert analyze["instances"][0]["model"] == "opus"
    assert review["rounds"][0]["instances"][0]["model"] is None
