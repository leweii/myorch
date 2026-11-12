// The new-run form, a tab of its own. Nothing here knows a flow, a role or an input by name: the project
// decides the tasks (/api/flows: the project's flows, with label, hint and order from core/inputs.py),
// the task decides the fields (its input specs), and the specs decide how a field looks, what is
// required, what folds under 更多选项 (`more`) and how "continue on this branch" works.
// Uses el/api/$/S/openTab/closeTab from app.js.
//
// State is NR, not the DOM. Every change sends a "newrun:change" event (detail: NR) that the
// knowledge picker (knowledge.js) and the step preview (flows.js) listen to.
"use strict";

const NR = { project: "", flow: "", values: {}, continued: null };  // continued: the run a continue started from

const RUN_STATUS = { COMPLETED: "完成", FAILED: "失败", CANCELED: "已取消", TERMINATED: "已终止", TIMED_OUT: "超时" };

function inputSpec(v) { return typeof v === "string" ? { type: "string" } : (v || { type: "string" }); }
// The flow's inputs as the chosen project sees them (the server resolves them: some inputs apply to
// some projects only, a ticket's prefix comes from the project).
function specsOf(f) {
  const own = NR.project && f?.inputs_for?.[NR.project];
  return Object.entries(own || f?.inputs || {}).map(([name, raw]) => [name, inputSpec(raw)]);
}

// "a {x} · b {y}": each " · " part is kept only if one of its fields has a value.
function fillTemplate(tpl, fields) {
  return tpl.split(" · ").map((part) => {
    let any = false, none = true;
    const out = part.replace(/\{([\w.]+)\}/g, (_, k) => { none = false; const v = fields[k] ?? ""; if (v !== "") any = true; return v; });
    return none || any ? out : null;
  }).filter((p) => p !== null).join(" · ");
}
function runFields(r) {
  const f = { run_id: r.run_id, status: RUN_STATUS[r.status] || r.status || "", pr: r.pr_url ? r.pr_url.split("/").pop() : "" };
  for (const [k, v] of Object.entries(r.inputs || {})) f[`inputs.${k}`] = String(v ?? "");
  return f;
}

// ---- field types: render(name, spec, ctx) -> the control; the label comes from the spec ----------

const textInput = (name, spec) => el("input", { name, placeholder: spec.hint || "" });
const FIELD_TYPES = {
  string: { render: textInput },
  ticket: { render: textInput, wrap: (field, spec) => spec.prefix ? el("div", { class: "affix" }, el("span", { class: "pre", text: spec.prefix }), field) : field },
  text: { render: (name, spec) => el("textarea", { name, placeholder: spec.hint || "", rows: 3 }) },
  select: {
    render: (name, spec) => el("select", { name }, (spec.options || []).map((o) => typeof o === "string"
      ? el("option", { value: o, text: o }) : el("option", { value: o.value, text: o.label || o.value }))),
    initial: (spec) => spec.options?.[0]?.value ?? spec.options?.[0] ?? "",
  },
  run: {
    render: (name, spec, ctx) => el("select", { name }, el("option", { value: "", text: spec.empty || "（不选）" }),
      ...runChoices(ctx.project, spec.role || ctx.flow.role).map((r) => el("option", { value: r.run_id,
        text: fillTemplate(spec.option || "{run_id} · {status}", runFields(r)) }))),
  },
  message: null,  // the first message: the form's own text box
  hidden: null,   // filled by the server (e.g. learn's runs)
};

function runChoices(project, role) {  // finished runs of this project with this role, newest first
  return (S.lastRuns?.runs || []).filter((r) => r.project === project && r.role === role && r.status !== "RUNNING");
}

// ---- catalog ------------------------------------------------------------------------------------

async function loadCatalog(force = false) {
  if (!S.flows || force) [S.flows, S.projects] = await Promise.all([api("/api/flows"), api("/api/projects")]);
}
function flowsFor(project) {  // already in task order (server)
  return (S.flows || []).filter((f) => !f.projects?.length || f.projects.includes(project));
}
function currentFlow() { return S.flows?.find((x) => x.flow === NR.flow); }

function changed() { document.dispatchEvent(new CustomEvent("newrun:change", { detail: NR })); }

// ---- the form -------------------------------------------------------------------------------------

