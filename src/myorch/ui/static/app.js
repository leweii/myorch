// myorch console. The middle of the page is the selected run's conversation: what you write goes
// verbatim to the run (the first message is the request; later ones answer the step that asked).
// Untrusted text (run content) is always inserted as textContent; only server-rendered, sanitised
// markdown (field `html`) and highlight.js output (escaped by the library) go through innerHTML.
"use strict";

const ICON = { pending: "·", running: "▶", waiting_approval: "⏸", waiting_input: "?", succeeded: "✓", failed: "✗",
  rejected: "✗", blocked: "-", skipped: "↷", cancelled: "⊘", RUNNING: "▶", COMPLETED: "✓", FAILED: "✗",
  CANCELED: "⊘", TERMINATED: "✗", TIMED_OUT: "✗" };
const S = { run: null, step: null, detail: null, sending: false, tab: "step", project: "", q: "", newMode: false };

const $ = (sel) => document.querySelector(sel);
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined) e.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return e;
}
const st = (s) => el("span", { class: `st ${s}`, title: s, text: ICON[s] || "·" });

async function api(path, opts = {}) {
  const init = { ...opts, headers: { ...(opts.headers || {}) } };
  if (init.method && init.method !== "GET") init.headers["X-Myorch-UI"] = "1";
  if (init.body && typeof init.body !== "string") { init.body = JSON.stringify(init.body); init.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, init);
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text();
  if (!r.ok && !(data && data.status)) throw new Error((data && data.error) || `HTTP ${r.status}`);
  return data;
}
const fmtTime = (iso) => { if (!iso) return ""; try { const d = new Date(iso); if (isNaN(d)) return ""; return `${d.getMonth() + 1}-${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; } catch { return ""; } };

// ---- top: run picker and inbox; left: one run's flow ---------------------------------------------

const EXEC = { agent: ["AI", "agent"], script: ["SH", "脚本"], approval: ["USR", "人工审批"], host: ["HOST", "宿主机动作"] };
const ACTIVE = new Set(["running", "waiting_input", "waiting_approval"]);
S.loopRound = {};

function toggle(panel, show) { panel.hidden = show === undefined ? !panel.hidden : !show; }
document.addEventListener("click", (e) => {
  for (const [btn, panel] of [["#runPickBtn", "#runPicker"], ["#inboxBtn", "#inboxPanel"], ["#userBtn", "#userPanel"]]) {
    if (!e.target.closest(btn) && !e.target.closest(panel)) $(panel).hidden = true;
  }
});

async function loadRuns() {
  let data;
  const q = S.q;
  try { data = await api(`/api/runs?limit=80${S.project ? `&project=${encodeURIComponent(S.project)}` : ""}${q ? `&label=${encodeURIComponent(q)}` : ""}`); }
  catch (e) { $("#runs").replaceChildren(el("div", { class: "muted small", text: `读取运行列表失败：${e.message}。请检查 Temporal 是否在运行。` })); return; }
  const warn = $("#volumeWarn");
  warn.textContent = data.volume_error ? "读不到运行数据卷，Docker 可能没有运行。现在只显示 Temporal 里的运行。文档、日志、发起人和开始时间暂时看不到。" : "";
  warn.title = data.volume_error || "";

  $("#inbox").replaceChildren(...(data.inbox.length ? data.inbox.map((w) => el("div", { class: "item", onclick: () => { toggle($("#inboxPanel"), false); selectRun(w.run_id, w.step); } },
    el("div", { class: "line1" }, st(w.kind === "approval" ? "waiting_approval" : w.kind === "watchdog" ? "failed" : "waiting_input"),
      el("span", { class: "id", text: `${w.project || "-"} · ${w.run_id}` })),
    el("div", { class: "sub", text: `${w.step}：${w.kind === "approval" ? "等审批" : w.kind === "loop" ? "循环已跑满轮数，等你决定" : w.kind === "watchdog" ? "watchdog 提醒" : "在提问"}` }),
    el("div", { class: "q", text: (w.text || "").slice(0, 240) })))
    : [el("div", { class: "muted small", text: "没有等你处理的事。" })]));
  $("#inboxCount").textContent = data.inbox.length || "";
  $("#inboxBtn").classList.toggle("hot", data.inbox.length > 0);

  if (q !== S.q) return;  // the search changed while this one was on its way
  $("#runCount").textContent = q ? `找到 ${data.runs.length} 个` : `${data.runs.length} 个`;
  S.lastRuns = data;
  renderRunTree(data);
  if (!S.run && !S.newMode && data.runs.length) {  // first visit: the run that is moving, else the newest
    const pick = data.inbox[0]?.run_id || data.runs.find((r) => r.status === "RUNNING")?.run_id || data.runs[0].run_id;
    selectRun(pick, data.inbox[0]?.run_id === pick ? data.inbox[0].step : null);
  }
}

// The run picker as a tree: one node per project (newest activity first), its runs indented below.
// A node opens by default when it holds the shown run or a running one; the user's own toggles are
// remembered (myorch.runs.open: project -> true/false).
const NO_PROJECT = "（无项目）";
function runTreeState() { try { return JSON.parse(localStorage.getItem("myorch.runs.open") || "{}"); } catch { return {}; } }
function renderRunTree(data) {
  const groups = new Map();
  for (const r of data.runs) {
    const k = r.project || NO_PROJECT;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(r);
  }
  const waitingRuns = new Set(data.inbox.map((w) => w.run_id));
  const saved = runTreeState();
  const nodes = [];
  for (const [project, runs] of groups) {
    const running = runs.filter((r) => r.status === "RUNNING").length;
    const waiting = runs.filter((r) => waitingRuns.has(r.run_id)).length;
    const open = S.q ? true : saved[project] ?? (runs.some((r) => r.run_id === S.run) || running > 0 || groups.size === 1);
    const head = el("button", { type: "button", class: `tnode${open ? " open" : ""}`, "aria-expanded": String(open),
      onclick: (e) => { e.stopPropagation(); const s = runTreeState(); s[project] = !open; localStorage.setItem("myorch.runs.open", JSON.stringify(s)); renderRunTree(S.lastRuns); } },
      el("span", { class: "caret", text: open ? "▾" : "▸" }), el("span", { class: "pname", text: project }),
      el("span", { class: "tcount", text: String(runs.length) }),
      running ? el("span", { class: "tag", text: `${running} 运行中` }) : null,
      waiting ? el("span", { class: "tag hot", text: `${waiting} 等你` }) : null);
    const kids = open ? el("div", { class: "tkids" }, runs.map((r) => el("div", { class: `item${r.run_id === S.run ? " active" : ""}`,
      onclick: () => { toggle($("#runPicker"), false); selectRun(r.run_id); } },
      el("div", { class: "line1" }, st(r.status), el("span", { class: "id", text: r.run_id }), ...labelChips(r.labels)),
      el("div", { class: "sub", text: [fmtTime(r.requested_at), r.initiator,
        r.progress ? `${r.progress.done}/${r.progress.total} · ${r.progress.current.join(", ")}` : ""].filter(Boolean).join(" · ") || "-" })))) : null;
    nodes.push(el("div", { class: "tgroup" }, head, kids));
  }
  $("#runs").replaceChildren(...nodes);
}

// Labels (Jira tickets …) as small chips; a click searches for that label.
function labelChips(labels) {
  return (labels || []).map((l) => el("span", { class: "tag label", title: `搜「${l}」`, text: l,
    onclick: (e) => { e.stopPropagation(); setSearch(l); toggle($("#runPicker"), true); } }));
}
function setSearch(q) { S.q = q.trim(); $("#runSearch").value = S.q; loadRuns(); }

async function loadProjects() {
  try {
    const ps = await api("/api/projects");
    const sel = $("#projectFilter");
    for (const p of ps) sel.append(el("option", { value: p.name, text: p.name }));
  } catch { /* ignore */ }
}

function selectRun(runId, step = null) {
  const changed = runId !== S.run;
  S.run = runId; S.step = step; S.detail = null; S.stepKey = null;
  if (changed) { S.loopRound = {}; S.autoStep = !step; S.convKey = null; $("#messages").replaceChildren(); $("#sendError").textContent = ""; }
  setNewMode(false);
  $("#runPickLabel").textContent = runId;
  history.replaceState(null, "", `#run=${encodeURIComponent(runId)}${step ? `&step=${encodeURIComponent(step)}` : ""}`);
  loadDetail();
  loadDocs();
  S.changesFor = null;
  if (S.tab === "changes") loadChanges();
  loadRuns();
}

async function loadDetail() {
  if (!S.run) return;
  const runId = S.run;
  let d;
  try { d = await api(`/api/runs/${encodeURIComponent(runId)}`); } catch (e) {
    $("#flow").replaceChildren(el("div", { class: "muted small", text: e.message })); return;
  }
  if (runId !== S.run) return;
  S.detail = d;
  if (S.autoStep && !S.step) {  // highlight what needs attention or is moving now
    const all = allInstances(d);
    const hit = all.find((i) => i.status.startsWith("waiting")) || all.find((i) => i.status === "running");
    const loopWait = d.tree.find((t) => t.kind === "loop" && t.status === "waiting_input");
    if (loopWait) S.step = loopWait.id; else if (hit) S.step = hit.id;
    S.autoStep = false;
  }
  renderRunHead(d);
  renderFlow(d);
  renderConversation(d);
  if (S.step) {
    const key = S.step + JSON.stringify(findInstance(S.step) || d.tree.find((t) => t.id === S.step) || null);
    if (key !== S.stepKey) { S.stepKey = key; renderStep(); }
  }
}

function allInstances(d) { return d.tree.flatMap((t) => (t.kind === "step" ? t.instances : t.rounds.flatMap((r) => r.instances))); }

function renderRunHead(d) {
  const m = d.mandate || {};
  const all = allInstances(d);
  const done = all.filter((i) => i.status === "succeeded" || i.status === "skipped").length;
  $("#runHead").replaceChildren(...[
    el("div", { class: "rh1" }, st(d.status), el("b", { text: ` ${d.flow}` }), el("span", { class: "muted", text: ` · ${d.project || "无项目"}` })),
    el("div", { class: "rid", text: d.run_id }),
    (d.labels || []).length ? el("div", { class: "labels small", title: "标签：在运行列表里按它搜" }, ...labelChips(d.labels)) : null,
    el("div", { class: "muted small", text: `${m.initiator ? `发起人：${m.initiator} · ` : ""}${fmtTime(m.requested_at)} · ${done}/${all.length} 步已完成` }),
    Object.keys(d.inputs || {}).length ? el("div", { class: "inputs small" }, Object.entries(d.inputs).filter(([, v]) => v !== "").map(([k, v]) => el("div", {}, el("span", { class: "muted", text: `${k} ` }), String(v).slice(0, 160)))) : null,
    (d.knowledge || []).length ? el("div", { class: "kmounted small", title: "这次运行挂载的知识库，启动时固定" }, el("span", { class: "muted", text: "知识库" }),
      d.knowledge.map((k) => el("span", { class: `tag k-${k.kind}`, title: k.kind === "mcp" ? k.url : k.version, text: k.kind === "mcp" ? `${k.name} · mcp` : `${k.name} · ${k.version.replace("sha256:", "").slice(0, 7)}` }))) : null].filter(Boolean));
}

// A node's status: its instances' (worst first), or pending when nothing was created yet.
function aggStatus(insts) {
  if (!insts.length) return "pending";
  for (const s of ["waiting_approval", "waiting_input", "running", "failed", "rejected", "blocked", "cancelled", "pending"]) if (insts.some((i) => i.status === s)) return s;
  return insts.every((i) => i.status === "skipped") ? "skipped" : "succeeded";
}

function stepCell(meta, insts, idForClick) {
  const status = aggStatus(insts);
  const [icon, label] = EXEC[meta.exec] || ["·", ""];
  const one = insts.length === 1 ? insts[0] : null;
  const selected = S.step != null && (insts.some((i) => i.id === S.step) || idForClick === S.step);
  const model = (one?.model || meta.model || "").replace("claude-", "");
  const cell = el("div", { class: `cell s-${status}${ACTIVE.has(status) ? " live" : ""}${selected ? " sel" : ""}`, title: `${label}${meta.when ? `\n条件：${meta.when}` : ""}`,
    onclick: () => selectStep(one ? one.id : (insts[0]?.id || idForClick)) },
    el("div", { class: "c1" }, el("span", { class: `ic k-${meta.exec}`, text: icon }), el("span", { class: "nid", text: meta.id }), st(status)),
    el("div", { class: "c2" }, model ? el("span", { class: "tag", text: model }) : null,
      meta.when ? el("span", { class: "tag cond", text: "条件" }) : null,
      meta.interaction === "allowed" ? el("span", { class: "tag", text: "可提问" }) : null,
      one && one.attempts > 1 ? el("span", { class: "tag", text: `第 ${one.attempts} 次尝试` }) : null),
    one?.summary ? el("div", { class: "c3", text: one.summary }) : null);
  if (insts.length > 1 || meta.fanout > 1) {
    cell.append(el("div", { class: "chips" }, insts.map((i) => el("span", { class: `chip s-${i.status}${i.id === S.step ? " sel" : ""}`, title: `${i.id}${i.summary ? `\n${i.summary}` : ""}`,
      onclick: (e) => { e.stopPropagation(); selectStep(i.id); } }, st(i.status), ` x${i.replica || "?"}`))));
  }
  return cell;
}

function rows(items, cellOf) {
  const byStage = new Map();
  for (const it of items) { const k = it._stage; if (!byStage.has(k)) byStage.set(k, []); byStage.get(k).push(it); }
  const keys = [...byStage.keys()].sort((a, b) => a - b);
  return keys.map((k, n) => el("div", { class: "stage" },
    el("div", { class: "rail" }, el("span", { class: "num", text: String(n + 1) }), n < keys.length - 1 ? el("span", { class: "line" }) : null),
    el("div", { class: `cells${byStage.get(k).length > 1 ? " par" : ""}` }, byStage.get(k).map(cellOf),
      byStage.get(k).length > 1 ? el("div", { class: "parnote", text: "并行" }) : null)));
}

function loopCell(t) {
  const rounds = t.rounds.map((r) => r.round);
  const shown = S.loopRound[t.id] ?? (rounds.length ? rounds[rounds.length - 1] : null);
  const insts = (t.rounds.find((r) => r.round === shown) || { instances: [] }).instances;
  const status = t.status;
  const box = el("div", { class: `loop s-${status}${ACTIVE.has(status) ? " live" : ""}${S.step === t.id ? " sel" : ""}` },
    el("div", { class: "lhead", onclick: () => selectStep(t.id) }, el("span", { class: "ic k-loop", text: "LOOP" }), el("b", { text: t.id }), st(status),
      el("span", { class: "muted small", title: `结束条件：${t.until}`, text: ` 第 ${t.round}/${t.max_rounds} 轮` })),
    rounds.length > 1 ? el("div", { class: "rtabs" }, rounds.map((r) => el("button", { class: `rtab${r === shown ? " on" : ""}`, text: `第 ${r} 轮`,
      onclick: () => { S.loopRound[t.id] = r; renderFlow(S.detail); } }))) : null,
    t.question ? el("div", { class: "lq small", text: `等你决定：${t.question.question.slice(0, 160)}` }) : null,
    el("div", { class: "inner" }, rows(t.steps.map((s) => ({ ...s, _stage: t.stages[s.id] })),
      (s) => stepCell(s, insts.filter((i) => i.step === s.id), null))));
  return box;
}

function renderFlow(d) {
  $("#flow").replaceChildren(...rows(d.tree.map((t) => ({ ...t, _stage: t.stage })),
    (t) => (t.kind === "loop" ? loopCell(t) : stepCell(t, t.instances, t.id))),
    el("div", { class: "legend small muted" }, Object.entries(EXEC).map(([k, [i, l]]) => el("span", { class: "lg" }, el("span", { class: `ic k-${k}`, text: i }), l))));
}

function findInstance(id) {
  if (!S.detail) return null;
  for (const t of S.detail.tree) {
    const all = t.kind === "step" ? t.instances : t.rounds.flatMap((r) => r.instances);
    const hit = all.find((i) => i.id === id);
    if (hit) return hit;
  }
  return null;
}

function selectStep(id) {
  S.step = id; S.stepKey = null; showTab("step");
  history.replaceState(null, "", `#run=${encodeURIComponent(S.run)}&step=${encodeURIComponent(id)}`);
  if (S.detail) { renderFlow(S.detail); S.stepKey = id; renderStep(); }
  loadDetail();
}

function kv(rows) { return el("div", { class: "kv" }, rows.filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => el("div", {}, el("span", { class: "k", text: k }), v instanceof Node ? el("span", {}, v) : el("span", { text: String(v) })))); }

