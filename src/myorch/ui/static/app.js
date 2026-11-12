// myorch console: a workbench. The side bar lists the runs (project › run › its steps), what waits for
// you, and the flow definitions. Each thing opens as a tab in the editor area: a run's conversation, a
// step, the changes, the docs, a file, a new run, a flow. The panel at the bottom shows live output.
// What you write in a run's tab goes verbatim to the run (the first message is the request; later ones
// answer the step that asked).
// Untrusted text (run content) is always inserted as textContent; only server-rendered, sanitised
// markdown (field `html`) and highlight.js output (escaped by the library) go through innerHTML.
"use strict";

const ICON = { pending: "·", running: "▶", waiting_approval: "⏸", waiting_input: "?", succeeded: "✓", failed: "✗",
  rejected: "✗", blocked: "-", skipped: "↷", cancelled: "⊘", RUNNING: "▶", COMPLETED: "✓", FAILED: "✗",
  CANCELED: "⊘", TERMINATED: "✗", TIMED_OUT: "✗" };
// run / step: of the active tab. detail: that run's detail. newMode: the active tab is the new-run form.
const S = { run: null, step: null, detail: null, project: "", q: "", newMode: false, loopRound: {}, lastRuns: null, view: "runs" };
const DETAIL = new Map();  // run id -> its detail, for every run that has a tab or an open tree node

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
const store = { get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage off */ } } };

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
const enc = encodeURIComponent;

const EXEC = { agent: ["AI", "agent"], script: ["SH", "脚本"], approval: ["USR", "人工审批"], host: ["HOST", "宿主机动作"] };
const ACTIVE = new Set(["running", "waiting_input", "waiting_approval"]);

function toggle(panel, show) { panel.hidden = show === undefined ? !panel.hidden : !show; }
document.addEventListener("click", (e) => {
  if (!e.target.closest("#userBtn") && !e.target.closest("#userPanel")) $("#userPanel").hidden = true;
});

// ---- side bar: three views; clicking the shown view's icon folds the side bar ---------------------

const VIEWS = { runs: "运行", inbox: "等你处理", flows: "流程定义" };
function showView(name, fold = false) {
  const off = document.body.classList.contains("side-off");
  if (fold && S.view === name && !off) { document.body.classList.add("side-off"); store.set("myorch.side", "off"); return; }
  document.body.classList.remove("side-off");
  store.set("myorch.side", "on");
  S.view = name;
  for (const v of Object.keys(VIEWS)) $(`#view-${v}`).hidden = v !== name;
  document.querySelectorAll(".activity [data-view]").forEach((b) => b.classList.toggle("on", b.dataset.view === name));
  $("#sideTitle").textContent = VIEWS[name];
  store.set("myorch.view", name);
  if (name === "flows" && typeof flOpenView === "function") flOpenView();  // flows.js
}

// ---- runs: the list, the inbox, the tree ------------------------------------------------------------

const runOf = (id) => S.lastRuns?.runs.find((r) => r.run_id === id) || null;
// What a run is called in tabs and the tree: its first label (a Jira ticket …), else its id.
function runLabel(id) {
  const d = DETAIL.get(id), r = runOf(id);
  return d?.labels?.[0] || r?.labels?.[0] || id;
}

async function loadRuns() {
  let data;
  const q = S.q;
  try { data = await api(`/api/runs?limit=80${S.project ? `&project=${enc(S.project)}` : ""}${q ? `&label=${enc(q)}` : ""}`); }
  catch (e) { $("#runs").replaceChildren(el("div", { class: "muted small pad", text: `读取运行列表失败：${e.message}。请检查 Temporal 是否在运行。` })); return; }
  const warn = $("#volumeWarn");
  warn.textContent = data.volume_error ? "读不到运行数据卷：文档、日志、发起人和开始时间暂时看不到" : "";
  warn.title = data.volume_error ? `Docker 可能没有运行。现在只显示 Temporal 里的运行。\n${data.volume_error}` : "";
  renderInbox(data);
  if (q !== S.q) return;  // the search changed while this one was on its way
  $("#runCount").textContent = q ? `找到 ${data.runs.length} 个` : `${data.runs.length} 个`;
  S.lastRuns = data;
  renderRunTree();
  renderTabbar();
  renderStatus();
  if (!TABS.list.length && !BOOT.picked && data.runs.length) {  // first visit: the run that needs you or is moving, else the newest
    BOOT.picked = true;
    const pick = data.inbox[0]?.run_id || data.runs.find((r) => r.status === "RUNNING")?.run_id || data.runs[0].run_id;
    openTab({ kind: "run", run: pick });
  }
}

const INBOX_KIND = { approval: ["审批", "wait"], loop: ["循环跑满", "wait"], watchdog: ["watchdog", "bad"] };
function renderInbox(data) {
  const items = data.inbox;
  const title = (id) => data.runs.find((r) => r.run_id === id)?.labels?.[0] || id;
  $("#inbox").replaceChildren(...(items.length ? items.map((w) => {
    const [kind, cls] = INBOX_KIND[w.kind] || ["回复", "wait"];
    return el("div", { class: "ibx", title: w.run_id, onclick: () => openTab({ kind: "run", run: w.run_id }) },
      el("div", { class: "ibx1" }, el("span", { class: `tag ${cls}`, text: kind }), el("span", { class: "id", text: title(w.run_id) }),
        el("span", { class: "spacer" }), el("span", { class: "muted small", text: w.project || "" })),
      el("div", { class: "ibx2" }, el("b", { text: w.step }), el("span", { class: "muted", text: { approval: " 在等审批", loop: " 跑满了轮数，等你决定", watchdog: " watchdog 提醒" }[w.kind] || " 在问" })),
      w.text ? el("div", { class: "ibxq", text: w.text.slice(0, 280) }) : null);
  }) : [el("div", { class: "muted small pad", text: "没有等你处理的事。" })]));
  $("#inboxCount").textContent = items.length || "";
  $("#stInbox").textContent = items.length ? `${items.length} 个等你处理` : "没有等你处理的事";
  $("#stInbox").classList.toggle("hot", items.length > 0);
  S.inboxRuns = new Set(items.map((w) => w.run_id));
}

function renderStatus() {
  const n = (S.lastRuns?.runs || []).filter((r) => r.status === "RUNNING").length;
  $("#stRuns").textContent = n ? `${n} 个运行中` : "";
}

// The tree: project › run › the run's steps. A project opens by default when it holds the shown run or
// a running one; a run opens when it has a tab; the user's toggles are kept.
const NO_PROJECT = "（无项目）";
const EXP = new Set(store.get("myorch.tree.runs", []));  // runs whose steps are shown
function saveExp() { store.set("myorch.tree.runs", [...EXP].slice(-30)); }
function toggleExpand(id) { if (EXP.has(id)) EXP.delete(id); else EXP.add(id); saveExp(); renderRunTree(); }

