// Resources: what runs use, as opposed to the flows that define the work. The side bar's Resources view lists
// projects, knowledge bases (a folder or a git repository) and MCP servers; each opens as a tab (ref {t: project|kb|kbform,
// id}). Knowledge bases and MCP servers are the same thing in myorch/knowledge.py (kind dir, git or mcp): one
// registry file each, one git commit per change. Uses el/kv/fileView/api/openTab/closeTab from app.js; the
// project dialog is projects.js. All text from the registry is inserted as textContent.
"use strict";

const RS = { rows: null, projects: null, form: null, check: null, filter: "", groups: {} };
const KB_TYPE = { dir: "local folder", git: "git repository", mcp: "MCP server" };
const KB_BADGE = { dir: "DIR", git: "GIT", mcp: "MCP" };

function kbKey(k) { return `${k.project || ""}/${k.name}`; }
function resCur() { const t = activeTab(); return t?.kind === "res" ? t.ref : null; }
const resIs = (t, id) => { const c = resCur(); return !!c && c.t === t && (id === undefined || c.id === id); };
function resOpenRef(ref, preview = true) { openTab({ kind: "res", ref }, { preview }); }

async function resLoad() {
  [RS.rows, RS.projects] = await Promise.all([api("/api/knowledge?all=1"), api("/api/projects")]);
  S.projects = RS.projects;  // newrun.js reads the same list
}

// After something outside this file changed a resource (a project was created): read again and redraw.
async function resRefresh() {
  if (!RS.rows && !resCur() && S.view !== "resources") return;
  try { await resLoad(); } catch { return; }
  resRender();
}

// The side bar is shown (app.js showView).
async function resOpenView() {
  if (RS.rows) resRenderList();
  try { await resLoad(); } catch (e) { $("#resList").replaceChildren(el("div", { class: "muted small pad", text: `Could not read the resources: ${e.message}` })); return; }
  resRenderList();
}

// A resource tab is shown (app.js showPane): its ref decides what the main area draws.
async function resShowTab(ref) {
  const pane = $("#pane-res");
  if (ref.t === "kbform" && !RS.form) RS.form = blankKbForm("dir");
  if (RS.rows) resRenderMain(); else pane.replaceChildren(el("p", { class: "muted small pad", text: "Loading…" }));
  try { await resLoad(); } catch (e) { pane.replaceChildren(el("p", { class: "muted small pad", text: `Could not read the resources: ${e.message}` })); return; }
  if (!resIs(ref.t, ref.id)) return;  // another tab was opened meanwhile
  resRenderMain();
  if (S.view === "resources") resRenderList();
}

function resRender() {
  if (S.view === "resources" && !document.body.classList.contains("side-off")) resRenderList();
  if (resCur()) resRenderMain();
}

// ---- the side bar -----------------------------------------------------------------------------------------

