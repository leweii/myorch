// Loaded in <head> so the saved theme applies before the first paint. Shared with the docs page
// (docs/build-html.py): light, dark, or auto (follow the system; html[data-theme] unset).
"use strict";
(() => {
  const OLD = { paper: "light", vellum: "light", blueprint: "dark", graphite: "dark" };  // the drawing-sheet themes
  const apply = (t) => {
    t = OLD[t] || t;
    if (!["light", "dark", "auto"].includes(t)) t = "auto";
    if (t === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.dataset.theme = t;
    document.querySelectorAll("[data-theme-pick]").forEach((b) => b.classList.toggle("on", b.dataset.themePick === t));
    return t;
  };
  let saved = null;
  try { saved = localStorage.getItem("myorch.theme"); } catch { /* storage off */ }
  saved = apply(saved);
  document.addEventListener("DOMContentLoaded", () => {
    apply(saved);
    document.querySelectorAll("[data-theme-pick]").forEach((b) => b.addEventListener("click", () => {
      saved = apply(b.dataset.themePick);
      try { localStorage.setItem("myorch.theme", saved); } catch { /* storage off */ }
    }));
  });
})();