function renderRunTree() {
  const data = S.lastRuns;
  if (!data) return;
  const groups = new Map();
  for (const r of data.runs) {
    const k = r.project || NO_PROJECT;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(r);
  }
  const saved = store.get("myorch.runs.open", {});
  const nodes = [];
  for (const [project, runs] of groups) {
    const running = runs.filter((r) => r.status === "RUNNING").length;
    const waiting = runs.filter((r) => S.inboxRuns?.has(r.run_id)).length;
    const open = S.q ? true : saved[project] ?? (runs.some((r) => r.run_id === S.run) || running > 0 || groups.size === 1);
    nodes.push(el("div", { class: "tr tp", role: "treeitem", "aria-expanded": String(open), style: "--d:0",
      onclick: () => { const s = store.get("myorch.runs.open", {}); s[project] = !open; store.set("myorch.runs.open", s); renderRunTree(); } },
      el("span", { class: "caret", text: open ? "▾" : "▸" }), el("span", { class: "nm", text: project }),
      waiting ? el("span", { class: "tag wait", text: `${waiting} 等你` }) : null,
      running ? el("span", { class: "tag", text: `${running} 运行中` }) : null,
      el("span", { class: "meta", text: String(runs.length) })));
    if (!open) continue;
    for (const r of runs) {
      nodes.push(runRow(r));
      if (EXP.has(r.run_id)) nodes.push(...stepRows(r.run_id));
    }
  }
  const box = $("#runs"), top = box.scrollTop;
  box.replaceChildren(...nodes);
  box.scrollTop = top;
}

function runRow(r) {
  const at = activeTab(), open = EXP.has(r.run_id);
  const sel = at?.run === r.run_id && at.kind !== "step";
  const status = S.inboxRuns?.has(r.run_id) ? "waiting_input" : r.status;
  return el("div", { class: `tr trun${sel ? " sel" : ""}`, role: "treeitem", style: "--d:1",
    title: [r.run_id, r.labels?.join(" "), r.initiator, fmtTime(r.requested_at)].filter(Boolean).join("\n"),
    onclick: () => openTab({ kind: "run", run: r.run_id }) },
    el("span", { class: "caret", text: open ? "▾" : "▸", title: open ? "收起步骤" : "展开步骤",
      onclick: (e) => { e.stopPropagation(); toggleExpand(r.run_id); } }),
    st(status), el("span", { class: "nm", text: r.labels?.[0] || r.run_id }),
    el("span", { class: "sub", text: r.labels?.[0] ? r.flow : "" }),
    el("span", { class: "meta", text: r.progress && r.status === "RUNNING" ? `${r.progress.done}/${r.progress.total}` : fmtTime(r.requested_at) }));
}

// A run's steps as tree rows: stage order, ∥ marks steps that run in parallel, a loop shows the steps
// of its shown round, a step with replicas has one row per replica.
function stepRows(run) {
  const d = DETAIL.get(run);
  if (!d) { loadDetail(run); return [el("div", { class: "tr ghost", style: "--d:2", text: "读取中…" })]; }
  const at = activeTab();
  const selId = at?.kind === "step" && at.run === run ? at.step : null;
  const rows = [];
  const row = (depth, o) => el("div", { class: `tr tstep${o.sel ? " sel" : ""}${o.idle ? " idle" : ""}`, role: "treeitem", style: `--d:${depth}`,
    title: o.title || "", onclick: o.click },
    el("span", { class: "par", text: o.par ? "∥" : "" }), st(o.status),
    o.exec ? el("span", { class: `ic k-${o.exec}`, text: (EXEC[o.exec] || ["·"])[0] }) : null,
    el("span", { class: "nm", text: o.name }), o.meta ? el("span", { class: "meta", text: o.meta }) : null);
  const open = (step) => () => openTab({ kind: "step", run, step }, { preview: true });
  const instRows = (depth, meta, insts, par) => {
    const status = aggStatus(insts), one = insts.length === 1 ? insts[0] : null;
    const id = one ? one.id : insts[0]?.id || meta.id;
    const model = (one?.model || meta.model || "").replace("claude-", "");
    const out = [row(depth, { status, exec: meta.exec, name: meta.id, par, idle: !insts.length, click: open(id),
      sel: selId === id || selId === meta.id, title: meta.when ? `条件：${meta.when}` : "",
      meta: one?.attempts > 1 ? `第 ${one.attempts} 次` : insts.length > 1 ? `×${insts.length}` : model })];
    if (insts.length > 1) {
      for (const i of insts) out.push(row(depth + 1, { status: i.status, name: `x${i.replica || "?"}`, sel: selId === i.id, click: open(i.id), title: i.summary || "" }));
    }
    return out;
  };
  const parallel = (items, stageOf) => { const n = new Map(); for (const it of items) n.set(stageOf(it), (n.get(stageOf(it)) || 0) + 1); return (it) => n.get(stageOf(it)) > 1; };
  const isPar = parallel(d.tree, (t) => t.stage);
  const byStage = (items, stageOf) => items.map((it, n) => [it, n]).sort((a, b) => stageOf(a[0]) - stageOf(b[0]) || a[1] - b[1]).map(([it]) => it);
  for (const t of byStage(d.tree, (x) => x.stage)) {
    if (t.kind !== "loop") { rows.push(...instRows(2, t, t.instances, isPar(t))); continue; }
    const insts = loopRoundInstances(run, t);
    rows.push(row(2, { status: t.status, name: `↻ ${t.id}`, meta: `${t.round}/${t.max_rounds} 轮`, par: isPar(t), sel: selId === t.id,
      click: open(t.id), title: `循环，直到 ${t.until}` }));
    const lp = parallel(t.steps, (s) => t.stages[s.id]);
    for (const s of byStage(t.steps, (x) => t.stages[x.id])) rows.push(...instRows(3, s, insts.filter((i) => i.step === s.id), lp(s)));
  }
  return rows;
}

function loopShownRound(run, t) {
  return S.loopRound[`${run}/${t.id}`] ?? (t.rounds.length ? t.rounds[t.rounds.length - 1].round : null);
}
function loopRoundInstances(run, t) {
  const r = loopShownRound(run, t);
  return (t.rounds.find((x) => x.round === r) || { instances: [] }).instances;
}

// Labels (Jira tickets …) as small chips; a click searches for that label.
function labelChips(labels) {
  return (labels || []).map((l) => el("span", { class: "tag label", title: `搜「${l}」`, text: l,
    onclick: (e) => { e.stopPropagation(); showView("runs"); setSearch(l); } }));
}
function setSearch(q) { S.q = q.trim(); $("#runSearch").value = S.q; loadRuns(); }

async function loadProjects() {
  try {
    const ps = await api("/api/projects");
    const sel = $("#projectFilter");
    for (const p of ps) sel.append(el("option", { value: p.name, text: p.name }));
  } catch { /* ignore */ }
}

// Kept for the other scripts: open a run, or one of its steps.
function selectRun(runId, step = null) {
  openTab(step ? { kind: "step", run: runId, step } : { kind: "run", run: runId });
}

// ---- tabs ------------------------------------------------------------------------------------------
// A tab is {kind, run?, step?, path?, log?, ref?}. Opening a step or a file from the tree makes a
// preview tab (italic): the next one replaces it, a double click keeps it. Tabs survive a reload.

const TABS = { list: [], active: null };
const BOOT = { picked: false };
const DRAFT = {}, SCROLL = {};  // per run: the composer's text, the conversation's scroll position

function tabKey(t) {
  switch (t.kind) {
    case "run": return `run:${t.run}`;
    case "step": return `step:${t.run}:${t.step}`;
    case "changes": case "docs": return `${t.kind}:${t.run}`;
    case "file": return `file:${t.run}:${t.log ? `log:${t.step}` : t.path}`;
    case "flow": return `flow:${t.ref.t}:${t.ref.id || ""}`;
    default: return t.kind;
  }
}
function activeTab() { return TABS.list.find((t) => t.key === TABS.active) || null; }