// Three groups, each folded by its header and given a "+" to add one; an item is one line (name, then what
// it is scoped to or built on) with its state under it.
function resRenderList() {
  if (!RS.rows) return;
  const box = $("#resList");
  const sub = (text) => el("div", { class: "sub", title: text, text });
  const projectItem = (p) => el("div", { class: `item${resIs("project", p.name) ? " active" : ""}`, "data-q": `${p.name} ${p.repo || ""} ${p.extends || ""}`.toLowerCase(),
    onclick: () => resOpenRef({ t: "project", id: p.name }), ondblclick: () => resOpenRef({ t: "project", id: p.name }, false) },
    el("div", { class: "line1" }, el("span", { class: "id", text: p.name }),
      p.error ? el("span", { class: "tag cond", text: "error" })
        : p.synced === false ? el("span", { class: "tag cond", title: `uv run myorch project sync ${p.name}`, text: "not synced" })
        : p.extends ? el("span", { class: "tag ghost", text: p.extends }) : null),
    sub(p.error || p.repo));
  const kbItem = (k) => el("div", { class: `item${resIs("kb", kbKey(k)) ? " active" : ""}`, "data-q": `${k.name} ${k.project || ""}`.toLowerCase(),
    onclick: () => resOpenRef({ t: "kb", id: kbKey(k) }), ondblclick: () => resOpenRef({ t: "kb", id: kbKey(k) }, false) },
    el("div", { class: "line1" }, el("span", { class: "ic k-script", text: KB_BADGE[k.kind] || k.kind }), el("span", { class: "id", text: k.name }),
      k.problems.length ? el("span", { class: "tag cond", text: "problems" })
        : el("span", { class: "tag ghost", text: k.project || (k.level === "template" ? "template" : "global") })),
    sub(k.kind === "mcp" ? "live queries" : k.latest ? `synced ${fmtTime(k.latest.synced_at)}` : "not synced yet (it syncs when a run starts)"));
  const bases = RS.rows.filter((k) => k.kind !== "mcp"), mcps = RS.rows.filter((k) => k.kind === "mcp");
  const group = (key, name, items, add, empty) => {
    const open = RS.groups[key] ?? true;
    const body = el("div", { class: "list grp-body", hidden: !open }, items.length ? items : el("div", { class: "muted small pad", text: empty }));
    const head = el("div", { class: `grp${open ? " open" : ""}`, "data-open": open ? "1" : "",
      onclick: () => { RS.groups[key] = body.hidden; body.hidden = !body.hidden; head.classList.toggle("open", !body.hidden); head.dataset.open = body.hidden ? "" : "1"; } },
      el("span", { class: "caret", text: "▸" }), el("span", { class: "gname", text: name }), el("span", { class: "n", text: String(items.length) }),
      el("button", { type: "button", class: "add", title: add.title, "aria-label": add.title, text: "+", onclick: (e) => { e.stopPropagation(); add.run(); } }));
    return el("div", { class: "grpbox" }, head, body);
  };
  const filter = el("input", { type: "search", class: "fd-filter", placeholder: "Filter by name", value: RS.filter,
    oninput: (e) => { RS.filter = e.target.value; resFilter(box); } });
  const keep = box.scrollTop, typing = document.activeElement?.classList.contains("fd-filter");
  box.replaceChildren(filter,
    group("projects", "Projects", RS.projects.map(projectItem), { title: "New project…", run: () => openProjectDialog() }, "No projects yet."),  // projects.js
    group("kb", "Knowledge bases", bases.map(kbItem), { title: "Register a knowledge base: a folder or a git repository", run: () => kbOpenForm(null, "dir") }, "None yet. A folder or a git repository agents may read."),
    group("mcp", "MCP servers", mcps.map(kbItem), { title: "Register an MCP server", run: () => kbOpenForm(null, "mcp") }, "None yet. Live queries, only the tools you allow."));
  resFilter(box);
  box.scrollTop = keep;
  if (typing) { filter.focus(); filter.setSelectionRange(filter.value.length, filter.value.length); }
}

// Hide what does not match, open the groups that have matches and hide the empty ones; in place, so typing keeps the focus.
function resFilter(box) {
  const q = (RS.filter || "").trim().toLowerCase();
  for (const g of box.querySelectorAll(".grpbox")) {
    const head = g.querySelector(".grp"), body = g.querySelector(".grp-body"), items = [...body.querySelectorAll(".item")];
    let n = 0;
    for (const it of items) { const hit = !q || it.dataset.q.includes(q); it.hidden = !hit; n += hit; }
    g.hidden = !!q && n === 0;
    body.hidden = q ? n === 0 : !head.dataset.open;
    head.classList.toggle("open", !body.hidden);
    head.querySelector(".n").textContent = q ? `${n}/${items.length}` : String(items.length);
  }
}

// ---- the tabs ---------------------------------------------------------------------------------------------

function resRenderMain() {
  const ref = resCur(), pane = $("#pane-res");
  if (!ref || !RS.rows) return;
  const [main, side] = ref.t === "kbform" && RS.form ? kbFormView(RS.form)
    : ref.t === "kb" ? kbView(RS.rows.find((k) => kbKey(k) === ref.id))
    : projectView(RS.projects.find((p) => p.name === ref.id));
  const keep = pane.querySelector(".fd-main")?.scrollTop || 0;
  pane.replaceChildren(main, side);
  const m = pane.querySelector(".fd-main"); if (m) m.scrollTop = keep;
}