function renderStep() {
  const pane = $("#tab-step");
  if (LO.key && LO.key !== `${S.run}/${S.step}`) stopLiveOutput();  // another step or run is shown now
  const i = findInstance(S.step);
  const loop = S.detail && S.detail.tree.find((t) => t.kind === "loop" && t.id === S.step);
  if (loop) {
    pane.replaceChildren(...[el("h4", { text: `循环 ${loop.id}` }), kv([["状态", loop.status], ["轮次", `${loop.round}/${loop.max_rounds}`], ["结束条件", loop.until], ["错误", loop.error]]),
      loop.question ? el("pre", { text: loop.question.question + "\n" + loop.question.options.map((o, n) => `${n + 1}. ${o}`).join("\n") }) : null].filter(Boolean));
    return;
  }
  if (!i) {
    const meta = S.detail && S.detail.tree.find((t) => t.id === S.step);
    pane.replaceChildren(meta ? el("div", {}, el("h4", { text: meta.id }), kv([["状态", "还没开始"], ["执行者", (EXEC[meta.exec] || [])[1]], ["模型", meta.model], ["条件", meta.when], ["依赖", (meta.needs || []).join(", ")]]))
      : el("p", { class: "muted", text: "在左侧选一个步骤。" }));
    return;
  }
  const kids = [el("h4", {}, st(i.status), ` ${i.id}`),
    kv([["状态", i.status], ["模型", i.model], ["尝试次数", i.attempts > 1 ? i.attempts : null], ["对话轮数", i.turns > 1 ? i.turns : null],
      ["退出码", i.exit_code], ["manifest", i.manifest_status !== "ok" ? i.manifest_status : null],
      ["审批", i.approvals_needed ? `${i.approvals.length}/${i.approvals_needed}：${i.approvals.join(", ") || "无"}` : null]])];
  if (i.status === "waiting_approval") kids.push(el("p", {}, el("button", { text: "看改动和 review 结论", onclick: () => showTab("changes") })));
  if (i.error) kids.push(el("h4", { text: "错误" }), el("pre", { text: i.error }));
  if ((i.status === "failed" || i.status === "cancelled") && (S.detail?.status === "failed" || S.detail?.status === "cancelled")) {
    kids.push(el("p", {}, el("button", { class: "ghost", text: "从这一步重新执行", title: "只重新执行这一步，以及被它挡住的后续步骤",
      onclick: () => resumeRun([i.id]) })));
  }
  const live = liveOutputFor(i);
  if (live) kids.push(el("h4", {}, "实时输出 ", el("span", { class: "muted small", text: "（每轮的日志，边跑边显示）" })), live);
  if (i.question) kids.push(el("h4", { text: "提问" }), el("pre", { text: i.question.question + (i.question.options.length ? "\n" + i.question.options.map((o, n) => `${n + 1}. ${o}`).join("\n") : "") }));
  if (i.summary) kids.push(el("h4", { text: "摘要" }), el("div", { class: "small", text: i.summary }));
  if (Object.keys(i.outputs || {}).length) kids.push(el("h4", { text: "outputs" }), fileView({ path: "outputs.json", title: `${i.id} · outputs`, text: JSON.stringify(i.outputs, null, 2), html: null }));
  if (i.notes) kids.push(el("h4", { text: "notes" }), el("div", { class: "small", text: i.notes }));
  const files = el("div", { class: "filelist" });
  const viewer = el("div", { class: "doc" });
  if ((S.detail?.knowledge || []).length && typeof knowledgeUseSection === "function") kids.push(...knowledgeUseSection(i.id));  // knowledge.js
  kids.push(el("h4", { text: "产物和日志" }), el("button", { class: "ghost", text: "读取产物和日志（约 2 秒）", onclick: async (ev) => {
    ev.target.disabled = true;
    try {
      const f = await api(`/api/runs/${encodeURIComponent(S.run)}/steps/${encodeURIComponent(i.id)}`);
      files.replaceChildren(...f.outputs.map((p) => el("div", { class: "f", text: p, onclick: () => showFile(`${f.out_dir}/${p}`, viewer) })),
        el("h4", { text: "日志（最后一轮）" }), fileView({ path: "step.log", title: `${i.id} · 日志`, text: f.log || "（没有日志）", html: null }));
    } catch (e) { files.replaceChildren(el("div", { class: "muted small", text: e.message })); }
    ev.target.disabled = false;
  } }), files, viewer);
  pane.replaceChildren(...kids);
}