function openTab(spec, opts = {}) {
  const t = { ...spec, key: tabKey(spec), preview: !!opts.preview };
  let have = TABS.list.find((x) => x.key === t.key);
  if (have) { if (!opts.preview) have.preview = false; }
  else {
    const prev = opts.preview ? TABS.list.findIndex((x) => x.preview && x.key !== TABS.active) : -1;
    const cur = TABS.list.find((x) => x.key === TABS.active);
    if (opts.preview && cur?.preview) TABS.list.splice(TABS.list.indexOf(cur), 1, t);  // replace the preview being looked at
    else if (prev >= 0) TABS.list.splice(prev, 1, t);
    else TABS.list.splice(cur ? TABS.list.indexOf(cur) + 1 : TABS.list.length, 0, t);
    have = t;
  }
  activate(have.key);
  return have;
}

function activate(key) {
  const prev = activeTab();
  if (prev) leavePane(prev);
  TABS.active = key;
  const t = activeTab();
  S.run = t?.run || null;
  S.step = t?.kind === "step" ? t.step : null;
  S.detail = S.run ? DETAIL.get(S.run) || null : null;
  S.newMode = t?.kind === "new";
  if (S.run && !EXP.has(S.run) && t.kind === "step") { EXP.add(S.run); saveExp(); }
  renderTabbar();
  showPane(t);
  setHash(t);
  saveTabs();
  renderRunTree();
}

function closeTab(key) {
  const i = TABS.list.findIndex((t) => t.key === key);
  if (i < 0) return;
  const [t] = TABS.list.splice(i, 1);
  if (t.kind === "run") delete DRAFT[t.run];
  if (TABS.active !== key) { renderTabbar(); saveTabs(); return; }
  const next = TABS.list[i] || TABS.list[i - 1];
  if (next) { TABS.active = null; activate(next.key); return; }
  TABS.active = null;
  Object.assign(S, { run: null, step: null, detail: null, newMode: false });
  renderTabbar(); showPane(null); setHash(null); saveTabs(); renderRunTree();
}

function saveTabs() {
  store.set("myorch.tabs", { list: TABS.list.map(({ key, ...t }) => t), active: TABS.active });
}
function restoreTabs() {
  const s = store.get("myorch.tabs", null);
  if (!s?.list) return;
  TABS.list = s.list.filter((t) => t.kind && t.kind !== "new").map((t) => ({ ...t, key: tabKey(t) }));
  const a = TABS.list.find((t) => t.key === s.active) || TABS.list[TABS.list.length - 1];
  if (a) activate(a.key);
}

function tabInfo(t) {
  const d = t.run ? DETAIL.get(t.run) : null, rl = t.run ? runLabel(t.run) : "";
  const icon = (text) => el("span", { class: "ti", text });
  switch (t.kind) {
    case "run": return { icon: st(d?.status || runOf(t.run)?.status || "pending"), title: rl, tip: `${t.run} · 对话` };
    case "step": {
      const i = d && findInstanceIn(d, t.step), loop = d?.tree.find((x) => x.kind === "loop" && x.id === t.step);
      return { icon: st(i?.status || loop?.status || "pending"), title: t.step, sub: rl, tip: `${t.run} · ${t.step}` };
    }
    case "changes": return { icon: icon("±"), title: "改动", sub: rl, tip: `${t.run} · 改动和 review 结论` };
    case "docs": return { icon: icon("▤"), title: "文档", sub: rl, tip: `${t.run} · docs/` };
    case "file": return { icon: icon("▢"), title: t.log ? `${t.step} · 日志` : t.path.split("/").pop(), sub: rl, tip: `${t.run} · ${t.log ? `${t.step} 最后一轮的日志` : t.path}` };
    case "new": return { icon: icon("＋"), title: "新运行", tip: "新运行" };
    case "flow": return { icon: icon("⑂"), title: t.ref.t === "kbform" ? "登记知识源" : t.ref.id, sub: { frag: "片段", kb: "知识库", ev: "评测" }[t.ref.t] || "", tip: t.ref.id || "" };
    default: return { icon: icon("·"), title: t.kind };
  }
}

function renderTabbar() {
  const bar = $("#tabbar");
  bar.replaceChildren(...TABS.list.map((t) => {
    const info = tabInfo(t);
    return el("div", { class: `etab${t.key === TABS.active ? " on" : ""}${t.preview ? " preview" : ""}`, role: "tab", title: info.tip,
      "aria-selected": String(t.key === TABS.active),
      onclick: () => { if (t.key !== TABS.active) activate(t.key); },
      ondblclick: () => { t.preview = false; renderTabbar(); saveTabs(); },
      onauxclick: (e) => { if (e.button === 1) { e.preventDefault(); closeTab(t.key); } } },
      info.icon, el("span", { class: "tt", text: info.title }), info.sub ? el("span", { class: "ts", text: info.sub }) : null,
      el("button", { type: "button", class: "tx", title: "关闭（中键也行）", text: "×", onclick: (e) => { e.stopPropagation(); closeTab(t.key); } }));
  }), el("button", { type: "button", class: "tnew", title: "新运行", text: "＋", onclick: () => openNewRun() }));
  bar.querySelector(".etab.on")?.scrollIntoView({ block: "nearest", inline: "nearest" });
}

// The address names the active tab, so a link opens the same thing (#run=…, &step=, &tab=changes|docs, #new, #flow=…).
function hashOf(t) {
  if (!t) return "";
  if (t.kind === "new") return "#new";
  if (t.kind === "flow") return t.ref.t === "flow" ? `#flow=${enc(t.ref.id)}` : "";
  const base = `#run=${enc(t.run)}`;
  if (t.kind === "step") return `${base}&step=${enc(t.step)}`;
  if (t.kind === "changes" || t.kind === "docs") return `${base}&tab=${t.kind}`;
  if (t.kind === "file") return t.log ? `${base}&log=${enc(t.step)}` : `${base}&file=${enc(t.path)}`;
  return base;
}
function setHash(t) {
  const h = hashOf(t);
  if (h !== location.hash) history.replaceState(null, "", h || location.pathname);
}
function applyHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  if (h.has("new")) return openNewRun(), true;
  if (h.get("flow")) return openFlows(h.get("flow")), true;  // flows.js
  const run = h.get("run");
  if (!run) return false;
  if (h.get("step")) openTab({ kind: "step", run, step: h.get("step") });
  else if (["changes", "docs"].includes(h.get("tab"))) openTab({ kind: h.get("tab"), run });
  else if (h.get("file")) openTab({ kind: "file", run, path: h.get("file") });
  else if (h.get("log")) openTab({ kind: "file", run, step: h.get("log"), log: true });
  else openTab({ kind: "run", run });
  return true;
}

// ---- panes: one element per kind of tab, drawn for the active tab ---------------------------------

const PANES = ["empty", "run", "step", "changes", "docs", "file", "flow", "new"];
function showPane(t) {
  const kind = t?.kind || "empty";
  for (const p of PANES) $(`#pane-${p}`).hidden = p !== kind;
  if (kind === "empty") return renderEmpty();
  if (kind === "run") { enterRunPane(t.run); if (S.detail) renderRunPane(S.detail); loadDetail(); }
  else if (kind === "step") { S.stepKey = null; renderStep(); loadDetail(); }
  else if (kind === "changes") { loadChanges(); if (!S.detail) loadDetail(); }
  else if (kind === "docs") { loadDocs(); if (!S.detail) loadDetail(); }
  else if (kind === "file") { loadFileTab(t); if (!S.detail) loadDetail(); }
  else if (kind === "new") renderNewPane();  // newrun.js
  else if (kind === "flow") flShowTab(t.ref);  // flows.js
}

function leavePane(t) {
  if (t.kind === "run") { SCROLL[t.run] = $("#messages").scrollTop; DRAFT[t.run] = $("#input").value; }
}

