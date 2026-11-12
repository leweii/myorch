// Knowledge sources in the console (myorch/knowledge.py, docs/notes/2026-11-03-knowledge-mounts.md):
// the picker in the new-run tab, and the step page's record of what the agent read or queried.
// Syncing is not done here: `myorch knowledge sync <name>` in a terminal. Uses el/api/$/S/fileView
// from app.js. All source text is inserted as textContent.
"use strict";

const KN = { project: undefined, rows: null, picked: new Set(), touched: false, error: "" };
const KIND_LABEL = { dir: "文件夹", git: "git", mcp: "MCP" };
const LEVEL_LABEL = { global: "全局", template: "模板", project: "项目" };

// What the start sends: the checked names, or null (the project's defaults) while the list is unknown.
function knowledgeSelection() {
  return KN.rows === null || KN.error ? null : [...KN.picked];
}

function knowledgeUsable(r) {
  // A folder or git source that was never synced is fine: starting a run syncs it first
  // (knowledge.refresh in relay.start and the CLI); a sync that fails then refuses the start with the reason.
  return !r.problems.length;
}

async function loadKnowledgePick() {
  const box = $("#sfKnowledge");
  if (!box || !S.newMode) return;
  const project = NR.project || null;  // newrun.js
  if (project !== KN.project) { KN.project = project; KN.touched = false; KN.rows = null; }
  try {
    KN.rows = await api(`/api/knowledge${project ? `?project=${encodeURIComponent(project)}` : ""}`);
    KN.error = "";
  } catch (e) { KN.rows = []; KN.error = e.message; }
  if (!KN.touched) {  // the project's defaults, as long as the user has not changed anything
    KN.picked = new Set(KN.rows.filter((r) => project && r.default_in.includes(project) && knowledgeUsable(r)).map((r) => r.name));
  }
  renderKnowledgePick();
}

function renderKnowledgePick() {
  const box = $("#sfKnowledge");
  if (!box) return;
  if (KN.error) { box.replaceChildren(el("div", { class: "warn small", text: `读不到知识库列表：${KN.error}。启动时用项目的默认知识库。` })); return; }
  if (!KN.rows || !KN.rows.length) { box.replaceChildren(); return; }
  const item = (r) => {
    const ok = knowledgeUsable(r);
    const why = r.problems.length ? r.problems.join("；") : (!r.latest && r.kind !== "mcp" ? "还没同步过：启动运行时会先同步" : "");
    const box = el("input", { type: "checkbox", disabled: ok ? null : "disabled" });
    box.checked = KN.picked.has(r.name);
    box.addEventListener("change", () => { KN.touched = true; if (box.checked) KN.picked.add(r.name); else KN.picked.delete(r.name); });
    const when = r.kind === "mcp" ? "实时查询" : r.latest ? `同步于 ${fmtTime(r.latest.synced_at)} · ${r.latest.files} 个文件` : "启动时同步";
    return el("label", { class: `kitem${ok ? "" : " off"}`, title: why || r.expanded || r.where },
      box, el("span", { class: "kname", text: r.name }),
      el("span", { class: "tag", text: KIND_LABEL[r.kind] || r.kind }), el("span", { class: "tag ghost", text: LEVEL_LABEL[r.level] || r.level }),
      el("span", { class: "kwhen", text: when }),
      why ? el("span", { class: "kwhy", text: why }) : null);
  };
  box.replaceChildren(el("div", { class: "khead" }, el("span", { text: "知识库" }),
    el("span", { class: "muted small", text: "只读挂进每个 agent 步骤，版本在启动时固定" })),
    el("div", { class: "klist" }, KN.rows.map(item)));
}

// ---- the step page: what the agent read or queried ---------------------------------------------
// d: /api/runs/<id>/steps/<iid>/knowledge, loaded by the step page (app.js renderStep) when it opens.

function renderKnowledgeUse(out, d) {
  const kids = [];
  if (!d.transcripts) kids.push(el("p", { class: "muted small", text: "这个步骤没有会话记录（脚本步骤，或者还没开始）。" }));
  else if (!d.queries.length && !d.reads.length) kids.push(el("p", { class: "muted small", text: "这个步骤没有用到知识库。" }));
  if (d.reads.length) {
    kids.push(el("div", { class: "muted small", text: `读了 ${d.reads.length} 次知识库文件` }),
      el("div", { class: "filelist" }, d.reads.map((r) => el("div", { class: "f", title: JSON.stringify(r.input) },
        el("span", { class: "tag", text: r.tool }), " ", (r.target || "").replace("/sandbox/workspace/knowledge/", "")))));
  }
  if (d.queries.length) {
    kids.push(el("div", { class: "muted small", text: `查询了 ${d.queries.length} 次 MCP 知识库` }));
    d.queries.forEach((q, n) => kids.push(fileView({ path: "result.json", title: `${n + 1}. ${q.tool} ${JSON.stringify(q.input)}`, text: q.result || "（没有返回内容）", html: null })));
  }
  out.replaceChildren(...kids);
}

// ---- wiring --------------------------------------------------------------------------------------

document.addEventListener("newrun:change", (e) => { if (e.detail.project !== KN.project) loadKnowledgePick(); });  // newrun.js
document.addEventListener("newrun:open", () => loadKnowledgePick());  // the new-run tab is shown