// ---- live output of the shown step --------------------------------------------------------------
// The worker copies a running step's log to the event log's output table; /channel/output (SSE) pushes
// it. One stream at a time, for the shown step while it runs. The panel element survives redraws of the
// step pane, so its lines and scroll position stay. The full log is under 「产物和日志」 when it ends.

const LO = { key: "", es: null, box: null, body: null, partial: "", turn: "", stick: true };
const LO_MAX_LINES = 4000;

function liveOutputFor(i) {
  const key = `${S.run}/${i.id}`;
  if (i.status !== "running") {
    stopLiveOutput();  // the step ended: the full log replaces the live view
    return null;
  }
  if (LO.key === key && LO.box) return LO.box;
  stopLiveOutput();
  LO.key = key;
  LO.body = el("div", { class: "liveout" }, el("div", { class: "lo-meta", text: "等待输出…" }));
  LO.body.addEventListener("scroll", () => { LO.stick = LO.body.scrollTop + LO.body.clientHeight >= LO.body.scrollHeight - 24; });
  const box = el("div", { class: "viewer logview" });
  box.append(el("div", { class: "vbar" }, el("span", { class: "vpath", text: `${i.id} · 实时输出` }), el("span", { class: "spacer" }), maxButton(box)), LO.body);
  box.addEventListener("maxchange", () => { if (LO.stick) LO.body.scrollTop = LO.body.scrollHeight; });
  LO.box = box;
  if (window.EventSource) {
    LO.es = new EventSource(`/channel/output?run=${encodeURIComponent(S.run)}&step=${encodeURIComponent(i.id)}`);
    LO.es.addEventListener("output", (m) => {
      let c = null;
      try { c = JSON.parse(m.data); } catch { return; }
      appendLiveOutput(c);
    });
  }
  return LO.box;
}