function renderEmpty() {
  $("#pane-empty").replaceChildren(el("div", { class: "welcome" },
    el("h2", { text: "myorch 控制台" }),
    el("p", { class: "muted", text: "在左边选一个运行，或者发起新运行。点运行看对话，展开看步骤；步骤在执行时，底部会显示它的实时输出。" }),
    el("div", { class: "row" }, el("button", { text: "＋ 新运行", onclick: () => openNewRun() }),
      el("button", { class: "ghost", text: "等你处理", onclick: () => showView("inbox") }),
      el("button", { class: "ghost", text: "流程定义", onclick: () => showView("flows") }))));
}

// ---- run detail ----------------------------------------------------------------------------------------

const LOADING = new Set();
async function loadDetail(run = S.run) {
  if (!run || LOADING.has(run)) return;
  LOADING.add(run);
  let d;
  try { d = await api(`/api/runs/${enc(run)}`); }
  catch (e) {
    if (run === S.run) {
      const t = activeTab();
      const msg = el("p", { class: "muted pad", text: `读取 ${run} 失败：${e.message}` });
      if (t?.kind === "run") $("#messages").replaceChildren(msg);
      if (t?.kind === "step") $("#pane-step").replaceChildren(msg);
    }
    return;
  } finally { LOADING.delete(run); }
  DETAIL.set(run, d);
  if (EXP.has(run)) renderRunTree();
  renderTabbar();
  if (run !== S.run) return;
  S.detail = d;
  const t = activeTab();
  if (t?.kind === "run") renderRunPane(d);
  if (t?.kind === "step") {
    const key = S.step + JSON.stringify(findInstance(S.step) || d.tree.find((x) => x.id === S.step) || null);
    if (key !== S.stepKey) { S.stepKey = key; renderStep(); }
  }
  syncPanel(d);
}

function allInstances(d) { return d.tree.flatMap((t) => (t.kind === "step" ? t.instances : t.rounds.flatMap((r) => r.instances))); }

// A node's status: its instances' (worst first), or pending when nothing was created yet.
function aggStatus(insts) {
  if (!insts.length) return "pending";
  for (const s of ["waiting_approval", "waiting_input", "running", "failed", "rejected", "blocked", "cancelled", "pending"]) if (insts.some((i) => i.status === s)) return s;
  return insts.every((i) => i.status === "skipped") ? "skipped" : "succeeded";
}

function findInstanceIn(d, id) {
  for (const t of d.tree) {
    const hit = (t.kind === "step" ? t.instances : t.rounds.flatMap((r) => r.instances)).find((i) => i.id === id);
    if (hit) return hit;
  }
  return null;
}
function findInstance(id) { return S.detail ? findInstanceIn(S.detail, id) : null; }
// The definition of an instance's step (its exec, model, condition), in the tree or inside a loop.
function stepMeta(d, step) {
  return d.tree.find((t) => t.kind !== "loop" && t.id === step) || d.tree.filter((t) => t.kind === "loop").flatMap((t) => t.steps).find((s) => s.id === step) || null;
}

function kv(rows) { return el("div", { class: "kv" }, rows.filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => el("div", {}, el("span", { class: "k", text: k }), v instanceof Node ? el("span", {}, v) : el("span", { text: String(v) })))); }

// ---- run pane: header, conversation, composer -----------------------------------------------------------

function enterRunPane(run) {
  const box = $("#messages");
  if (box.dataset.run !== run) {
    box.replaceChildren(); box.dataset.run = ""; S.convKey = null;
    $("#runHead").replaceChildren();
    $("#input").value = DRAFT[run] || "";
    $("#sendError").textContent = "";
  }
}

function renderRunPane(d) {
  renderRunHead(d);
  renderConversation(d);
}

function renderRunHead(d) {
  const m = d.mandate || {};
  const all = allInstances(d);
  const done = all.filter((i) => i.status === "succeeded" || i.status === "skipped").length;
  const inputs = Object.entries(d.inputs || {}).filter(([, v]) => v !== "");
  const btn = (text, title, fn) => el("button", { type: "button", class: "ghost small", text, title, onclick: fn });
  $("#runHead").replaceChildren(...[
    el("div", { class: "rh1" }, st(d.status), el("b", { text: d.labels?.[0] || d.flow }),
      el("span", { class: "muted", text: `${d.labels?.[0] ? `${d.flow} · ` : ""}${d.project || "无项目"}` }),
      ...labelChips((d.labels || []).slice(1)),
      el("span", { class: "spacer" }),
      btn("改动", "相对 base 的 diff、review 结论、风险判定", () => openTab({ kind: "changes", run: d.run_id })),
      btn("文档", "工作区的 docs/", () => openTab({ kind: "docs", run: d.run_id })),
      btn("流程定义", `${d.flow} 的定义`, () => openFlows(d.flow))),
    el("div", { class: "rh2" }, el("span", { class: "mono", text: d.run_id }),
      m.initiator ? el("span", { text: `发起人 ${m.initiator}` }) : null, m.requested_at ? el("span", { text: fmtTime(m.requested_at) }) : null,
      el("span", { text: `${done}/${all.length} 步已完成` }),
      ...(d.knowledge || []).map((k) => el("span", { class: `tag k-${k.kind}`, title: `知识库，启动时固定：${k.kind === "mcp" ? k.url : k.version}`,
        text: k.kind === "mcp" ? `${k.name} · mcp` : `${k.name} · ${k.version.replace("sha256:", "").slice(0, 7)}` }))),
    inputs.length ? el("details", { class: "inputs", open: store.get("myorch.inputs.open", false) || null,
      ontoggle: (e) => store.set("myorch.inputs.open", e.currentTarget.open) },
      el("summary", { text: `输入 · ${inputs.map(([k, v]) => `${k}=${String(v).slice(0, 24)}`).join("  ")}` }),
      el("div", { class: "igrid" }, inputs.map(([k, v]) => [el("span", { class: "muted", text: k }), el("span", { class: "pre", text: String(v).slice(0, 2000) })]))) : null]
    .filter(Boolean));
}

const WHO = { request: "你（发起）", reply: "你", approve: "你", reject: "你", cancel: "你" };
const WHAT = { approve: "批准", reject: "驳回", cancel: "取消了运行", changes: "提了修改意见给", resume: "重新执行了" };

// The instances a resume would run again (RunState.reopen): failed or cancelled, in a run that ended so.
function resumable(d) {
  if (!d || (d.status !== "failed" && d.status !== "cancelled")) return [];
  return allInstances(d).filter((i) => i.status === "failed" || i.status === "cancelled").map((i) => i.id);
}

// A failed or cancelled run continues: the same run, its failed steps again (relay.resume).
async function resumeRun(steps) {
  const what = steps ? `步骤 ${steps.join("、")}` : "所有失败和取消的步骤";
  if (!confirm(`接着执行 ${S.run}：重新执行${what}，以及被它们挡住的后续步骤。已经成功的步骤保留结果。`)) return;
  $("#sendError").textContent = "";
  try {
    await api(`/api/runs/${enc(S.run)}/do/resume`, { method: "POST", body: steps ? { steps } : {} });
    S.convKey = null; loadDetail(); loadRuns();
  } catch (e) { const m = `没有接着执行：${e.message}`; $("#sendError").textContent = m; if (activeTab()?.kind !== "run") alert(m); }
}

