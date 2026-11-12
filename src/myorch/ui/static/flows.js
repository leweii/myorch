// Flow definitions, drawn live from flows/*.yaml (ui/flowgraph.py): the side bar's 流程定义 view lists
// flows, fragments, eval cases and knowledge sources; each opens as a tab (ref {t: flow|frag|kb|ev|kbform,
// id}). The new-run tab shows the steps it will run. Both poll /api/flowdefs/version while visible and
// redraw when a flow or fragment file changes. Uses el/st/EXEC/api/openTab from app.js (loaded first).
// All flow text is inserted as textContent.
"use strict";

const FL = { filter: "", groups: {}, overview: null, flow: null, frag: null, kb: null, kbForm: null, knowledge: [], cases: [], ev: null, project: null, inputs: {}, graph: null, sel: null, version: null, at: null };
const FL_POLL_MS = 2500;

function flQuery(flow, project, inputs) {
  const q = new URLSearchParams();
  if (project) q.set("project", project);
  for (const [k, v] of Object.entries(inputs || {})) if (v !== "" && v !== undefined) q.set(`i.${k}`, v);
  return `/api/flowdefs/${encodeURIComponent(flow)}?${q}`;
}

// ---- drawing a definition tree ---------------------------------------------------------------

// Items in stage order, each stage numbered; the items of one stage side by side.
function rows(items, cellOf) {
  const byStage = new Map();
  for (const it of items) { const k = it._stage; if (!byStage.has(k)) byStage.set(k, []); byStage.get(k).push(it); }
  const keys = [...byStage.keys()].sort((a, b) => a - b);
  return keys.map((k, n) => el("div", { class: "stage" },
    el("div", { class: "rail" }, el("span", { class: "num", text: String(n + 1) }), n < keys.length - 1 ? el("span", { class: "line" }) : null),
    el("div", { class: `cells${byStage.get(k).length > 1 ? " par" : ""}` }, byStage.get(k).map(cellOf),
      byStage.get(k).length > 1 ? el("div", { class: "parnote", text: "并行" }) : null)));
}

// The compact form (the new-run tab's preview): one row per step, stage numbers, ∥ for parallel steps,
// a loop's steps indented under it.
function defOutline(tree, onPick) {
  const out = [];
  const tags = (s) => [s.model ? s.model.replace("claude-", "") : "", s.when ? "条件" : "", s.interaction === "allowed" ? "可提问" : "",
    s.approval ? `审批 ${s.approval.count}` : "", s.fanout > 1 ? `×${s.fanout}` : ""].filter(Boolean).join(" · ");
  const row = (s, depth, num, par) => el("div", { class: "orow", style: `--d:${depth}`, title: s.when ? `当 ${s.when}` : "", onclick: () => onPick(s) },
    el("span", { class: "onum", text: num }), el("span", { class: "par", text: par ? "∥" : "" }),
    el("span", { class: `ic k-${s.kind === "loop" ? "loop" : s.exec}`, text: s.kind === "loop" ? "↻" : (EXEC[s.exec] || ["·"])[0] }),
    el("span", { class: "nm", text: s.id }), el("span", { class: "meta", text: s.kind === "loop" ? `最多 ${s.max_rounds} 轮` : tags(s) }));
  const stages = (items, stageOf) => { const ks = [...new Set(items.map(stageOf))].sort((a, b) => a - b); const n = new Map(); for (const it of items) n.set(stageOf(it), (n.get(stageOf(it)) || 0) + 1); return { num: (it) => ks.indexOf(stageOf(it)) + 1, par: (it) => n.get(stageOf(it)) > 1 }; };
  const byStage = (items, stageOf) => items.map((it, n) => [it, n]).sort((a, b) => stageOf(a[0]) - stageOf(b[0]) || a[1] - b[1]).map(([it]) => it);
  const top = stages(tree, (t) => t.stage);
  let last = null;
  for (const t of byStage(tree, (x) => x.stage)) {
    const num = top.num(t);
    out.push(row(t, 0, num !== last ? String(num) : "", top.par(t)));
    last = num;
    if (t.kind === "loop") {
      const ls = stages(t.steps, (s) => t.stages[s.id]);
      for (const s of byStage(t.steps, (x) => t.stages[x.id])) out.push(row(s, 1, "", ls.par(s)));
      if (t.reflect) out.push(row(t.reflect, 1, "", false));
    }
  }
  return el("div", { class: "outline" }, out);
}

