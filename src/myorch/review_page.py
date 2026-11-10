"""One page per run for the human approval gate: what was asked, what changed, what review and the
checks found, and how risky it is — read from the run's workspace, without digging into the volume.

`myorch review <run>` writes it to myorch-data/reviews/<run>.html and opens it. Nothing here leaves
the machine, so it also serves projects whose repository is public (review: local).
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

import markdown as md

CSS = """
body{font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;max-width:1100px;margin:24px auto;padding:0 20px;color:#1f2328}
h1{font-size:22px;margin-bottom:4px} h2{font-size:18px;border-bottom:1px solid #d0d7de;padding-bottom:4px;margin-top:32px}
.meta{color:#59636e;font-size:13px} .meta code{font-size:12px}
.box{border:1px solid #d0d7de;border-radius:6px;padding:4px 16px;margin:8px 0;background:#fff}
.ok{color:#1a7f37} .bad{color:#cf222e} .warn{color:#9a6700}
table{border-collapse:collapse;width:100%;font-size:13px} td,th{border:1px solid #d0d7de;padding:4px 8px;vertical-align:top;text-align:left}
th{background:#f6f8fa} .sev-Important{color:#cf222e;font-weight:600} .sev-Nit{color:#9a6700} .sev-Pre-existing{color:#59636e}
pre.diff{font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;background:#f6f8fa;padding:12px;overflow-x:auto;border-radius:6px}
pre.diff .add{background:#dafbe1;display:block} pre.diff .del{background:#ffebe9;display:block}
pre.diff .hunk{color:#0550ae;display:block} pre.diff .file{font-weight:600;display:block;margin-top:12px}
details>summary{cursor:pointer;color:#0969da} .cmd{font:13px ui-monospace,Menlo,monospace;background:#f6f8fa;padding:8px 12px;border-radius:6px;display:block}
"""


def _md(text: str) -> str:
    return md.markdown(text or "（无）", extensions=["tables", "fenced_code"])


def _diff_html(diff: str) -> str:
    lines = []
    for line in diff.splitlines():
        cls = ("file" if line.startswith("diff --git") else "hunk" if line.startswith("@@") else
               "add" if line.startswith("+") and not line.startswith("+++") else
               "del" if line.startswith("-") and not line.startswith("---") else "")
        esc = html.escape(line)
        lines.append(f'<span class="{cls}">{esc}</span>' if cls else esc)
    return '<pre class="diff">' + "\n".join(lines) + "</pre>"


def _json(text: str | None, default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def _rounds(files: dict[str, str], pattern: str) -> list[tuple[int, str]]:
    out = []
    for path in files:
        m = re.fullmatch(pattern, path)
        if m:
            out.append((int(m.group(1)), path))
    return sorted(out)


def build(run_id: str, files: dict[str, str], waiting: str = "", approve_hint: str = "") -> str:
    """HTML for one run. `files` is store.review_bundle(); `waiting` describes the gate, if any."""
    mandate = _json(files.get(".myorch/mandate.json"), {})
    steps = {re.sub(r"^\.myorch/steps/|/out/manifest\.json$", "", p): _json(t, {})
             for p, t in files.items() if p.endswith("/out/manifest.json")}
    req = mandate.get("request") or {}
    title = next((t for sid in ("develop", "analyze-requirement")  # ck-development merged the analysis into develop
                  if (t := (steps.get(sid, {}).get("outputs") or {}).get("title"))), "") or req.get("task", run_id)[:80]
    project = (mandate.get("project") or {}).get("name", "-")
    source = mandate.get("source") or ""
    src = f' · 来源 <a href="{html.escape(source)}">{html.escape(source)}</a>' if source.startswith("http") else ""

    parts = [f"<h1>{html.escape(title)}</h1>",
             f'<div class="meta">run <code>{run_id}</code> · 项目 {html.escape(project)} · 流程 {html.escape((mandate.get("flow") or {}).get("name", "-"))}'
             f' · 发起人 {html.escape(mandate.get("initiator", "-"))}{src}</div>']
    if waiting:
        parts.append(f'<div class="box"><p class="warn"><b>等你审批：</b>{html.escape(waiting)}</p>{approve_hint}</div>')

    # 1. what was asked
    parts += ["<h2>1. 需求（agent 理解的）</h2>",
              f'<div class="box">{_md(files.get("docs/requirements.md", ""))}</div>',
              f"<details><summary>原始请求</summary><pre>{html.escape(json.dumps(req, ensure_ascii=False, indent=1))}</pre></details>"]

    # 2. what changed
    diff = files.get("git/diff", "")
    stat, _, body = diff.partition("\n\n")
    parts += ["<h2>2. 改动</h2>",
              f'<p class="meta">提交：{html.escape(files.get("git/log", "").strip()) or "（无）"}</p>',
              f'<pre class="diff">{html.escape(stat)}</pre>', _diff_html(body)]

    # 3. checks
    ci = _rounds(files, r"docs/ci/round-(\d+)/result\.json")
    if ci:
        n, path = ci[-1]
        r = _json(files[path], {})
        verdict = ('<span class="ok">通过</span>' if r.get("passed") else
                   f'<span class="bad">未通过：{html.escape("、".join(map(str, r.get("failed", []))))}</span>')
        env = r.get("environment") or []
        extra = f'；环境失败（不计入）：{html.escape("、".join(env))}' if env else ""
        parts += ["<h2>3. 检查</h2>", f"<p>第 {n} 轮：{verdict}{extra}</p>",
                  f"<details><summary>检查报告</summary>{_md(files.get(f'docs/ci/round-{n}/report.md', ''))}</details>"]
    # end-to-end block (only projects with e2e.image): the last round's smoke and acceptance results
    e2e = sorted({int(m.group(1)) for p in files if (m := re.match(r"docs/e2e/round-(\d+)/", p))})
    if e2e:
        n = e2e[-1]
        parts.append(f"<h2>3b. 端到端测试（{len(e2e)} 轮）</h2>")
        for part, label in (("smoke", "冒烟用例"), ("acceptance", "验收用例")):
            r = _json(files.get(f"docs/e2e/round-{n}/{part}/result.json"), None)
            if r is None:
                parts.append(f"<p>{label}：没有结果</p>")
                continue
            verdict = ('<span class="ok">通过</span>' if r.get("passed") else
                       f'<span class="bad">未通过：{html.escape("、".join(map(str, r.get("failed", []))))}</span>')
            parts += [f"<p>{label}，第 {n} 轮：{verdict}</p>",
                      f"<details><summary>{label}报告</summary>{_md(files.get(f'docs/e2e/round-{n}/{part}/report.md', ''))}</details>"]
        if f"docs/e2e/round-{n}/acceptance.md" in files:
            parts.append(f"<details><summary>验收标准对照</summary>{_md(files[f'docs/e2e/round-{n}/acceptance.md'])}</details>")
        parts.append(f'<p class="meta">截图在工作区 docs/e2e/round-{n}/*/screenshots/</p>')

    # 4. review
    summaries = _rounds(files, r"docs/review/round-(\d+)/summary\.md")
    verified = _rounds(files, r"docs/review/round-(\d+)/verified\.json")
    parts.append(f"<h2>4. Review（{len(summaries)} 轮）</h2>")
    if verified:
        findings = _json(files[verified[-1][1]], [])
        if findings:
            rows = "".join(
                f'<tr><td class="sev-{html.escape(str(f.get("severity")))}">{html.escape(str(f.get("severity")))}</td>'
                f'<td><code>{html.escape(str(f.get("file", "")))}:{html.escape(str(f.get("line", "")))}</code></td>'
                f'<td><b>{html.escape(str(f.get("title", "")))}</b><br>{html.escape(str(f.get("evidence", "")))}</td>'
                f'<td>{html.escape(str(f.get("status", "")))}</td></tr>' for f in findings)
            parts.append(f"<p>最后一轮核实过的发现（不阻塞的也列出来）：</p><table><tr><th>严重度</th><th>位置</th><th>内容</th><th>状态</th></tr>{rows}</table>")
        else:
            parts.append("<p>最后一轮没有发现。</p>")
    if summaries:
        parts.append(f"<details><summary>最后一轮总结</summary>{_md(files[summaries[-1][1]])}</details>")

    # 5. risk
    rr, cr = steps.get("risk-rules", {}), steps.get("classify-risk", {})
    parts += ["<h2>5. 风险</h2>",
              f"<p><b>规则：</b>{html.escape(rr.get('summary', '（无）'))}</p>",
              f"<p><b>分类器：</b>{html.escape(cr.get('summary', '（无）'))}</p>",
              f"<details><summary>风险分析</summary>{_md(files.get('docs/risk.md', ''))}</details>"]

    # 6. how it was built
    parts += ["<h2>6. 实现记录</h2>", f"<details><summary>implementation.md</summary>{_md(files.get('docs/implementation.md', ''))}</details>"]
    return (f'<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>{html.escape(title)} · {run_id}</title>'
            f"<style>{CSS}</style></head><body>{''.join(parts)}</body></html>")
