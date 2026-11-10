"""Build the User manual, one page per language with sidebar navigation:
docs/index.html (English, the home page) and docs/zh/index.html (Chinese).

    uv run --with markdown python docs/build-html.py

Chapters are README.md and NN-*.md: the Chinese originals in docs/, the English translations in docs/en/
(docs/en/TRANSLATING.md). ARCHITECTURE.md (Chinese at the repo root, English in docs/en/) is not a
chapter: links to it go to the file on GitHub.
A line `<!-- diagram: name.html -->` in a chapter is replaced by docs/diagrams/name.html (Chinese) or
docs/diagrams/en/name.html (English), HTML drawings made by docs/diagrams/gen.py, so the page needs no
network and no JavaScript for diagrams.
"""
from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from pathlib import Path

import markdown
from markdown.extensions.toc import slugify_unicode

DOCS = Path(__file__).resolve().parent
REPO = DOCS.parent
# The pages are published from docs/ (GitHub Pages), so a link to anything else in the repo (code,
# deploy/README.md, a note's .md) points at GitHub; links between the pages and diagrams stay relative.
GITHUB = "https://github.com/leweii/myorch"


def file_link(p: Path, frag: str, page_dir: Path) -> str:
    """A link from a page in page_dir to the file p (resolved) that is not a chapter of a manual."""
    suffix = f"#{frag}" if frag else ""
    if DOCS in p.parents and p.suffix == ".html" or REPO not in [p, *p.parents]:
        return os.path.relpath(p, page_dir) + suffix
    kind = "tree" if p.is_dir() else "blob"
    return f"{GITHUB}/{kind}/main/{p.relative_to(REPO).as_posix()}{suffix}"


@dataclass(frozen=True)
class Lang:
    """One language of the docs: where its sources are, where its pages go, and the page's own words."""
    code: str                 # html lang
    src: Path                 # the chapters' directory
    flows: Path               # the flows' prose (flows/<flow>.md and its translations)
    workflows: Path           # the Flow manual's home (README.md)
    diagrams: Path            # the HTML drawings embedded into the chapters
    out: Path                 # where this language's index.html goes; its Flow manual is out/workflows/
    other: str                # the same page in the other language, relative to out/
    words: dict[str, str]

    @property
    def chapters(self) -> list[Path]:
        return [self.src / "README.md", *sorted(self.src.glob("[0-9][0-9]-*.md"))]


EN = Lang("en", DOCS / "en", DOCS / "en" / "flows", DOCS / "en" / "workflows" / "README.md",
          DOCS / "diagrams" / "en", DOCS, "zh/", {
              "title": "myorch docs", "manual": "User manual", "flow_manual": "Flow manual", "home": "Home",
              "other": "中文",
              "theme": "Theme", "light": "Light", "dark": "Dark", "auto": "System", "filter": "Filter chapters  /"})
ZH = Lang("zh-CN", DOCS, REPO / "flows", DOCS / "workflows" / "README.md",
          DOCS / "diagrams", DOCS / "zh", "../", {
              "title": "myorch 文档", "manual": "使用手册", "flow_manual": "流程手册", "home": "首页",
              "other": "English",
              "theme": "主题", "light": "浅色", "dark": "深色", "auto": "跟随系统", "filter": "筛选章节  /"})
LANGS = [EN, ZH]


def chapter_key(path: Path) -> str:
    return "index" if path.name == "README.md" else path.stem


def fix_links(text: str, src_dir: Path, lang: Lang, chapter: str = "") -> str:
    """Relative links, resolved from the source file's directory: links to a chapter become in-page
    anchors, links to a flow's .md go to its chapter in the Flow manual, others are made relative
    to the directory of this language's index.html."""
    chapters = {p.resolve(): chapter_key(p) for p in lang.chapters}
    others = {p.resolve(): chapter_key(p) for other in LANGS if other is not lang for p in other.chapters}

    def repl(m: re.Match) -> str:
        target = html.unescape(m.group(1))
        if target.startswith("#") and chapter and not target.startswith(("#ch-", f"#{chapter}--")):
            return f'href="#{chapter}--{html.escape(target[1:], quote=True)}"'  # a heading in the same chapter
        if re.match(r"^[a-z]+:|^#|^/", target):
            return m.group(0)
        page, _, frag = target.partition("#")
        if not page:
            return m.group(0)
        p = (src_dir / page).resolve()
        if p in chapters:
            key = chapters[p]
            return f'href="#{key}--{frag}"' if frag else f'href="#ch-{key}"'
        if p in others:  # the same chapter in the other language (heading ids differ, so the chapter only)
            return f'href="{lang.other}index.html#ch-{others[p]}"'
        if p.parent == lang.flows and p.suffix == ".md":
            return f'href="workflows/index.html#ch-{p.stem}"'
        if p == lang.workflows:
            return f'href="workflows/index.html#{"index--" + frag if frag else "ch-index"}"'
        return 'href="' + html.escape(file_link(p, frag, lang.out), quote=True) + '"'
    return re.sub(r'href="([^"]+)"', repl, text)


