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

const EXEC = { agent: ["AI", "agent"], script: ["SH", "script"], approval: ["USR", "human approval"], host: ["HOST", "host action"] };
const ACTIVE = new Set(["running", "waiting_input", "waiting_approval"]);

function toggle(panel, show) { panel.hidden = show === undefined ? !panel.hidden : !show; }
document.addEventListener("click", (e) => {
  if (!e.target.closest("#userBtn") && !e.target.closest("#userPanel")) $("#userPanel").hidden = true;
});

// ---- side bar: three views; clicking the shown view's icon folds the side bar ---------------------

const VIEWS = { runs: "Runs", inbox: "Waiting on you", flows: "Flow definitions" };
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
  catch (e) { $("#runs").replaceChildren(el("div", { class: "muted small pad", text: `Could not read the run list: ${e.message}. Check that Temporal is running.` })); return; }
  const warn = $("#volumeWarn");
  warn.textContent = data.volume_error ? "Cannot read the run data volume: docs, logs, initiators and start times are not shown for now" : "";
  warn.title = data.volume_error ? `Docker may not be running. Only the runs in Temporal are shown now.\n${data.volume_error}` : "";
  renderInbox(data);
  if (q !== S.q) return;  // the search changed while this one was on its way
  $("#runCount").textContent = q ? `${data.runs.length} found` : `${data.runs.length}`;
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

const INBOX_KIND = { approval: ["approval", "wait"], loop: ["loop full", "wait"], watchdog: ["watchdog", "bad"] };
function renderInbox(data) {
  const items = data.inbox;
  const title = (id) => data.runs.find((r) => r.run_id === id)?.labels?.[0] || id;
  $("#inbox").replaceChildren(...(items.length ? items.map((w) => {
    const [kind, cls] = INBOX_KIND[w.kind] || ["reply", "wait"];
    return el("div", { class: "ibx", title: w.run_id, onclick: () => openTab({ kind: "run", run: w.run_id }) },
      el("div", { class: "ibx1" }, el("span", { class: `tag ${cls}`, text: kind }), el("span", { class: "id", text: title(w.run_id) }),
        el("span", { class: "spacer" }), el("span", { class: "muted small", text: w.project || "" })),
      el("div", { class: "ibx2" }, el("b", { text: w.step }), el("span", { class: "muted", text: { approval: " waits for approval", loop: " ran all its rounds, waits for your decision", watchdog: " watchdog alert" }[w.kind] || " asks" })),
      w.text ? el("div", { class: "ibxq", text: w.text.slice(0, 280) }) : null);
  }) : [el("div", { class: "muted small pad", text: "Nothing is waiting on you." })]));
  $("#inboxCount").textContent = items.length || "";
  $("#stInbox").textContent = items.length ? `${items.length} waiting on you` : "Nothing waiting on you";
  $("#stInbox").classList.toggle("hot", items.length > 0);
  S.inboxRuns = new Set(items.map((w) => w.run_id));
}

function renderStatus() {
  const n = (S.lastRuns?.runs || []).filter((r) => r.status === "RUNNING").length;
  $("#stRuns").textContent = n ? `${n} running` : "";
}

// The tree: project › run › the run's steps. A project opens by default when it holds the shown run or
// a running one; a run opens when it has a tab; the user's toggles are kept.
const NO_PROJECT = "(no project)";
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
      waiting ? el("span", { class: "tag wait", text: `${waiting} waiting` }) : null,
      running ? el("span", { class: "tag", text: `${running} running` }) : null,
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
    el("span", { class: "caret", text: open ? "▾" : "▸", title: open ? "Collapse steps" : "Expand steps",
      onclick: (e) => { e.stopPropagation(); toggleExpand(r.run_id); } }),
    st(status), el("span", { class: "nm", text: r.labels?.[0] || r.run_id }),
    el("span", { class: "sub", text: r.labels?.[0] ? r.flow : "" }),
    el("span", { class: "meta", text: r.progress && r.status === "RUNNING" ? `${r.progress.done}/${r.progress.total}` : fmtTime(r.requested_at) }));
}