function stopLiveOutput() {
  if (LO.es) LO.es.close();
  Object.assign(LO, { key: "", es: null, box: null, body: null, partial: "", turn: "", stick: true });
}

function liveLineClass(line) {
  if (line.startsWith("→ ")) return "lo-tool";
  if (line.startsWith("  ✗ ")) return "lo-err";
  if (line.startsWith("  ← ")) return "lo-res";
  if (line.startsWith("· ") || line.startsWith("═══")) return "lo-meta";
  return "";
}

function appendLiveOutput(c) {
  const box = LO.body;
  if (!box) return;
  if (box.firstChild && box.firstChild.textContent === "等待输出…" && box.childElementCount === 1) box.replaceChildren();
  const turn = `${c.attempt}/${c.turn}`;
  if (turn !== LO.turn) {
    if (LO.partial) { box.append(el("div", { text: LO.partial })); LO.partial = ""; }
    if (LO.turn) box.append(el("div", { class: "lo-sep", text: `—— 第 ${c.attempt} 次尝试 · 第 ${c.turn} 轮 ——` }));
    LO.turn = turn;
  }
  const lines = (LO.partial + c.text).split("\n");
  LO.partial = lines.pop();
  const frag = document.createDocumentFragment();
  for (const line of lines) frag.append(el("div", { class: liveLineClass(line), text: line || "\u00a0" }));
  box.append(frag);
  while (box.childElementCount > LO_MAX_LINES) box.firstChild.remove();
  if (LO.stick) box.scrollTop = box.scrollHeight;
}

async function loadDocs() {
  const pane = $("#tab-docs");
  if (!S.run) return;
  const runId = S.run;
  pane.replaceChildren(el("p", { class: "muted small", text: "正在读取 docs/ …" }));
  const viewer = el("div", { class: "doc" });
  try {
    const files = await api(`/api/runs/${encodeURIComponent(runId)}/files?dir=docs`);
    if (runId !== S.run) return;
    pane.replaceChildren(el("h4", { text: `${runId} 的 docs/` }),
      el("div", { class: "filelist" }, files.length ? files.map((p) => el("div", { class: "f", text: p, onclick: () => showFile(p, viewer) })) : el("div", { class: "muted small", text: "还没有文档。" })),
      viewer);
  } catch (e) { pane.replaceChildren(el("p", { class: "muted small", text: e.message })); }
}

async function showFile(path, target) {
  target.replaceChildren(el("div", { class: "muted small", text: `正在读取 ${path} …` }));
  try {
    const f = await api(`/api/runs/${encodeURIComponent(S.run)}/file?path=${encodeURIComponent(path)}`);
    target.replaceChildren(fileView(f));
  } catch (e) { target.replaceChildren(el("div", { class: "muted small", text: e.message })); }
}

// ---- file viewer: syntax highlight, JSON formatting, full screen ---------------------------------
// f = { path, text, html, title? }. html is the server's sanitised Markdown rendering (only for .md).
// highlight.js output is HTML-escaped by the library, so it may go through innerHTML.

const EXT_LANG = { py: "python", pyi: "python", java: "java", kt: "kotlin", kts: "kotlin", groovy: "groovy", gradle: "gradle",
  scala: "scala", js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript",
  json: "json", jsonl: "json", ndjson: "json", yaml: "yaml", yml: "yaml", toml: "ini", ini: "ini", cfg: "ini", conf: "ini",
  properties: "properties", xml: "xml", html: "xml", htm: "xml", svg: "xml", sh: "bash", bash: "bash", zsh: "bash",
  sql: "sql", go: "go", rs: "rust", rb: "ruby", php: "php", c: "c", h: "c", cc: "cpp", cpp: "cpp", hpp: "cpp", cs: "csharp",
  swift: "swift", css: "css", scss: "scss", less: "less", lua: "lua", r: "r", md: "markdown", diff: "diff", patch: "diff",
  proto: "protobuf", graphql: "graphql", gql: "graphql", mk: "makefile" };
const NAME_LANG = { dockerfile: "dockerfile", makefile: "makefile", jenkinsfile: "groovy", gradlew: "bash", "nginx.conf": "nginx" };
const HL_MAX = 400000;  // larger files are shown without highlighting

const parsesAsJson = (s) => { try { JSON.parse(s); return true; } catch { return false; } };

function langOf(path, text) {
  const name = path.split("/").pop().toLowerCase();
  if (NAME_LANG[name]) return NAME_LANG[name];
  if (name.startsWith("dockerfile.")) return "dockerfile";
  const ext = name.includes(".") ? name.split(".").pop() : "";
  if (EXT_LANG[ext]) return EXT_LANG[ext];
  const t = (text || "").trimStart();
  if ((t[0] === "{" || t[0] === "[") && parsesAsJson(text)) return "json";
  if (t.startsWith("<?xml")) return "xml";
  return "plaintext";
}

// Re-indent JSON without parsing the values: big integers, number spelling and key order stay exact.
function prettyJson(src) {
  let out = "", ind = 0, inStr = false, esc = false;
  const nl = () => "\n" + "  ".repeat(ind);
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (inStr) { out += c; if (esc) esc = false; else if (c === "\\") esc = true; else if (c === '"') inStr = false; continue; }
    if (c === '"') { inStr = true; out += c; }
    else if (c === "{" || c === "[") {
      let j = i + 1; while (j < src.length && /\s/.test(src[j])) j++;
      if (src[j] === (c === "{" ? "}" : "]")) { out += c + src[j]; i = j; } else { ind++; out += c + nl(); }
    } else if (c === "}" || c === "]") { ind--; out += nl() + c; }
    else if (c === ",") out += "," + nl();
    else if (c === ":") out += ": ";
    else if (!/\s/.test(c)) out += c;
  }
  return out;
}