function defCell(s, onPick, selId) {
  const [icon, label] = EXEC[s.exec] || ["·", ""];
  const tags = [
    s.model ? el("span", { class: "tag", text: s.model.replace("claude-", "") }) : null,
    s.profile ? el("span", { class: "tag ghost", text: s.profile }) : null,
    s.when ? el("span", { class: "tag cond", text: "条件" }) : null,
    s.interaction === "allowed" ? el("span", { class: "tag", text: "可提问" }) : null,
    s.approval ? el("span", { class: "tag", text: `审批 ${s.approval.count}` }) : null,
    s.use ? el("span", { class: "tag frag", text: s.use.replace(/^lib\//, "").replace(/\.yaml$/, "") }) : null,
  ].filter(Boolean);
  return el("div", { class: `cell def${s.id === selId ? " sel" : ""}`, title: `${label}${s.when ? `\n条件：${s.when}` : ""}`, onclick: (e) => { e.stopPropagation(); onPick(s); } },
    el("div", { class: "c1" }, el("span", { class: `ic k-${s.exec}`, text: icon }), el("span", { class: "nid", text: s.id }),
      s.fanout > 1 ? el("span", { class: "fan", text: `×${s.fanout}` }) : null),
    tags.length ? el("div", { class: "c2" }, tags) : null,
    s.when ? el("div", { class: "c3 when", text: `当 ${s.when}` }) : null);
}

function defLoop(t, onPick, selId) {
  const steps = t.steps.map((s) => ({ ...s, _stage: t.stages[s.id] }));
  return el("div", { class: `loop def${t.id === selId ? " sel" : ""}`, onclick: (e) => { e.stopPropagation(); onPick(t); } },
    el("div", { class: "lhead" }, el("span", { class: "ic k-loop", text: "LOOP" }), el("b", { text: t.id }),
      t.use ? el("span", { class: "tag frag", text: t.use.replace(/^lib\//, "").replace(/\.yaml$/, "") }) : null,
      el("span", { class: "muted small", text: `最多 ${t.max_rounds} 轮 · 直到 ${t.until} · 跑满后 ${t.on_exhausted}` })),
    t.when ? el("div", { class: "c3 when lw", text: `当 ${t.when}` }) : null,
    el("div", { class: "inner" }, rows(steps, (s) => defCell(s, onPick, selId)),
      t.reflect ? el("div", { class: "reflect" }, el("span", { class: "muted small", text: "每轮结束后" }), defCell(t.reflect, onPick, selId)) : null));
}

function defTree(tree, onPick, selId) {
  return el("div", { class: "flow defflow" }, rows(tree.map((t) => ({ ...t, _stage: t.stage })),
    (t) => (t.kind === "loop" ? defLoop(t, onPick, selId) : defCell(t, onPick, selId))));
}

function defDetail(s) {
  if (!s) return el("p", { class: "muted small", text: "点一个步骤看它的配置。" });
  if (s.kind === "loop") {
    return el("div", {}, el("h4", { text: `循环 ${s.id}` }),
      kv([["来自片段", s.use], ["最多轮数", s.max_rounds], ["结束条件", s.until], ["跑满后", s.on_exhausted], ["执行条件", s.when],
        ["依赖", (s.needs || []).join(", ")], ["步骤", s.steps.map((x) => x.id).join(" → ")], ["每轮后", s.reflect?.id]]));
  }
  const a = s.approval;
  return el("div", {}, el("h4", {}, el("span", { class: `ic k-${s.exec}`, text: (EXEC[s.exec] || ["·"])[0] }), ` ${s.id}`),
    kv([["执行者", (EXEC[s.exec] || [])[1]], ["harness", s.harness], ["动作", s.action], ["来自片段", s.use], ["profile", s.profile],
      ["模型", s.model], ["镜像", s.image], ["并行副本", s.fanout > 1 ? s.fanout : null], ["执行条件", s.when],
      ["依赖", (s.needs || []).join(", ")], ["可提问", s.interaction === "allowed" ? "是" : null],
      ["输出", (s.outputs || []).join(", ")], ["失败时", s.on_failed], ["重试", s.retries || null], ["超时", s.timeout],
      ["审批", a ? `${a.count} 人${a.when ? `，当 ${a.when}` : ""}${a.require_initiator ? "，含发起人" : ""}${a.changes ? `，可退回 ${a.changes}` : ""}` : null]]));
}

// ---- the 流程定义 view (side bar) and the flow tabs -------------------------------------------------

async function flLoadOverview() {
  FL.overview = await api("/api/flowdefs");
  FL.version = FL.overview.version; FL.at = new Date();
  try { FL.knowledge = await api("/api/knowledge?all=1"); } catch { FL.knowledge = []; }  // shared and per-project sources
  try { FL.cases = await api("/api/evals"); } catch { FL.cases = []; }  // reviewer-eval cases (myorch/evals.py)
}

async function flLoadGraph() {
  if (!FL.flow) { FL.graph = null; return; }
  FL.graph = await api(flQuery(FL.flow, FL.project, FL.inputs));
}

async function flEnsure() {
  if (!FL.projects) FL.projects = (await api("/api/projects")).filter((p) => !p.error);
  if (!FL.project) {
    const want = localStorage.getItem("myorch.project");
    FL.project = FL.projects.find((p) => p.name === want)?.name || FL.projects[0]?.name || null;
  }
  if (!FL.overview) await flLoadOverview();
}

function flLive() {
  const t = FL.at ? `${String(FL.at.getHours()).padStart(2, "0")}:${String(FL.at.getMinutes()).padStart(2, "0")}:${String(FL.at.getSeconds()).padStart(2, "0")}` : "";
  return el("span", { class: "livetag", title: "改了 flows/ 下的文件，这里几秒内会重画" }, el("span", { class: "dot" }), `LIVE · ${FL.version || ""} · ${t}`);
}

// What the active tab shows, when it is a flow tab.
function flCur() { const t = activeTab(); return t?.kind === "flow" ? t.ref : null; }
const refIs = (t, id) => { const c = flCur(); return !!c && c.t === t && (id === undefined || c.id === id); };
function flOpenRef(ref, preview = true) { openTab({ kind: "flow", ref }, { preview }); }

// The side bar is shown (app.js showView).
async function flOpenView() {
  try { await flEnsure(); } catch (e) { $("#flowList").replaceChildren(el("div", { class: "muted small pad", text: `读取流程失败：${e.message}` })); return; }
  flRenderList();
}

// A flow tab is shown (app.js showPane): its ref decides what the main area draws.
async function flShowTab(ref) {
  const pane = $("#pane-flow");
  if (!FL.overview) pane.replaceChildren(el("p", { class: "muted small pad", text: "读取中…" }));
  try { await flEnsure(); } catch (e) { pane.replaceChildren(el("p", { class: "muted small pad", text: `读取流程失败：${e.message}` })); return; }
  FL.frag = ref.t === "frag" ? ref.id : null;
  FL.kb = ref.t === "kb" ? ref.id : ref.t === "kbform" ? "+form" : null;
  FL.ev = ref.t === "ev" ? ref.id : null;
  if (ref.t === "kbform" && !FL.kbForm) FL.kbForm = { edit: false, name: "", project: "", kind: "dir", fields: {} };
  if (ref.t === "flow" && (FL.flow !== ref.id || !FL.graph)) {
    FL.flow = ref.id; FL.sel = null;
    const f = FL.overview.flows.find((x) => x.flow === ref.id);
    FL.inputs = f?.presets_by ? { [f.presets_by]: f.defaults?.[f.presets_by] || f.presets[0] } : {};
    try { await flLoadGraph(); } catch (e) { FL.graph = { error: e.message }; }
  }
  if (!refIs(ref.t, ref.id)) return;  // another tab was opened meanwhile
  flRenderMain();
  if (S.view === "flows") flRenderList();
}

function flRender() {
  if (S.view === "flows" && !document.body.classList.contains("side-off")) flRenderList();
  if (flCur()) flRenderMain();
}

function flRenderList() {
  const o = FL.overview;
  if (!o) return;
  const box = $("#flowList");
  // One compact line per flow: name and role; the description is the tooltip and in the main pane.
  const flowItem = (f) => el("div", { class: `item fl${refIs("flow", f.flow) ? " active" : ""}`,
    title: f.description || "", "data-q": `${f.flow} ${f.role || ""} ${f.label || ""}`.toLowerCase(), onclick: () => flOpenRef({ t: "flow", id: f.flow }), ondblclick: () => flOpenRef({ t: "flow", id: f.flow }, false) },
    el("div", { class: "line1" }, el("span", { class: "id", text: f.flow }), f.error ? el("span", { class: "tag cond", text: "错误" }) : null,
      el("span", { class: "role", text: f.label || f.role || "" })));
  const fragItem = (g) => el("div", { class: `item fl${refIs("frag", g.name) ? " active" : ""}`, "data-q": g.name.toLowerCase(),
    title: `${g.description || ""}${g.used_by.length ? `\n用在 ${g.used_by.join("、")}` : "\n没有流程用到"}`, onclick: () => flOpenRef({ t: "frag", id: g.name }) },
    el("div", { class: "line1" }, el("span", { class: `ic k-${g.kind === "loop" ? "loop" : "script"}`, text: g.kind === "loop" ? "LOOP" : "STEP" }), el("span", { class: "id", text: g.name }),
      g.used_by.length ? null : el("span", { class: "role", text: "没人用" })));
  const STATUS = { pending: "待确认", confirmed: "已收入", rejected: "不要" };
  const caseItem = (c) => el("div", { class: `item${refIs("ev", c.id) ? " active" : ""}`, onclick: () => flOpenRef({ t: "ev", id: c.id }) },
    el("div", { class: "line1" }, el("span", { class: "id", text: c.id }), el("span", { class: `tag${c.status === "pending" ? " cond" : ""}`, text: STATUS[c.status] || c.status })),
    el("div", { class: "sub", text: `${c.project} · ${c.findings.length} 条发现${c.status === "confirmed" ? ` · 收入 ${c.keep.length} 条` : ""}` }));
  const kbItem = (k) => el("div", { class: `item${refIs("kb", kbKey(k)) ? " active" : ""}`, onclick: () => flOpenRef({ t: "kb", id: kbKey(k) }) },
    el("div", { class: "line1" }, el("span", { class: "ic k-script", text: { dir: "DIR", git: "GIT", mcp: "MCP" }[k.kind] || k.kind }),
      el("span", { class: "id", text: k.name }), k.problems.length ? el("span", { class: "tag cond", text: "问题" }) : null),
    el("div", { class: "sub", text: `${k.project ? `${k.project} · ` : k.level === "template" ? "模板 · " : "全局 · "}${k.kind === "mcp" ? "实时查询" : k.latest ? `同步于 ${fmtTime(k.latest.synced_at)}` : "还没有快照（发起运行时同步）"}` }));
  const kbNew = el("div", { class: `item${refIs("kbform") && !FL.kbForm?.edit ? " active" : ""}`, onclick: () => kbOpenForm(null) },
    el("div", { class: "line1" }, el("span", { class: "id", text: "＋ 登记知识源" })), el("div", { class: "sub", text: "文件夹、git 仓库或 MCP 服务" }));
  const filter = el("input", { type: "search", class: "fd-filter", placeholder: "筛选：名字或角色，例如 ck、review、学习", value: FL.filter,
    oninput: (e) => { FL.filter = e.target.value; flFilter(box); } });
  const groups = flGroups(o.flows);
  const active = o.flows.find((f) => refIs("flow", f.flow));
  const keep = box.scrollTop, typing = document.activeElement?.classList.contains("fd-filter");
  box.replaceChildren(el("div", { class: "row fd-live" }, flLive()), filter,
    el("h5", { text: `流程 · ${o.flows.length}` }),
    ...groups.map((g) => flGroup(`p:${g.key}`, g.key || "通用", g.prefix, g.flows.map(flowItem),
      g.key === (FL.project || "") || (active && g.flows.includes(active)), g.title)),
    flGroup("frag", "片段", "lib/*", o.fragments.map(fragItem), refIs("frag"), "flows/lib/*.yaml：流程用 use: 引用"),
    el("h5", { text: `评测集 · ${FL.cases.filter((c) => c.status === "confirmed").length}/${FL.cases.length}` }), el("div", { class: "list" },
      FL.cases.length ? FL.cases.map(caseItem) : el("div", { class: "muted small", text: "还没有候选案例。在终端运行 myorch eval collect。" })),
    el("h5", { text: `知识库 · ${FL.knowledge.length}` }), el("div", { class: "list" }, kbNew, ...FL.knowledge.map(kbItem)));
  flFilter(box);
  box.scrollTop = keep;
  if (typing) { filter.focus(); filter.setSelectionRange(filter.value.length, filter.value.length); }
}

function flRenderMain() {
  const o = FL.overview, pane = $("#pane-flow");
  if (!o) return;
  let main, side;
  const kbRow = FL.kb && FL.knowledge.find((k) => kbKey(k) === FL.kb);
  const evCase = FL.ev && FL.cases.find((c) => c.id === FL.ev);
  if (FL.ev) {
    [main, side] = evCase ? caseView(evCase) : [el("section", { class: "fd-main" }, el("p", { class: "muted", text: "这个案例已不存在。" })), el("aside", { class: "fd-side" })];
  } else if (FL.kb === "+form" && FL.kbForm) {
    [main, side] = kbFormView(FL.kbForm);
  } else if (FL.kb) {
    const k = kbRow;
    main = el("section", { class: "fd-main" }, k ? [
      el("div", { class: "fd-title" }, el("span", { class: "big", text: k.name }), el("span", { class: "file", text: `myorch-knowledge/${k.file}` })),
      k.problems.length ? el("div", { class: "warn", text: k.problems.join("；") }) : null,
      kv([["类型", { dir: "本机文件夹", git: "git 仓库", mcp: "MCP 服务" }[k.kind]], ["登记在", k.level], ["位置", k.where], ["展开后", k.expanded],
        ["先读", k.index], ["工具", (k.tools || []).join(", ")], ["能用的项目", (k.projects || []).join(", ") || "所有项目"],
        ["默认挂载的项目", (k.default_in || []).join(", ")],
        ["最新快照", k.latest ? `${k.latest.version} · ${fmtTime(k.latest.synced_at)} · ${k.latest.files} 个文件 · ${(k.latest.bytes / 1024).toFixed(1)} KB` : (k.kind === "mcp" ? "MCP 没有快照" : "未同步（启动运行时会先同步）")],
        ["保留版本", k.kind === "mcp" ? null : String(k.versions)]]),
      k.kind !== "mcp" ? el("p", { class: "muted small", text: k.kind === "dir"
        ? "快照：每次发起运行时自动同步。内容没变就沿用原来的快照。新快照只影响之后启动的运行。"
        : "快照：发起运行时，最新快照超过 1 小时就重新克隆。克隆失败时用上一次的快照。" }) : null,
      k.level === "template" ? el("p", { class: "muted small", text: "登记在模板里，控制台不改模板。要改，编辑上面的文件。" })
        : el("div", { class: "evbar" },
          el("button", { text: "编辑", onclick: () => kbOpenForm(k) }),
          el("button", { class: "ghost danger", text: "删除登记", onclick: () => kbRemove(k) }))] : el("p", { class: "muted", text: "这个知识源已不存在。" }));
    side = el("aside", { class: "fd-side" }, el("p", { class: "muted small", text: "知识库只读挂进 agent 步骤：/sandbox/workspace/knowledge/<名字>/。MCP 知识库经 OpenShell provider 访问，token 只读。" }));
  } else if (FL.frag) {
    const g = o.fragments.find((x) => x.name === FL.frag);
    main = el("section", { class: "fd-main" }, g ? [
      el("div", { class: "fd-title" }, el("span", { class: "big", text: g.name }), el("span", { class: "file", text: g.file })),
      g.error ? el("div", { class: "warn", text: g.error }) : null,
      el("div", { class: "desc", text: g.description || "（没有说明）" }),
      el("h4", { text: "参数" }), el("div", { class: "chips2" }, (g.params || []).map((p) => el("span", { class: "tag", text: p }))),
      el("h4", { text: "用到它的流程" }), el("div", { class: "chips2" }, g.used_by.length ? g.used_by.map((f) => el("button", { class: "ghost small", text: f, onclick: () => flOpenRef({ t: "flow", id: f }, false) })) : el("span", { class: "muted small", text: "没有" })),
      el("p", { class: "muted small", text: "片段要和参数一起才能展开。点上面的流程，看它在流程里的样子。" })] : el("p", { class: "muted", text: "片段已不存在。" }));
    side = el("aside", { class: "fd-side" }, el("p", { class: "muted small", text: "片段由流程通过 use: 引用。" }));
  } else {
    const f = o.flows.find((x) => x.flow === FL.flow);
    const gr = FL.graph;
    const projSel = el("select", { class: "small", onchange: (e) => { FL.project = e.target.value || null; if (FL.project) localStorage.setItem("myorch.project", FL.project); flRefreshGraph(); } },
      el("option", { value: "", text: "（不选项目）" }), (FL.projects || []).map((p) => el("option", { value: p.name, text: p.name })));
    projSel.value = FL.project || "";
    const presetSel = f?.presets_by ? el("select", { class: "small", onchange: (e) => { FL.inputs = { ...FL.inputs, [f.presets_by]: e.target.value }; flRefreshGraph(); } },
      f.presets.map((p) => el("option", { value: p, text: p }))) : null;
    if (presetSel) presetSel.value = FL.inputs[f.presets_by] || f.defaults?.[f.presets_by] || f.presets[0];
    main = el("section", { class: "fd-main", onclick: () => { FL.sel = null; flRenderMain(); } }, f ? [
      el("div", { class: "fd-title" }, el("span", { class: "big", text: f.flow }), el("span", { class: "file", text: f.file }),
        f.doc ? el("span", { class: "muted small", text: `· 说明 ${f.doc}` }) : null, el("span", { class: "spacer" }), flLive()),
      f.description ? el("div", { class: "desc", text: f.description }) : null,
      el("div", { class: "fd-controls" },
        el("label", {}, "项目", projSel),
        presetSel ? el("label", {}, f.presets_by, presetSel) : null,
        f.uses?.length ? el("span", { class: "uses" }, "片段 ", f.uses.map((u) => el("span", { class: "tag frag", text: u.replace(/^lib\//, "").replace(/\.yaml$/, "") }))) : null),
      gr?.error ? el("div", { class: "warn", text: gr.error }) : null,
      gr?.tree ? defTree(gr.tree, (s) => { FL.sel = s; flRenderMain(); }, FL.sel?.id) : null] : el("p", { class: "muted", text: `没有流程 ${FL.flow || ""}。` }));
    side = el("aside", { class: "fd-side" }, defDetail(FL.sel));
  }
  const keep = pane.querySelector(".fd-main")?.scrollTop || 0;
  pane.replaceChildren(main, side);
  const m = pane.querySelector(".fd-main"); if (m) m.scrollTop = keep;
}

// Flows grouped by project (the flow's `projects`; none = 通用), each group in role order. The group of
// the selected project, and the one holding the selected flow, start open; a click toggles and is kept.
function flGroups(flows) {
  const rank = (k) => { const i = (FL.projects || []).findIndex((p) => p.name === k); return k === "" ? 1e9 : i < 0 ? 1e6 : i; };
  const by = new Map();
  for (const f of flows) for (const k of (f.projects?.length ? f.projects : [""])) { if (!by.has(k)) by.set(k, []); by.get(k).push(f); }
  return [...by.entries()].sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b)).map(([key, fs]) => {
    fs.sort((a, b) => (a.order ?? 99) - (b.order ?? 99) || a.flow.localeCompare(b.flow));
    const pfx = fs.map((f) => (f.flow.match(/^([a-z0-9]+-)/) || [])[1]);
    const repo = (FL.projects || []).find((p) => p.name === key)?.repo;
    return { key, flows: fs, prefix: key && pfx.every((p) => p && p === pfx[0]) ? `${pfx[0]}*` : "",
      title: key ? `项目 ${key}${repo ? `（${repo}）` : ""}` : "不限项目的流程" };
  });
}

function flGroup(key, name, prefix, items, openByDefault, title) {
  const open = FL.groups[key] ?? openByDefault;
  const body = el("div", { class: "list grp-body", hidden: !open }, items);
  const head = el("div", { class: `grp${open ? " open" : ""}`, title, "data-open": open ? "1" : "",
    onclick: () => { FL.groups[key] = body.hidden; body.hidden = !body.hidden; head.classList.toggle("open", !body.hidden); head.dataset.open = body.hidden ? "" : "1"; } },
    el("span", { class: "caret", text: "▸" }), el("span", { class: "gname", text: name }),
    prefix ? el("span", { class: "pfx", text: prefix }) : null, el("span", { class: "n", text: String(items.length) }));
  return el("div", { class: "grpbox" }, head, body);
}

// The filter box: hide what does not match, open the groups that have matches, hide the empty ones.
// Done in place (no redraw) so typing keeps the focus; flRenderList calls it again after a redraw.
function flFilter(list) {
  const q = (FL.filter || "").trim().toLowerCase();
  for (const box of list.querySelectorAll(".grpbox")) {
    const head = box.querySelector(".grp"), body = box.querySelector(".grp-body");
    let n = 0;
    for (const it of body.querySelectorAll(".item")) { const hit = !q || it.dataset.q.includes(q); it.hidden = !hit; n += hit; }
    box.hidden = !!q && n === 0;
    body.hidden = q ? n === 0 : !head.dataset.open;
    head.classList.toggle("open", !body.hidden);
    head.querySelector(".n").textContent = q ? `${n}/${body.querySelectorAll(".item").length}` : String(body.querySelectorAll(".item").length);
  }
}

async function flRefreshGraph() {
  try { await flLoadGraph(); } catch (e) { FL.graph = { error: e.message }; }
  if (FL.sel) {  // keep the selection when the step still exists
    const all = (FL.graph?.tree || []).flatMap((t) => (t.kind === "loop" ? [t, ...t.steps, ...(t.reflect ? [t.reflect] : [])] : [t]));
    FL.sel = all.find((x) => x.id === FL.sel.id) || null;
  }
  flRender();
}

// Open a flow's definition in a tab (from a run's header, the new-run preview, a #flow= link).
async function openFlows(flow) {
  try { await flEnsure(); } catch (e) { alert(`读取流程失败：${e.message}`); return; }
  const id = flow || FL.flow || FL.overview.flows[0]?.flow;
  if (id) flOpenRef({ t: "flow", id }, false);
}

// ---- an eval case: the person keeps the findings that are real problems ----------------------------

function caseView(c) {
  const picked = new Set(c.status === "confirmed" ? c.keep : c.findings.map((_, i) => i));
  const row = (f, i) => {
    const box = el("input", { type: "checkbox" });
    box.checked = picked.has(i);
    box.addEventListener("change", () => (box.checked ? picked.add(i) : picked.delete(i)));
    const where = (f.locations || []).map((l) => `${l.file}${l.line ? `:${l.line}` : ""}`).join("  ") || `${f.file}${f.line ? `:${f.line}` : ""}`;
    return el("label", { class: "evf" }, box, el("div", {},
      el("div", { class: "evh" }, el("span", { class: `tag${f.severity === "Important" ? " cond" : ""}`, text: f.severity }), el("b", { text: f.title || "" })),
      el("div", { class: "evw", text: where }),
      f.evidence ? el("div", { class: "eve", text: f.evidence }) : null));
  };
  const decide = async (keep) => {
    try { await api(`/api/evals/${encodeURIComponent(c.id)}`, { method: "POST", body: { keep } }); FL.cases = await api("/api/evals"); flRender(); }
    catch (e) { alert(e.message); }
  };
  const main = el("section", { class: "fd-main" },
    el("div", { class: "fd-title" }, el("span", { class: "big", text: c.id }), el("span", { class: "file", text: `${c.project} · ${c.repo}` })),
    el("div", { class: "desc", text: c.origin === "git"
      ? `这是复盘 ${c.id} 找到的引入问题的提交。下面是复盘认为 reviewer 应该报出的发现，勾选确实成立的；收入评测集后才计分。`
      : "这是一次历史 review 第 1 轮看过的改动。勾选其中确实是问题的发现；收入评测集后，用它给 reviewer 打分（myorch eval run / report）。" }),
    Object.keys(c.replay_scores || {}).length ? el("div", {}, el("h4", { text: "回放" }), kv(Object.entries(c.replay_scores).map(([phase, r]) => [
      { before: "采纳前（当时的知识库）", after: "采纳后（新的知识库）" }[phase] || phase,
      el("span", {}, el("a", { href: `#run=${encodeURIComponent(r.run)}`, text: r.run }),
        r.pending ? " · 还在跑" : ` · 拦住 ${r.caught}/${r.expected}，其他发现 ${r.extra} 条${c.status === "confirmed" ? "" : "（按全部期望的发现算，确认后按勾选的算）"}`)]))) : null,
    kv([["状态", STATUS_TEXT[c.status] || c.status], ["比较范围", `${c.base_ref} @ ${c.base_sha.slice(0, 8)} .. ${c.head_sha.slice(0, 8)}`],
      ["head", c.head_guessed ? "推断的：分支上的第一个提交。下面的提交列表里核对一下" : "当时记录的"], ["来源流程", c.flow]]),
    el("h4", { text: c.origin === "git" ? "引入问题的提交" : "分支上的提交" }), el("pre", { class: "small", text: (c.commits || []).join("\n") || "（没有）" }),
    el("h4", { text: `发现 · ${c.findings.length}` }), el("div", { class: "evlist" }, c.findings.map(row)),
    el("div", { class: "evbar" },
      el("button", { text: "收入评测集（勾选的是真问题）", onclick: () => decide([...picked]) }),
      el("button", { class: "ghost danger", text: "都不是真问题，不要这个案例", onclick: () => decide([]) })));
  const side = el("aside", { class: "fd-side" }, el("h4", { text: "需求" }),
    fileView({ path: "requirement.md", title: "当时的需求文档", text: c.requirement || "（没有）", html: null }));
  return [main, side];
}
const STATUS_TEXT = { pending: "待确认", confirmed: "已收入评测集", rejected: "不要" };

// ---- preview under the new-run form -----------------------------------------------------------

let previewTimer = null, previewKey = null;
function previewInputs() { return formValues(); }  // newrun.js
function schedulePreview(force = false) {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(() => renderPreview(force), 250);
}
async function renderPreview(force) {
  const box = $("#sfPreview");
  if (!box || !S.newMode) return;
  const flow = NR.flow;
  if (!flow) return;
  const project = NR.project || null, inputs = previewInputs();
  const key = JSON.stringify([flow, project, inputs, FL.version]);
  if (key === previewKey && !force) return;
  previewKey = key;
  let g;
  try { g = await api(flQuery(flow, project, inputs)); } catch (e) { g = { error: e.message }; }
  FL.version = g.version || FL.version;
  const count = (g.tree || []).reduce((n, t) => n + (t.kind === "loop" ? t.steps.length : 1), 0);
  box.replaceChildren(
    el("div", { class: "pv-head" }, el("b", { text: "这次会跑的步骤" }), g.tree ? el("span", { class: "muted small", text: `${count} 步` }) : null,
      el("span", { class: "spacer" }), el("button", { type: "button", class: "ghost small", text: "看流程定义", onclick: () => openFlows(flow) })),
    el("div", { class: "pv-live muted small" }, el("span", { class: "livetag" }, el("span", { class: "dot" }), `实时跟 ${flow} 的定义同步`)),
    g.error ? el("div", { class: "warn", text: g.error }) : defOutline(g.tree, () => openFlows(flow)),
    el("div", { class: "muted small pv-foot", text: "点一步看它的定义（条件、模型、提示词）。" }));
}

// ---- live: redraw when flows/ changes --------------------------------------------------------

async function flPoll() {
  const dlgOpen = !!flCur() || (S.view === "flows" && !document.body.classList.contains("side-off")), pvOpen = S.newMode;
  if (!dlgOpen && !pvOpen) return;
  let v;
  try { v = (await api("/api/flowdefs/version")).version; } catch { return; }
  if (v === FL.version) return;
  FL.version = v;
  if (dlgOpen) { await flLoadOverview(); await flRefreshGraph(); }
  if (pvOpen) schedulePreview(true);
}

document.addEventListener("newrun:change", () => schedulePreview());  // newrun.js: project, task or a field changed
document.addEventListener("newrun:open", () => schedulePreview(true));
setInterval(flPoll, FL_POLL_MS);


// ---- registering a knowledge source (myorch/knowledge.py register / check / unregister) ------------

function kbKey(k) { return `${k.project || ""}/${k.name}`; }

function kbOpenForm(k) {
  FL.kbForm = k ? { edit: true, name: k.name, project: k.project || "", kind: k.kind, fields: { ...(k.fields || {}) } }
    : { edit: false, name: "", project: "", kind: "dir", fields: {} };
  FL.kbCheck = null;
  if (refIs("kbform")) flRender(); else flOpenRef({ t: "kbform" }, false);
}

async function kbRemove(k) {
  if (!confirm(`删除知识源 ${k.name} 的登记？已有的快照保留，之后启动的运行不能再挂它。`)) return;
  try {
    await api("/api/knowledge/remove", { method: "POST", body: { name: k.name, project: k.project || null } });
    closeTab(tabKey({ kind: "flow", ref: { t: "kb", id: kbKey(k) } })); await flLoadOverview(); flRender();
  } catch (e) { alert(e.message); }
}

const KB_FIELDS = {  // [field, label, hint, kind of input]
  dir: [["path", "文件夹", "本机路径，可以用 ~ 和 ${ENV}。不要直接用 Obsidian vault，用你整理过的文件夹"],
    ["include", "只要这些文件", "glob，逗号分隔，例如 **/*.md；不写表示全部"], ["exclude", "不要这些文件", "glob，逗号分隔"],
    ["index", "先读", "agent 先读的文件，例如 README.md"]],
  git: [["repo", "仓库", "owner/name"], ["ref", "分支或标签", "main"], ["provider", "OpenShell provider", "能克隆这个仓库的 provider"],
    ["image", "镜像（可选）", "带 git 和 python3；不写用项目的开发镜像"], ["include", "只要这些文件", "glob，逗号分隔"],
    ["exclude", "不要这些文件", "glob，逗号分隔"], ["index", "先读", "例如 README.md"]],
  mcp: [["url", "MCP 地址", "https://…，例如 https://mcp.atlassian.com/v1/mcp"], ["provider", "OpenShell provider", "在网关上加 token 的 provider"],
    ["token_env", "token 占位变量", "provider 的环境变量名，例如 ATLASSIAN_TOKEN"], ["auth", "认证方式", "", "auth"],
    ["email", "邮箱（basic 时）", "token 所属账号的邮箱"], ["tools", "允许的工具", "逗号分隔。只有这些能调用，其余一律禁用。只写读的工具"],
    ["index", "用法", "告诉 agent 什么时候用哪个工具，例如：读工单用 getJiraIssue"]],
};

function kbFormView(f) {
  const projects = (S.projects || []).filter((p) => !p.error).map((p) => p.name);
  if (!S.projects) api("/api/projects").then((ps) => { S.projects = ps; flRender(); }).catch(() => {});
  const read = () => {
    const box = $("#kbForm");
    const data = { kind: box.querySelector('[name="kind"]').value };
    box.querySelectorAll("[data-f]").forEach((e) => { data[e.dataset.f] = e.type === "checkbox" ? e.checked : e.value; });
    return { name: box.querySelector('[name="name"]').value.trim(), project: box.querySelector('[name="project"]').value || null, data };
  };
  const field = ([k, label, hint, type]) => {
    const v = f.fields[k];
    const input = type === "auth"
      ? el("select", { "data-f": k }, el("option", { value: "bearer", text: "bearer：服务账号的 API key" }), el("option", { value: "basic", text: "basic：个人 API token + 邮箱" }))
      : el("input", { "data-f": k, placeholder: hint });
    input.value = Array.isArray(v) ? v.join(", ") : v ?? (type === "auth" ? "bearer" : "");
    return el("label", {}, label, input);
  };
  const kindSel = el("select", { name: "kind", disabled: f.edit }, ["dir", "git", "mcp"].map((k) => el("option", { value: k, text: { dir: "本机文件夹", git: "git 仓库", mcp: "MCP 服务" }[k] })));
  kindSel.value = f.kind;
  kindSel.addEventListener("change", () => { FL.kbForm = { ...f, kind: kindSel.value, fields: {} }; FL.kbCheck = null; flRender(); });
  const nameIn = el("input", { name: "name", placeholder: "小写字母、数字和 -，例如 mdm-docs", disabled: f.edit });
  nameIn.value = f.name;
  const projSel = el("select", { name: "project", disabled: f.edit }, el("option", { value: "", text: "全局（所有项目都能用）" }),
    ...projects.map((p) => el("option", { value: p, text: `只给项目 ${p}` })));
  projSel.value = f.project || "";
  const ro = el("input", { type: "checkbox", "data-f": "token_read_only" });
  ro.checked = !!f.fields.token_read_only;
  const result = FL.kbCheck;
  const act = async (op) => {
    const b = read();
    FL.kbForm = { ...f, name: b.name, project: b.project || "", fields: b.data };
    try {
      const r = await api(`/api/knowledge/${op}`, { method: "POST", body: { ...b, replace: f.edit } });
      FL.kbCheck = { ...r, op };
      if (op === "register") {
        await flLoadOverview(); FL.kbForm = null;
        closeTab(tabKey({ kind: "flow", ref: { t: "kbform" } }));
        flOpenRef({ t: "kb", id: `${b.project || ""}/${b.name}` }, false);
        return;
      }
    } catch (e) { FL.kbCheck = { error: e.message }; }
    flRender();
  };
  const main = el("section", { class: "fd-main" },
    el("div", { class: "fd-title" }, el("span", { class: "big", text: f.edit ? `编辑 ${f.name}` : "登记知识源" }),
      el("span", { class: "file", text: "写进 myorch-knowledge，并提交一次 git" })),
    el("div", { class: "kbform", id: "kbForm" },
      el("label", {}, "名字", nameIn), el("label", {}, "范围", projSel), el("label", {}, "类型", kindSel),
      ...KB_FIELDS[f.kind].map(field),
      f.kind === "mcp" ? el("label", { class: "check" }, ro, "我确认这个 token 只能读（不能建、改、删任何东西）") : null),
    el("div", { class: "evbar" },
      el("button", { class: "ghost", text: "检查", onclick: () => act("check") }),
      el("button", { text: f.edit ? "保存修改" : "登记", onclick: () => act("register") }),
      el("button", { class: "ghost", text: "取消", onclick: () => { FL.kbForm = null; closeTab(tabKey({ kind: "flow", ref: { t: "kbform" } })); } })),
    result?.error ? el("div", { class: "warn", text: result.error }) : null,
    result && !result.error ? el("div", {},
      result.problems?.length ? el("div", { class: "warn", text: `问题：${result.problems.join("；")}` }) : el("div", { class: "ok small", text: "没有问题。" }),
      result.files !== undefined ? el("div", { class: "small", text: `同步时会收进 ${result.files} 个文件，共 ${(result.bytes / 1024).toFixed(1)} KB。` }) : null,
      result.findings?.length ? el("pre", { class: "small", text: result.findings.join("\n") }) : null,
      result.sample?.length ? el("pre", { class: "small", text: result.sample.join("\n") + (result.files > result.sample.length ? "\n…" : "") }) : null,
      result.yaml ? fileView({ path: "source.yaml", title: "登记文件", text: result.yaml, html: null }) : null) : null);
  const side = el("aside", { class: "fd-side" }, el("p", { class: "muted small", text:
    f.kind === "mcp" ? "MCP 知识源：agent 只能调用「允许的工具」里的工具，其余由 myorch-mcp 在启动时禁用；读不到工具列表时整个服务器不可用。token 存在 OpenShell 网关里，sandbox 只看到占位符。Atlassian 的 MCP 要组织管理员先打开 API token 访问。"
    : f.kind === "git" ? "git 知识源：在一次性的 sandbox 里用 provider 克隆，过滤、扫描密钥后存成快照。发起运行时，快照超过 1 小时就重新克隆。"
    : "文件夹知识源：发起运行时自动同步。会扫描密钥，扫到就不同步、运行不启动。「检查」会先告诉你会收进哪些文件。" }));
  return [main, side];
}