// Open the form. preset: {project, flow, values, continued}; without one, the open form as it was, or
// the last project and its last task.
async function openNewRun(preset = {}) {
  await loadCatalog();
  const fresh = preset.project || preset.flow || preset.continued || !TABS.list.some((t) => t.kind === "new");
  if (fresh) {
    const names = S.projects.filter((p) => !p.error).map((p) => p.name);
    NR.project = [preset.project, localStorage.getItem("myorch.project"), S.detail?.project].find((n) => n && names.includes(n)) || names[0] || "";
    NR.flow = preset.flow || "";
    NR.values = { ...(preset.values || {}) };
    NR.continued = preset.continued || null;
    $("#nrMessage").value = "";
    $("#nrError").textContent = "";
  }
  openTab({ kind: "new" });
}

// Drawn when the tab is shown (app.js showPane).
function renderNewPane() {
  if (!S.flows) { loadCatalog().then(renderNewPane).catch((e) => { $("#nrError").textContent = e.message; }); return; }
  const names = S.projects.filter((p) => !p.error).map((p) => p.name);
  $("#sfProject").replaceChildren(...names.map((n) => el("option", { value: n, text: `${n} · ${S.projects.find((x) => x.name === n).repo}` })));
  if (!names.includes(NR.project)) NR.project = names[0] || "";
  $("#sfProject").value = NR.project;
  $("#nrNote").textContent = NR.continued?.note || "";
  renderStartInputs();
  document.dispatchEvent(new CustomEvent("newrun:open", { detail: NR }));
  $("#nrMessage").focus();
}

function renderStartInputs() {
  const project = NR.project, flows = flowsFor(project);
  const want = NR.flow || localStorage.getItem(`myorch.task.${project}`);
  const f = flows.find((x) => x.flow === want) || flows.find((x) => x.default) || flows[0];
  if (!f) {
    $("#sfTasks").replaceChildren(el("div", { class: "warn small", text: `项目 ${project} 没有可以用的流程。` }));
    $("#sfInputs").replaceChildren(); $("#sfMoreInputs").replaceChildren(); $("#sfMore").hidden = true; $("#sfFlowDesc").textContent = ""; NR.flow = ""; changed(); return;
  }
  NR.flow = f.flow;
  $("#sfTasks").replaceChildren(...flows.map((x) => el("button", { type: "button", role: "tab", "aria-selected": String(x.flow === f.flow), class: `task${x.flow === f.flow ? " on" : ""}`, title: x.flow,
    text: x.label || x.flow, onclick: () => { NR.flow = x.flow; NR.continued = null; localStorage.setItem(`myorch.task.${project}`, x.flow); renderStartInputs(); } })));
  $("#sfFlowDesc").replaceChildren(el("span", { class: "hint", text: f.hint || "" }), el("span", { class: "tag ghost", text: f.flow }));

  const projectRequired = new Set(S.projects.find((p) => p.name === project)?.required_inputs || []);
  const ctx = { project, flow: f };
  const fields = [], more = [];
  for (const [name, spec] of specsOf(f)) {
    const type = FIELD_TYPES[spec.type] === undefined ? FIELD_TYPES.string : FIELD_TYPES[spec.type];
    if (!type) continue;
    const field = type.render(name, spec, ctx);
    const value = NR.values[name] ?? f.defaults?.[name] ?? (type.initial ? type.initial(spec) : "");
    field.value = value;
    NR.values[name] = field.value;  // a select keeps only a value it has
    const must = !!spec.required || projectRequired.has(name);
    if (must) field.required = true;
    (spec.more && !must ? more : fields).push(el("label", { class: `field f-${spec.type}` },
      el("span", { class: "fl" }, el("span", { text: spec.label || name }), must ? el("span", { class: "req", text: "必填" }) : null),
      type.wrap ? type.wrap(field, spec) : field));
  }
  $("#sfInputs").replaceChildren(...fields);
  $("#sfMoreInputs").replaceChildren(...more);
  $("#sfMore").hidden = !more.length;
  if (more.some((l) => l.querySelector("[name]")?.value)) $("#sfMore").open = true;  // a value set there stays in sight
  const msg = messageSpec(f);
  $("#nrMsgLabel").textContent = `${msg?.label || "补充说明"}${msg?.required ? "（必填）" : "（可选）"}`;
  $("#nrMessage").placeholder = NR.continued?.hint || msg?.hint || "要改什么，越具体越好。";
  changed();
}

function messageSpec(f) { return specsOf(f).find(([, s]) => s.type === "message")?.[1] || null; }

// The values the run starts with: the current flow's form fields (hidden and message inputs are not among them).
function formValues() {
  const f = currentFlow();
  return Object.fromEntries(specsOf(f).filter(([, s]) => FIELD_TYPES[s.type] !== null).map(([n]) => [n, NR.values[n] ?? ""]));
}