// Returns { text, note }. Only JSON is formatted; other languages are shown as written.
function formatText(text, lang, path) {
  if (lang !== "json") return { text, note: "" };
  if (/\.(jsonl|ndjson)$/i.test(path)) {
    let bad = 0;
    const out = text.split("\n").filter((l) => l.trim()).map((l) => { if (!parsesAsJson(l)) { bad++; return l; } return prettyJson(l); });
    return { text: out.join("\n"), note: bad ? `已格式化，${bad} 行不是合法 JSON，保留原文` : "已格式化" };
  }
  if (!parsesAsJson(text)) return { text, note: "不是合法 JSON，显示原文" };
  const out = prettyJson(text);
  return { text: out, note: out === text.trim() ? "" : "已格式化" };
}

function highlighted(text, lang) {
  const code = el("code", { class: `hljs language-${lang}` });
  if (window.hljs && lang !== "plaintext" && text.length <= HL_MAX && hljs.getLanguage(lang)) {
    code.innerHTML = hljs.highlight(text, { language: lang, ignoreIllegals: true }).value;
  } else code.textContent = text;
  return code;
}

function highlightMdBlocks(root) {
  if (!window.hljs) return;
  root.querySelectorAll("pre code").forEach((c) => {
    const m = /language-([\w+-]+)/.exec(c.className);
    const lang = m && (hljs.getLanguage(m[1]) ? m[1] : null);
    if (!lang || c.textContent.length > HL_MAX) return;
    c.innerHTML = hljs.highlight(c.textContent, { language: lang, ignoreIllegals: true }).value;
    c.classList.add("hljs");
  });
}

function fileView(f, opts = {}) {
  if (f.image) return imageView(f, opts);
  const lang = langOf(f.path, f.text);
  const isMd = typeof f.html === "string";
  const st = { mode: isMd ? "render" : "source", fmt: true, wrap: lang === "plaintext" || lang === "markdown", full: false, ...opts };
  const box = el("div", { class: `viewer${st.full ? " full" : ""}` });
  const draw = () => {
    const shown = st.fmt ? formatText(f.text, lang, f.path) : { text: f.text, note: "" };
    const rendered = isMd && st.mode === "render";
    let body;
    if (rendered) {
      body = Object.assign(el("div", { class: "vbody md" }), { innerHTML: f.html });
      highlightMdBlocks(body);
    } else {
      const n = shown.text.split("\n").length;
      body = el("div", { class: `vbody code${st.wrap ? " wrap" : ""}` },
        st.wrap ? null : el("pre", { class: "gut", "aria-hidden": "true", text: Array.from({ length: n }, (_, k) => k + 1).join("\n") }),
        el("pre", { class: "src" }, highlighted(shown.text, lang)));
    }
    const btn = (label, on, fn, title) => el("button", { type: "button", class: `vbtn${on ? " on" : ""}`, title, text: label, onclick: fn });
    const set = (k, v) => () => { st[k] = v; draw(); };
    const copy = btn("复制", false, async (e) => {
      const b = e.currentTarget;
      try { await navigator.clipboard.writeText(rendered ? f.text : shown.text); b.textContent = "已复制"; }
      catch { b.textContent = "复制失败"; }
      setTimeout(() => { b.textContent = "复制"; }, 1200);
    }, "复制当前显示的内容");
    const bar = el("div", { class: "vbar" },
      el("span", { class: "vpath", title: f.path, text: f.title || f.path }),
      el("span", { class: "tag vlang", text: lang === "plaintext" ? "text" : lang }),
      shown.note && !rendered ? el("span", { class: "vnote", text: shown.note }) : null,
      el("span", { class: "spacer" }),
      isMd ? el("span", { class: "seg" }, btn("渲染", st.mode === "render", set("mode", "render")), btn("源码", st.mode === "source", set("mode", "source"))) : null,
      lang === "json" ? btn("格式化", st.fmt, set("fmt", !st.fmt), "格式化 / 显示原文") : null,
      rendered ? null : btn("换行", st.wrap, set("wrap", !st.wrap), "长行自动换行"),
      copy,
      st.full ? btn("关闭", false, () => $("#viewerDlg").close(), "关闭（Esc）")
        : btn("⤢ 全屏", false, () => openFull(f, st), "在弹窗里全屏查看"));
    box.replaceChildren(bar, body);
  };
  draw();
  return box;
}

// f.image is the server's /raw address for an image in the run's workspace (screenshots, attachments).
function imageView(f, opts = {}) {
  const full = !!opts.full;
  const btn = (label, fn, title) => el("button", { type: "button", class: "vbtn", title, text: label, onclick: fn });
  const img = el("img", { class: "vimg", src: f.image, alt: f.path });
  const body = el("div", { class: "vbody image" }, img);
  img.addEventListener("error", () => body.replaceChildren(el("div", { class: "muted small", text: `读取失败：${f.path}` })));
  const bar = el("div", { class: "vbar" },
    el("span", { class: "vpath", title: f.path, text: f.title || f.path }),
    el("span", { class: "tag vlang", text: "image" }),
    el("span", { class: "spacer" }),
    el("a", { class: "vbtn", href: f.image, target: "_blank", rel: "noopener", text: "原图" }),
    full ? btn("关闭", () => $("#viewerDlg").close(), "关闭（Esc）") : btn("⤢ 全屏", () => openFull(f, {}), "在弹窗里全屏查看"));
  return el("div", { class: `viewer${full ? " full" : ""}` }, bar, body);
}

function openFull(f, st) {
  const dlg = $("#viewerDlg");
  dlg.replaceChildren(fileView(f, { ...st, full: true }));
  if (!dlg.open) dlg.showModal();
}

// ---- maximise: a panel fills the window where it is ------------------------------------------------
// The element is pinned over the page (.maxed) rather than moved into a dialog, so a live log keeps
// streaming into it and keeps its scroll position. Esc, the scrim or the button put it back.
const MAX = { box: null, scrim: null, watch: null };

function maxButton(box) {
  return el("button", { type: "button", class: "vbtn maxbtn", text: "⤢ 最大化", title: "铺满窗口查看（Esc 还原）",
    onclick: () => setMax(box, MAX.box !== box) });
}

function setMax(box, on) {
  if (on && MAX.box && MAX.box !== box) setMax(MAX.box, false);
  if (!on && MAX.box !== box) return;
  box.classList.toggle("maxed", on);
  const b = box.querySelector(":scope > * > .maxbtn");
  if (b) { b.textContent = on ? "⤡ 还原" : "⤢ 最大化"; b.title = on ? "还原（Esc）" : "铺满窗口查看（Esc 还原）"; }
  if (!MAX.scrim) {
    MAX.scrim = el("div", { class: "maxscrim", hidden: true, onclick: () => MAX.box && setMax(MAX.box, false) });
    document.body.append(MAX.scrim);
  }
  MAX.scrim.hidden = !on;
  document.body.classList.toggle("has-max", on);
  MAX.box = on ? box : null;
  // the pane may be redrawn under it (step ended, run switched): drop the scrim with it
  if (MAX.watch) { MAX.watch.disconnect(); MAX.watch = null; }
  if (on) {
    MAX.watch = new MutationObserver(() => { if (MAX.box && !MAX.box.isConnected) setMax(MAX.box, false); });
    MAX.watch.observe(document.body, { childList: true, subtree: true });
  }
  box.dispatchEvent(new Event("maxchange"));
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && MAX.box && !document.querySelector("dialog[open]")) { e.preventDefault(); setMax(MAX.box, false); }
});