function projectView(p) {
  const row = (k) => RS.rows.find((r) => r.name === k);
  const main = el("section", { class: "fd-main" }, p && !p.error ? [
    el("div", { class: "fd-title" }, el("span", { class: "big", text: p.name }), el("span", { class: "file", text: `myorch-knowledge/${p.name}/project.yaml` })),
    kv([["Repository", p.repo], ["Base branch", p.base], ["Template", p.extends], ["Image", p.image], ["Acts as", p.identity],
      ["Approvers", (p.approvers || []).join(", ")], ["GitHub provider", p.providers?.github], ["Required inputs", (p.required_inputs || []).join(", ")],
      ["Pack in the volume", p.synced === null ? "unknown (Docker is not reachable)" : p.synced ? "synced" : `not synced: uv run myorch project sync ${p.name}`]]),
    el("h4", { text: "Knowledge mounted by default" }),
    el("div", { class: "chips2" }, (p.knowledge || []).length ? p.knowledge.map((n) => row(n)
      ? el("button", { class: "ghost small", text: n, onclick: () => resOpenRef({ t: "kb", id: kbKey(row(n)) }, false) }) : el("span", { class: "tag cond", title: "not registered", text: n }))
      : el("span", { class: "muted small", text: "none" })),
    el("div", { class: "evbar" }, el("button", { text: "New run on this project", onclick: () => openNewRun({ project: p.name }) }))]  // newrun.js
    : [el("p", { class: p ? "warn" : "muted", text: p ? `This project's pack has an error: ${p.error}` : "This project no longer exists." })]);
  const side = el("aside", { class: "fd-side" }, el("p", { class: "muted small", text:
    "A project pack is files in myorch-knowledge/<name>/: settings in project.yaml, prompts, checks and risk rules. The console creates packs; learn and onboard propose changes to them, and you adopt the ones you want." }));
  return [main, side];
}