function renderConversation(d) {
  const conv = d.conversation;
  const key = JSON.stringify(conv);
  if (key === S.convKey) return;  // nothing new: keep the scroll position
  S.convKey = key;
  const box = $("#messages");
  const fresh = box.dataset.run !== d.run_id;
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  box.replaceChildren(...conv.items.map(renderItem));
  if (fresh) box.scrollTop = SCROLL[d.run_id] ?? box.scrollHeight;
  else if (atBottom) box.scrollTop = box.scrollHeight;
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
    return el("div", { class: `event s-${status}`, title: "打开这一步", onclick: () => openTab({ kind: "step", run: S.run, step: insts[0].id }, { preview: true }) },
      el("div", { class: "eh" }, st(status), el("span", { class: `ic k-${it.exec}`, text: icon }), el("b", { text: name }),
        insts[0].model ? el("span", { class: "tag", text: insts[0].model.replace("claude-", "") }) : null,
        insts.length > 1 ? el("span", { class: "muted small", text: ` ×${insts.length} 并行` }) : null,
        status === "running" ? el("button", { type: "button", class: "ghost small", text: "实时输出", title: "在底部面板看它的输出",
          onclick: (e) => { e.stopPropagation(); panelOpen(S.run, insts.find((i) => i.status === "running")?.id || insts[0].id); } }) : null),
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
      el("button", { class: "ghost small", text: "看改动和 review 结论", onclick: () => openTab({ kind: "changes", run: S.run }) }));
  }
  if (it.kind === "pr") return el("div", { class: "event s-succeeded" }, el("div", { class: "eh" }, el("span", { class: "ic k-pr", text: "PR" }), el("b", { text: "PR 已创建 " }), el("a", { href: it.url, target: "_blank", rel: "noopener", text: it.url })));
  if (it.kind === "watchdog") {  // myorch/watchdog.py: alive but not getting anywhere; the user decides
    return bubble("agent asking watchdog", `WATCHDOG · ${it.step} · ${it.label}`,
      el("div", { class: "pre", text: it.reason }),
      it.evidence ? el("pre", { class: "small", text: it.evidence }) : null,
      el("div", { class: "small muted", text: "watchdog 只提醒，不处置。可以取消这个运行，或者等它继续。" }),
      el("button", { class: "ghost small", text: "知道了", onclick: async () => {
        try { await api(`/api/runs/${enc(S.run)}/do/dismiss`, { method: "POST", body: { text: it.alert } }); S.convKey = null; loadDetail(); loadRuns(); }
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
    hint.textContent = "先看「改动」再决定。";
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
      : "这个运行已经结束。要继续改，点「＋」发起新运行。";
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
  if (!S.run || S.target?.mode !== "reply" || !text.trim()) return;
  $("#sendBtn").disabled = true;
  try {
    await api(`/api/runs/${enc(S.run)}/do/reply`, { method: "POST", body: { step: S.target.step, text } });
    $("#input").value = ""; delete DRAFT[S.run];
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
    await api(`/api/runs/${enc(S.run)}/do/${action}`, { method: "POST", body: { step: S.target.step, text: $("#input").value } });
    $("#input").value = ""; delete DRAFT[S.run];
    S.convKey = null;
    await loadDetail(); loadRuns();
  } catch (e) { $("#sendError").textContent = `没有完成：${e.message}`; }
}

async function cancelRun() {
  if (!S.run || !confirm(`取消运行 ${S.run}？正在执行的步骤会被停止。`)) return;
  try { await api(`/api/runs/${enc(S.run)}/do/cancel`, { method: "POST", body: {} }); S.convKey = null; loadDetail(); loadRuns(); }
  catch (e) { $("#sendError").textContent = `没有取消：${e.message}`; }
}

// ---- step pane -----------------------------------------------------------------------------------------
// Everything about one step, loaded as soon as it opens: its record, its products and last log, what
// it read from the knowledge sources. Live output is in the panel below.

const SF = new Map();  // `${run}/${iid}` -> {sig, p: promise of the step's files}
function stepFiles(run, i) {
  const k = `${run}/${i.id}`, sig = `${i.status}/${i.attempts}/${i.turns}`;
  let c = SF.get(k);
  if (!c || c.sig !== sig) { c = { sig, p: api(`/api/runs/${enc(run)}/steps/${enc(i.id)}`) }; SF.set(k, c); c.p.catch(() => SF.delete(k)); }
  return c.p;
}
const KU = new Map();  // the same for the knowledge record
function stepKnowledge(run, i) {
  const k = `${run}/${i.id}`, sig = `${i.status}/${i.attempts}/${i.turns}`;
  let c = KU.get(k);
  if (!c || c.sig !== sig) { c = { sig, p: api(`/api/runs/${enc(run)}/steps/${enc(i.id)}/knowledge`) }; KU.set(k, c); c.p.catch(() => KU.delete(k)); }
  return c.p;
}

function renderStep() {
  const pane = $("#pane-step"), d = S.detail, run = S.run;
  if (!d) { pane.replaceChildren(el("p", { class: "muted pad", text: "正在读取…" })); return; }
  const i = findInstance(S.step);
  const loop = d.tree.find((t) => t.kind === "loop" && t.id === S.step);
  if (loop) return renderLoop(pane, run, loop);
  if (!i) {
    const meta = stepMeta(d, S.step);
    pane.replaceChildren(el("div", { class: "page" }, meta
      ? [el("div", { class: "ph" }, st("pending"), el("span", { class: `ic k-${meta.exec}`, text: (EXEC[meta.exec] || ["·"])[0] }), el("h2", { text: meta.id })),
        kv([["状态", "还没开始"], ["执行者", (EXEC[meta.exec] || [])[1]], ["模型", meta.model], ["条件", meta.when], ["依赖", (meta.needs || []).join(", ")]])]
      : el("p", { class: "muted", text: `这个运行里没有步骤 ${S.step}。` })));
    return;
  }
  const btn = (text, fn, cls = "ghost small", title) => el("button", { type: "button", class: cls, text, title, onclick: fn });
  const exec = i.exec || stepMeta(d, i.step)?.exec;
  const head = el("div", { class: "ph" }, st(i.status), exec ? el("span", { class: `ic k-${exec}`, text: (EXEC[exec] || ["·"])[0] }) : null,
    el("h2", { text: i.id }), i.model ? el("span", { class: "tag", text: i.model.replace("claude-", "") }) : null,
    el("span", { class: "muted small", text: i.status }), el("span", { class: "spacer" }),
    btn("实时输出", () => panelOpen(run, i.id), "ghost small", "在底部面板看这一步的输出"),
    i.status === "waiting_approval" ? btn("看改动和 review 结论", () => openTab({ kind: "changes", run })) : null,
    (i.status === "failed" || i.status === "cancelled") && (d.status === "failed" || d.status === "cancelled")
      ? btn("从这一步重新执行", () => resumeRun([i.id]), "ghost small", "只重新执行这一步，以及被它挡住的后续步骤") : null,
    btn("对话", () => openTab({ kind: "run", run }), "ghost small", "回到这个运行的对话"));
  const main = [];
  if (i.error) main.push(el("h4", { text: "错误" }), el("pre", { class: "err", text: i.error }));
  if (i.question) main.push(el("h4", { text: "提问" }), el("pre", { text: i.question.question + (i.question.options.length ? "\n" + i.question.options.map((o, n) => `${n + 1}. ${o}`).join("\n") : "") }));
  if (i.summary) main.push(el("h4", { text: "摘要" }), el("div", { class: "pre", text: i.summary }));
  if (i.notes) main.push(el("h4", { text: "notes" }), el("div", { class: "pre small", text: i.notes }));
  if (Object.keys(i.outputs || {}).length) main.push(el("h4", { text: "outputs" }), fileView({ path: "outputs.json", title: `${i.id} · outputs`, text: JSON.stringify(i.outputs, null, 2), html: null }));
  if (!main.length) main.push(el("p", { class: "muted small", text: i.status === "running" ? "正在执行。输出在底部的实时输出里，结束后这里显示摘要。" : "没有摘要。" }));

  const files = el("div", { class: "filelist" }, el("div", { class: "muted small", text: "读取中（约 2 秒）…" }));
  const side = [el("h4", { text: "状态" }), kv([["状态", i.status], ["模型", i.model], ["尝试次数", i.attempts > 1 ? i.attempts : null], ["对话轮数", i.turns > 1 ? i.turns : null],
    ["退出码", i.exit_code], ["manifest", i.manifest_status !== "ok" ? i.manifest_status : null],
    ["审批", i.approvals_needed ? `${i.approvals.length}/${i.approvals_needed}：${i.approvals.join(", ") || "无"}` : null]]),
    el("h4", { text: "产物和日志" }), files];
  let kbox = null;
  if ((d.knowledge || []).length && exec === "agent") { kbox = el("div", { class: "kuse" }, el("div", { class: "muted small", text: "读取中…" })); side.push(el("h4", { text: "知识库读取" }), kbox); }
  pane.replaceChildren(el("div", { class: "page" }, head, el("div", { class: "pgrid" }, el("div", { class: "pmain" }, main), el("div", { class: "pside" }, side))));

  const still = () => activeTab()?.kind === "step" && S.run === run && S.step === i.id;
  stepFiles(run, i).then((f) => {
    if (!still()) return;
    files.replaceChildren(
      el("div", { class: "f", title: "最后一轮的完整日志", onclick: () => openTab({ kind: "file", run, step: i.id, log: true }, { preview: true }) }, el("span", { class: "fi", text: "≡" }), "日志（最后一轮）"),
      ...f.outputs.map((p) => el("div", { class: "f", title: p, onclick: () => openTab({ kind: "file", run, path: `${f.out_dir}/${p}` }, { preview: true }) }, el("span", { class: "fi", text: "▢" }), p)));
  }).catch((e) => { if (still()) files.replaceChildren(el("div", { class: "muted small", text: e.message })); });
  if (kbox && typeof renderKnowledgeUse === "function") {  // knowledge.js
    stepKnowledge(run, i).then((u) => { if (still()) renderKnowledgeUse(kbox, u); })
      .catch((e) => { if (still()) kbox.replaceChildren(el("div", { class: "muted small", text: e.message })); });
  }
}

function renderLoop(pane, run, t) {
  const shown = loopShownRound(run, t);
  const insts = loopRoundInstances(run, t);
  pane.replaceChildren(el("div", { class: "page" },
    el("div", { class: "ph" }, st(t.status), el("span", { class: "ic k-loop", text: "LOOP" }), el("h2", { text: t.id }), el("span", { class: "muted small", text: `第 ${t.round}/${t.max_rounds} 轮` })),
    kv([["状态", t.status], ["轮次", `${t.round}/${t.max_rounds}`], ["结束条件", t.until], ["错误", t.error]]),
    t.question ? [el("h4", { text: "等你决定" }), el("pre", { text: t.question.question + "\n" + t.question.options.map((o, n) => `${n + 1}. ${o}`).join("\n") }),
      el("button", { class: "ghost small", text: "去对话里回复", onclick: () => openTab({ kind: "run", run }) })] : null,
    t.rounds.length ? el("div", { class: "rtabs" }, t.rounds.map((r) => el("button", { type: "button", class: `rtab${r.round === shown ? " on" : ""}`, text: `第 ${r.round} 轮`,
      onclick: () => { S.loopRound[`${run}/${t.id}`] = r.round; renderLoop(pane, run, t); renderRunTree(); } }))) : null,
    el("div", { class: "filelist" }, t.steps.map((s) => {
      const si = insts.filter((i) => i.step === s.id);
      return el("div", { class: "f", onclick: () => openTab({ kind: "step", run, step: si[0]?.id || s.id }, { preview: true }) },
        st(aggStatus(si)), ` ${s.id}`, si[0]?.summary ? el("span", { class: "muted", text: ` · ${si[0].summary.slice(0, 120)}` }) : null);
    }))));
}

// ---- live output: the panel at the bottom ----------------------------------------------------------------
// The worker copies a running step's log to the event log's output table (kept 7 days); /channel/output
// (SSE) replays it from the start and then follows. One stream, for the panel tab on show.

const PANEL = { tabs: store.get("myorch.panel.tabs", []), active: store.get("myorch.panel.active", null), open: store.get("myorch.panel.open", false),
  max: false, closedByUser: store.get("myorch.panel.closed", false), es: null, key: "", body: null, partial: "", turn: "", stick: true, wrap: true };
const LO_MAX_LINES = 4000;
const pkey = (run, step) => `${run}/${step}`;

function panelOpen(run, step, { focus = true, reveal = true } = {}) {
  const key = pkey(run, step);
  if (!PANEL.tabs.some((t) => t.key === key)) { PANEL.tabs.push({ key, run, step }); while (PANEL.tabs.length > 8) PANEL.tabs.shift(); }
  if (focus || !PANEL.active) PANEL.active = key;
  if (reveal) setPanelOpen(true, false);
  panelSave(); renderPanel();
}
function panelClose(key) {
  const i = PANEL.tabs.findIndex((t) => t.key === key);
  if (i < 0) return;
  PANEL.tabs.splice(i, 1);
  if (PANEL.active === key) PANEL.active = (PANEL.tabs[i] || PANEL.tabs[i - 1])?.key || null;
  if (!PANEL.tabs.length) setPanelOpen(false, false);
  panelSave(); renderPanel();
}
function panelSave() { store.set("myorch.panel.tabs", PANEL.tabs); store.set("myorch.panel.active", PANEL.active); store.set("myorch.panel.open", PANEL.open); }
function setPanelOpen(on, byUser = true) {
  PANEL.open = on;
  if (byUser) { PANEL.closedByUser = !on; store.set("myorch.panel.closed", !on); }
  $("#work").classList.toggle("panel-off", !on);
  if (!on) setPanelMax(false);
  $("#panelBtn").classList.toggle("on", on);
  panelSave(); renderPanel();
}
function setPanelMax(on) {
  PANEL.max = on;
  $("#work").classList.toggle("panel-max", on);
  if (PANEL.body && PANEL.stick) PANEL.body.scrollTop = PANEL.body.scrollHeight;
  renderPanelBar();
}

// A step tab opens on a running step: its output comes up in the panel, unless the user put the panel away.
function syncPanel(d) {
  const t = activeTab();
  if (t?.kind === "step" && t.run === d.run_id) {
    const i = findInstanceIn(d, t.step);
    if (i?.status === "running") panelOpen(d.run_id, i.id, { reveal: !PANEL.closedByUser });
  }
  renderPanelBar();
}

function panelStatus(t) { const d = DETAIL.get(t.run); return (d && findInstanceIn(d, t.step)?.status) || "pending"; }

function renderPanel() {
  renderPanelBar();
  const want = PANEL.open ? PANEL.active : null;
  if (want === PANEL.key && (want === null || PANEL.body)) return;
  panelConnect(want);
}

function renderPanelBar() {
  $("#ptabs").replaceChildren(...PANEL.tabs.map((t) => el("div", { class: `ptab${t.key === PANEL.active ? " on" : ""}`, title: `${t.run} · ${t.step}`,
    onclick: () => { PANEL.active = t.key; panelSave(); renderPanel(); }, onauxclick: (e) => { if (e.button === 1) panelClose(t.key); } },
    st(panelStatus(t)), el("span", { class: "tt", text: t.step }), el("span", { class: "ts", text: runLabel(t.run) }),
    el("button", { type: "button", class: "tx", text: "×", title: "关闭", onclick: (e) => { e.stopPropagation(); panelClose(t.key); } }))),
    ...(PANEL.tabs.length ? [] : [el("span", { class: "muted small", text: "实时输出：打开一个正在执行的步骤，或者点它的「实时输出」。" })]));
  const t = PANEL.tabs.find((x) => x.key === PANEL.active);
  const b = (text, title, fn, on) => el("button", { type: "button", class: `vbtn${on ? " on" : ""}`, text, title, onclick: fn });
  $("#pactions").replaceChildren(...[
    t ? b("打开步骤", "在编辑区打开这一步", () => openTab({ kind: "step", run: t.run, step: t.step })) : null,
    t ? b("完整日志", "最后一轮的完整日志", () => openTab({ kind: "file", run: t.run, step: t.step, log: true })) : null,
    b("换行", "长行自动换行", () => { PANEL.wrap = !PANEL.wrap; PANEL.body?.classList.toggle("nowrap", !PANEL.wrap); renderPanelBar(); }, PANEL.wrap),
    b(PANEL.max ? "⤡ 还原" : "⤢ 最大化", PANEL.max ? "还原（Esc）" : "占满编辑区", () => setPanelMax(!PANEL.max)),
    b("⌄", "收起", () => setPanelOpen(false))].filter(Boolean));
}

function panelConnect(key) {
  if (PANEL.es) PANEL.es.close();
  Object.assign(PANEL, { es: null, key: key || "", partial: "", turn: "", stick: true, body: null });
  const box = $("#pbody");
  if (!key) { box.replaceChildren(); return; }
  const t = PANEL.tabs.find((x) => x.key === key);
  const body = el("div", { class: `liveout${PANEL.wrap ? "" : " nowrap"}` },
    el("div", { class: "lo-meta", text: panelStatus(t) === "running" ? "等待输出…" : "读取输出记录…（输出保留 7 天；没有记录时点「完整日志」）" }));
  body.addEventListener("scroll", () => { PANEL.stick = body.scrollTop + body.clientHeight >= body.scrollHeight - 24; });
  PANEL.body = body;
  box.replaceChildren(body);
  if (!window.EventSource) return;
  PANEL.es = new EventSource(`/channel/output?run=${enc(t.run)}&step=${enc(t.step)}`);
  PANEL.es.addEventListener("output", (m) => {
    let c = null;
    try { c = JSON.parse(m.data); } catch { return; }
    if (PANEL.key === key) appendLiveOutput(c);
  });
}

function liveLineClass(line) {
  if (line.startsWith("→ ")) return "lo-tool";
  if (line.startsWith("  ✗ ")) return "lo-err";
  if (line.startsWith("  ← ")) return "lo-res";
  if (line.startsWith("· ") || line.startsWith("═══")) return "lo-meta";
  return "";
}

function appendLiveOutput(c) {
  const box = PANEL.body;
  if (!box) return;
  if (box.childElementCount === 1 && box.firstChild.classList.contains("lo-meta") && !PANEL.turn) box.replaceChildren();
  const turn = `${c.attempt}/${c.turn}`;
  if (turn !== PANEL.turn) {
    if (PANEL.partial) { box.append(el("div", { text: PANEL.partial })); PANEL.partial = ""; }
    if (PANEL.turn) box.append(el("div", { class: "lo-sep", text: `—— 第 ${c.attempt} 次尝试 · 第 ${c.turn} 轮 ——` }));
    PANEL.turn = turn;
  }
  const lines = (PANEL.partial + c.text).split("\n");
  PANEL.partial = lines.pop();
  const frag = document.createDocumentFragment();
  for (const line of lines) frag.append(el("div", { class: liveLineClass(line), text: line || " " }));
  box.append(frag);
  while (box.childElementCount > LO_MAX_LINES) box.firstChild.remove();
  if (PANEL.stick) box.scrollTop = box.scrollHeight;
}

// ---- docs, files, changes ----------------------------------------------------------------------------

async function loadDocs(force = false) {
  const pane = $("#pane-docs"), run = S.run;
  if (!run) return;
  if (!force && pane.dataset.run === run && pane.childElementCount) return;
  pane.dataset.run = run;
  const viewer = el("div", { class: "dview" }, el("p", { class: "muted small pad", text: "在左边选一个文件。" }));
  const list = el("div", { class: "dlist" }, el("div", { class: "muted small", text: "读取中…" }));
  pane.replaceChildren(list, viewer);
  try {
    const files = await api(`/api/runs/${enc(run)}/files?dir=docs`);
    if (run !== S.run) return;
    list.replaceChildren(el("div", { class: "row" }, el("h4", { text: "docs/" }), el("span", { class: "spacer" }), el("button", { class: "ghost small", text: "刷新", onclick: () => loadDocs(true) })),
      el("div", { class: "filelist" }, files.length ? files.map((p) => el("div", { class: "f", title: p, text: p.replace(/^docs\//, ""), onclick: (e) => {
        list.querySelectorAll(".f.on").forEach((x) => x.classList.remove("on")); e.currentTarget.classList.add("on"); showFile(p, viewer);
      } })) : el("div", { class: "muted small", text: "还没有文档。" })));
  } catch (e) { list.replaceChildren(el("p", { class: "muted small", text: e.message })); }
}

async function showFile(path, target) {
  target.replaceChildren(el("div", { class: "muted small pad", text: `正在读取 ${path} …` }));
  try {
    const f = await api(`/api/runs/${enc(S.run)}/file?path=${enc(path)}`);
    target.replaceChildren(fileView(f));
  } catch (e) { target.replaceChildren(el("div", { class: "muted small pad", text: e.message })); }
}

async function loadFileTab(t) {
  const pane = $("#pane-file"), key = t.key;
  pane.replaceChildren(el("div", { class: "muted small pad", text: t.log ? "正在读取日志（约 2 秒）…" : `正在读取 ${t.path} …` }));
  try {
    let f;
    if (t.log) {
      if (!DETAIL.get(t.run)) await loadDetail(t.run);
      const i = DETAIL.get(t.run) && findInstanceIn(DETAIL.get(t.run), t.step);
      const sf = i ? await stepFiles(t.run, i) : await api(`/api/runs/${enc(t.run)}/steps/${enc(t.step)}`);
      f = { path: "step.log", title: `${t.step} · 日志（最后一轮）`, text: sf.log || "（没有日志）", html: null };
    } else f = await api(`/api/runs/${enc(t.run)}/file?path=${enc(t.path)}`);
    if (TABS.active === key) pane.replaceChildren(fileView(f));
  } catch (e) { if (TABS.active === key) pane.replaceChildren(el("div", { class: "muted small pad", text: e.message })); }
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


// The changes tab: one drawn page per run, kept until 刷新 (the diff takes a helper container, ~2 s).
const CH = new Map();
async function loadChanges(force = false) {
  const pane = $("#pane-changes"), runId = S.run;
  if (!runId) return;
  if (!force && CH.has(runId)) { pane.replaceChildren(CH.get(runId)); return; }
  pane.replaceChildren(el("p", { class: "muted small pad", text: "正在读取改动（约 2 秒）…" }));
  let d;
  try { d = await api(`/api/runs/${enc(runId)}/changes`); }
  catch (e) { if (runId === S.run) pane.replaceChildren(el("p", { class: "muted small pad", text: `读取改动失败：${e.message}` })); return; }
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
  const page = el("div", { class: "page wide" }, kids);
  CH.set(runId, page);
  if (runId === S.run && activeTab()?.kind === "changes") pane.replaceChildren(page);
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

// ---- wiring ----------------------------------------------------------------------------------------------

$("#composer").addEventListener("submit", (e) => { e.preventDefault(); sendMessage(); });
$("#input").addEventListener("keydown", (e) => {
  if (e.isComposing || e.key !== "Enter" || e.shiftKey) return;
  e.preventDefault(); $("#composer").requestSubmit();
});
$("#input").addEventListener("input", (e) => { if (S.run) DRAFT[S.run] = e.target.value; const t = activeTab(); if (t?.preview) { t.preview = false; renderTabbar(); saveTabs(); } });
$("#approveBtn").addEventListener("click", () => decideApproval("approve"));
$("#rejectBtn").addEventListener("click", () => decideApproval("reject"));
$("#changesBtn").addEventListener("click", () => {
  if (!$("#input").value.trim()) { $("#sendError").textContent = "先在输入框里写修改意见。"; $("#input").focus(); return; }
  decideApproval("changes");
});
$("#seeChanges").addEventListener("click", () => openTab({ kind: "changes", run: S.run }));
$("#cancelRunBtn").addEventListener("click", cancelRun);
$("#resumeRunBtn").addEventListener("click", () => resumeRun(null));
$("#continueRunBtn").addEventListener("click", () => continueRun());
$("#projectFilter").addEventListener("change", (e) => { S.project = e.target.value; loadRuns(); });
let searchTimer = null;
$("#runSearch").addEventListener("input", (e) => { clearTimeout(searchTimer); searchTimer = setTimeout(() => setSearch(e.target.value), 300); });
document.querySelectorAll(".activity [data-view]").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view, true)));
$("#userBtn").addEventListener("click", () => toggle($("#userPanel")));
$("#stInbox").addEventListener("click", () => showView("inbox"));
$("#panelBtn").addEventListener("click", () => setPanelOpen(!PANEL.open));
$("#viewerDlg").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });  // backdrop
window.addEventListener("hashchange", applyHash);  // links such as an eval case's replay run
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && PANEL.max && !MAX.box && !document.querySelector("dialog[open]")) { e.preventDefault(); setPanelMax(false); }
});