async function loadChanges(force = false) {
  const pane = $("#tab-changes");
  if (!S.run) return;
  if (!force && S.changesFor === S.run) return;
  const runId = S.run;
  S.changesFor = runId;
  pane.replaceChildren(el("p", { class: "muted small", text: "正在读取改动（约 2 秒）…" }));
  let d;
  try { d = await api(`/api/runs/${encodeURIComponent(runId)}/changes`); }
  catch (e) { pane.replaceChildren(el("p", { class: "muted small", text: `读取改动失败：${e.message}` })); S.changesFor = null; return; }
  if (runId !== S.run) return;
  const kids = [el("div", { class: "row" }, el("h4", { text: `相对 ${d.base} 的改动` }), el("span", { class: "spacer" }),
    el("button", { class: "ghost small", text: "刷新", onclick: () => loadChanges(true) }))];
  if (d.no_diff_reason || !d.files.length) {
    kids.push(el("p", { class: "muted small", text: d.no_diff_reason ? "这个运行没有代码仓库，或者还没有拉取代码。" : "没有改动。" }));
  } else {
    const added = d.files.reduce((n, f) => n + f.added, 0), removed = d.files.reduce((n, f) => n + f.removed, 0);
    const diffs = el("div", { class: "diffs" });
    for (const f of d.files) diffs.append(renderDiff(f));
    const box = el("div", { class: "diffbox" });
    box.append(el("div", { class: "dbar" }, el("span", { class: "small muted", text: `${d.files.length} 个文件，+${added} −${removed}` }),
      el("span", { class: "spacer" }), maxButton(box)),
    el("div", { class: "dbody" },
      d.log ? el("pre", { class: "small", text: d.log }) : null,
      el("div", { class: "filelist" }, d.files.map((f, n) => el("div", { class: "f", onclick: () => diffs.children[n]?.scrollIntoView({ block: "start" }) },
        el("span", { text: f.path }), el("span", { class: "add", text: ` +${f.added}` }), el("span", { class: "del", text: ` −${f.removed}` })))),
      diffs));
    kids.push(box);
  }
  if (d.review_round) {
    kids.push(el("h4", { text: `Review 结论（第 ${d.review_round} 轮）` }));
    if (d.summary_html) kids.push(Object.assign(el("div", { class: "md small" }), { innerHTML: d.summary_html }));
    const open = d.findings.filter((f) => f.status !== "fixed");
    if (open.length) kids.push(el("table", { class: "findings" }, el("tr", {}, ["严重程度", "位置", "问题"].map((h) => el("th", { text: h }))),
      open.map((f) => el("tr", {}, el("td", { class: `sev-${f.severity}`, text: f.severity || "" }), el("td", { text: `${f.file || ""}${f.line ? `:${f.line}` : ""}` }),
        el("td", {}, el("div", { text: f.title || "" }), f.evidence ? el("div", { class: "muted", text: f.evidence }) : null)))));
  }
  if (d.risk_html) kids.push(el("h4", { text: "风险判定" }), Object.assign(el("div", { class: "md small" }), { innerHTML: d.risk_html }));
  pane.replaceChildren(...kids);
}

function renderDiff(f) {
  const pre = el("pre", { class: "diff" });
  for (const line of f.diff.split("\n")) {
    const cls = line.startsWith("diff --git") ? "file" : line.startsWith("@@") ? "hunk"
      : line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---") ? "del" : "";
    pre.append(el("span", { class: cls, text: line + "\n" }));
  }
  return el("details", { open: true }, el("summary", { text: `${f.path}  +${f.added} −${f.removed}` }), pre);
}

function showTab(name) {
  if (name === "changes") loadChanges();
  S.tab = name;
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  for (const t of ["step", "docs", "changes"]) $(`#tab-${t}`).hidden = t !== name;
}

// ---- center: the run's conversation ----------------------------------------------------------

const WHO = { request: "你（发起）", reply: "你", approve: "你", reject: "你", cancel: "你" };
const WHAT = { approve: "批准", reject: "驳回", cancel: "取消了运行", changes: "提了修改意见给", resume: "重新执行了" };

// The instances a resume would run again (RunState.reopen): failed or cancelled, in a run that ended so.
function resumable(d) {
  if (!d || (d.status !== "failed" && d.status !== "cancelled")) return [];
  const all = d.tree.flatMap((t) => t.kind === "step" ? t.instances : t.rounds.flatMap((r) => r.instances));
  return all.filter((i) => i.status === "failed" || i.status === "cancelled").map((i) => i.id);
}

// A failed or cancelled run continues: the same run, its failed steps again (relay.resume).
async function resumeRun(steps) {
  const what = steps ? `步骤 ${steps.join("、")}` : "所有失败和取消的步骤";
  if (!confirm(`接着执行 ${S.run}：重新执行${what}，以及被它们挡住的后续步骤。已经成功的步骤保留结果。`)) return;
  $("#sendError").textContent = "";
  try {
    await api(`/api/runs/${encodeURIComponent(S.run)}/do/resume`, { method: "POST", body: steps ? { steps } : {} });
    S.convKey = null; loadDetail(); loadRuns();
  } catch (e) { $("#sendError").textContent = `没有接着执行：${e.message}`; }
}

function renderConversation(d) {
  const conv = d.conversation;
  const key = JSON.stringify(conv);
  if (key === S.convKey) return;  // nothing new: keep the scroll position
  S.convKey = key;
  const box = $("#messages");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  box.replaceChildren(...conv.items.map(renderItem));
  if (atBottom || !box.dataset.run || box.dataset.run !== d.run_id) box.scrollTop = box.scrollHeight;
  box.dataset.run = d.run_id;
  renderComposer(d, conv.target);
}

function bubble(cls, who, ...kids) {
  return el("div", { class: `msg ${cls}` }, who ? el("div", { class: "who", text: who }) : null, el("div", { class: "bubble" }, ...kids));
}