def embed_diagrams(text: str, lang: Lang) -> str:
    """<!-- diagram: name.html --> becomes the content of the language's diagrams/name.html (an HTML drawing)."""
    def repl(m: re.Match) -> str:
        return f'<div class="diagram">{(lang.diagrams / m.group(1)).read_text(encoding="utf-8")}</div>'
    return re.sub(r"<!-- diagram: ([\w.-]+\.html) -->", repl, text)


def render(path: Path, lang: Lang) -> tuple[str, str, list]:
    key = chapter_key(path)
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc", "sane_lists"],
        extension_configs={"toc": {"slugify": lambda v, sep: f"{key}--{slugify_unicode(v, sep)}"}},
    )
    body = embed_diagrams(fix_links(md.convert(path.read_text(encoding="utf-8")), path.parent, lang, key), lang)
    tokens = md.toc_tokens
    title = tokens[0]["name"] if tokens else path.name
    subs = tokens[0]["children"] if tokens else []
    return key, f'<section class="chapter" id="ch-{key}">{body}</section>', [title, subs]


def build(lang: Lang) -> None:
    sections, nav = [], []
    labels = {"index": lang.words["home"]}
    for path in lang.chapters:
        key, sec, (title, subs) = render(path, lang)
        sections.append(sec)
        label = labels.get(key) or html.unescape(title)
        items = "".join(f'<li><a href="#{s["id"]}">{s["name"]}</a></li>' for s in subs) if key != "index" else ""
        nav.append(f'<li class="ch"><a class="ch-link" href="#ch-{key}">{label}</a>'
                   + (f'<ul class="sub">{items}</ul>' if items else "") + "</li>")
    xlink = f'<a class="xlink" href="workflows/index.html">{lang.words["flow_manual"]} →</a>'
    out = page(lang, lang.words["manual"], lang.words["title"], xlink, lang.other + "index.html", "", "\n".join(nav), "\n".join(sections))
    lang.out.mkdir(parents=True, exist_ok=True)
    (lang.out / "index.html").write_text(out, encoding="utf-8")
    print(f"wrote {lang.out / 'index.html'} ({len(lang.chapters)} chapters)")


def main() -> None:
    for lang in LANGS:
        build(lang)


def page(lang: Lang, sub: str, title: str, xlink: str, other: str, css: str, nav: str, body: str) -> str:
    """The page shell. docs/workflows/build.py uses it too, so both manuals look the same. `other` is the
    same page in the other language; the switch keeps the chapter you are reading."""
    w = lang.words
    return (TEMPLATE.replace("{{LANG}}", lang.code).replace("{{TITLE}}", title).replace("{{SUB}}", sub)
            .replace("{{XLINK}}", xlink).replace("{{OTHER}}", other).replace("{{OTHER_LABEL}}", w["other"])
            .replace("{{THEME}}", w["theme"]).replace("{{LIGHT}}", w["light"]).replace("{{DARK}}", w["dark"])
            .replace("{{AUTO}}", w["auto"]).replace("{{FILTER}}", w["filter"])
            .replace("{{CSS}}", css).replace("{{NAV}}", nav).replace("{{BODY}}", body))


