// Flow definitions, drawn live from flows/*.yaml (ui/flowgraph.py): the side bar's Flow definitions view lists
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
      byStage.get(k).length > 1 ? el("div", { class: "parnote", text: "parallel" }) : null)));
}

// The compact form (the new-run tab's preview): one row per step, stage numbers, ∥ for parallel steps,
// a loop's steps indented under it.
function defOutline(tree, onPick) {
  const out = [];
  const tags = (s) => [s.model ? s.model.replace("claude-", "") : "", s.when ? "condition" : "", s.interaction === "allowed" ? "may ask" : "",
    s.approval ? `approval ${s.approval.count}` : "", s.fanout > 1 ? `×${s.fanout}` : ""].filter(Boolean).join(" · ");
  const row = (s, depth, num, par) => el("div", { class: "orow", style: `--d:${depth}`, title: s.when ? `when ${s.when}` : "", onclick: () => onPick(s) },
    el("span", { class: "onum", text: num }), el("span", { class: "par", text: par ? "∥" : "" }),
    el("span", { class: `ic k-${s.kind === "loop" ? "loop" : s.exec}`, text: s.kind === "loop" ? "↻" : (EXEC[s.exec] || ["·"])[0] }),
    el("span", { class: "nm", text: s.id }), el("span", { class: "meta", text: s.kind === "loop" ? `at most ${s.max_rounds} rounds` : tags(s) }));
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
    s.when ? el("span", { class: "tag cond", text: "condition" }) : null,
    s.interaction === "allowed" ? el("span", { class: "tag", text: "may ask" }) : null,
    s.approval ? el("span", { class: "tag", text: `approval ${s.approval.count}` }) : null,
    s.use ? el("span", { class: "tag frag", text: s.use.replace(/^lib\//, "").replace(/\.yaml$/, "") }) : null,
  ].filter(Boolean);
  return el("div", { class: `cell def${s.id === selId ? " sel" : ""}`, title: `${label}${s.when ? `\nCondition: ${s.when}` : ""}`, onclick: (e) => { e.stopPropagation(); onPick(s); } },
    el("div", { class: "c1" }, el("span", { class: `ic k-${s.exec}`, text: icon }), el("span", { class: "nid", text: s.id }),
      s.fanout > 1 ? el("span", { class: "fan", text: `×${s.fanout}` }) : null),
    tags.length ? el("div", { class: "c2" }, tags) : null,
    s.when ? el("div", { class: "c3 when", text: `when ${s.when}` }) : null);
}

function defLoop(t, onPick, selId) {
  const steps = t.steps.map((s) => ({ ...s, _stage: t.stages[s.id] }));
  return el("div", { class: `loop def${t.id === selId ? " sel" : ""}`, onclick: (e) => { e.stopPropagation(); onPick(t); } },
    el("div", { class: "lhead" }, el("span", { class: "ic k-loop", text: "LOOP" }), el("b", { text: t.id }),
      t.use ? el("span", { class: "tag frag", text: t.use.replace(/^lib\//, "").replace(/\.yaml$/, "") }) : null,
      el("span", { class: "muted small", text: `at most ${t.max_rounds} rounds · until ${t.until} · when exhausted ${t.on_exhausted}` })),
    t.when ? el("div", { class: "c3 when lw", text: `when ${t.when}` }) : null,
    el("div", { class: "inner" }, rows(steps, (s) => defCell(s, onPick, selId)),
      t.reflect ? el("div", { class: "reflect" }, el("span", { class: "muted small", text: "after each round" }), defCell(t.reflect, onPick, selId)) : null));
}

function defTree(tree, onPick, selId) {
  return el("div", { class: "flow defflow" }, rows(tree.map((t) => ({ ...t, _stage: t.stage })),
    (t) => (t.kind === "loop" ? defLoop(t, onPick, selId) : defCell(t, onPick, selId))));
}

function defDetail(s) {
  if (!s) return el("p", { class: "muted small", text: "Click a step to see its configuration." });
  if (s.kind === "loop") {
    return el("div", {}, el("h4", { text: `Loop ${s.id}` }),
      kv([["From fragment", s.use], ["Max rounds", s.max_rounds], ["Until", s.until], ["When exhausted", s.on_exhausted], ["Condition", s.when],
        ["Needs", (s.needs || []).join(", ")], ["Steps", s.steps.map((x) => x.id).join(" → ")], ["After each round", s.reflect?.id]]));
  }
  const a = s.approval;
  return el("div", {}, el("h4", {}, el("span", { class: `ic k-${s.exec}`, text: (EXEC[s.exec] || ["·"])[0] }), ` ${s.id}`),
    kv([["Executor", (EXEC[s.exec] || [])[1]], ["harness", s.harness], ["Action", s.action], ["From fragment", s.use], ["profile", s.profile],
      ["Model", s.model], ["Image", s.image], ["Parallel copies", s.fanout > 1 ? s.fanout : null], ["Condition", s.when],
      ["Needs", (s.needs || []).join(", ")], ["May ask", s.interaction === "allowed" ? "yes" : null],
      ["Outputs", (s.outputs || []).join(", ")], ["On failure", s.on_failed], ["Retries", s.retries || null], ["Timeout", s.timeout],
      ["Approval", a ? `${a.count} ${a.count === 1 ? "person" : "people"}${a.when ? `, when ${a.when}` : ""}${a.require_initiator ? ", including the initiator" : ""}${a.changes ? `, can send back to ${a.changes}` : ""}` : null]]));
}

// ---- the Flow definitions view (side bar) and the flow tabs -------------------------------------------------

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
  return el("span", { class: "livetag", title: "When a file under flows/ changes, this redraws within seconds" }, el("span", { class: "dot" }), `LIVE · ${FL.version || ""} · ${t}`);
}

// What the active tab shows, when it is a flow tab.
function flCur() { const t = activeTab(); return t?.kind === "flow" ? t.ref : null; }
const refIs = (t, id) => { const c = flCur(); return !!c && c.t === t && (id === undefined || c.id === id); };
function flOpenRef(ref, preview = true) { openTab({ kind: "flow", ref }, { preview }); }

// The side bar is shown (app.js showView).
async function flOpenView() {
  try { await flEnsure(); } catch (e) { $("#flowList").replaceChildren(el("div", { class: "muted small pad", text: `Could not read the flows: ${e.message}` })); return; }
  flRenderList();
}

// A flow tab is shown (app.js showPane): its ref decides what the main area draws.
async function flShowTab(ref) {
  const pane = $("#pane-flow");
  if (!FL.overview) pane.replaceChildren(el("p", { class: "muted small pad", text: "Loading…" }));
  try { await flEnsure(); } catch (e) { pane.replaceChildren(el("p", { class: "muted small pad", text: `Could not read the flows: ${e.message}` })); return; }
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
    el("div", { class: "line1" }, el("span", { class: "id", text: f.flow }), f.error ? el("span", { class: "tag cond", text: "error" }) : null,
      el("span", { class: "role", text: f.label || f.role || "" })));
  const fragItem = (g) => el("div", { class: `item fl${refIs("frag", g.name) ? " active" : ""}`, "data-q": g.name.toLowerCase(),
    title: `${g.description || ""}${g.used_by.length ? `\nUsed in ${g.used_by.join(", ")}` : "\nNo flow uses it"}`, onclick: () => flOpenRef({ t: "frag", id: g.name }) },
    el("div", { class: "line1" }, el("span", { class: `ic k-${g.kind === "loop" ? "loop" : "script"}`, text: g.kind === "loop" ? "LOOP" : "STEP" }), el("span", { class: "id", text: g.name }),
      g.used_by.length ? null : el("span", { class: "role", text: "unused" })));
  const STATUS = { pending: "to confirm", confirmed: "kept", rejected: "dropped" };
  const caseItem = (c) => el("div", { class: `item${refIs("ev", c.id) ? " active" : ""}`, onclick: () => flOpenRef({ t: "ev", id: c.id }) },
    el("div", { class: "line1" }, el("span", { class: "id", text: c.id }), el("span", { class: `tag${c.status === "pending" ? " cond" : ""}`, text: STATUS[c.status] || c.status })),
    el("div", { class: "sub", text: `${c.project} · ${c.findings.length} findings${c.status === "confirmed" ? ` · ${c.keep.length} kept` : ""}` }));
  const kbItem = (k) => el("div", { class: `item${refIs("kb", kbKey(k)) ? " active" : ""}`, onclick: () => flOpenRef({ t: "kb", id: kbKey(k) }) },
    el("div", { class: "line1" }, el("span", { class: "ic k-script", text: { dir: "DIR", git: "GIT", mcp: "MCP" }[k.kind] || k.kind }),
      el("span", { class: "id", text: k.name }), k.problems.length ? el("span", { class: "tag cond", text: "problems" }) : null),
    el("div", { class: "sub", text: `${k.project ? `${k.project} · ` : k.level === "template" ? "template · " : "global · "}${k.kind === "mcp" ? "live queries" : k.latest ? `synced ${fmtTime(k.latest.synced_at)}` : "no snapshot yet (synced when a run starts)"}` }));
  const kbNew = el("div", { class: `item${refIs("kbform") && !FL.kbForm?.edit ? " active" : ""}`, onclick: () => kbOpenForm(null) },
    el("div", { class: "line1" }, el("span", { class: "id", text: "+ Register knowledge source" })), el("div", { class: "sub", text: "A folder, a git repository or an MCP server" }));
  const filter = el("input", { type: "search", class: "fd-filter", placeholder: "Filter by name or role, e.g. ck, review, learn", value: FL.filter,
    oninput: (e) => { FL.filter = e.target.value; flFilter(box); } });
  const groups = flGroups(o.flows);
  const active = o.flows.find((f) => refIs("flow", f.flow));
  const keep = box.scrollTop, typing = document.activeElement?.classList.contains("fd-filter");
  box.replaceChildren(el("div", { class: "row fd-live" }, flLive()), filter,
    el("h5", { text: `Flows · ${o.flows.length}` }),
    ...groups.map((g) => flGroup(`p:${g.key}`, g.key || "General", g.prefix, g.flows.map(flowItem),
      g.key === (FL.project || "") || (active && g.flows.includes(active)), g.title)),
    flGroup("frag", "Fragments", "lib/*", o.fragments.map(fragItem), refIs("frag"), "flows/lib/*.yaml: flows reference them with use:"),
    el("h5", { text: `Eval set · ${FL.cases.filter((c) => c.status === "confirmed").length}/${FL.cases.length}` }), el("div", { class: "list" },
      FL.cases.length ? FL.cases.map(caseItem) : el("div", { class: "muted small", text: "No candidate cases yet. Run myorch eval collect in a terminal." })),
    el("h5", { text: `Knowledge · ${FL.knowledge.length}` }), el("div", { class: "list" }, kbNew, ...FL.knowledge.map(kbItem)));
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
    [main, side] = evCase ? caseView(evCase) : [el("section", { class: "fd-main" }, el("p", { class: "muted", text: "This case no longer exists." })), el("aside", { class: "fd-side" })];
  } else if (FL.kb === "+form" && FL.kbForm) {
    [main, side] = kbFormView(FL.kbForm);
  } else if (FL.kb) {
    const k = kbRow;
    main = el("section", { class: "fd-main" }, k ? [
      el("div", { class: "fd-title" }, el("span", { class: "big", text: k.name }), el("span", { class: "file", text: `myorch-knowledge/${k.file}` })),
      k.problems.length ? el("div", { class: "warn", text: k.problems.join("; ") }) : null,
      kv([["Type", { dir: "local folder", git: "git repository", mcp: "MCP server" }[k.kind]], ["Registered in", k.level], ["Location", k.where], ["Expanded", k.expanded],
        ["Read first", k.index], ["Tools", (k.tools || []).join(", ")], ["Projects that may use it", (k.projects || []).join(", ") || "all projects"],
        ["Mounted by default in", (k.default_in || []).join(", ")],
        ["Latest snapshot", k.latest ? `${k.latest.version} · ${fmtTime(k.latest.synced_at)} · ${k.latest.files} files · ${(k.latest.bytes / 1024).toFixed(1)} KB` : (k.kind === "mcp" ? "MCP has no snapshots" : "not synced (it syncs when a run starts)")],
        ["Versions kept", k.kind === "mcp" ? null : String(k.versions)]]),
      k.kind !== "mcp" ? el("p", { class: "muted small", text: k.kind === "dir"
        ? "Snapshots: synced automatically each time a run starts. If the content has not changed, the old snapshot is reused. A new snapshot affects only runs started later."
        : "Snapshots: when a run starts, the repository is cloned again if the latest snapshot is older than 1 hour. If the clone fails, the previous snapshot is used." }) : null,
      k.level === "template" ? el("p", { class: "muted small", text: "Registered in the template. The console does not change templates. To change it, edit the file above." })
        : el("div", { class: "evbar" },
          el("button", { text: "Edit", onclick: () => kbOpenForm(k) }),
          el("button", { class: "ghost danger", text: "Unregister", onclick: () => kbRemove(k) }))] : el("p", { class: "muted", text: "This knowledge source no longer exists." }));
    side = el("aside", { class: "fd-side" }, el("p", { class: "muted small", text: "Knowledge is mounted read-only into agent steps: /sandbox/workspace/knowledge/<name>/. MCP knowledge is reached through an OpenShell provider; the agent can call only the registered tools." }));
  } else if (FL.frag) {
    const g = o.fragments.find((x) => x.name === FL.frag);
    main = el("section", { class: "fd-main" }, g ? [
      el("div", { class: "fd-title" }, el("span", { class: "big", text: g.name }), el("span", { class: "file", text: g.file })),
      g.error ? el("div", { class: "warn", text: g.error }) : null,
      el("div", { class: "desc", text: g.description || "(no description)" }),
      el("h4", { text: "Parameters" }), el("div", { class: "chips2" }, (g.params || []).map((p) => el("span", { class: "tag", text: p }))),
      el("h4", { text: "Flows that use it" }), el("div", { class: "chips2" }, g.used_by.length ? g.used_by.map((f) => el("button", { class: "ghost small", text: f, onclick: () => flOpenRef({ t: "flow", id: f }, false) })) : el("span", { class: "muted small", text: "none" })),
      el("p", { class: "muted small", text: "A fragment expands only together with its parameters. Click a flow above to see it inside the flow." })] : el("p", { class: "muted", text: "The fragment no longer exists." }));
    side = el("aside", { class: "fd-side" }, el("p", { class: "muted small", text: "Flows reference fragments with use:." }));
  } else {
    const f = o.flows.find((x) => x.flow === FL.flow);
    const gr = FL.graph;
    const projSel = el("select", { class: "small", onchange: (e) => { FL.project = e.target.value || null; if (FL.project) localStorage.setItem("myorch.project", FL.project); flRefreshGraph(); } },
      el("option", { value: "", text: "(no project)" }), (FL.projects || []).map((p) => el("option", { value: p.name, text: p.name })));
    projSel.value = FL.project || "";
    const presetSel = f?.presets_by ? el("select", { class: "small", onchange: (e) => { FL.inputs = { ...FL.inputs, [f.presets_by]: e.target.value }; flRefreshGraph(); } },
      f.presets.map((p) => el("option", { value: p, text: p }))) : null;
    if (presetSel) presetSel.value = FL.inputs[f.presets_by] || f.defaults?.[f.presets_by] || f.presets[0];
    // A click on empty space clears the selected step. Not on the controls: a redraw would close an open select.
    main = el("section", { class: "fd-main", onclick: (e) => {
      if (!FL.sel || e.target.closest(".fd-controls, select, input, button, a, label")) return;
      FL.sel = null; flRenderMain();
    } }, f ? [
      el("div", { class: "fd-title" }, el("span", { class: "big", text: f.flow }), el("span", { class: "file", text: f.file }),
        f.doc ? el("span", { class: "muted small", text: `· notes ${f.doc}` }) : null, el("span", { class: "spacer" }), flLive()),
      f.description ? el("div", { class: "desc", text: f.description }) : null,
      el("div", { class: "fd-controls" },
        el("label", {}, "Project", projSel),
        presetSel ? el("label", {}, f.presets_by, presetSel) : null,
        f.uses?.length ? el("span", { class: "uses" }, "fragments ", f.uses.map((u) => el("span", { class: "tag frag", text: u.replace(/^lib\//, "").replace(/\.yaml$/, "") }))) : null),
      gr?.error ? el("div", { class: "warn", text: gr.error }) : null,
      gr?.tree ? defTree(gr.tree, (s) => { FL.sel = s; flRenderMain(); }, FL.sel?.id) : null] : el("p", { class: "muted", text: `No flow ${FL.flow || ""}.` }));
    side = el("aside", { class: "fd-side" }, defDetail(FL.sel));
  }
  const keep = pane.querySelector(".fd-main")?.scrollTop || 0;
  pane.replaceChildren(main, side);
  const m = pane.querySelector(".fd-main"); if (m) m.scrollTop = keep;
}

// Flows grouped by project (the flow's `projects`; none = General), each group in role order. The group of
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
      title: key ? `Project ${key}${repo ? ` (${repo})` : ""}` : "Flows for any project" };
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
  try { await flEnsure(); } catch (e) { alert(`Could not read the flows: ${e.message}`); return; }
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
      ? `These are the commits that introduced the problem, found by postmortem ${c.id}. Below are the findings the postmortem says the reviewer should have reported. Tick the ones that really hold; they count only after the case is kept in the eval set.`
      : "These are the changes that round 1 of a past review looked at. Tick the findings that really are problems. Once kept in the eval set, the case scores the reviewer (myorch eval run / report)." }),
    Object.keys(c.replay_scores || {}).length ? el("div", {}, el("h4", { text: "Replay" }), kv(Object.entries(c.replay_scores).map(([phase, r]) => [
      { before: "before adoption (the knowledge at the time)", after: "after adoption (the new knowledge)" }[phase] || phase,
      el("span", {}, el("a", { href: `#run=${encodeURIComponent(r.run)}`, text: r.run }),
        r.pending ? " · still running" : ` · caught ${r.caught}/${r.expected}, ${r.extra} other findings${c.status === "confirmed" ? "" : " (counted against all expected findings; after confirming, against the ticked ones)"}`)]))) : null,
    kv([["Status", STATUS_TEXT[c.status] || c.status], ["Compared range", `${c.base_ref} @ ${c.base_sha.slice(0, 8)} .. ${c.head_sha.slice(0, 8)}`],
      ["head", c.head_guessed ? "guessed: the first commit on the branch. Check it in the commit list below" : "recorded at the time"], ["Source flow", c.flow]]),
    el("h4", { text: c.origin === "git" ? "Commits that introduced the problem" : "Commits on the branch" }), el("pre", { class: "small", text: (c.commits || []).join("\n") || "(none)" }),
    el("h4", { text: `Findings · ${c.findings.length}` }), el("div", { class: "evlist" }, c.findings.map(row)),
    el("div", { class: "evbar" },
      el("button", { text: "Keep in the eval set (the ticked ones are real problems)", onclick: () => decide([...picked]) }),
      el("button", { class: "ghost danger", text: "None are real problems, drop this case", onclick: () => decide([]) })));
  const side = el("aside", { class: "fd-side" }, el("h4", { text: "Requirements" }),
    fileView({ path: "requirement.md", title: "The requirements document at the time", text: c.requirement || "(none)", html: null }));
  return [main, side];
}
const STATUS_TEXT = { pending: "to confirm", confirmed: "kept in the eval set", rejected: "dropped" };

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
    el("div", { class: "pv-head" }, el("b", { text: "Steps this run will run" }), g.tree ? el("span", { class: "muted small", text: `${count} steps` }) : null,
      el("span", { class: "spacer" }), el("button", { type: "button", class: "ghost small", text: "See flow definition", onclick: () => openFlows(flow) })),
    el("div", { class: "pv-live muted small" }, el("span", { class: "livetag" }, el("span", { class: "dot" }), `Live: follows the definition of ${flow}`)),
    g.error ? el("div", { class: "warn", text: g.error }) : defOutline(g.tree, () => openFlows(flow)),
    el("div", { class: "muted small pv-foot", text: "Click a step to see its definition (condition, model, prompt)." }));
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
  if (!confirm(`Unregister knowledge source ${k.name}? Existing snapshots are kept. Runs started later can no longer mount it.`)) return;
  try {
    await api("/api/knowledge/remove", { method: "POST", body: { name: k.name, project: k.project || null } });
    closeTab(tabKey({ kind: "flow", ref: { t: "kb", id: kbKey(k) } })); await flLoadOverview(); flRender();
  } catch (e) { alert(e.message); }
}

const KB_FIELDS = {  // [field, label, hint, kind of input]
  dir: [["path", "Folder", "A local path; ~ and ${ENV} work. Do not use an Obsidian vault directly; use a folder you curated"],
    ["include", "Only these files", "globs, comma-separated, e.g. **/*.md; empty means all"], ["exclude", "Not these files", "globs, comma-separated"],
    ["index", "Read first", "the file the agent reads first, e.g. README.md"]],
  git: [["repo", "Repository", "owner/name"], ["ref", "Branch or tag", "main"], ["provider", "OpenShell provider", "a provider that can clone this repository"],
    ["image", "Image (optional)", "with git and python3; empty means the project's development image"], ["include", "Only these files", "globs, comma-separated"],
    ["exclude", "Not these files", "globs, comma-separated"], ["index", "Read first", "e.g. README.md"]],
  mcp: [["url", "MCP URL", "https://…, e.g. https://mcp.atlassian.com/v1/mcp"], ["provider", "OpenShell provider", "the provider that adds the token at the gateway"],
    ["token_env", "Token placeholder variable", "the provider's environment variable name, e.g. ATLASSIAN_TOKEN"], ["auth", "Authentication", "", "auth"],
    ["email", "Email (for basic)", "the email of the account the token belongs to"], ["tools", "Allowed tools", "comma-separated. Only these can be called; all others are disabled. List read-only tools only"],
    ["index", "Usage", "tell the agent when to use which tool, e.g.: read issues with getJiraIssue"]],
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
      ? el("select", { "data-f": k }, el("option", { value: "bearer", text: "bearer: a service account's API key" }), el("option", { value: "basic", text: "basic: a personal API token + email" }))
      : el("input", { "data-f": k, placeholder: hint });
    input.value = Array.isArray(v) ? v.join(", ") : v ?? (type === "auth" ? "bearer" : "");
    return el("label", {}, label, input);
  };
  const kindSel = el("select", { name: "kind", disabled: f.edit }, ["dir", "git", "mcp"].map((k) => el("option", { value: k, text: { dir: "local folder", git: "git repository", mcp: "MCP server" }[k] })));
  kindSel.value = f.kind;
  kindSel.addEventListener("change", () => { FL.kbForm = { ...f, kind: kindSel.value, fields: {} }; FL.kbCheck = null; flRender(); });
  const nameIn = el("input", { name: "name", placeholder: "lowercase letters, digits and -, e.g. mdm-docs", disabled: f.edit });
  nameIn.value = f.name;
  const projSel = el("select", { name: "project", disabled: f.edit }, el("option", { value: "", text: "Global (every project can use it)" }),
    ...projects.map((p) => el("option", { value: p, text: `Only project ${p}` })));
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
    el("div", { class: "fd-title" }, el("span", { class: "big", text: f.edit ? `Edit ${f.name}` : "Register knowledge source" }),
      el("span", { class: "file", text: "Written to myorch-knowledge as one git commit" })),
    el("div", { class: "kbform", id: "kbForm" },
      el("label", {}, "Name", nameIn), el("label", {}, "Scope", projSel), el("label", {}, "Type", kindSel),
      ...KB_FIELDS[f.kind].map(field),
      f.kind === "mcp" ? el("label", { class: "check" }, ro, "This token is read-only (it cannot create, change or delete anything). Optional, for the record only") : null),
    el("div", { class: "evbar" },
      el("button", { class: "ghost", text: "Check", onclick: () => act("check") }),
      el("button", { text: f.edit ? "Save changes" : "Register", onclick: () => act("register") }),
      el("button", { class: "ghost", text: "Cancel", onclick: () => { FL.kbForm = null; closeTab(tabKey({ kind: "flow", ref: { t: "kbform" } })); } })),
    result?.error ? el("div", { class: "warn", text: result.error }) : null,
    result && !result.error ? el("div", {},
      result.problems?.length ? el("div", { class: "warn", text: `Problems: ${result.problems.join("; ")}` }) : el("div", { class: "ok small", text: "No problems." }),
      result.files !== undefined ? el("div", { class: "small", text: `A sync will take in ${result.files} files, ${(result.bytes / 1024).toFixed(1)} KB in total.` }) : null,
      result.findings?.length ? el("pre", { class: "small", text: result.findings.join("\n") }) : null,
      result.sample?.length ? el("pre", { class: "small", text: result.sample.join("\n") + (result.files > result.sample.length ? "\n…" : "") }) : null,
      result.yaml ? fileView({ path: "source.yaml", title: "Registration file", text: result.yaml, html: null }) : null) : null);
  const side = el("aside", { class: "fd-side" }, el("p", { class: "muted small", text:
    f.kind === "mcp" ? "MCP knowledge source: the agent can call only the tools in Allowed tools; myorch-mcp disables the rest at start. If the tool list cannot be read, the whole server is unavailable. The token is kept in the OpenShell gateway; the sandbox sees only a placeholder. For the Atlassian MCP, an organisation admin must first turn on API token access."
    : f.kind === "git" ? "git knowledge source: cloned with the provider in a one-off sandbox, filtered, scanned for secrets, and stored as a snapshot. When a run starts, it is cloned again if the snapshot is older than 1 hour."
    : "Folder knowledge source: synced automatically when a run starts. It is scanned for secrets; if one is found, it is not synced and the run does not start. Check first tells you which files it will take in." }));
  return [main, side];
}
