"""Build the Flow manual, separate from the User manual (docs/build-html.py), one page per language:
docs/workflows/index.html (English) and docs/zh/workflows/index.html (Chinese).

    uv run --with markdown python docs/workflows/build.py

One page, sidebar navigation, same look as the User manual (docs/build-html.py's page shell):
- Home: the manual's README.md, written by hand (roles, which flow when, starting and stepping in):
  docs/workflows/README.md (Chinese), docs/en/workflows/README.md (English).
- One chapter per flow in flows/*.yaml. The step graph, the inputs and the step facts are generated
  from the YAML with the same expansion as `myorch run` and the console (myorch.ui.flowgraph), for one
  project (the flow's first `projects:` entry, else the first pack of PROJECT_ORDER that exists: mdm,
  whose development flow has every step) and default inputs. Flows are shared by every project; a
  project pack can leave steps out (flow-level `if:`) and change parameters (flows.development). The
  prose is written by hand: flows/<flow>.md (Chinese), docs/en/flows/<flow>.md (English); a line
  `<!-- flow-graph -->` in it places the graph, otherwise it goes before the prose.
- Fragments: flows/lib/*.yaml, generated (description, params, which flows use it).

The flows' own texts (input labels and hints, role names, fragment descriptions) are Chinese; the
English page takes them from docs/workflows/en.py and lists the ones it has no English for.

Project values come from the project packs in ../myorch-knowledge (or $MYORCH_KNOWLEDGE); a flow whose
project pack is missing shows its expansion error instead of a graph.

Rebuild after changing a flow, a fragment, a flow's .md or the README.
"""
from __future__ import annotations

import html
import importlib.util
import os
import re
import sys
from pathlib import Path
from typing import Any

import markdown
import yaml
from markdown.extensions.toc import slugify_unicode

HERE = Path(__file__).resolve().parent          # docs/workflows/
DOCS = HERE.parent                               # docs/
REPO = DOCS.parent
FLOWS = REPO / "flows"
KB = Path(os.environ.get("MYORCH_KNOWLEDGE") or REPO.parent / "myorch-knowledge")  # project packs, for {{project.x}}

sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE))
from en import EN as FLOW_TEXT_EN  # noqa: E402
from myorch.core.inputs import ROLE_INFO  # noqa: E402
from myorch.ui.flowgraph import FlowDefs  # noqa: E402

_spec = importlib.util.spec_from_file_location("docs_build_html", DOCS / "build-html.py")
shell = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = shell  # its dataclass looks itself up there
_spec.loader.exec_module(shell)

# Since 2026-11-10 every flow is shared by every project (no `projects:`), so they all land in one
# group. PROJECT_ORDER is also the order in which a pack is picked to draw the graphs: mdm first, its
# development flow has every step (Jira, takeaway).
PROJECT_ORDER = ["mdm", "blog", "myorch"]
MODEL = {"claude-opus-5-5": "Opus", "claude-sonnet-5-5": "Sonnet", "claude-haiku-5-5": "Haiku", "claude-fable-5-2": "Fable"}

