// Keyboard shortcuts, and the page that lists them (?). One table: the dialog is drawn from it, so a
// binding and its description cannot drift apart. Keys are matched by their position (e.code), not by the
// character they type: Option+N on a Mac types a dead key, and the layout should not matter.
// Alt chords work while typing in a box; plain keys (? and /) only when no box has the focus.

function tabStep(d) {
  const list = TABS.list;
  if (list.length < 2) return;
  const i = list.findIndex((t) => t.key === TABS.active);
  activate(list[(i + d + list.length) % list.length].key);
}
function tabAt(n) {  // 1-based; 9 is the last, as in browsers
  const t = n === 9 ? TABS.list[TABS.list.length - 1] : TABS.list[n - 1];
  if (t) activate(t.key);
}
// Move the active tab one place; pinned and unpinned tabs stay in their own groups.
function tabShift(d) {
  const list = TABS.list, i = list.findIndex((t) => t.key === TABS.active), j = i + d;
  if (i < 0 || j < 0 || j >= list.length || !!list[j].pinned !== !!list[i].pinned) return;
  [list[i], list[j]] = [list[j], list[i]];
  renderTabbar(); saveTabs();
}
function toggleSide() {
  if (document.body.classList.contains("side-off")) showView(S.view || "runs");
  else { document.body.classList.add("side-off"); store.set("myorch.side", "off"); }
}
function focusSearch() {
  showView("runs");
  const box = $("#runSearch");
  box.focus(); box.select();
}

// [keys, what it does, run]. keys: "Alt+Shift+T"; the last part is a key position: a letter, a digit,
// [ ], / or ?. "Alt+1–8" style rows are documentation only (run is null) and bound below.
const SHORTCUTS = [
  ["General", [
    ["?", "Show keyboard shortcuts", () => toggleKeys()],
    ["Alt+N", "New run", () => openNewRun().catch((e) => alert(e.message))],
    ["/", "Search runs", focusSearch],
    ["Alt+B", "Show or hide the side bar", toggleSide],
    ["Alt+J", "Show or hide the live output", () => setPanelOpen(!PANEL.open)],
  ]],
  ["Side bar", [
    ["Alt+Shift+1", "Runs", () => showView("runs")],
    ["Alt+Shift+2", "Waiting on you", () => showView("inbox")],
    ["Alt+Shift+3", "Flow definitions", () => showView("flows")],
    ["Alt+Shift+4", "Resources", () => showView("resources")],
  ]],
  ["Tabs", [
    ["Alt+]", "Next tab", () => tabStep(1)],
    ["Alt+[", "Previous tab", () => tabStep(-1)],
    ["Alt+1–8", "Go to tab 1 to 8", null],
    ["Alt+9", "Go to the last tab", () => tabAt(9)],
    ["Alt+Shift+]", "Move tab right", () => tabShift(1)],
    ["Alt+Shift+[", "Move tab left", () => tabShift(-1)],
    ["Alt+W", "Close tab", () => TABS.active && closeTab(TABS.active)],
    ["Alt+Shift+W", "Close other tabs (not pinned)", () => TABS.active && closeTabs(TABS.list.filter((o) => !o.pinned && o.key !== TABS.active).map((o) => o.key), TABS.active)],
    ["Alt+Shift+T", "Reopen closed tab", () => reopenClosed()],
    ["Alt+P", "Pin or unpin tab", () => TABS.active && togglePin(TABS.active)],
  ]],
  ["In a box", [
    ["Enter", "Send a reply (Shift+Enter: new line)", null],
    [IS_MAC ? "Cmd+Enter" : "Ctrl+Enter", "Start the run (New run)", null],
    ["Esc", "Close a dialog, menu or maximised view", null],
  ]],
];

const CODE_OF = { "[": "BracketLeft", "]": "BracketRight" };
const keyCode = (k) => CODE_OF[k] || (/^[A-Z]$/.test(k) ? `Key${k}` : /^\d$/.test(k) ? `Digit${k}` : k);
// "alt|shift|code" -> run; a plain key (? or /) by the character it types: "char|?" -> run
const BINDINGS = new Map();
for (const [, rows] of SHORTCUTS) for (const [spec, , run] of rows) {
  if (!run) continue;
  if (spec.length === 1) { BINDINGS.set(`char|${spec}`, run); continue; }
  const parts = spec.split("+"), k = parts.pop();
  BINDINGS.set(`${+parts.includes("Alt")}|${+parts.includes("Shift")}|${keyCode(k)}`, run);
}
for (let n = 1; n <= 8; n++) BINDINGS.set(`1|0|Digit${n}`, () => tabAt(n));

const typing = (t) => t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));

document.addEventListener("keydown", (e) => {
  if (e.isComposing || e.ctrlKey || e.metaKey || e.repeat && !/Bracket/.test(e.code)) return;
  const run = e.altKey ? BINDINGS.get(`1|${+e.shiftKey}|${e.code}`) : BINDINGS.get(`char|${e.key}`);
  if (!run) return;
  if (!e.altKey && typing(e.target)) return;  // ? and / are text in a box
  const dlg = document.querySelector("dialog[open]");
  if (dlg && dlg.id !== "keysDlg") return;  // another dialog has the keyboard
  if (dlg && run !== BINDINGS.get("char|?")) dlg.close();
  e.preventDefault();
  run();
});

function toggleKeys() {
  const dlg = $("#keysDlg");
  if (dlg.open) { dlg.close(); return; }
  const keys = (spec) => el("span", { class: "kd-keys" },
    keyParts(spec).map((k) => el("kbd", { text: k })));
  const group = ([title, rows]) => el("div", {}, el("h5", { text: title }),
    rows.map(([spec, what]) => el("div", { class: "kd-row" }, el("span", { text: what }), keys(spec))));
  dlg.replaceChildren(el("div", { class: "kd-body" },
    el("div", { class: "kd-head" }, el("div", { class: "np-title", text: "Keyboard shortcuts" }), el("span", { class: "spacer" }),
      el("span", { class: "muted small", text: "Press ? or Esc to close" })),
    el("div", { class: "kd-groups" }, el("div", {}, group(SHORTCUTS[0]), group(SHORTCUTS[1]), group(SHORTCUTS[3])), el("div", {}, group(SHORTCUTS[2])))));
  dlg.showModal();
}
$("#keysDlg").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });  // backdrop
$("#newRunBtn").title = `New run (${keyLabel("Alt+N")})`;