function renderItem(it) {
  if (it.kind === "request") return bubble("user", `${WHO.request}${it.by ? ` · ${it.by}` : ""}${it.at ? ` · ${fmtTime(it.at)}` : ""}`, el("div", { class: "pre", text: it.text || "（没有记录原文）" }));
  if (it.kind === "step") {
    const [icon] = EXEC[it.exec] || ["·"];
    const insts = it.instances;
    const status = aggStatus(insts);
    const name = `${it.step}${it.round ? ` · 第 ${it.round} 轮` : ""}`;
    return el("div", { class: `event s-${status}`, onclick: () => selectStep(insts[0].id) },
      el("div", { class: "eh" }, st(status), el("span", { class: `ic k-${it.exec}`, text: icon }), el("b", { text: name }),
        insts[0].model ? el("span", { class: "tag", text: insts[0].model.replace("claude-", "") }) : null,
        insts.length > 1 ? el("span", { class: "muted small", text: ` ×${insts.length} 并行` }) : null),
      insts.map((i) => (i.summary || i.error) ? el("div", { class: "es" }, insts.length > 1 ? el("span", { class: "muted", text: `x${i.replica} ` }) : null,
        el("span", { class: i.error ? "err" : "", text: i.error || i.summary })) : null));
  }
  if (it.kind === "question") {
    return bubble(`agent${it.answered ? " answered" : " asking"}`, `${it.step}${it.answered ? "" : " · 在等你回复"}`,
      el("div", { class: "pre", text: it.text }),
      it.options?.length ? el("ol", { class: "opts" }, it.options.map((o) => el("li", { text: o }))) : null);
  }
  if (it.kind === "said") {
    const text = it.what === "reply" ? it.text : `${WHAT[it.what] || it.what} ${it.step || ""}${it.text ? `：${it.text}` : ""}`;
    return bubble("user", `${WHO[it.what] || "你"}${it.by ? ` · ${it.by}` : ""}${it.at ? ` · ${fmtTime(it.at)}` : ""}${it.what === "reply" ? ` → ${it.step}` : ""}`, el("div", { class: "pre", text }));
  }
  if (it.kind === "approval") {
    return bubble("agent asking", `${it.step} · 等你审批`, el("div", { text: it.missing || `需要 ${it.needed} 人批准` }),
      el("div", { class: "small muted", text: `已批准：${it.approvals.join(", ") || "无"}` }),
      el("button", { class: "ghost small", text: "看改动和 review 结论", onclick: () => showTab("changes") }));
  }
  if (it.kind === "pr") return el("div", { class: "event s-succeeded" }, el("div", { class: "eh" }, el("span", { class: "ic k-pr", text: "PR" }), el("b", { text: "PR 已创建 " }), el("a", { href: it.url, target: "_blank", rel: "noopener", text: it.url })));
  if (it.kind === "watchdog") {  // myorch/watchdog.py: alive but not getting anywhere; the user decides
    return bubble("agent asking watchdog", `WATCHDOG · ${it.step} · ${it.label}`,
      el("div", { class: "pre", text: it.reason }),
      it.evidence ? el("pre", { class: "small", text: it.evidence }) : null,
      el("div", { class: "small muted", text: "watchdog 只提醒，不处置。可以取消这个运行，或者等它继续。" }),
      el("button", { class: "ghost small", text: "知道了", onclick: async () => {
        try { await api(`/api/runs/${encodeURIComponent(S.run)}/do/dismiss`, { method: "POST", body: { text: it.alert } }); S.convKey = null; loadDetail(); loadRuns(); }
        catch (e) { $("#sendError").textContent = e.message; }
      } }));
  }
  if (it.kind === "end") {
    const line = el("div", { class: `endline s-${it.status}`, text: { succeeded: "✓ 运行完成", failed: "✗ 运行失败", cancelled: "⊘ 运行已取消" }[it.status] || it.status });
    const buttons = [
      resumable(S.detail).length ? el("button", { class: "ghost small", text: "重新执行失败的步骤", title: "同一个运行接着跑：失败和取消的步骤重新执行，已经成功的保留结果",
        onclick: () => resumeRun(null) }) : null,
      canContinue(S.detail) ? el("button", { class: "ghost small", text: continueLabel(S.detail), title: continuation(S.detail).spec.continue.title || "",
        onclick: continueRun }) : null].filter(Boolean);  // newrun.js
    return buttons.length ? el("div", {}, line, el("div", { class: "resumebar" }, ...buttons)) : line;
  }
  return el("div");
}

function renderComposer(d, target) {
  S.target = target;
  const input = $("#input"), send = $("#sendBtn"), hint = $("#composerHint");
  $("#approveBar").hidden = target.mode !== "approve";
  $("#changesBtn").hidden = !d.can_request_changes;
  $("#cancelRunBtn").hidden = d.status !== "running";
  const retry = resumable(d);
  $("#resumeRunBtn").hidden = !retry.length;
  $("#resumeRunBtn").title = retry.length ? `重新执行：${retry.join("、")}，以及被它们挡住的后续步骤` : "";
  $("#continueRunBtn").hidden = !canContinue(d);  // newrun.js: a flow input with a `continue` spec
  if (canContinue(d)) $("#continueRunBtn").textContent = continueLabel(d);
  let label, placeholder, can = true;
  if (target.mode === "reply") {
    label = `发给 → ${target.step}（在等你回复）`;
    placeholder = target.options?.length ? `回复原样发给 ${target.step}。可以回选项编号，也可以写自己的回答。` : `回复原样发给 ${target.step}，它会接着原来的会话继续。`;
    hint.textContent = target.others?.length ? `还有：${target.others.join("、")} 也在等你，回复完这一个再处理。` : "Enter 发送，Shift+Enter 换行。";
  } else if (target.mode === "approve") {
    label = `${target.step} 在等你审批：${target.missing}`;
    placeholder = "审批备注（可选），随批准或驳回一起记录。";
    hint.textContent = "看完右栏「改动」再决定。";
    can = false;
  } else if (target.mode === "busy") {
    label = `正在执行：${target.running.join("、") || "调度中"}`;
    placeholder = "运行中的步骤不能对话。等它提问、要审批或结束后再发。";
    hint.textContent = "";
    can = false;
  } else {
    label = `运行已结束（${target.status}）`;
    placeholder = retry.length ? `失败的步骤：${retry.join("、")}。排除原因后，点右下「重新执行失败的步骤」，在这个运行里接着执行。`
      : canContinue(d) && continueEnded(d) ? continueEnded(d)
      : "这个运行已经结束。要继续改，点顶部「＋ 新运行」。";
    hint.textContent = retry.length ? "已经成功的步骤保留结果，不会再执行。" : "";
    can = false;
  }
  $("#target").textContent = label;
  $("#target").className = `target mode-${target.mode}`;
  input.placeholder = placeholder;
  input.disabled = !(can || target.mode === "approve");
  send.hidden = !can;
}

async function sendMessage() {
  const text = $("#input").value;
  $("#sendError").textContent = "";
  if (S.newMode) return startRun(text);
  if (!S.run || S.target?.mode !== "reply" || !text.trim()) return;
  $("#sendBtn").disabled = true;
  try {
    await api(`/api/runs/${encodeURIComponent(S.run)}/do/reply`, { method: "POST", body: { step: S.target.step, text } });
    $("#input").value = "";
    S.convKey = null;
    await loadDetail();
  } catch (e) { $("#sendError").textContent = `没有发出：${e.message}`; }
  $("#sendBtn").disabled = false;
}

async function decideApproval(action) {
  if (!S.run || S.target?.mode !== "approve") return;
  if (action === "reject" && !confirm(`驳回 ${S.target.step}？驳回后运行会终止。`)) return;
  $("#sendError").textContent = "";
  try {
    await api(`/api/runs/${encodeURIComponent(S.run)}/do/${action}`, { method: "POST", body: { step: S.target.step, text: $("#input").value } });
    $("#input").value = "";
    S.convKey = null;
    await loadDetail(); loadRuns();
  } catch (e) { $("#sendError").textContent = `没有完成：${e.message}`; }
}