# The page's own words, per language. The flows' texts go through tx().
WORDS: dict[str, dict[str, Any]] = {
    "zh-CN": {
        "flows": "流程", "type": {"string": "文本", "message": "消息", "select": "选项", "ticket": "工单号", "run": "运行", "int": "整数", "bool": "是/否"},
        "exhausted": {"ask": "跑满后问你", "fail": "跑满后失败", "accept": "跑满后接受，继续往下"},
        "script": "脚本", "host": "宿主机动作", "approval_n": "审批 · {n} 人", "approval_risk": "审批 · 人数按风险算",
        "parallel": "×{n} 并行", "may_ask": "可能问你", "cond": "有条件", "skip": "失败当跳过", "waits": "等 {x}", "sep": "、",
        "loop_head": "循环 · 最多 {n} 轮", "until": "直到", "reflect": "跑满仍未满足时先反思，再问你：再跑一轮 / 接受 / 终止",
        "legend": ["脚本", "宿主机动作", "需要你：审批、提问", "有条件（鼠标停在「有条件」上看表达式）", "同一行的步骤并行"],
        "no_inputs": "没有输入。", "colon": "：", "by_project": "看项目：", "required": "必填", "one_of": "二选一",
        "only_if": "只对 {x} 成立的项目显示", "inputs_head": ["输入", "标签", "类型", "必填", "默认", "说明"],
        "presets": "按输入 {x} 选一组参数（presets）：",
        "approvers": "审批人数 {x}", "changes": "可以提修改意见：再跑一轮 {x}", "can_ask": "可以向你提问",
        "instances": "{n} 个实例并行", "on_failed": "失败时 {x}", "timeout": "超时 {x}", "retries": "重试 {x}", "semi": "；",
        "loop_row": "循环 {id}：最多 {n} 轮", "loop_until": "，直到 {x}", "loop_cond": "。条件 {x}", "loop_use": "。片段 {x}", "comma": "，",
        "facts": "每一步的执行者、条件、输出（从 YAML 生成）", "facts_head": ["步骤", "执行", "条件", "outputs", "人和并行", "限制"],
        "file": "文件", "prose": "，说明 {x}", "role": "角色", "task": "（控制台里的任务「{x}」）", "projects": "项目",
        "shared": "所有项目共用", "hint": "一句话", "fragments": "片段", "expand_failed": "展开失败：{x}",
        "where_project": "按项目 {x} 和默认输入展开（别的项目的项目包可能裁掉步骤、换掉参数）", "where_default": "按默认输入展开",
        "steps": "步骤图", "generated": "从 {file} 生成，{where}，和 <code>myorch expand</code>、控制台「流程」看到的一样。",
        "inputs": "输入", "unused": "没有流程用它", "none": "无", "kind": {"loop": "循环", "step": "步骤"},
        "fragments_intro": ("片段是 <code>flows/lib/</code> 下可以复用的步骤或循环。流程用 <code>use:</code> 引用它，"
                            "发起时展开。写法见<a href=\"../index.html#ch-08-flow-syntax\">使用手册 · 8. 写流程</a>。这张表从片段文件生成。"),
        "fragments_head": ["文件", "类型", "做什么", "参数", "用它的流程"],
        "home": "首页", "reference": "参考", "manual": "流程手册", "title": "myorch 流程手册", "user_manual": "使用手册",
    },
    "en": {
        "flows": "Flows", "type": {"string": "text", "message": "message", "select": "options", "ticket": "ticket", "run": "run", "int": "integer", "bool": "yes/no"},
        "exhausted": {"ask": "asks you when out of rounds", "fail": "fails when out of rounds", "accept": "accepts when out of rounds and goes on"},
        "script": "script", "host": "host action", "approval_n": "approval · {n} people", "approval_risk": "approval · count set by risk",
        "parallel": "×{n} parallel", "may_ask": "may ask you", "cond": "conditional", "skip": "failure counts as skipped", "waits": "waits for {x}", "sep": ", ",
        "loop_head": "loop · up to {n} rounds", "until": "until", "reflect": "Out of rounds and still not done: reflect first, then ask you: one more round / accept / stop",
        "legend": ["script", "host action", "needs you: approvals, questions", "conditional (hover over “conditional” to see the expression)", "steps in the same row run in parallel"],
        "no_inputs": "No inputs.", "colon": ": ", "by_project": "depends on the project: ", "required": "required", "one_of": "one of two",
        "only_if": "shown only for projects where {x} holds", "inputs_head": ["Input", "Label", "Type", "Required", "Default", "Description"],
        "presets": "The input {x} picks a set of parameters (presets):",
        "approvers": "approvers {x}", "changes": "changes can be requested: runs {x} again", "can_ask": "can ask you questions",
        "instances": "{n} instances in parallel", "on_failed": "on failure {x}", "timeout": "timeout {x}", "retries": "retries {x}", "semi": "; ",
        "loop_row": "Loop {id}: up to {n} rounds", "loop_until": ", until {x}", "loop_cond": ". Condition {x}", "loop_use": ". Fragment {x}", "comma": ", ",
        "facts": "Each step's executor, condition and outputs (generated from the YAML)", "facts_head": ["Step", "Runs as", "Condition", "outputs", "People and parallelism", "Limits"],
        "file": "File", "prose": ", prose {x}", "role": "Role", "task": " (the console task “{x}”)", "projects": "Projects",
        "shared": "shared by every project", "hint": "In short", "fragments": "Fragments", "expand_failed": "Expansion failed: {x}",
        "where_project": "expanded for project {x} with default inputs (another project's pack may leave steps out or change parameters)", "where_default": "expanded with default inputs",
        "steps": "Step graph", "generated": "Generated from {file}, {where}; the same as <code>myorch expand</code> and the console's Flows page (流程).",
        "inputs": "Inputs", "unused": "no flow uses it", "none": "none", "kind": {"loop": "loop", "step": "step"},
        "fragments_intro": ("Fragments are reusable steps or loops in <code>flows/lib/</code>. A flow refers to one with <code>use:</code>; "
                            "it is expanded when a run starts. Syntax: <a href=\"../index.html#ch-08-flow-syntax\">User manual · 8. Writing flows</a>. "
                            "This table is generated from the fragment files."),
        "fragments_head": ["File", "Kind", "What it does", "Params", "Used by"],
        "home": "Home", "reference": "Reference", "manual": "Flow manual", "title": "myorch Flow manual", "user_manual": "User manual",
    },
}