function kbView(k) {
  const main = el("section", { class: "fd-main" }, k ? [
    el("div", { class: "fd-title" }, el("span", { class: "big", text: k.name }), el("span", { class: "file", text: `myorch-knowledge/${k.file}` })),
    k.problems.length ? el("div", { class: "warn", text: k.problems.join("; ") }) : null,
    kv([["Type", KB_TYPE[k.kind]], ["Registered in", k.level], ["Location", k.where], ["Expanded", k.expanded],
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
        el("button", { class: "ghost danger", text: "Unregister", onclick: () => kbRemove(k) }))] : el("p", { class: "muted", text: "This resource no longer exists." }));
  const side = el("aside", { class: "fd-side" }, el("p", { class: "muted small", text: "Knowledge is mounted read-only into agent steps: /sandbox/workspace/knowledge/<name>/. MCP knowledge is reached through an OpenShell provider; the agent can call only the registered tools." }));
  return [main, side];
}

// ---- registering a knowledge base or MCP server (myorch/knowledge.py register / check / unregister) ---------

const blankKbForm = (kind) => ({ edit: false, name: "", project: "", kind, fields: {} });

function kbOpenForm(k, kind = "dir") {
  RS.form = k ? { edit: true, name: k.name, project: k.project || "", kind: k.kind, fields: { ...(k.fields || {}) } } : blankKbForm(kind);
  RS.check = null;
  if (resIs("kbform")) resRender(); else resOpenRef({ t: "kbform" }, false);
}

async function kbRemove(k) {
  if (!confirm(`Unregister ${k.name}? Existing snapshots are kept. Runs started later can no longer mount it.`)) return;
  try {
    await api("/api/knowledge/remove", { method: "POST", body: { name: k.name, project: k.project || null } });
    closeTab(tabKey({ kind: "res", ref: { t: "kb", id: kbKey(k) } })); await resLoad(); resRender();
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
  const projects = (RS.projects || []).filter((p) => !p.error).map((p) => p.name);
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
  const kindSel = el("select", { name: "kind", disabled: f.edit }, ["dir", "git", "mcp"].map((k) => el("option", { value: k, text: KB_TYPE[k] })));
  kindSel.value = f.kind;
  kindSel.addEventListener("change", () => { RS.form = { ...f, kind: kindSel.value, fields: {} }; RS.check = null; resRender(); });
  const nameIn = el("input", { name: "name", placeholder: "lowercase letters, digits and -, e.g. mdm-docs", disabled: f.edit });
  nameIn.value = f.name;
  const projSel = el("select", { name: "project", disabled: f.edit }, el("option", { value: "", text: "Global (every project can use it)" }),
    ...projects.map((p) => el("option", { value: p, text: `Only project ${p}` })));
  projSel.value = f.project || "";
  const ro = el("input", { type: "checkbox", "data-f": "token_read_only" });
  ro.checked = !!f.fields.token_read_only;
  const result = RS.check;
  const close = () => closeTab(tabKey({ kind: "res", ref: { t: "kbform" } }));
  const act = async (op) => {
    const b = read();
    RS.form = { ...f, name: b.name, project: b.project || "", fields: b.data };
    try {
      const r = await api(`/api/knowledge/${op}`, { method: "POST", body: { ...b, replace: f.edit } });
      RS.check = { ...r, op };
      if (op === "register") {
        await resLoad(); RS.form = null;
        close();
        resOpenRef({ t: "kb", id: `${b.project || ""}/${b.name}` }, false);
        return;
      }
    } catch (e) { RS.check = { error: e.message }; }
    resRender();
  };
  const noun = f.kind === "mcp" ? "MCP server" : "knowledge base";
  const main = el("section", { class: "fd-main" },
    el("div", { class: "fd-title" }, el("span", { class: "big", text: f.edit ? `Edit ${f.name}` : `Register ${noun}` }),
      el("span", { class: "file", text: "Written to myorch-knowledge as one git commit" })),
    el("div", { class: "kbform", id: "kbForm" },
      el("label", {}, "Name", nameIn), el("label", {}, "Scope", projSel), el("label", {}, "Type", kindSel),
      ...KB_FIELDS[f.kind].map(field),
      f.kind === "mcp" ? el("label", { class: "check" }, ro, "This token is read-only (it cannot create, change or delete anything). Optional, for the record only") : null),
    el("div", { class: "evbar" },
      el("button", { class: "ghost", text: "Check", onclick: () => act("check") }),
      el("button", { text: f.edit ? "Save changes" : "Register", onclick: () => act("register") }),
      el("button", { class: "ghost", text: "Cancel", onclick: () => { RS.form = null; close(); } })),
    result?.error ? el("div", { class: "warn", text: result.error }) : null,
    result && !result.error ? el("div", {},
      result.problems?.length ? el("div", { class: "warn", text: `Problems: ${result.problems.join("; ")}` }) : el("div", { class: "ok small", text: "No problems." }),
      result.files !== undefined ? el("div", { class: "small", text: `A sync will take in ${result.files} files, ${(result.bytes / 1024).toFixed(1)} KB in total.` }) : null,
      result.findings?.length ? el("pre", { class: "small", text: result.findings.join("\n") }) : null,
      result.sample?.length ? el("pre", { class: "small", text: result.sample.join("\n") + (result.files > result.sample.length ? "\n…" : "") }) : null,
      result.yaml ? fileView({ path: "source.yaml", title: "Registration file", text: result.yaml, html: null }) : null) : null);
  const side = el("aside", { class: "fd-side" }, el("p", { class: "muted small", text:
    f.kind === "mcp" ? "MCP server: the agent can call only the tools in Allowed tools; myorch-mcp disables the rest at start. If the tool list cannot be read, the whole server is unavailable. The token is kept in the OpenShell gateway; the sandbox sees only a placeholder. For the Atlassian MCP, an organisation admin must first turn on API token access."
    : f.kind === "git" ? "git knowledge base: cloned with the provider in a one-off sandbox, filtered, scanned for secrets, and stored as a snapshot. When a run starts, it is cloned again if the snapshot is older than 1 hour."
    : "Folder knowledge base: synced automatically when a run starts. It is scanned for secrets; if one is found, it is not synced and the run does not start. Check first tells you which files it will take in." }));
  return [main, side];
}