async function cancelRun() {
  if (!S.run || !confirm(`取消运行 ${S.run}？正在执行的步骤会被停止。`)) return;
  try { await api(`/api/runs/${encodeURIComponent(S.run)}/do/cancel`, { method: "POST", body: {} }); S.convKey = null; loadDetail(); loadRuns(); }
  catch (e) { $("#sendError").textContent = `没有取消：${e.message}`; }
}

// ---- wiring -----------------------------------------------------------------------------------

$("#composer").addEventListener("submit", (e) => { e.preventDefault(); sendMessage(); });
$("#input").addEventListener("keydown", (e) => {
  if (e.isComposing || e.key !== "Enter") return;
  if (S.newMode ? (e.metaKey || e.ctrlKey) : !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); }
});
$("#approveBtn").addEventListener("click", () => decideApproval("approve"));
$("#rejectBtn").addEventListener("click", () => decideApproval("reject"));
$("#changesBtn").addEventListener("click", () => {
  if (!$("#input").value.trim()) { $("#sendError").textContent = "先在输入框里写修改意见。"; $("#input").focus(); return; }
  decideApproval("changes");
});
$("#seeChanges").addEventListener("click", () => showTab("changes"));
$("#cancelRunBtn").addEventListener("click", cancelRun);
$("#resumeRunBtn").addEventListener("click", () => resumeRun(null));
$("#continueRunBtn").addEventListener("click", () => continueRun());
$("#projectFilter").addEventListener("change", (e) => { S.project = e.target.value; loadRuns(); });
let searchTimer = null;
$("#runSearch").addEventListener("input", (e) => { clearTimeout(searchTimer); searchTimer = setTimeout(() => setSearch(e.target.value), 300); });
$("#runPickBtn").addEventListener("click", () => toggle($("#runPicker")));
$("#inboxBtn").addEventListener("click", () => toggle($("#inboxPanel")));
$("#userBtn").addEventListener("click", () => toggle($("#userPanel")));
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
$("#viewerDlg").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });  // backdrop

// ---- pane widths: drag a gutter, arrow keys nudge it, double-click resets ------------------------
// One set of widths for every tab: switching tabs never moves the gutters.

const PANE_MIN = { left: 240, right: 300 }, CENTER_MIN = 320;
const paneProp = (side) => `--w-${side}`;
const paneKey = (side) => `myorch.w.${side}`;
function applyPaneWidths() {
  const lay = $(".layout");
  for (const side of ["left", "right"]) {
    let v = null;
    try { v = localStorage.getItem(paneKey(side)); } catch { /* storage off */ }
    if (v && +v > 0) lay.style.setProperty(paneProp(side), `${+v}px`); else lay.style.removeProperty(paneProp(side));
  }
}
function setPaneWidth(side, px, save) {
  const lay = $(".layout");
  const other = $(side === "left" ? "aside.right" : "aside.left").getBoundingClientRect().width;
  const max = lay.clientWidth - 24 - 24 - other - CENTER_MIN;  // padding, two gutters, the other sheet, the center minimum
  const w = Math.round(Math.max(PANE_MIN[side], Math.min(max, px)));
  lay.style.setProperty(paneProp(side), `${w}px`);
  if (save) { try { localStorage.setItem(paneKey(side), String(w)); } catch { /* storage off */ } }
  return w;
}
document.querySelectorAll(".gutter").forEach((g) => {
  const side = g.dataset.side, pane = () => $(side === "left" ? "aside.left" : "aside.right");
  g.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    g.setPointerCapture(e.pointerId);
    const x0 = e.clientX, start = pane().getBoundingClientRect().width;
    let w = start;
    g.classList.add("on"); g.dataset.w = Math.round(start); document.body.classList.add("resizing");
    const move = (ev) => { w = setPaneWidth(side, start + (side === "left" ? ev.clientX - x0 : x0 - ev.clientX), false); g.dataset.w = w; };
    const up = () => {
      g.removeEventListener("pointermove", move); g.removeEventListener("pointerup", up); g.removeEventListener("pointercancel", up);
      g.classList.remove("on"); document.body.classList.remove("resizing");
      setPaneWidth(side, w, true);
    };
    g.addEventListener("pointermove", move); g.addEventListener("pointerup", up); g.addEventListener("pointercancel", up);
  });
  g.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const step = (e.shiftKey ? 64 : 16) * ((e.key === "ArrowRight") === (side === "left") ? 1 : -1);
    setPaneWidth(side, pane().getBoundingClientRect().width + step, true);
  });
  g.addEventListener("dblclick", () => {
    try { localStorage.removeItem(paneKey(side)); } catch { /* storage off */ }
    applyPaneWidths();
  });
});
applyPaneWidths();

// ---- live: the channel relay's event stream ----------------------------------------------------
// The worker writes events; /channel/events (SSE) pushes each one as it is written, named by kind.
// An event of the shown run redraws it; any event refreshes the run list and inbox. EventSource
// reconnects by itself and sends Last-Event-ID, so nothing is missed. A slow poll stays as a safety
// net: runs started before the event-detail patch, and a run terminated from outside, write no events.

const EVENT_KINDS = ["run_started", "question", "approval", "approval_reminder", "pr_opened", "run_finished",
  "step_started", "step_finished", "approval_progress", "loop_round", "replied", "changes_requested"];
const LIVE = { timers: {}, ok: false };
function liveSoon(key, fn, ms) { clearTimeout(LIVE.timers[key]); LIVE.timers[key] = setTimeout(fn, ms); }
function connectLive(after) {
  if (!window.EventSource) return;
  const es = new EventSource(`/channel/events?after=${after}`);
  const on = (m) => {
    let e = null;
    try { e = JSON.parse(m.data); } catch { return; }
    if (e.run_id === S.run && !S.newMode) liveSoon("detail", loadDetail, 120);
    liveSoon("runs", loadRuns, 400);
    if (e.run_id === S.run && e.kind === "step_finished" && S.tab === "docs") liveSoon("docs", loadDocs, 800);
  };
  for (const k of EVENT_KINDS) es.addEventListener(k, on);
  es.onopen = () => { LIVE.ok = true; $("#meta").classList.add("live-on"); };
  es.onerror = () => { LIVE.ok = false; $("#meta").classList.remove("live-on"); };
}

(async () => {
  let seq = 0;
  try { const m = await api("/api/meta"); $("#meta").textContent = `用户 ${m.user}`; seq = m.seq || 0; } catch { /* ignore */ }
  loadProjects();
  const h = new URLSearchParams(location.hash.slice(1));
  if (h.has("new")) openNewRun();  // newrun.js
  else if (h.get("run")) selectRun(h.get("run"), h.get("step"));
  else loadRuns();
  if (["step", "docs", "changes"].includes(h.get("tab"))) showTab(h.get("tab"));
  connectLive(seq);
  setInterval(loadRuns, 30000);  // safety net, see above
  setInterval(() => { if (S.run && !S.newMode && (!LIVE.ok || S.detail?.source === "engine")) loadDetail(); }, 5000);
})();