LANG: Any = None                 # the shell.Lang being built
W: dict[str, Any] = {}           # its words
MISSING: dict[str, None] = {}    # flow texts with no English (in the order met)
CJK = re.compile(r"[　-〿一-鿿＀-￯]")


def tx(v: Any) -> str:
    """A text from a flow (label, hint, description, role name), in the page's language."""
    s = str(v)
    if LANG.code != "en" or not CJK.search(s):
        return s
    if s in FLOW_TEXT_EN:
        return FLOW_TEXT_EN[s]
    MISSING[s] = None
    return s


def esc(v: Any) -> str:
    return html.escape(str(v), quote=True)


def model_name(m: str | None) -> str:
    return MODEL.get(m or "", m or "")


# ---------------------------------------------------------------- graph

def kind_line(s: dict[str, Any]) -> str:
    if s["exec"] == "agent":
        return " · ".join(x for x in ("agent", model_name(s["model"]), s["profile"] or "") if x)
    if s["exec"] == "script":
        return " · ".join(x for x in (W["script"], s["profile"] or "") if x)
    if s["exec"] == "host":
        return f"{W['host']} · {s['action']}"
    a = s.get("approval") or {}
    n = a.get("count")
    return W["approval_n"].format(n=n) if isinstance(n, int) else W["approval_risk"]


def card(s: dict[str, Any], prev: set[str] | None) -> str:
    human = s["exec"] == "approval"
    cls = ["fg-c", f"k-{s['exec']}"]
    if human:
        cls.append("human")
    if s.get("when"):
        cls.append("cond")
    chips = []
    if (s.get("fanout") or 1) > 1:
        chips.append(f'<i class="chip">{W["parallel"].format(n=s["fanout"])}</i>')
    if s.get("interaction") == "allowed":
        chips.append(f'<i class="chip ask">{W["may_ask"]}</i>')
    if s.get("when"):
        chips.append(f'<i class="chip cond" title="{esc(s["when"])}">{W["cond"]}</i>')
    if s.get("on_failed") == "skip":
        chips.append(f'<i class="chip">{W["skip"]}</i>')
    needs = s.get("needs") or []
    wait = ""
    if prev is not None and needs and set(needs) != prev:
        wait = f'<span class="wait">{esc(W["waits"].format(x=W["sep"].join(needs)))}</span>'
    return (f'<div class="{" ".join(cls)}" title="{esc(s["id"])}"><b>{esc(s["id"])}</b><span>{esc(kind_line(s))}</span>'
            + (f'<div class="chips">{"".join(chips)}</div>' if chips else "") + wait + "</div>")


def by_stage(items: list[dict[str, Any]], stage: dict[str, int]) -> list[list[dict[str, Any]]]:
    out: dict[int, list] = {}
    for it in items:
        out.setdefault(stage[it["id"]], []).append(it)
    return [out[k] for k in sorted(out)]


def stages_html(groups: list[list[dict[str, Any]]], render) -> str:
    parts, prev = [], None
    for i, g in enumerate(groups):
        if i:
            parts.append('<div class="fg-down"><i></i></div>')
        parts.append('<div class="fg-row">' + "".join(render(x, prev) for x in g) + "</div>")
        prev = {x["id"] for x in g}
    return "".join(parts)


def exhausted(v: Any) -> str:
    return W["exhausted"].get(str(v or "ask"), str(v))