// The flow's rules, before asking the server (which checks them again, core/inputs.py).
function formProblem(f, text) {
  const projectRequired = new Set(S.projects.find((p) => p.name === NR.project)?.required_inputs || []);
  const values = formValues(), specs = Object.fromEntries(specsOf(f));
  const label = (n) => specs[n]?.label || n;
  const missing = Object.keys(values).filter((n) => (specs[n].required || projectRequired.has(n)) && !String(values[n]).trim());
  if (missing.length) return `请先填：${missing.map(label).join("、")}`;
  const msg = messageSpec(f);
  if (msg?.required && !text.trim()) return `请写${msg.label || "补充说明"}。`;
  for (const group of f.require_one_of || []) {
    if (!group.some((n) => String(values[n] ?? "").trim())) return `${group.map(label).join(" 或 ")}，至少填一项。`;
  }
  return "";
}

async function startRun() {
  const f = currentFlow(), text = $("#nrMessage").value;
  if (!f) return;
  $("#nrError").textContent = "";
  const problem = formProblem(f, text);
  if (problem) { $("#nrError").textContent = problem; return; }
  $("#nrStart").disabled = true;
  try {
    const knowledge = typeof knowledgeSelection === "function" ? knowledgeSelection() : null;  // knowledge.js
    const r = await api("/api/runs", { method: "POST", body: { flow: f.flow, project: NR.project, inputs: formValues(), message: text, ...(knowledge ? { knowledge } : {}) } });
    localStorage.setItem("myorch.flow", f.flow);
    if (NR.project) localStorage.setItem("myorch.project", NR.project);
    NR.continued = null;
    $("#nrMessage").value = "";
    closeTab("new");
    openTab({ kind: "run", run: r.run_id });
    loadRuns();
  } catch (e) { $("#nrError").textContent = `没有启动：${e.message}`; }
  $("#nrStart").disabled = false;
}

// ---- continue: a new run that starts from a finished one (a run input with `continue`) ----------

// The flow and input that continue run d: in d's project, a run input whose role is d's role and
// that has a `continue` spec. d's own flow first.
function continuation(d) {
  if (!d || d.status === "running" || !d.project || !S.flows) return null;
  const flows = flowsFor(d.project).sort((a, b) => (b.flow === d.flow) - (a.flow === d.flow));
  for (const f of flows) {
    for (const [name, spec] of specsOf(f)) {
      if (spec.type === "run" && spec.continue && (spec.role || f.role) === d.role) return { flow: f, name, spec };
    }
  }
  return null;
}
function canContinue(d) { return !!continuation(d); }
function continueLabel(d) { return continuation(d)?.spec.continue.label || "接着改"; }
function continueEnded(d) { return continuation(d)?.spec.continue.ended || ""; }

async function continueRun() {
  const d = S.detail, rid = S.run, c = continuation(d);
  if (!c) return;
  const fields = { run_id: rid };
  const values = { [c.name]: rid };
  for (const k of c.spec.carry || []) if (d.inputs?.[k]) values[k] = d.inputs[k];
  const cont = c.spec.continue;
  await openNewRun({ project: d.project, flow: c.flow.flow, values,
    continued: { run: rid, note: cont.note ? fillTemplate(cont.note, fields) : "", hint: cont.hint ? fillTemplate(cont.hint, fields) : "" } });
}

// ---- wiring -----------------------------------------------------------------------------------------

$("#newRunBtn").addEventListener("click", () => openNewRun().catch((e) => alert(e.message)));
$("#sfProject").addEventListener("change", (e) => { NR.project = e.target.value; NR.flow = ""; NR.continued = null; $("#nrNote").textContent = ""; renderStartInputs(); });
const onField = (e) => { if (e.target.name) { NR.values[e.target.name] = e.target.value; changed(); } };
for (const box of ["#sfInputs", "#sfMoreInputs"]) { $(box).addEventListener("input", onField); $(box).addEventListener("change", onField); }
$("#nrStart").addEventListener("click", () => startRun());
$("#nrCancel").addEventListener("click", () => closeTab("new"));
$("#nrMessage").addEventListener("keydown", (e) => {
  if (!e.isComposing && e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); startRun(); }
});
// The continue button and the "continue" texts need the flows: load them once, then redraw the run.
loadCatalog().then(() => { if (S.run) { S.convKey = null; loadDetail(); } }).catch(() => {});
