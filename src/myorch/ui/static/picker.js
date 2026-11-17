// Menus drawn by the page, not the browser: a dropdown in place of the native <select> menu, and showMenu for
// a context menu at a point (the tabs' right click, app.js). One is open at a time.
// A dropdown in place of the browser's native <select> menu. Every <select> in the page is upgraded
// (also the ones scripts add later): the select stays in the DOM, hidden, and remains the source of
// truth, so code that reads .value / FormData or listens for "change" is unchanged. A select with
// data-action="<key>" gets that DD_ACTIONS entry as a last row (e.g. "New project…", projects.js).
// data-native keeps a select native. Uses el/$ from app.js. Option text is inserted as textContent.
"use strict";

const DD_ACTIONS = {};  // key -> { label, run() }
const ddValue = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
let ddOpen = null;  // { close() } of the open menu: at most one

function enhanceSelect(sel) {
  if (sel.dataset.enhanced || sel.hasAttribute("data-native") || sel.multiple) return;
  sel.dataset.enhanced = "1";
  const label = el("span", { class: "dd-label" });
  const trigger = el("button", { type: "button", class: `dd-trigger${sel.classList.contains("small") ? " small" : ""}`,
    "aria-haspopup": "listbox", "aria-expanded": "false", title: sel.title || null }, label);
  sel.classList.add("dd-native");
  sel.after(trigger);

  const sync = () => {
    label.textContent = sel.selectedOptions[0]?.textContent || "";
    trigger.disabled = sel.disabled;
  };
  Object.defineProperty(sel, "value", { configurable: true, get() { return ddValue.get.call(this); },
    set(v) { ddValue.set.call(this, v); sync(); } });
  new MutationObserver(sync).observe(sel, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["disabled"] });
  sel.addEventListener("change", sync);
  sync();

  function open() {
    if (ddOpen) ddOpen.close();
    const action = DD_ACTIONS[sel.dataset.action];
    const rows = [...sel.options].map((o) => el("button", { type: "button", role: "option", class: "dd-opt", disabled: o.disabled ? "disabled" : null,
      "aria-selected": String(o.value === sel.value && o.selected), onclick: () => { close(); pick(o.value); } },
      el("span", { class: "dd-check", text: o.selected ? "✓" : "" }), el("span", { text: o.textContent })));
    const kids = [...rows];
    if (action) kids.push(el("div", { class: "dd-sep" }), el("button", { type: "button", class: "dd-opt dd-act", onclick: () => { close(); action.run(); } },
      el("span", { class: "dd-check", text: "+" }), el("span", { text: action.label })));
    const panel = el("div", { class: "dd-panel", role: "listbox" }, kids);
    (trigger.closest("dialog") || document.body).append(panel);  // a modal dialog covers anything outside it
    const r = trigger.getBoundingClientRect();
    panel.style.minWidth = `${Math.round(r.width)}px`;
    panel.style.maxHeight = `${Math.max(120, Math.min(360, innerHeight - r.bottom - 16))}px`;
    const below = innerHeight - r.bottom;
    panel.style.left = `${Math.max(8, Math.min(r.left, innerWidth - panel.offsetWidth - 8))}px`;
    panel.style.top = panel.offsetHeight > below - 12 && r.top > below ? `${Math.max(8, r.top - panel.offsetHeight - 4)}px` : `${r.bottom + 4}px`;
    trigger.setAttribute("aria-expanded", "true");
    const items = () => [...panel.querySelectorAll(".dd-opt:not(:disabled)")];
    (panel.querySelector('[aria-selected="true"]') || items()[0])?.focus();

    const onDown = (e) => { if (!panel.contains(e.target) && !trigger.contains(e.target)) close(); };
    const onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); close(); trigger.focus(); }
      else if (e.key === "Tab") close();
      else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const list = items(), i = list.indexOf(document.activeElement);
        list[(i + (e.key === "ArrowDown" ? 1 : -1) + list.length) % list.length]?.focus();
      }
    };
    const onScroll = (e) => { if (!panel.contains(e.target)) close(); };
    function close() {
      panel.remove();
      trigger.setAttribute("aria-expanded", "false");
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", onScroll, true);
      if (ddOpen === handle) ddOpen = null;
    }
    const handle = { close };
    ddOpen = handle;
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", onScroll, true);
  }
  function pick(value) {
    if (value === sel.value) return;
    sel.value = value;
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    trigger.focus();
  }
  trigger.addEventListener("click", () => (trigger.getAttribute("aria-expanded") === "true" ? ddOpen?.close() : open()));
  trigger.addEventListener("keydown", (e) => { if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); open(); } });
}

const enhanceAll = (root) => root.querySelectorAll?.("select").forEach(enhanceSelect);
enhanceAll(document);
new MutationObserver((muts) => muts.forEach((m) => m.addedNodes.forEach((n) => {
  if (n.nodeType !== 1) return;
  if (n.tagName === "SELECT") enhanceSelect(n); else enhanceAll(n);
}))).observe(document.body, { childList: true, subtree: true });


// A context menu at a point. items: {label, run, disabled, hint} or "-" for a separator.
function showMenu(x, y, items) {
  if (ddOpen) ddOpen.close();
  const rows = items.map((it) => (it === "-" ? el("div", { class: "dd-sep" })
    : el("button", { type: "button", role: "menuitem", class: "dd-opt", disabled: it.disabled ? "disabled" : null, onclick: () => { close(); it.run(); } },
      el("span", { text: it.label }), it.hint ? el("span", { class: "dd-hint", text: it.hint }) : null)));
  const panel = el("div", { class: "dd-panel ctx", role: "menu" }, rows);
  document.body.append(panel);
  panel.style.left = `${Math.max(8, Math.min(x, innerWidth - panel.offsetWidth - 8))}px`;
  panel.style.top = `${Math.max(8, Math.min(y, innerHeight - panel.offsetHeight - 8))}px`;
  const live = () => [...panel.querySelectorAll(".dd-opt:not(:disabled)")];
  live()[0]?.focus();

  const onDown = (e) => { if (!panel.contains(e.target)) close(); };
  const onKey = (e) => {
    if (e.key === "Escape") { e.stopPropagation(); close(); }
    else if (e.key === "Tab") close();
    else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
      e.preventDefault();
      const list = live(), i = list.indexOf(document.activeElement);
      list[e.key === "Home" ? 0 : e.key === "End" ? list.length - 1 : (i + (e.key === "ArrowDown" ? 1 : -1) + list.length) % list.length]?.focus();
    }
  };
  const onScroll = (e) => { if (!panel.contains(e.target)) close(); };
  function close() {
    panel.remove();
    document.removeEventListener("mousedown", onDown, true);
    document.removeEventListener("contextmenu", onDown, true);
    document.removeEventListener("keydown", onKey, true);
    window.removeEventListener("resize", close);
    window.removeEventListener("blur", close);
    window.removeEventListener("scroll", onScroll, true);
    if (ddOpen === handle) ddOpen = null;
  }
  const handle = { close };
  ddOpen = handle;
  document.addEventListener("mousedown", onDown, true);
  document.addEventListener("contextmenu", onDown, true);  // a right click elsewhere closes this one (and opens its own)
  document.addEventListener("keydown", onKey, true);
  window.addEventListener("resize", close);
  window.addEventListener("blur", close);
  window.addEventListener("scroll", onScroll, true);
}