def loop_html(t: dict[str, Any], prev: set[str] | None) -> str:
    head = [f"<b>{esc(t['id'])}</b>", esc(W["loop_head"].format(n=t["max_rounds"]))]
    if t.get("until"):
        head.append(f'{W["until"]} <code>{esc(t["until"])}</code>')
    head.append(f'<span class="{"ask" if t.get("on_exhausted", "ask") == "ask" else ""}">{esc(exhausted(t.get("on_exhausted")))}</span>')
    if t.get("use"):
        head.append(f'<span class="use">{esc(t["use"])}</span>')
    if t.get("when"):
        head.append(f'<i class="chip cond" title="{esc(t["when"])}">{W["cond"]}</i>')
    inner = stages_html(by_stage(t["steps"], t["stages"]), card)
    if t.get("reflect"):
        inner += f'<div class="fg-reflect"><span>{W["reflect"]}</span>' + card(t["reflect"], None) + "</div>"
    return f'<div class="fg-loop{" cond" if t.get("when") else ""}"><div class="fg-lh">{" · ".join(head)}</div>{inner}</div>'


def graph_html(g: dict[str, Any]) -> str:
    def render(t, prev):
        return loop_html(t, prev) if t["kind"] == "loop" else card(t, prev)
    top = {t["id"]: t["stage"] for t in g["tree"]}
    body = stages_html(by_stage(g["tree"], top), render)
    lg = W["legend"]
    legend = (f'<div class="fg-legend"><span><i class="sw agent"></i>agent</span><span><i class="sw script"></i>{lg[0]}</span>'
              f'<span><i class="sw host"></i>{lg[1]}</span><span><i class="sw human"></i>{lg[2]}</span>'
              f'<span><i class="sw cond"></i>{lg[3]}</span><span>{lg[4]}</span></div>')
    return f'<div class="fg">{body}{legend}</div>'


# ---------------------------------------------------------------- tables

def inputs_table(doc: dict[str, Any]) -> str:
    inputs = doc.get("inputs") or {}
    if not inputs:
        return f"<p>{W['no_inputs']}</p>"
    defaults = doc.get("defaults") or {}
    req = {n for group in (doc.get("require_one_of") or []) for n in group}
    rows = []
    for name, spec in inputs.items():
        spec = spec if isinstance(spec, dict) else {"type": spec or "string"}
        t = W["type"].get(spec.get("type", "string"), spec.get("type", "string"))
        if spec.get("type") == "select":
            t += W["colon"] + " / ".join(f'<code>{esc(o.get("value"))}</code>' for o in spec.get("options") or [])
        r = spec.get("required")
        need = (f'{W["by_project"]}<code>{esc(r)}</code>' if isinstance(r, str) else W["required"] if r else W["one_of"] if name in req else "")
        d = defaults.get(name, "")
        only = W["only_if"].format(x=f'<code>{esc(spec.get("if"))}</code>')
        rows.append(f"<tr><td><code>{esc(name)}</code></td><td>{esc(tx(spec.get('label', '')))}</td><td>{t}</td>"
                    f"<td>{need}</td><td>{f'<code>{esc(d)}</code>' if d not in ('', None) else ''}</td>"
                    f"<td>{esc(tx(spec.get('hint', '')))}"
                    + (f'<br><span class="small">{only}</span>' if spec.get("if") else "")
                    + "</td></tr>")
    head = "".join(f"<th>{h}</th>" for h in W["inputs_head"])
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def presets_table(doc: dict[str, Any]) -> str:
    p = doc.get("presets")
    if not isinstance(p, dict) or "by" not in p:
        return ""
    names = [k for k in p if k != "by"]
    keys = list(dict.fromkeys(k for n in names for k in (p[n] or {})))
    head = "".join(f"<th>{esc(k)}</th>" for k in keys)
    rows = "".join(f"<tr><td><code>{esc(n)}</code></td>" + "".join(f"<td>{esc(model_name(str((p[n] or {}).get(k, ''))))}</td>" for k in keys) + "</tr>"
                   for n in names)
    by = f"<code>{esc(p['by'])}</code>"
    return (f"<p>{W['presets'].format(x=by)}</p>"
            f"<table><thead><tr><th>{esc(p['by'])}</th>{head}</tr></thead><tbody>{rows}</tbody></table>")


