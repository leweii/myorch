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
        ("q1", "question", "design", {"question": "导出字段用 snake_case 还是 camelCase？", "options": ["snake_case", "camelCase"]}),
        ("r1", "replied", "design", {"text": "snake_case，和下游报表一致", "question": "导出字段用 snake_case 还是 camelCase？"}),
        ("c1", "changes_requested", "human-review", {"by": "jakobhe", "text": "导出前要脱敏手机号"}),
        ("f1", "step_finished", "implement", {"status": "failed", "attempts": 2, "error": "checks failed: checkstyle"}),
        ("ok", "step_finished", "design", {"status": "succeeded"}),
        ("rs", "run_resumed", "", {"steps": ["implement"], "by": "jakobhe"}),
        ("end", "run_finished", "", {"status": "succeeded"}),
    ])
    [t] = trail(log, ["development-2"])
    assert (t["flow"], t["continued_from"], t["status"]) == ("development", "development-1", "succeeded")
    assert t["qa"] == [{"step": "design", "question": "导出字段用 snake_case 还是 camelCase？", "options": ["snake_case", "camelCase"],
                        "answer": "snake_case，和下游报表一致"}]
    assert t["changes"][0]["text"] == "导出前要脱敏手机号" and t["failures"][0]["step"] == "implement"
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
        ("q1", "question", "develop", {"question": "超限时返回 400 还是在响应里报错？", "options": ["400", "响应里报错"]}),
        ("r1", "replied", "develop", {"text": "响应里报错，前端已经会展示 errorMessage", "question": "超限时返回 400 还是在响应里报错？"}),
        ("c1", "changes_requested", "human-review", {"by": "jakobhe", "text": "variant 转 draft 时也要校验"}),
        ("end", "run_finished", "", {"status": "rejected"}),
    ])
    notes = tmp_path / "ui" / "runs"
    notes.mkdir(parents=True)
    (notes / "ck-development-11111111.jsonl").write_text(
        json.dumps({"kind": "reject", "step": "human-review", "by": "jakobhe", "text": "文案要用工单原文"}, ensure_ascii=False) + "\n"
        + json.dumps({"kind": "comment", "text": "ignored"}) + "\n", encoding="utf-8")
    md = previous_conversation(log, "ck-development-11111111", tmp_path)
    assert "超限时返回 400 还是在响应里报错？" in md and "答：响应里报错，前端已经会展示 errorMessage" in md
    assert "提修改意见（jakobhe，步骤 human-review）：variant 转 draft 时也要校验" in md
    assert "驳回（jakobhe，步骤 human-review）：文案要用工单原文" in md and "ignored" not in md
    assert "上一次的结果：rejected" in md

    declared = {"from_run": {"type": "run", "role": "development", "carry": ["ticket"]}}
    files = _continue_files(log, {"from_run": "ck-development-11111111"}, declared, tmp_path)
    assert list(files) == [CONVERSATION_FILE] and files[CONVERSATION_FILE] == md
    assert _continue_files(log, {"from_run": ""}, declared, tmp_path) == {}