# Tokens shared with the console (src/myorch/ui/static/app.css): one neutral family, five inks, one
# accent. Dark is a second set of the same names; "跟随系统" leaves html[data-theme] unset.
LIGHT = """
    --canvas: #f6f6f5; --surface: #fff; --well: #f3f3f2; --raised: #fff;
    --line: rgba(15,15,20,.08); --line-strong: rgba(15,15,20,.15);
    --text-strong: #17171a; --text: #3b3b41; --text-faint: #6e6e76; --text-ghost: #a3a3aa;
    --accent: #2563eb; --accent-soft: #edf2fe; --accent-text: #1d4fd7;
    --success: #15803d; --success-soft: #ebf6ee; --danger: #c2321f; --danger-soft: #fcefec;
    --warning: #a16207; --warning-soft: #fbf4e4;
    --shadow-card: 0 1px 2px rgba(15,15,20,.05), 0 0 0 1px var(--line);
    --hc: rgba(15,15,20,.09);
    color-scheme: light;"""
DARK = """
    --canvas: #0e0e10; --surface: #151518; --well: #1c1c20; --raised: #232328;
    --line: rgba(255,255,255,.08); --line-strong: rgba(255,255,255,.15);
    --text-strong: #f4f4f5; --text: #d2d2d7; --text-faint: #9d9da6; --text-ghost: #66666e;
    --accent: #5b9bff; --accent-soft: rgba(91,155,255,.14); --accent-text: #8ab8ff;
    --success: #4ade80; --success-soft: rgba(74,222,128,.12); --danger: #ff7a66; --danger-soft: rgba(255,122,102,.12);
    --warning: #f5b94a; --warning-soft: rgba(245,185,74,.12);
    --shadow-card: 0 0 0 1px var(--line);
    --hc: rgba(255,255,255,.08);
    color-scheme: dark;"""