// A run's steps as tree rows: stage order, ∥ marks steps that run in parallel, a loop shows the steps
// of its shown round, a step with replicas has one row per replica.
function stepRows(run) {
  const d = DETAIL.get(run);
  if (!d) { loadDetail(run); return [el("div", { class: "tr ghost", style: "--d:2", text: "Loading…" })]; }
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
      sel: selId === id || selId === meta.id, title: meta.when ? `Condition: ${meta.when}` : "",
      meta: one?.attempts > 1 ? `attempt ${one.attempts}` : insts.length > 1 ? `×${insts.length}` : model })];
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
    rows.push(row(2, { status: t.status, name: `↻ ${t.id}`, meta: `round ${t.round}/${t.max_rounds}`, par: isPar(t), sel: selId === t.id,
      click: open(t.id), title: `Loop until ${t.until}` }));
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
  return (labels || []).map((l) => el("span", { class: "tag label", title: `Search "${l}"`, text: l,
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
    case "run": return { icon: st(d?.status || runOf(t.run)?.status || "pending"), title: rl, tip: `${t.run} · conversation` };
    case "step": {
      const i = d && findInstanceIn(d, t.step), loop = d?.tree.find((x) => x.kind === "loop" && x.id === t.step);
      return { icon: st(i?.status || loop?.status || "pending"), title: t.step, sub: rl, tip: `${t.run} · ${t.step}` };
    }
    case "changes": return { icon: icon("±"), title: "Changes", sub: rl, tip: `${t.run} · changes and review verdict` };
    case "docs": return { icon: icon("▤"), title: "Docs", sub: rl, tip: `${t.run} · docs/` };
    case "file": return { icon: icon("▢"), title: t.log ? `${t.step} · log` : t.path.split("/").pop(), sub: rl, tip: `${t.run} · ${t.log ? `${t.step} log of the last turn` : t.path}` };
    case "new": return { icon: icon("+"), title: "New run", tip: "New run" };
    case "flow": return { icon: icon("⑂"), title: t.ref.t === "kbform" ? "Register knowledge source" : t.ref.id, sub: { frag: "fragment", kb: "knowledge", ev: "eval" }[t.ref.t] || "", tip: t.ref.id || "" };
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
      el("button", { type: "button", class: "tx", title: "Close (middle click works too)", text: "×", onclick: (e) => { e.stopPropagation(); closeTab(t.key); } }));
  }), el("button", { type: "button", class: "tnew", title: "New run", text: "+", onclick: () => openNewRun() }));
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
    el("h2", { text: "myorch console" }),
    el("p", { class: "muted", text: "Pick a run on the left, or start a new run. Click a run to see its conversation; expand it to see its steps. While a step runs, its live output shows at the bottom." }),
    el("div", { class: "row" }, el("button", { text: "+ New run", onclick: () => openNewRun() }),
      el("button", { class: "ghost", text: "Waiting on you", onclick: () => showView("inbox") }),
      el("button", { class: "ghost", text: "Flow definitions", onclick: () => showView("flows") }))));
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
      const msg = el("p", { class: "muted pad", text: `Could not read ${run}: ${e.message}` });
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
      el("span", { class: "muted", text: `${d.labels?.[0] ? `${d.flow} · ` : ""}${d.project || "no project"}` }),
      ...labelChips((d.labels || []).slice(1)),
      el("span", { class: "spacer" }),
      btn("Changes", "Diff against base, review verdict, risk verdict", () => openTab({ kind: "changes", run: d.run_id })),
      btn("Docs", "The workspace's docs/", () => openTab({ kind: "docs", run: d.run_id })),
      btn("Flow definition", `Definition of ${d.flow}`, () => openFlows(d.flow))),
    el("div", { class: "rh2" }, el("span", { class: "mono", text: d.run_id }),
      m.initiator ? el("span", { text: `initiator ${m.initiator}` }) : null, m.requested_at ? el("span", { text: fmtTime(m.requested_at) }) : null,
      el("span", { text: `${done}/${all.length} steps done` }),
      ...(d.knowledge || []).map((k) => el("span", { class: `tag k-${k.kind}`, title: `Knowledge, fixed at start: ${k.kind === "mcp" ? k.url : k.version}`,
        text: k.kind === "mcp" ? `${k.name} · mcp` : `${k.name} · ${k.version.replace("sha256:", "").slice(0, 7)}` }))),
    inputs.length ? el("details", { class: "inputs", open: store.get("myorch.inputs.open", false) || null,
      ontoggle: (e) => store.set("myorch.inputs.open", e.currentTarget.open) },
      el("summary", { text: `Inputs · ${inputs.map(([k, v]) => `${k}=${String(v).slice(0, 24)}`).join("  ")}` }),
      el("div", { class: "igrid" }, inputs.map(([k, v]) => [el("span", { class: "muted", text: k }), el("span", { class: "pre", text: String(v).slice(0, 2000) })]))) : null]
    .filter(Boolean));
}

