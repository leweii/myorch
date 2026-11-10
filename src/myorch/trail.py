"""What happened in runs besides their documents, from the event log: the questions people answered,
change requests, rejections, failed steps, resumes, and which run continued which. learn reads it
(docs/learn/trail.json, written into the learn run when it starts) to keep decisions and lessons,
not only review findings. The log lives on the host, so the sandbox cannot read it itself.
"""

from __future__ import annotations

from typing import Any

TEXT = 1500  # per answer or error: enough to learn from, short enough for the prompt


def _cut(s: Any) -> str:
    s = str(s or "")
    return s if len(s) <= TEXT else s[:TEXT] + "…"


def trail(log, run_ids: list[str]) -> list[dict[str, Any]]:
    out = []
    for rid in run_ids:
        t: dict[str, Any] = {"run": rid, "flow": "", "continued_from": "", "status": "", "qa": [], "changes": [],
                             "rejected": [], "failures": [], "resumes": []}
        asked: dict[str, dict] = {}
        for e in log.read(run_id=rid, limit=5000):
            d = e.data or {}
            if e.kind == "run_started":
                t["flow"] = d.get("flow") or ""
                t["continued_from"] = (d.get("inputs") or {}).get("from_run") or ""
            elif e.kind == "question":
                asked[e.step] = {"step": e.step, "question": _cut(d.get("question")), "options": list(d.get("options") or [])[:6]}
            elif e.kind == "replied":
                q = asked.pop(e.step, {"step": e.step, "question": _cut(d.get("question")), "options": []})
                t["qa"].append({**q, "answer": _cut(d.get("text"))})
            elif e.kind == "changes_requested":
                t["changes"].append({"step": e.step, "by": d.get("by"), "text": _cut(d.get("text"))})
            elif e.kind == "approval_progress" and d.get("rejected_by"):
                t["rejected"].append({"step": e.step, "by": d.get("rejected_by")})
            elif e.kind == "step_finished" and d.get("status") == "failed":
                t["failures"].append({"step": e.step, "attempts": d.get("attempts"), "error": _cut(d.get("error") or d.get("summary"))})
            elif e.kind == "run_resumed":
                t["resumes"].append({"steps": d.get("steps") or [], "by": d.get("by")})
            elif e.kind == "run_finished":
                t["status"] = d.get("status") or ""
        out.append(t)
    return out


CONVERSATION_FILE = "docs/previous/conversation.md"


def _console_notes(data_root, run_id: str) -> list[dict[str, Any]]:
    """Approval comments and rejections typed in the console (myorch-data/ui/runs/<run>.jsonl)."""
    import json
    from pathlib import Path

    if data_root is None:
        return []
    p = Path(data_root) / "ui" / "runs" / f"{run_id}.jsonl"
    out = []
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("kind") in ("approve", "reject"):
                out.append(e)
    return out


def previous_conversation(log, run_id: str, data_root=None) -> str:
    """What people said to a run, for the run that continues its branch (from_run): the questions the
    agent asked and the answers, change requests, approvals and rejections with their comments, and
    what failed. The event log and the console notes live on the host, so the starting run gets this
    as docs/previous/conversation.md (the sandbox cannot read them)."""
    t = trail(log, [run_id])[0]
    notes = _console_notes(data_root, run_id)
    lines = [f"# 上一次运行（{run_id}）里人说过的话", "",
             "这是人对上一次运行说的话：回答、修改意见、审批意见。它们是人的决定，优先于上一次文档里的假设。",
             "上一次的原始请求在 external-request.md。", ""]
    lines += ["## agent 问的问题和人的回答", ""]
    if t["qa"]:
        for i, q in enumerate(t["qa"], 1):
            lines.append(f"### {i}. 步骤 {q['step']}")
            lines.append("")
            lines.append("问：" + q["question"])
            if q.get("options"):
                lines.append("")
                lines.append("选项：" + "；".join(str(o) for o in q["options"]))
            lines.append("")
            lines.append("答：" + q["answer"])
            lines.append("")
    else:
        lines += ["（没有提问）", ""]
    lines += ["## 审批时的意见", ""]
    said = False
    for c in t["changes"]:
        lines += [f"- 提修改意见（{c.get('by') or '?'}，步骤 {c['step']}）：{c['text']}"]
        said = True
    for n in notes:
        what = "批准" if n.get("kind") == "approve" else "驳回"
        text = _cut(n.get("text"))
        lines += [f"- {what}（{n.get('by') or '?'}，步骤 {n.get('step') or '?'}）" + (f"：{text}" if text else "")]
        said = True
    noted = {(n.get("step"), n.get("by")) for n in notes if n.get("kind") == "reject"}
    for r in t["rejected"]:
        if (r["step"], r.get("by")) not in noted:
            lines += [f"- 驳回（{r.get('by') or '?'}，步骤 {r['step']}），没有写原因"]
            said = True
    if not said:
        lines += ["（没有审批意见）"]
    lines += [""]
    if t["failures"] or t["resumes"]:
        lines += ["## 失败和重新执行", ""]
        for f in t["failures"]:
            lines += [f"- 步骤 {f['step']} 失败（尝试 {f.get('attempts') or '?'} 次）：{f['error']}"]
        for r in t["resumes"]:
            lines += [f"- {r.get('by') or '?'} 重新执行了：{', '.join(r['steps']) or '失败的步骤'}"]
        lines += [""]
    lines += [f"上一次的结果：{t['status'] or '未知'}"]
    return "\n".join(lines) + "\n"
