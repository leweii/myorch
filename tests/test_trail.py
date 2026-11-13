"""What learn gets from the event log (myorch/trail.py): answers, change requests, rejections,
failures, resumes, and which run continued which."""

from __future__ import annotations

import json

from myorch.cli import _learn_files
from myorch.storage.events import EventLog
from myorch.trail import trail


def test_the_trail_keeps_what_people_said_and_what_failed(tmp_path):
    log = EventLog(tmp_path / "events.db")
    log.append("development-2", [
        ("started", "run_started", "", {"flow": "development", "inputs": {"task": "t", "from_run": "development-1"}}),
        ("q1", "question", "design", {"question": "Export fields in snake_case or camelCase?", "options": ["snake_case", "camelCase"]}),
        ("r1", "replied", "design", {"text": "snake_case, same as the downstream reports", "question": "Export fields in snake_case or camelCase?"}),
        ("c1", "changes_requested", "human-review", {"by": "jakobhe", "text": "Mask phone numbers before export"}),
        ("f1", "step_finished", "implement", {"status": "failed", "attempts": 2, "error": "checks failed: checkstyle"}),
        ("ok", "step_finished", "design", {"status": "succeeded"}),
        ("rs", "run_resumed", "", {"steps": ["implement"], "by": "jakobhe"}),
        ("end", "run_finished", "", {"status": "succeeded"}),
    ])
    [t] = trail(log, ["development-2"])
    assert (t["flow"], t["continued_from"], t["status"]) == ("development", "development-1", "succeeded")
    assert t["qa"] == [{"step": "design", "question": "Export fields in snake_case or camelCase?", "options": ["snake_case", "camelCase"],
                        "answer": "snake_case, same as the downstream reports"}]
    assert t["changes"][0]["text"] == "Mask phone numbers before export" and t["failures"][0]["step"] == "implement"
    assert t["resumes"] == [{"steps": ["implement"], "by": "jakobhe"}]
    files = _learn_files(log, {"runs": "development-2, development-9"})
    assert [x["run"] for x in json.loads(files["docs/learn/trail.json"])] == ["development-2", "development-9"]
    assert _learn_files(log, {"runs": ""}) == {}


def test_a_continued_run_gets_what_people_said_to_the_previous_one(tmp_path):
    """from_run: questions and answers, change requests and the console's approval comments of the
    previous run arrive as docs/previous/conversation.md (myorch/trail.py previous_conversation)."""
    from myorch.cli import _continue_files
    from myorch.trail import CONVERSATION_FILE, previous_conversation

    log = EventLog(tmp_path / "events.db")
    log.append("ck-development-11111111", [
        ("started", "run_started", "", {"flow": "ck-development", "inputs": {"ticket": "18000"}}),
        ("q1", "question", "develop", {"question": "Over the limit: return 400 or report the error in the response?", "options": ["400", "error in the response"]}),
        ("r1", "replied", "develop", {"text": "Error in the response; the frontend already shows errorMessage", "question": "Over the limit: return 400 or report the error in the response?"}),
        ("c1", "changes_requested", "human-review", {"by": "jakobhe", "text": "Also validate when a variant turns into a draft"}),
        ("end", "run_finished", "", {"status": "rejected"}),
    ])
    notes = tmp_path / "ui" / "runs"
    notes.mkdir(parents=True)
    (notes / "ck-development-11111111.jsonl").write_text(
        json.dumps({"kind": "reject", "step": "human-review", "by": "jakobhe", "text": "Use the ticket's original wording"}, ensure_ascii=False) + "\n"
        + json.dumps({"kind": "comment", "text": "ignored"}) + "\n", encoding="utf-8")
    md = previous_conversation(log, "ck-development-11111111", tmp_path)
    assert "Over the limit: return 400 or report the error in the response?" in md and "A: Error in the response; the frontend already shows errorMessage" in md
    assert "Requested changes (jakobhe, step human-review): Also validate when a variant turns into a draft" in md
    assert "Rejected (jakobhe, step human-review): Use the ticket's original wording" in md and "ignored" not in md
    assert "Result of the previous run: rejected" in md

    declared = {"from_run": {"type": "run", "role": "development", "carry": ["ticket"]}}
    files = _continue_files(log, {"from_run": "ck-development-11111111"}, declared, tmp_path)
    assert list(files) == [CONVERSATION_FILE] and files[CONVERSATION_FILE] == md
    assert _continue_files(log, {"from_run": ""}, declared, tmp_path) == {}