// ---- sizes: drag the side bar's edge and the panel's top edge; double click resets -----------------------

const SIDE_MIN = 200, SIDE_MAX = 640, PANEL_MIN = 90;
function applySizes() {
  const w = store.get("myorch.w.side", null), h = store.get("myorch.h.panel", null);
  if (w) document.body.style.setProperty("--w-side", `${w}px`); else document.body.style.removeProperty("--w-side");
  if (h) $("#work").style.setProperty("--h-panel", `${h}px`); else $("#work").style.removeProperty("--h-panel");
}
function dragger(handle, axis, read, write, reset) {
  handle.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    const p0 = axis === "x" ? e.clientX : e.clientY, start = read();
    let v = start;
    handle.classList.add("on"); document.body.classList.add(axis === "x" ? "resizing" : "resizing-y");
    const move = (ev) => { v = write(start + (axis === "x" ? ev.clientX - p0 : p0 - ev.clientY), false); };
    const up = () => {
      handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", up); handle.removeEventListener("pointercancel", up);
      handle.classList.remove("on"); document.body.classList.remove("resizing", "resizing-y");
      write(v, true);
    };
    handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", up); handle.addEventListener("pointercancel", up);
  });
  handle.addEventListener("dblclick", reset);
}
const sideGutter = $(".gutter[data-side=side]");
dragger(sideGutter, "x", () => $("#side").getBoundingClientRect().width, (px, save) => {
  const w = Math.round(Math.max(SIDE_MIN, Math.min(SIDE_MAX, window.innerWidth - 480, px)));
  document.body.style.setProperty("--w-side", `${w}px`);
  if (save) store.set("myorch.w.side", w);
  return w;
}, () => { store.set("myorch.w.side", null); applySizes(); });
sideGutter.addEventListener("keydown", (e) => {
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  e.preventDefault();
  const w = $("#side").getBoundingClientRect().width + (e.shiftKey ? 64 : 16) * (e.key === "ArrowRight" ? 1 : -1);
  const v = Math.round(Math.max(SIDE_MIN, Math.min(SIDE_MAX, w)));
  document.body.style.setProperty("--w-side", `${v}px`); store.set("myorch.w.side", v);
});
dragger($("#pgutter"), "y", () => $("#panel").getBoundingClientRect().height, (px, save) => {
  const h = Math.round(Math.max(PANEL_MIN, Math.min($("#work").clientHeight - 140, px)));
  $("#work").style.setProperty("--h-panel", `${h}px`);
  if (save) store.set("myorch.h.panel", h);
  return h;
}, () => { store.set("myorch.h.panel", null); applySizes(); });
applySizes();