def facts_rows(items: list[dict[str, Any]], prefix: str = "") -> list[str]:
    rows = []
    for s in items:
        a = s.get("approval")
        extra = []
        if a:
            extra.append(W["approvers"].format(x=f"<code>{esc(a['count'])}</code>"))
            if a.get("changes"):
                extra.append(W["changes"].format(x=f"<code>{esc(a['changes'])}</code>"))
        if s.get("interaction") == "allowed":
            extra.append(W["can_ask"])
        if s.get("fanout", 1) and s.get("fanout", 1) > 1:
            extra.append(W["instances"].format(n=s["fanout"]))
        if s.get("on_failed") and s["on_failed"] != "fail":
            extra.append(W["on_failed"].format(x=f"<code>{esc(s['on_failed'])}</code>"))
        lim = " · ".join(x for x in (s.get("timeout") and W["timeout"].format(x=s["timeout"]),
                                     s.get("retries") and W["retries"].format(x=s["retries"])) if x)
        outs = W["sep"].join(f"<code>{esc(o)}</code>" for o in s.get("outputs") or [])
        when = f"<code>{esc(s['when'])}</code>" if s.get("when") else ""
        rows.append(f"<tr><td><code>{esc(prefix + s['id'])}</code></td><td>{esc(kind_line(s))}</td>"
                    f"<td>{when}</td>"
                    f"<td>{outs}</td><td>{W['semi'].join(extra)}</td><td>{esc(lim)}</td></tr>")
    return rows