TEMPLATE = """<!DOCTYPE html>
<html lang="{{LANG}}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<script>
  // Apply the saved theme before the first paint (same key and values as the console's theme.js).
  (() => { let t = null; try { t = localStorage.getItem("myorch.theme"); } catch {}
    t = {paper: "light", vellum: "light", blueprint: "dark", graphite: "dark"}[t] || t;
    if (t === "light" || t === "dark") document.documentElement.dataset.theme = t; })();
</script>
<style>
  /* Quiet and legible: one neutral family, hierarchy from size, weight and space rather than boxes.
     Borders only where they separate; the accent marks links and where you are. */
  :root {""" + LIGHT + """
    --font: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    --mono: "SF Mono", ui-monospace, Menlo, Consolas, monospace;
    --r-sm: 6px; --r-md: 8px; --r-lg: 12px; --top: 56px; --side: 280px;
  }
  :root[data-theme="dark"] {""" + DARK + """ }
  @media (prefers-color-scheme: dark) { :root:not([data-theme]) {""" + DARK + """ } }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; scroll-padding-top: calc(var(--top) + 24px); }
  body { margin: 0; background: var(--surface); color: var(--text); font: 400 15px/1.8 var(--font);
    -webkit-font-smoothing: antialiased; font-variant-numeric: tabular-nums; }
  ::selection { background: var(--accent-soft); color: var(--text-strong); }

  /* ---- top bar ---- */
  .top { position: fixed; inset: 0 0 auto 0; height: var(--top); z-index: 20; display: flex; align-items: center; gap: 12px;
    padding: 0 20px 0 24px; background: color-mix(in srgb, var(--surface) 88%, transparent); backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--line); }
  .brand { font-weight: 650; font-size: 16px; color: var(--text-strong); letter-spacing: -.01em; text-decoration: none; }
  .brand-sub { color: var(--text-faint); font-size: 14px; }
  .brand-sub::before { content: "/"; margin-right: 12px; color: var(--text-ghost); }
  .top .spacer { flex: 1; }
  .seg { display: inline-flex; padding: 2px; gap: 2px; background: var(--well); border-radius: var(--r-md); }
  .seg button { font: 500 13px/1.3 var(--font); color: var(--text-faint); background: transparent; border: 0; cursor: pointer;
    padding: 5px 12px; border-radius: var(--r-sm); }
  .seg button:hover { color: var(--text-strong); }
  .seg button.on { background: var(--raised); color: var(--text-strong); box-shadow: var(--shadow-card); }

  /* ---- sidebar ---- */
  aside { position: fixed; top: var(--top); left: 0; bottom: 0; width: var(--side); overflow-y: auto;
    border-right: 1px solid var(--line); padding: 20px 14px 40px; }
  aside input { width: 100%; margin: 0 0 16px; padding: 7px 12px; border: 1px solid transparent; border-radius: var(--r-md);
    font: 400 13.5px/1.4 var(--font); background: var(--well); color: var(--text-strong); }
  aside input::placeholder { color: var(--text-ghost); }
  aside input:focus { outline: none; border-color: var(--accent); background: var(--surface); }
  aside ul { list-style: none; margin: 0; padding: 0; }
  aside a { display: block; color: var(--text); text-decoration: none; border-radius: var(--r-sm); padding: 5px 10px; line-height: 1.45; }
  aside a:hover { background: var(--well); color: var(--text-strong); }
  aside a.ch-link { font-size: 14px; font-weight: 500; }
  aside ul.sub { display: none; margin: 2px 0 8px 10px; padding-left: 8px; border-left: 1px solid var(--line); }
  aside li.ch.open ul.sub { display: block; }
  aside ul.sub a { font-size: 13px; color: var(--text-faint); padding: 4px 10px; }
  aside a.active { background: var(--accent-soft); color: var(--accent-text); font-weight: 600; }
  aside ul.sub a.active { background: transparent; color: var(--accent-text); font-weight: 500; box-shadow: -9px 0 0 -8px var(--accent); }

  /* ---- content ---- */
  main { margin-left: var(--side); padding: calc(var(--top) + 40px) 56px 160px; }
  .chapter { max-width: 980px; }
  .chapter + .chapter { margin-top: 72px; padding-top: 56px; border-top: 1px solid var(--line); }
  h1, h2, h3, h4 { color: var(--text-strong); line-height: 1.35; letter-spacing: -.01em; }
  h1 { font-size: 30px; font-weight: 700; margin: 0 0 20px; }
  h2 { font-size: 21px; font-weight: 650; margin: 48px 0 12px; }
  h3 { font-size: 17px; font-weight: 600; margin: 32px 0 8px; }
  h4 { font-size: 15px; font-weight: 600; margin: 24px 0 6px; }
  p { margin: 10px 0; }
  a { color: var(--accent-text); text-decoration: none; }
  a:hover { text-decoration: underline; text-underline-offset: 3px; }
  b, strong { font-weight: 600; color: var(--text-strong); }
  code { font-family: var(--mono); font-size: .85em; background: var(--well); color: var(--text-strong); padding: .15em .4em; border-radius: 4px; }
  pre { background: var(--well); padding: 14px 18px; border-radius: var(--r-md); overflow-x: auto; line-height: 1.65; margin: 14px 0; }
  pre code { background: none; padding: 0; font-size: 13px; color: var(--text-strong); }
  .diagram { width: min(1200px, calc(100vw - var(--side) - 112px)); margin: 20px 0; padding: 20px; background: var(--well); border-radius: var(--r-lg); overflow-x: auto; }
  table { border-collapse: collapse; margin: 16px 0; font-size: 14px; line-height: 1.65; display: block; overflow-x: auto; }
  th, td { padding: 9px 14px 9px 0; text-align: left; vertical-align: top; border-bottom: 1px solid var(--line); }
  th { font-size: 12.5px; font-weight: 600; color: var(--text-faint); white-space: nowrap; border-bottom-color: var(--line-strong); }
  td:first-child, th:first-child { padding-left: 2px; }
  tbody tr:hover td { background: color-mix(in srgb, var(--well) 60%, transparent); }
  ul, ol { padding-left: 22px; margin: 10px 0; }
  li { margin: 4px 0; }
  li::marker { color: var(--text-ghost); }
  blockquote { margin: 16px 0; padding: 10px 16px; border-left: 3px solid var(--accent); background: var(--accent-soft);
    border-radius: 0 var(--r-md) var(--r-md) 0; color: var(--text); }
  blockquote p { margin: 4px 0; }
  hr { border: 0; border-top: 1px solid var(--line); margin: 36px 0; }
  .hidden { display: none !important; }
  aside::-webkit-scrollbar { width: 8px; } aside::-webkit-scrollbar-thumb { background: var(--line-strong); border-radius: 8px; border: 2px solid var(--surface); }
  @media (max-width: 960px) {
    .diagram { width: auto; }
    .top { position: sticky; } .brand-sub { display: none; }
    aside { position: static; width: auto; border-right: 0; border-bottom: 1px solid var(--line); max-height: 40vh; }
    main { margin-left: 0; padding: 32px 20px 80px; }
  }
  .xlink { font-size: 13.5px; font-weight: 500; color: var(--text-faint); margin-right: 8px; }
  .xlink:hover { color: var(--accent-text); text-decoration: none; }
{{CSS}}
</style>
</head>
<body>
<header class="top">
  <a class="brand" href="#ch-index">myorch</a><span class="brand-sub">{{SUB}}</span>
  <span class="spacer"></span>
  {{XLINK}}
  <a class="xlink" id="other-lang" href="{{OTHER}}">{{OTHER_LABEL}}</a>
  <div class="seg" role="radiogroup" aria-label="{{THEME}}">
    <button data-theme-pick="light">{{LIGHT}}</button><button data-theme-pick="dark">{{DARK}}</button><button data-theme-pick="auto">{{AUTO}}</button>
  </div>
</header>
<aside>
  <input id="q" type="search" placeholder="{{FILTER}}">
  <ul id="nav">
{{NAV}}
  </ul>
</aside>
<main>
{{BODY}}
</main>
<script>
  const links = [...document.querySelectorAll('aside a')];
  const byId = new Map(links.map(a => [a.getAttribute('href').slice(1), a]));
  const targets = [...document.querySelectorAll('section.chapter, section.chapter h2')];
  let current = null;
  function setActive(id) {
    if (id === current) return;
    current = id;
    links.forEach(a => a.classList.remove('active'));
    document.querySelectorAll('aside li.ch').forEach(li => li.classList.remove('open'));
    const a = byId.get(id); if (!a) return;
    a.classList.add('active');
    const ch = a.closest('li.ch');
    ch?.classList.add('open');
    ch?.querySelector('.ch-link')?.classList.add('active');
    a.scrollIntoView({block: 'nearest'});
  }
  function onScroll() {
    let best = null;
    for (const t of targets) { if (t.getBoundingClientRect().top < 120) best = t; }
    if (best) setActive(best.id);
  }
  window.addEventListener('scroll', onScroll, {passive: true});
  onScroll();
  const q = document.getElementById('q');
  q.addEventListener('input', () => {
    const v = q.value.trim().toLowerCase();
    document.querySelectorAll('aside li.ch').forEach(li => {
      const hit = !v || li.textContent.toLowerCase().includes(v);
      li.classList.toggle('hidden', !hit);
      li.classList.toggle('open', !!v && hit);
      li.querySelectorAll('ul.sub li').forEach(s => s.classList.toggle('hidden', !!v && !s.textContent.toLowerCase().includes(v) && !li.querySelector('.ch-link').textContent.toLowerCase().includes(v)));
    });
  });
  // Diagrams are drawn on a fixed canvas (docs/diagrams/gen.py); scale each one to the width it has.
  function fitDiagrams() {
    document.querySelectorAll('.diagram .dg').forEach(d => {
      const avail = d.parentElement.clientWidth - 40;
      d.style.zoom = Math.min(1.15, avail / parseFloat(d.style.width));
    });
  }
  fitDiagrams();
  window.addEventListener('resize', fitDiagrams);
  // Theme (shared with the console): light, dark, or auto (follow the system).
  (() => {
    const apply = (t) => {
      if (t === "light" || t === "dark") document.documentElement.dataset.theme = t; else document.documentElement.removeAttribute("data-theme");
      document.querySelectorAll("[data-theme-pick]").forEach(b => b.classList.toggle("on", b.dataset.themePick === t));
    };
    let cur = "auto"; try { cur = localStorage.getItem("myorch.theme") || "auto"; } catch {}
    cur = {paper: "light", vellum: "light", blueprint: "dark", graphite: "dark"}[cur] || cur;
    if (!["light", "dark", "auto"].includes(cur)) cur = "auto";
    apply(cur);
    document.querySelectorAll("[data-theme-pick]").forEach(b => b.addEventListener("click", () => {
      apply(b.dataset.themePick); try { localStorage.setItem("myorch.theme", b.dataset.themePick); } catch {}
    }));
  })();
  // The other language: chapter ids (#ch-…) are the same in both, heading ids are not; keep the chapter.
  document.getElementById('other-lang').addEventListener('click', e => {
    let ch = null;
    for (const s of document.querySelectorAll('section.chapter')) { if (s.getBoundingClientRect().top < 120) ch = s; }
    if (ch) e.currentTarget.href = e.currentTarget.getAttribute('href').split('#')[0] + '#' + ch.id;
  });
  document.addEventListener('keydown', e => { if (e.key === '/' && document.activeElement !== q) { e.preventDefault(); q.focus(); } });
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