const WHO = { request: "You (started)", reply: "You", approve: "You", reject: "You", cancel: "You" };
const WHAT = { approve: "approved", reject: "rejected", cancel: "cancelled the run", changes: "requested changes for", resume: "re-ran" };

// The instances a resume would run again (RunState.reopen): failed or cancelled, in a run that ended so.
function resumable(d) {
  if (!d || (d.status !== "failed" && d.status !== "cancelled")) return [];
  return allInstances(d).filter((i) => i.status === "failed" || i.status === "cancelled").map((i) => i.id);
}

// A failed or cancelled run continues: the same run, its failed steps again (relay.resume).
async function resumeRun(steps) {
  const what = steps ? `step ${steps.join(", ")}` : "all failed and cancelled steps";
  if (!confirm(`Continue ${S.run}: re-run ${what}, and the later steps they blocked. Steps that succeeded keep their results.`)) return;
  $("#sendError").textContent = "";
  try {
    await api(`/api/runs/${enc(S.run)}/do/resume`, { method: "POST", body: steps ? { steps } : {} });
    S.convKey = null; loadDetail(); loadRuns();
  } catch (e) { const m = `Not continued: ${e.message}`; $("#sendError").textContent = m; if (activeTab()?.kind !== "run") alert(m); }
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
  if (it.kind === "request") return bubble("user", `${WHO.request}${it.by ? ` · ${it.by}` : ""}${it.at ? ` · ${fmtTime(it.at)}` : ""}`, el("div", { class: "pre", text: it.text || "(the original text was not recorded)" }));
  if (it.kind === "step") {
    const [icon] = EXEC[it.exec] || ["·"];
    const insts = it.instances;
    const status = aggStatus(insts);
    const name = `${it.step}${it.round ? ` · round ${it.round}` : ""}`;
    return el("div", { class: `event s-${status}`, title: "Open this step", onclick: () => openTab({ kind: "step", run: S.run, step: insts[0].id }, { preview: true }) },
      el("div", { class: "eh" }, st(status), el("span", { class: `ic k-${it.exec}`, text: icon }), el("b", { text: name }),
        insts[0].model ? el("span", { class: "tag", text: insts[0].model.replace("claude-", "") }) : null,
        insts.length > 1 ? el("span", { class: "muted small", text: ` ×${insts.length} parallel` }) : null,
        status === "running" ? el("button", { type: "button", class: "ghost small", text: "Live output", title: "See its output in the bottom panel",
          onclick: (e) => { e.stopPropagation(); panelOpen(S.run, insts.find((i) => i.status === "running")?.id || insts[0].id); } }) : null),
      insts.map((i) => (i.summary || i.error) ? el("div", { class: "es" }, insts.length > 1 ? el("span", { class: "muted", text: `x${i.replica} ` }) : null,
        el("span", { class: i.error ? "err" : "", text: i.error || i.summary })) : null));
  }
  if (it.kind === "question") {
    return bubble(`agent${it.answered ? " answered" : " asking"}`, `${it.step}${it.answered ? "" : " · waiting for your reply"}`,
      el("div", { class: "pre", text: it.text }),
      it.options?.length ? el("ol", { class: "opts" }, it.options.map((o) => el("li", { text: o }))) : null);
  }
  if (it.kind === "said") {
    const text = it.what === "reply" ? it.text : `${WHAT[it.what] || it.what} ${it.step || ""}${it.text ? `: ${it.text}` : ""}`;
    return bubble("user", `${WHO[it.what] || "You"}${it.by ? ` · ${it.by}` : ""}${it.at ? ` · ${fmtTime(it.at)}` : ""}${it.what === "reply" ? ` → ${it.step}` : ""}`, el("div", { class: "pre", text }));
  }
  if (it.kind === "approval") {
    return bubble("agent asking", `${it.step} · waiting for your approval`, el("div", { text: it.missing || `${it.needed} approval(s) needed` }),
      el("div", { class: "small muted", text: `Approved by: ${it.approvals.join(", ") || "nobody"}` }),
      el("button", { class: "ghost small", text: "See changes and review verdict", onclick: () => openTab({ kind: "changes", run: S.run }) }));
  }
  if (it.kind === "pr") return el("div", { class: "event s-succeeded" }, el("div", { class: "eh" }, el("span", { class: "ic k-pr", text: "PR" }), el("b", { text: "PR opened " }), el("a", { href: it.url, target: "_blank", rel: "noopener", text: it.url })));
  if (it.kind === "watchdog") {  // myorch/watchdog.py: alive but not getting anywhere; the user decides
    return bubble("agent asking watchdog", `WATCHDOG · ${it.step} · ${it.label}`,
      el("div", { class: "pre", text: it.reason }),
      it.evidence ? el("pre", { class: "small", text: it.evidence }) : null,
      el("div", { class: "small muted", text: "The watchdog only alerts; it takes no action. You can cancel this run or let it go on." }),
      el("button", { class: "ghost small", text: "Got it", onclick: async () => {
        try { await api(`/api/runs/${enc(S.run)}/do/dismiss`, { method: "POST", body: { text: it.alert } }); S.convKey = null; loadDetail(); loadRuns(); }
        catch (e) { $("#sendError").textContent = e.message; }
      } }));
  }
  if (it.kind === "end") {
    const line = el("div", { class: `endline s-${it.status}`, text: { succeeded: "✓ Run succeeded", failed: "✗ Run failed", cancelled: "⊘ Run cancelled" }[it.status] || it.status });
    const buttons = [
      resumable(S.detail).length ? el("button", { class: "ghost small", text: "Re-run failed steps", title: "The same run goes on: failed and cancelled steps run again; succeeded steps keep their results",
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
  $("#resumeRunBtn").title = retry.length ? `Re-run: ${retry.join(", ")}, and the later steps they blocked` : "";
  $("#continueRunBtn").hidden = !canContinue(d);  // newrun.js: a flow input with a `continue` spec
  if (canContinue(d)) $("#continueRunBtn").textContent = continueLabel(d);
  let label, placeholder, can = true;
  if (target.mode === "reply") {
    label = `To → ${target.step} (waiting for your reply)`;
    placeholder = target.options?.length ? `The reply goes verbatim to ${target.step}. Reply with an option number or write your own answer.` : `The reply goes verbatim to ${target.step}. It continues its earlier session.`;
    hint.textContent = target.others?.length ? `Also waiting on you: ${target.others.join(", ")}. Handle them after this one.` : "Enter sends. Shift+Enter adds a new line.";
  } else if (target.mode === "approve") {
    label = `${target.step} waits for your approval: ${target.missing}`;
    placeholder = "Approval note (optional). Recorded with the approval or rejection.";
    hint.textContent = "Look at Changes before you decide.";
    can = false;
  } else if (target.mode === "busy") {
    label = `Running: ${target.running.join(", ") || "scheduling"}`;
    placeholder = "You cannot talk to a running step. Send when it asks, needs approval or ends.";
    hint.textContent = "";
    can = false;
  } else {
    label = `Run ended (${target.status})`;
    placeholder = retry.length ? `Failed steps: ${retry.join(", ")}. Fix the cause, then click "Re-run failed steps" at the bottom right to continue this run.`
      : canContinue(d) && continueEnded(d) ? continueEnded(d)
      : "This run has ended. To make more changes, click \"+\" to start a new run.";
    hint.textContent = retry.length ? "Steps that succeeded keep their results and do not run again." : "";
    can = false;
  }
  $("#target").textContent = label;
  $("#target").className = `target mode-${target.mode}`;
  // Nothing to write (running or ended): one row with the status, the explanation and the buttons,
  // instead of a disabled text box. The text box is kept for replies and approval notes.
  const compact = !can && target.mode !== "approve";
  $("#composer").classList.toggle("compact", compact);
  if (compact) hint.textContent = [placeholder, hint.textContent].filter(Boolean).join(" ");
  hint.title = compact ? hint.textContent : "";
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
  } catch (e) { $("#sendError").textContent = `Not sent: ${e.message}`; }
  $("#sendBtn").disabled = false;
}

async function decideApproval(action) {
  if (!S.run || S.target?.mode !== "approve") return;
  if (action === "reject" && !confirm(`Reject ${S.target.step}? The run ends after a rejection.`)) return;
  $("#sendError").textContent = "";
  try {
    await api(`/api/runs/${enc(S.run)}/do/${action}`, { method: "POST", body: { step: S.target.step, text: $("#input").value } });
    $("#input").value = ""; delete DRAFT[S.run];
    S.convKey = null;
    await loadDetail(); loadRuns();
  } catch (e) { $("#sendError").textContent = `Not done: ${e.message}`; }
}

async function cancelRun() {
  if (!S.run || !confirm(`Cancel run ${S.run}? Running steps are stopped.`)) return;
  try { await api(`/api/runs/${enc(S.run)}/do/cancel`, { method: "POST", body: {} }); S.convKey = null; loadDetail(); loadRuns(); }
  catch (e) { $("#sendError").textContent = `Not cancelled: ${e.message}`; }
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
  if (!d) { pane.replaceChildren(el("p", { class: "muted pad", text: "Loading…" })); return; }
  const i = findInstance(S.step);
  const loop = d.tree.find((t) => t.kind === "loop" && t.id === S.step);
  if (loop) return renderLoop(pane, run, loop);
  if (!i) {
    const meta = stepMeta(d, S.step);
    pane.replaceChildren(el("div", { class: "page" }, meta
      ? [el("div", { class: "ph" }, st("pending"), el("span", { class: `ic k-${meta.exec}`, text: (EXEC[meta.exec] || ["·"])[0] }), el("h2", { text: meta.id })),
        kv([["Status", "not started"], ["Executor", (EXEC[meta.exec] || [])[1]], ["Model", meta.model], ["Condition", meta.when], ["Needs", (meta.needs || []).join(", ")]])]
      : el("p", { class: "muted", text: `This run has no step ${S.step}.` })));
    return;
  }
  const btn = (text, fn, cls = "ghost small", title) => el("button", { type: "button", class: cls, text, title, onclick: fn });
  const exec = i.exec || stepMeta(d, i.step)?.exec;
  const head = el("div", { class: "ph" }, st(i.status), exec ? el("span", { class: `ic k-${exec}`, text: (EXEC[exec] || ["·"])[0] }) : null,
    el("h2", { text: i.id }), i.model ? el("span", { class: "tag", text: i.model.replace("claude-", "") }) : null,
    el("span", { class: "muted small", text: i.status }), el("span", { class: "spacer" }),
    btn("Live output", () => panelOpen(run, i.id), "ghost small", "See this step's output in the bottom panel"),
    i.status === "waiting_approval" ? btn("See changes and review verdict", () => openTab({ kind: "changes", run })) : null,
    (i.status === "failed" || i.status === "cancelled") && (d.status === "failed" || d.status === "cancelled")
      ? btn("Re-run from this step", () => resumeRun([i.id]), "ghost small", "Re-run only this step and the later steps it blocked") : null,
    btn("Conversation", () => openTab({ kind: "run", run }), "ghost small", "Back to this run's conversation"));
  const main = [];
  if (i.error) main.push(el("h4", { text: "Error" }), el("pre", { class: "err", text: i.error }));
  if (i.question) main.push(el("h4", { text: "Question" }), el("pre", { text: i.question.question + (i.question.options.length ? "\n" + i.question.options.map((o, n) => `${n + 1}. ${o}`).join("\n") : "") }));
  if (i.summary) main.push(el("h4", { text: "Summary" }), el("div", { class: "pre", text: i.summary }));
  if (i.notes) main.push(el("h4", { text: "notes" }), el("div", { class: "pre small", text: i.notes }));
  if (Object.keys(i.outputs || {}).length) main.push(el("h4", { text: "outputs" }), fileView({ path: "outputs.json", title: `${i.id} · outputs`, text: JSON.stringify(i.outputs, null, 2), html: null }));
  if (!main.length) main.push(el("p", { class: "muted small", text: i.status === "running" ? "Running. The output is in the live output at the bottom; the summary shows here when it ends." : "No summary." }));

  const files = el("div", { class: "filelist" }, el("div", { class: "muted small", text: "Loading (about 2 s)…" }));
  const side = [el("h4", { text: "Status" }), kv([["Status", i.status], ["Model", i.model], ["Attempts", i.attempts > 1 ? i.attempts : null], ["Turns", i.turns > 1 ? i.turns : null],
    ["Exit code", i.exit_code], ["manifest", i.manifest_status !== "ok" ? i.manifest_status : null],
    ["Approvals", i.approvals_needed ? `${i.approvals.length}/${i.approvals_needed}: ${i.approvals.join(", ") || "none"}` : null]]),
    el("h4", { text: "Artifacts and logs" }), files];
  let kbox = null;
  if ((d.knowledge || []).length && exec === "agent") { kbox = el("div", { class: "kuse" }, el("div", { class: "muted small", text: "Loading…" })); side.push(el("h4", { text: "Knowledge reads" }), kbox); }
  pane.replaceChildren(el("div", { class: "page" }, head, el("div", { class: "pgrid" }, el("div", { class: "pmain" }, main), el("div", { class: "pside" }, side))));

  const still = () => activeTab()?.kind === "step" && S.run === run && S.step === i.id;
  stepFiles(run, i).then((f) => {
    if (!still()) return;
    files.replaceChildren(
      el("div", { class: "f", title: "The full log of the last turn", onclick: () => openTab({ kind: "file", run, step: i.id, log: true }, { preview: true }) }, el("span", { class: "fi", text: "≡" }), "Log (last turn)"),
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
    el("div", { class: "ph" }, st(t.status), el("span", { class: "ic k-loop", text: "LOOP" }), el("h2", { text: t.id }), el("span", { class: "muted small", text: `round ${t.round}/${t.max_rounds}` })),
    kv([["Status", t.status], ["Round", `${t.round}/${t.max_rounds}`], ["Until", t.until], ["Error", t.error]]),
    t.question ? [el("h4", { text: "Waiting for your decision" }), el("pre", { text: t.question.question + "\n" + t.question.options.map((o, n) => `${n + 1}. ${o}`).join("\n") }),
      el("button", { class: "ghost small", text: "Reply in the conversation", onclick: () => openTab({ kind: "run", run }) })] : null,
    t.rounds.length ? el("div", { class: "rtabs" }, t.rounds.map((r) => el("button", { type: "button", class: `rtab${r.round === shown ? " on" : ""}`, text: `round ${r.round}`,
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
    el("button", { type: "button", class: "tx", text: "×", title: "Close", onclick: (e) => { e.stopPropagation(); panelClose(t.key); } }))),
    ...(PANEL.tabs.length ? [] : [el("span", { class: "muted small", text: "Live output: open a running step, or click its \"Live output\"." })]));
  const t = PANEL.tabs.find((x) => x.key === PANEL.active);
  const b = (text, title, fn, on) => el("button", { type: "button", class: `vbtn${on ? " on" : ""}`, text, title, onclick: fn });
  $("#pactions").replaceChildren(...[
    t ? b("Open step", "Open this step in the editor area", () => openTab({ kind: "step", run: t.run, step: t.step })) : null,
    t ? b("Full log", "The full log of the last turn", () => openTab({ kind: "file", run: t.run, step: t.step, log: true })) : null,
    b("Wrap", "Wrap long lines", () => { PANEL.wrap = !PANEL.wrap; PANEL.body?.classList.toggle("nowrap", !PANEL.wrap); renderPanelBar(); }, PANEL.wrap),
    b(PANEL.max ? "⤡ Restore" : "⤢ Maximise", PANEL.max ? "Restore (Esc)" : "Fill the editor area", () => setPanelMax(!PANEL.max)),
    b("⌄", "Hide", () => setPanelOpen(false))].filter(Boolean));
}

function panelConnect(key) {
  if (PANEL.es) PANEL.es.close();
  Object.assign(PANEL, { es: null, key: key || "", partial: "", turn: "", stick: true, body: null });
  const box = $("#pbody");
  if (!key) { box.replaceChildren(); return; }
  const t = PANEL.tabs.find((x) => x.key === key);
  const body = el("div", { class: `liveout${PANEL.wrap ? "" : " nowrap"}` },
    el("div", { class: "lo-meta", text: panelStatus(t) === "running" ? "Waiting for output…" : "Reading the output record… (output is kept 7 days; if there is none, click \"Full log\")" }));
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
    if (PANEL.turn) box.append(el("div", { class: "lo-sep", text: `—— attempt ${c.attempt} · turn ${c.turn} ——` }));
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
  const viewer = el("div", { class: "dview" }, el("p", { class: "muted small pad", text: "Pick a file on the left." }));
  const list = el("div", { class: "dlist" }, el("div", { class: "muted small", text: "Loading…" }));
  pane.replaceChildren(list, viewer);
  try {
    const files = await api(`/api/runs/${enc(run)}/files?dir=docs`);
    if (run !== S.run) return;
    list.replaceChildren(el("div", { class: "row" }, el("h4", { text: "docs/" }), el("span", { class: "spacer" }), el("button", { class: "ghost small", text: "Refresh", onclick: () => loadDocs(true) })),
      el("div", { class: "filelist" }, files.length ? files.map((p) => el("div", { class: "f", title: p, text: p.replace(/^docs\//, ""), onclick: (e) => {
        list.querySelectorAll(".f.on").forEach((x) => x.classList.remove("on")); e.currentTarget.classList.add("on"); showFile(p, viewer);
      } })) : el("div", { class: "muted small", text: "No docs yet." })));
  } catch (e) { list.replaceChildren(el("p", { class: "muted small", text: e.message })); }
}

async function showFile(path, target) {
  target.replaceChildren(el("div", { class: "muted small pad", text: `Loading ${path} …` }));
  try {
    const f = await api(`/api/runs/${enc(S.run)}/file?path=${enc(path)}`);
    target.replaceChildren(fileView(f));
  } catch (e) { target.replaceChildren(el("div", { class: "muted small pad", text: e.message })); }
}

async function loadFileTab(t) {
  const pane = $("#pane-file"), key = t.key;
  pane.replaceChildren(el("div", { class: "muted small pad", text: t.log ? "Loading the log (about 2 s)…" : `Loading ${t.path} …` }));
  try {
    let f;
    if (t.log) {
      if (!DETAIL.get(t.run)) await loadDetail(t.run);
      const i = DETAIL.get(t.run) && findInstanceIn(DETAIL.get(t.run), t.step);
      const sf = i ? await stepFiles(t.run, i) : await api(`/api/runs/${enc(t.run)}/steps/${enc(t.step)}`);
      f = { path: "step.log", title: `${t.step} · log (last turn)`, text: sf.log || "(no log)", html: null };
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
    return { text: out.join("\n"), note: bad ? `Formatted. ${bad} lines are not valid JSON and stay as they are` : "Formatted" };
  }
  if (!parsesAsJson(text)) return { text, note: "Not valid JSON. Showing the original text" };
  const out = prettyJson(text);
  return { text: out, note: out === text.trim() ? "" : "Formatted" };
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
    const copy = btn("Copy", false, async (e) => {
      const b = e.currentTarget;
      try { await navigator.clipboard.writeText(rendered ? f.text : shown.text); b.textContent = "Copied"; }
      catch { b.textContent = "Copy failed"; }
      setTimeout(() => { b.textContent = "Copy"; }, 1200);
    }, "Copy what is shown now");
    const bar = el("div", { class: "vbar" },
      el("span", { class: "vpath", title: f.path, text: f.title || f.path }),
      el("span", { class: "tag vlang", text: lang === "plaintext" ? "text" : lang }),
      shown.note && !rendered ? el("span", { class: "vnote", text: shown.note }) : null,
      el("span", { class: "spacer" }),
      isMd ? el("span", { class: "seg" }, btn("Rendered", st.mode === "render", set("mode", "render")), btn("Source", st.mode === "source", set("mode", "source"))) : null,
      lang === "json" ? btn("Format", st.fmt, set("fmt", !st.fmt), "Format / show the original text") : null,
      rendered ? null : btn("Wrap", st.wrap, set("wrap", !st.wrap), "Wrap long lines"),
      copy,
      st.full ? btn("Close", false, () => $("#viewerDlg").close(), "Close (Esc)")
        : btn("⤢ Full screen", false, () => openFull(f, st), "View full screen in a dialog"));
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
  img.addEventListener("error", () => body.replaceChildren(el("div", { class: "muted small", text: `Could not load: ${f.path}` })));
  const bar = el("div", { class: "vbar" },
    el("span", { class: "vpath", title: f.path, text: f.title || f.path }),
    el("span", { class: "tag vlang", text: "image" }),
    el("span", { class: "spacer" }),
    el("a", { class: "vbtn", href: f.image, target: "_blank", rel: "noopener", text: "Original" }),
    full ? btn("Close", () => $("#viewerDlg").close(), "Close (Esc)") : btn("⤢ Full screen", () => openFull(f, {}), "View full screen in a dialog"));
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
  return el("button", { type: "button", class: "vbtn maxbtn", text: "⤢ Maximise", title: "Fill the window (Esc restores)",
    onclick: () => setMax(box, MAX.box !== box) });
}

function setMax(box, on) {
  if (on && MAX.box && MAX.box !== box) setMax(MAX.box, false);
  if (!on && MAX.box !== box) return;
  box.classList.toggle("maxed", on);
  const b = box.querySelector(":scope > * > .maxbtn");
  if (b) { b.textContent = on ? "⤡ Restore" : "⤢ Maximise"; b.title = on ? "Restore (Esc)" : "Fill the window (Esc restores)"; }
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


// The changes tab: one drawn page per run, kept until Refresh (the diff takes a helper container, ~2 s).
const CH = new Map();
async function loadChanges(force = false) {
  const pane = $("#pane-changes"), runId = S.run;
  if (!runId) return;
  if (!force && CH.has(runId)) { pane.replaceChildren(CH.get(runId)); return; }
  pane.replaceChildren(el("p", { class: "muted small pad", text: "Loading the changes (about 2 s)…" }));
  let d;
  try { d = await api(`/api/runs/${enc(runId)}/changes`); }
  catch (e) { if (runId === S.run) pane.replaceChildren(el("p", { class: "muted small pad", text: `Could not read the changes: ${e.message}` })); return; }
  const kids = [el("div", { class: "row" }, el("h4", { text: `Changes against ${d.base}` }), el("span", { class: "spacer" }),
    el("button", { class: "ghost small", text: "Refresh", onclick: () => loadChanges(true) }))];
  if (d.no_diff_reason || !d.files.length) {
    kids.push(el("p", { class: "muted small", text: d.no_diff_reason ? "This run has no code repository, or has not fetched the code yet." : "No changes." }));
  } else {
    const added = d.files.reduce((n, f) => n + f.added, 0), removed = d.files.reduce((n, f) => n + f.removed, 0);
    const diffs = el("div", { class: "diffs" });
    for (const f of d.files) diffs.append(renderDiff(f));
    const box = el("div", { class: "diffbox" });
    box.append(el("div", { class: "dbar" }, el("span", { class: "small muted", text: `${d.files.length} files, +${added} −${removed}` }),
      el("span", { class: "spacer" }), maxButton(box)),
    el("div", { class: "dbody" },
      d.log ? el("pre", { class: "small", text: d.log }) : null,
      el("div", { class: "filelist" }, d.files.map((f, n) => el("div", { class: "f", onclick: () => diffs.children[n]?.scrollIntoView({ block: "start" }) },
        el("span", { text: f.path }), el("span", { class: "add", text: ` +${f.added}` }), el("span", { class: "del", text: ` −${f.removed}` })))),
      diffs));
    kids.push(box);
  }
  if (d.review_round) {
    kids.push(el("h4", { text: `Review verdict (round ${d.review_round})` }));
    if (d.summary_html) kids.push(Object.assign(el("div", { class: "md small" }), { innerHTML: d.summary_html }));
    const open = d.findings.filter((f) => f.status !== "fixed");
    if (open.length) kids.push(el("table", { class: "findings" }, el("tr", {}, ["Severity", "Location", "Problem"].map((h) => el("th", { text: h }))),
      open.map((f) => el("tr", {}, el("td", { class: `sev-${f.severity}`, text: f.severity || "" }), el("td", { text: `${f.file || ""}${f.line ? `:${f.line}` : ""}` }),
        el("td", {}, el("div", { text: f.title || "" }), f.evidence ? el("div", { class: "muted", text: f.evidence }) : null)))));
  }
  if (d.risk_html) kids.push(el("h4", { text: "Risk verdict" }), Object.assign(el("div", { class: "md small" }), { innerHTML: d.risk_html }));
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
  if (!$("#input").value.trim()) { $("#sendError").textContent = "Write your change requests in the input box first."; $("#input").focus(); return; }
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
  es.onopen = () => { LIVE.ok = true; $("#meta").classList.add("live-on"); $("#meta").title = "Event stream connected"; };
  es.onerror = () => { LIVE.ok = false; $("#meta").classList.remove("live-on"); $("#meta").title = "Event stream disconnected, reconnecting"; };
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