// ---- live: the channel relay's event stream ------------------------------------------------------------
// The worker writes events; /channel/events (SSE) pushes each one as it is written, named by kind.
// An event of a run with an open tab or tree node redraws it; any event refreshes the run list and inbox.
// EventSource reconnects by itself and sends Last-Event-ID, so nothing is missed. A slow poll stays as a
// safety net: runs started before the event-detail patch, and a run terminated from outside, write no events.

const EVENT_KINDS = ["run_started", "question", "approval", "approval_reminder", "pr_opened", "run_finished",
  "step_started", "step_finished", "approval_progress", "loop_round", "replied", "changes_requested"];
const LIVE = { timers: {}, ok: false };
function liveSoon(key, fn, ms) { clearTimeout(LIVE.timers[key]); LIVE.timers[key] = setTimeout(fn, ms); }
const watched = (run) => run === S.run || EXP.has(run) || TABS.list.some((t) => t.run === run) || PANEL.tabs.some((t) => t.run === run);
function connectLive(after) {
  if (!window.EventSource) return;
  const es = new EventSource(`/channel/events?after=${after}`);
  const on = (m) => {
    let e = null;
    try { e = JSON.parse(m.data); } catch { return; }
    if (e.run_id && watched(e.run_id)) liveSoon(`detail:${e.run_id}`, () => loadDetail(e.run_id), 120);
    liveSoon("runs", loadRuns, 400);
    if (e.run_id === S.run && e.kind === "step_finished") {
      if (activeTab()?.kind === "docs") liveSoon("docs", () => loadDocs(true), 800);
      CH.delete(e.run_id);
    }
  };
  for (const k of EVENT_KINDS) es.addEventListener(k, on);
  es.onopen = () => { LIVE.ok = true; $("#meta").classList.add("live-on"); $("#meta").title = "事件流已连接"; };
  es.onerror = () => { LIVE.ok = false; $("#meta").classList.remove("live-on"); $("#meta").title = "事件流断开，正在重连"; };
}

(async () => {
  let seq = 0;
  try { const m = await api("/api/meta"); $("#meta").textContent = m.user; seq = m.seq || 0; } catch { /* ignore */ }
  loadProjects();
  showView(store.get("myorch.view", "runs"));
  if (store.get("myorch.side", "on") === "off") document.body.classList.add("side-off");
  $("#work").classList.toggle("panel-off", !PANEL.open);
  $("#panelBtn").classList.toggle("on", PANEL.open);
  restoreTabs();
  if (applyHash()) BOOT.picked = true;
  else if (TABS.list.length) BOOT.picked = true;
  else showPane(null);
  renderPanel();
  for (const t of PANEL.tabs) if (!DETAIL.has(t.run)) loadDetail(t.run).then(renderPanelBar);
  loadRuns();
  connectLive(seq);
  setInterval(loadRuns, 30000);  // safety net, see above
  setInterval(() => { if (S.run && (!LIVE.ok || S.detail?.source === "engine")) loadDetail(); }, 5000);
})();
