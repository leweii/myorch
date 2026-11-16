// A new project pack from the console (POST /api/projects, myorch/project.py create_project). The pack
// is written and committed to myorch-knowledge; the GitHub provider holds a token, so what is still
// to do by hand is shown as commands to run in a terminal. Uses el/api/$/S from app.js.
"use strict";

const PJ = { dlg: $("#projectDlg"), busy: false };

async function openProjectDialog() {
  $("#pjResult").hidden = true;
  $("#pjForm").hidden = false;
  $("#pjError").textContent = "";
  $("#pjCreate").hidden = false;
  $("#pjCancel").textContent = "Cancel";
  $("#projectForm").reset();
  let templates = [];
  try { templates = await api("/api/templates"); } catch { /* the form still works without a template */ }
  $("#pjTemplate").replaceChildren(el("option", { value: "", text: "none (write checks and image yourself)" }),
    ...templates.map((t) => el("option", { value: t, text: t })));
  PJ.dlg.showModal();
  $("#pjRepo").focus();
}

function showProjectResult(r) {
  const lines = [el("p", {}, "Created ", el("b", { text: r.name }), r.commit ? ` and committed to myorch-knowledge (${r.commit}).` : ".")];
  if (r.version) lines.push(el("p", { class: "muted small", text: `Synced: ${r.files} files (${r.version}).` }));
  if (r.sync_error) lines.push(el("p", { class: "warn small", text: `Not synced: ${r.sync_error}` }));
  lines.push(el("p", { class: "muted small", text: "Still to do, in order. The provider needs a token, so run these in your own terminal (never paste the token anywhere else):" }),
    el("ol", { class: "pj-steps" }, r.next.map((n) => el("li", {}, /^(uv|read) /.test(n) ? el("code", { text: n }) : n))));
  $("#pjResult").replaceChildren(...lines);
  $("#pjForm").hidden = true;
  $("#pjResult").hidden = false;
  $("#pjCreate").hidden = true;
  $("#pjCancel").textContent = "Close";
}

async function createProject(e) {
  e.preventDefault();
  if (PJ.busy) return;
  PJ.busy = true;
  $("#pjCreate").disabled = true;
  $("#pjError").textContent = "";
  try {
    const r = await api("/api/projects", { method: "POST", body: {
      repo: $("#pjRepo").value, name: $("#pjName").value, template: $("#pjTemplate").value,
      base: $("#pjBase").value, account: $("#pjAccount").value, sync: $("#pjSync").checked } });
    showProjectResult(r);
    S.projects = await api("/api/projects");  // newrun.js reads it; the new project is selectable at once
    if (typeof resRefresh === "function") resRefresh();  // resources.js: its list and project page
    $("#projectFilter").append(el("option", { value: r.name, text: r.name }));
    if (typeof NR !== "undefined") { NR.project = r.name; NR.flow = ""; if (!$("#pane-new").hidden) renderNewPane(); }
  } catch (err) { $("#pjError").textContent = err.message; }
  finally { PJ.busy = false; $("#pjCreate").disabled = false; }
}

DD_ACTIONS["new-project"] = { label: "New project…", run: openProjectDialog };  // the last row of the project dropdowns (picker.js)
$("#pjCancel").addEventListener("click", () => PJ.dlg.close());
$("#projectForm").addEventListener("submit", createProject);