def facts_table(g: dict[str, Any]) -> str:
    rows: list[str] = []
    for t in g["tree"]:
        if t["kind"] == "loop":
            rows.append(f'<tr class="grp"><td colspan="6">'
                        + W["loop_row"].format(id=f'<code>{esc(t["id"])}</code>', n=esc(t["max_rounds"]))
                        + (W["loop_until"].format(x=f'<code>{esc(t["until"])}</code>') if t.get("until") else "")
                        + W["comma"] + esc(exhausted(t.get("on_exhausted")))
                        + (W["loop_cond"].format(x=f'<code>{esc(t["when"])}</code>') if t.get("when") else "")
                        + (W["loop_use"].format(x=f'<code>{esc(t["use"])}</code>') if t.get("use") else "") + "</td></tr>")
            rows += facts_rows(t["steps"], "　")
            if t.get("reflect"):
                rows += facts_rows([t["reflect"]], "　")
        else:
            rows += facts_rows([t])
    head = "".join(f"<th>{h}</th>" for h in W["facts_head"])
    return (f"<details class=\"facts\"><summary>{W['facts']}</summary>"
            f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></details>")


# ---------------------------------------------------------------- markdown and links


def out_dir() -> Path:
    return LANG.out / "workflows"


def link_target(src_dir: Path, target: str, flow_ids: dict[Path, str]) -> str | None:
    """Rewrite a relative link in a source .md so it works from this language's workflows/index.html."""
    if re.match(r"^[a-z]+:|^#|^/", target):
        return None
    path, _, frag = target.partition("#")
    if not path:
        return None
    p = (src_dir / path).resolve()
    if p in flow_ids:                                   # another flow's doc: an anchor on this page
        return f"#{flow_ids[p]}--{frag}" if frag else f"#ch-{flow_ids[p]}"
    if p == LANG.workflows.resolve():
        return f"#index--{frag}" if frag else "#ch-index"
    chapters = {c.resolve(): shell.chapter_key(c) for c in LANG.chapters}
    if p in chapters:                                    # a User manual chapter
        key = chapters[p]
        return f"../index.html#{key}--{frag}" if frag else f"../index.html#ch-{key}"
    return shell.file_link(p, frag, out_dir())


def render_md(text: str, key: str, src_dir: Path, flow_ids: dict[Path, str]) -> tuple[str, list]:
    md = markdown.Markdown(extensions=["tables", "fenced_code", "toc", "sane_lists"],
                           extension_configs={"toc": {"slugify": lambda v, sep: f"{key}--{slugify_unicode(v, sep)}"}})
    body = md.convert(text)

    def repl(m: re.Match) -> str:
        target = html.unescape(m.group(1))
        if target.startswith("#") and not target.startswith(("#ch-", f"#{key}--")):
            return f'href="#{key}--{esc(target[1:])}"'  # a heading in the same chapter
        new = link_target(src_dir, target, flow_ids)
        return m.group(0) if new is None else f'href="{esc(new)}"'
    return re.sub(r'href="([^"]+)"', repl, body), md.toc_tokens


def split_title(text: str) -> tuple[str, str]:
    """The first `# ` heading and the rest."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("# "):
            return line[2:].strip(), "\n".join(lines[i + 1:])
    return "", text


# ---------------------------------------------------------------- chapters

def flow_chapter(fd: FlowDefs, item: dict[str, Any], flow_ids: dict[Path, str]) -> tuple[str, str, list]:
    flow = item["flow"]
    key = flow
    path = FLOWS / f"{flow}.yaml"
    doc = yaml.safe_load(path.read_text()) or {}
    projects = item.get("projects") or []
    project = projects[0] if projects else next((p for p in PROJECT_ORDER if (KB / p).is_dir()), None)
    g = fd.graph(flow, project, {})
    role = item.get("role") or ""
    label = tx(ROLE_INFO.get(role, {}).get("label", ""))
    md_path = LANG.flows / f"{flow}.md"
    title, prose = (split_title(md_path.read_text()) if md_path.is_file() else ("", ""))
    title = flow  # the chapter is named after the flow; the .md's own title is not shown
    zh_md = FLOWS / f"{flow}.md"

    meta = [f"<tr><td>{W['file']}</td><td><code>flows/{esc(path.name)}</code>"
            + (W["prose"].format(x=f"<code>{esc(os.path.relpath(md_path, REPO))}</code>") if md_path.is_file() else "") + "</td></tr>",
            f"<tr><td>{W['role']}</td><td><code>{esc(role)}</code>{esc(W['task'].format(x=label)) if label else ''}</td></tr>",
            f"<tr><td>{W['projects']}</td><td>{esc(W['sep'].join(projects)) if projects else W['shared']}</td></tr>"]
    hint = tx(doc.get("hint") or ROLE_INFO.get(role, {}).get("hint", ""))
    if hint:
        meta.append(f"<tr><td>{W['hint']}</td><td>{esc(hint)}</td></tr>")
    uses = item.get("uses") or []
    if uses:
        meta.append(f"<tr><td>{W['fragments']}</td><td>" + W["sep"].join(f'<a href="#fragments--{esc(Path(u).stem)}"><code>{esc(u)}</code></a>' for u in uses) + "</td></tr>")
    head = f'<table class="meta"><tbody>{"".join(meta)}</tbody></table>'

    if "error" in g:
        graph = f'<p class="err">{esc(W["expand_failed"].format(x=g["error"]))}</p>'
    else:
        where = W["where_project"].format(x=f"<code>{esc(project)}</code>") if project else W["where_default"]
        graph = (f'<h2 id="{key}--steps">{W["steps"]}</h2><p class="small">'
                 + W["generated"].format(file=f"<code>flows/{esc(path.name)}</code>", where=where) + "</p>"
                 + graph_html(g) + facts_table(g))
    generated = (f'<h2 id="{key}--inputs">{W["inputs"]}</h2>' + inputs_table(doc) + presets_table(doc) + graph)

    if not md_path.is_file() and zh_md.is_file():
        print(f"warning: {md_path.relative_to(REPO)} is missing (the Chinese {zh_md.relative_to(REPO)} exists)", file=sys.stderr)
    body, toc = render_md(prose, key, LANG.flows, flow_ids) if prose else ("", [])
    marker = "<!-- flow-graph -->"
    if marker in body:
        body = body.replace(f"<p>{marker}</p>", generated).replace(marker, generated)
    else:
        body = generated + body
    subs = [{"id": f"{key}--inputs", "name": W["inputs"]}]
    if "error" not in g:
        subs.append({"id": f"{key}--steps", "name": W["steps"]})
    subs += [{"id": t["id"], "name": t["name"]} for t in toc[0]["children"]] if toc and toc[0].get("level") == 1 else \
            [{"id": t["id"], "name": t["name"]} for t in toc]
    h1 = f"<h1>{esc(title)}{f' <small>{esc(label)}</small>' if label else ''}</h1>"
    return key, f'<section class="chapter" id="ch-{key}">{h1}{head}{body}</section>', [f"{flow}", subs, label]


def fragments_chapter(ov: dict[str, Any]) -> tuple[str, str, list]:
    rows, subs = [], []
    for fr in ov["fragments"]:
        fid = f"fragments--{fr['name']}"
        users = W["sep"].join(f'<a href="#ch-{esc(u)}">{esc(u)}</a>' for u in fr.get("used_by") or []) or W["unused"]
        params = W["sep"].join(f"<code>{esc(p)}</code>" for p in fr.get("params") or []) or W["none"]
        kind = W["kind"].get(fr.get("kind", ""), "")
        rows.append(f'<tr id="{fid}"><td><code>{esc(fr["file"])}</code></td><td>{kind}</td><td>{esc(tx(fr.get("description", "")))}</td>'
                    f"<td>{params}</td><td>{users}</td></tr>")
    head = "".join(f"<th>{h}</th>" for h in W["fragments_head"])
    body = (f"<h1>{W['fragments']}</h1><p>{W['fragments_intro']}</p>"
            f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>")
    return "fragments", f'<section class="chapter" id="ch-fragments">{body}</section>', [W["fragments"], subs, ""]


def flow_order(ov: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    groups: dict[str, list] = {}
    for f in ov["flows"]:
        p = (f.get("projects") or [""])[0]
        groups.setdefault(p, []).append(f)
    keys = [p for p in PROJECT_ORDER if p in groups] + sorted(p for p in groups if p not in PROJECT_ORDER and p) + ([""] if "" in groups else [])
    return [(k, sorted(groups[k], key=lambda f: (f.get("order", 99), f["flow"]))) for k in keys]


CSS = """
  /* ---- generated step graphs (docs/workflows/build.py) ---- */
  .fg { margin: 16px 0 8px; padding: 18px; background: var(--well); border-radius: var(--r-lg); font-size: 13px; line-height: 1.45; }
  .fg-row { display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }
  .fg-row > * { flex: 1 1 0; min-width: 170px; max-width: 420px; }
  .fg-row > .fg-loop { max-width: none; flex-basis: 100%; }
  .fg-c { background: var(--raised); box-shadow: var(--shadow-card); border-radius: 10px; padding: 8px 12px; text-align: center; }
  .fg-c b { display: block; font: 600 13px/1.4 var(--mono); color: var(--text-strong); }
  .fg-c > span { display: block; font-size: 12px; color: var(--text-faint); }
  .fg-c .wait { font-size: 11.5px; color: var(--text-ghost); }
  .fg-c.k-agent { box-shadow: inset 3px 0 0 var(--accent), var(--shadow-card); }
  .fg-c.k-host { background: repeating-linear-gradient(-45deg, var(--hc) 0 1px, transparent 1px 7px), var(--raised); }
  .fg-c.human { background: var(--warning-soft); box-shadow: 0 0 0 1.5px var(--warning); }
  .fg-c.human b { color: var(--warning); }
  .fg-c.cond { outline: 1px dashed var(--line-strong); outline-offset: 3px; }
  .fg .chips { display: flex; gap: 4px; justify-content: center; flex-wrap: wrap; margin-top: 4px; }
  .fg .chip { font-style: normal; font-size: 11px; font-weight: 500; background: var(--well); color: var(--text-faint); border-radius: 5px; padding: 0 6px; white-space: nowrap; }
  .fg .chip.ask { background: var(--warning-soft); color: var(--warning); }
  .fg .chip.cond { border: 1px dashed var(--line-strong); background: transparent; cursor: help; }
  .fg-down { display: flex; justify-content: center; height: 26px; }
  .fg-down i { display: block; width: 1.5px; height: 20px; margin-top: 2px; background: var(--text-ghost); position: relative; }
  .fg-down i::after { content: ""; position: absolute; bottom: -2px; left: -4.25px; border-top: 7px solid var(--text-ghost); border-left: 5px solid transparent; border-right: 5px solid transparent; }
  .fg-loop { border: 1px solid var(--line-strong); border-radius: 14px; padding: 10px 14px 14px; }
  .fg-loop.cond { border-style: dashed; }
  .fg-lh { font-size: 12px; color: var(--text-faint); margin-bottom: 10px; text-align: left; }
  .fg-lh b { font: 600 12.5px var(--mono); color: var(--text-strong); }
  .fg-lh code { font-size: 11.5px; }
  .fg-lh .ask { color: var(--warning); font-weight: 600; }
  .fg-lh .use { font-family: var(--mono); font-size: 11.5px; color: var(--text-ghost); }
  .fg-reflect { margin-top: 12px; padding-top: 10px; border-top: 1px dashed var(--line-strong); display: flex; gap: 12px; align-items: center; }
  .fg-reflect > span { flex: 1 1 auto; font-size: 12px; color: var(--text-faint); text-align: right; }
  .fg-reflect > .fg-c { flex: 0 0 220px; }
  .fg-legend { display: flex; flex-wrap: wrap; gap: 6px 16px; margin-top: 16px; font-size: 12px; color: var(--text-faint); }
  .fg-legend .sw { display: inline-block; width: 14px; height: 10px; border-radius: 3px; margin-right: 5px; vertical-align: -1px; background: var(--raised); box-shadow: var(--shadow-card); }
  .fg-legend .sw.agent { box-shadow: inset 3px 0 0 var(--accent), var(--shadow-card); }
  .fg-legend .sw.host { background: repeating-linear-gradient(-45deg, var(--hc) 0 1px, transparent 1px 4px), var(--raised); }
  .fg-legend .sw.human { background: var(--warning-soft); box-shadow: 0 0 0 1.5px var(--warning); }
  .fg-legend .sw.cond { background: transparent; box-shadow: none; outline: 1px dashed var(--line-strong); }
  details.facts { margin: 8px 0 16px; }
  details.facts summary { cursor: pointer; color: var(--text-faint); font-size: 13.5px; }
  details.facts summary:hover { color: var(--text-strong); }
  details.facts td code { white-space: normal; word-break: break-word; }
  tr.grp td { color: var(--text-faint); font-size: 13px; background: color-mix(in srgb, var(--well) 60%, transparent); }
  table.meta td:first-child { color: var(--text-faint); white-space: nowrap; width: 80px; }
  table.meta td { border-bottom: 0; padding-top: 3px; padding-bottom: 3px; }
  h1 small { font-size: 16px; font-weight: 500; color: var(--text-faint); margin-left: 6px; }
  p.small, span.small { font-size: 13px; color: var(--text-faint); }
  p.err { color: var(--danger); }
  aside .grp-t { font-size: 11.5px; font-weight: 600; color: var(--text-ghost); padding: 14px 10px 4px; text-transform: none; }
  aside .role { color: var(--text-ghost); font-weight: 400; font-size: 12.5px; margin-left: 4px; }
"""



def build(fd: FlowDefs, ov: dict[str, Any], lang: Any) -> None:
    global LANG, W
    LANG, W = lang, WORDS[lang.code]
    flow_ids = {(lang.flows / f"{f['flow']}.md").resolve(): f["flow"] for f in ov["flows"]}
    sections, nav = [], []

    rtitle, rbody = split_title(lang.workflows.read_text(encoding="utf-8"))
    body, toc = render_md(rbody, "index", lang.workflows.parent, flow_ids)
    sections.append(f'<section class="chapter" id="ch-index"><h1>{esc(rtitle)}</h1>{body}</section>')
    items = "".join(f'<li><a href="#{t["id"]}">{t["name"]}</a></li>' for t in toc)
    nav.append(f'<li class="ch"><a class="ch-link" href="#ch-index">{W["home"]}</a><ul class="sub">{items}</ul></li>')

    for proj, flows in flow_order(ov):
        nav.append(f'<li class="grp-t">{esc(proj or W["flows"])}</li>')
        for item in flows:
            key, sec, (name, subs, label) = flow_chapter(fd, item, flow_ids)
            sections.append(sec)
            items = "".join(f'<li><a href="#{s["id"]}">{s["name"]}</a></li>' for s in subs)
            role = f'<span class="role">{esc(label)}</span>' if label else ""
            nav.append(f'<li class="ch"><a class="ch-link" href="#ch-{key}">{esc(name)}{role}</a><ul class="sub">{items}</ul></li>')

    key, sec, (name, subs, _) = fragments_chapter(ov)
    sections.append(sec)
    nav.append(f'<li class="grp-t">{W["reference"]}</li><li class="ch"><a class="ch-link" href="#ch-{key}">{name}</a></li>')

    xlink = f'<a class="xlink" href="../index.html">{W["user_manual"]} →</a>'
    out = shell.page(lang, W["manual"], W["title"], xlink, "../" + lang.other + "workflows/index.html", CSS, "\n".join(nav), "\n".join(sections))
    out_dir().mkdir(parents=True, exist_ok=True)
    (out_dir() / "index.html").write_text(out, encoding="utf-8")
    print(f"wrote {out_dir() / 'index.html'} ({len(ov['flows'])} flows, {len(ov['fragments'])} fragments)")


def main() -> None:
    fd = FlowDefs(REPO, KB)
    ov = fd.overview()
    for lang in shell.LANGS:
        build(fd, ov, lang)
    if MISSING:
        print("warning: docs/workflows/en.py has no English for these flow texts (the English page shows them in Chinese):\n"
              + "\n".join(f"    {t!r}: '',".replace("\n", " ") for t in MISSING), file=sys.stderr)


if __name__ == "__main__":
    main()
