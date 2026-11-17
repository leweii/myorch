#!/usr/bin/env python3
"""Write an inventory of the owner's chosen git repositories (REPOS below) for the blog's weekly
projects-page run. Sandboxes cannot read ~/GitHub, only the knowledge root (~/orca/kb), so the run reads this
inventory as the knowledge source `github-inventory` instead.

  python3 deploy/github-inventory.py [--repo PATH ...] [--out ~/orca/kb/github-inventory]

Output: INDEX.md (one table row per repository, newest activity first), repos/<name>.md (details: the whole README,
user-facing docs, the changelog's head) and repos/<name>/images/ (the README's images, screenshots and the app icon,
for the projects' detail pages). Reads local git data and committed files only, and asks GitHub (gh) only whether each
repository is public.
Run it before the schedule fires (deploy/com.myorch.github-inventory.plist does, every Monday 08:50).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

LANGS = {".swift": "Swift", ".kt": "Kotlin", ".java": "Java", ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript",
         ".js": "JavaScript", ".jsx": "JavaScript", ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".cs": "C#", ".cpp": "C++",
         ".c": "C", ".dart": "Dart", ".vue": "Vue", ".html": "HTML", ".css": "CSS", ".sh": "Shell", ".sql": "SQL"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "build", "dist", "Pods", ".gradle", "DerivedData", "__pycache__", ".build"}
MANIFESTS = ("package.json", "pyproject.toml", "Package.swift", "build.gradle", "build.gradle.kts", "pom.xml", "Cargo.toml",
             "go.mod", "Gemfile", "requirements.txt", "docker-compose.yml", "Dockerfile")
SECRET = re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*\S+")


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def languages(repo: Path) -> list[str]:
    files = git(repo, "ls-files").splitlines()
    count = Counter(LANGS[Path(f).suffix] for f in files if Path(f).suffix in LANGS and not set(Path(f).parts) & SKIP_DIRS)
    return [f"{lang} ({n})" for lang, n in count.most_common(4)]


def redact(text: str) -> str:
    return "\n".join("[redacted]" if SECRET.search(l) else l for l in text.splitlines()).strip()


def readme(repo: Path, lines: int = 400) -> str:
    for name in ("README.md", "README", "readme.md", "README.rst"):
        p = repo / name
        if p.is_file():
            return redact("\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[:lines]))
    return ""


# User-facing documents worth reading for a detail page (by file name); internal ones (CI, release, market research,
# implementation plans) are left out on purpose.
DOC_NAME = re.compile(r"(?i)(overview|intro|features?|architecture|usage|user[-_ ]?guide|getting[-_ ]?started|faq|help)[^/]*\.md$")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
SHOT_DIR = re.compile(r"(?i)(^|/)(screenshots?|docs?/images|media)/")
MAX_IMAGE, MAX_IMAGES = 2 * 1024 * 1024, 16  # per image after scaling; per repository


def docs(repo: Path, files: list[str], per_file: int = 200, most: int = 4) -> list[tuple[str, str]]:
    picked = [f for f in files if DOC_NAME.search(f) and "test" not in f.lower()][:most]
    return [(f, redact("\n".join((repo / f).read_text(encoding="utf-8", errors="replace").splitlines()[:per_file]))) for f in picked]


def changelog(repo: Path, lines: int = 60) -> str:
    for name in ("CHANGELOG.md", "CHANGES.md", "HISTORY.md"):
        if (repo / name).is_file():
            return redact("\n".join((repo / name).read_text(encoding="utf-8", errors="replace").splitlines()[:lines]))
    return ""


def blob(repo: Path, path: str) -> bytes:
    """A file as committed at HEAD (the working tree may lack it or differ)."""
    r = subprocess.run(["git", "-C", str(repo), "show", f"HEAD:{path}"], capture_output=True)
    return r.stdout if r.returncode == 0 else b""


def images(repo: Path, files: list[str], text: str) -> list[dict]:
    """The README's images (with their alt text), the largest app icon, then screenshots. Committed files only."""
    found: dict[str, str] = {}
    for alt, src in re.findall(r'!\[([^\]]*)\]\(([^)\s]+)', text) + [("", m) for m in re.findall(r'<img[^>]+src="([^"]+)"', text)]:
        src = src.split("#")[0].lstrip("./")
        if src in files and src.lower().endswith(IMAGE_EXT):
            found.setdefault(src, alt)
    icons = [f for f in files if ".appiconset/" in f and f.lower().endswith(".png")]
    if icons:
        found.setdefault(max(icons, key=lambda f: len(blob(repo, f))), "app icon")
    for f in files:
        if SHOT_DIR.search(f) and f.lower().endswith(IMAGE_EXT):
            found.setdefault(f, "")
    return [{"source": f, "file": re.sub(r"[^A-Za-z0-9._@-]+", "-", f), "alt": alt} for f, alt in list(found.items())[:MAX_IMAGES]]


def copy_image(repo: Path, source: str, dest: Path) -> int:
    """Copy one image, scaled down to at most 1600 px on its long side (sips, macOS), so the inventory stays small."""
    dest.write_bytes(blob(repo, source))
    if dest.suffix.lower() in (".png", ".jpg", ".jpeg"):
        subprocess.run(["sips", "-Z", "1600", str(dest)], capture_output=True)
    if dest.stat().st_size > MAX_IMAGE:
        dest.unlink()
        return 0
    return dest.stat().st_size


def visibility(slug: str) -> str:
    """public / private / unknown, asked of GitHub with the host's gh (the run's own token only sees the blog repo)."""
    r = subprocess.run(["gh", "api", f"repos/{slug}", "--jq", ".private"], capture_output=True, text=True)
    return {"false": "public", "true": "private"}.get(r.stdout.strip(), "unknown") if r.returncode == 0 else "unknown"


def describe(repo: Path) -> dict:
    remote = git(repo, "remote", "get-url", "origin")
    gh = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?$", remote)
    commits = git(repo, "rev-list", "--count", "HEAD")
    first = git(repo, "log", "--reverse", "--format=%cs", "--max-parents=0").splitlines()
    files = git(repo, "-c", "core.quotepath=false", "ls-files").splitlines()
    text = readme(repo)
    return {
        "name": repo.name,
        "github": gh.group(1) if gh else "",
        "visibility": visibility(gh.group(1)) if gh else "no remote",
        "url": f"https://github.com/{gh.group(1)}" if gh else "",
        "commits": int(commits) if commits.isdigit() else 0,
        "created": first[0] if first else "",
        "last_commit": git(repo, "log", "-1", "--format=%cs"),
        "last_subject": git(repo, "log", "-1", "--format=%s"),
        "branch": git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
        "languages": languages(repo),
        "manifests": sorted(m for m in MANIFESTS if (repo / m).exists()),
        "top_level": sorted(p.name for p in repo.iterdir() if not p.name.startswith(".") and p.name not in SKIP_DIRS)[:30],
        "readme": text,
        "docs": docs(repo, files),
        "changelog": changelog(repo),
        "images": images(repo, files, text),
    }


# The page is public, so nothing is listed unless it is named here (the owner's choice, 2026-11-17: "just these for now").
# To add a project, add its folder; a repository that is not listed is never read into the inventory, not even its name.
REPOS = ["~/GitHub/sound-pin", "~/orca/knowingyou-english-learning", "~/orca/knowingyou-voice-record", "~/orca/myorch"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", action="append", help="a repository folder to list (repeatable); default: REPOS in this file")
    ap.add_argument("--out", default="~/orca/kb/github-inventory")
    a = ap.parse_args()
    out = Path(a.out).expanduser()
    repos = [Path(r).expanduser() for r in (a.repo or REPOS)]
    missing = [str(r) for r in repos if not (r / ".git").exists()]
    infos = sorted((describe(r) for r in repos if (r / ".git").exists()), key=lambda i: i["last_commit"], reverse=True)
    for i in infos:
        if i["visibility"] != "public":
            i["url"] = ""  # a private repository's link is a 404 for readers: say so instead of linking

    if out.exists():
        shutil.rmtree(out)
    (out / "repos").mkdir(parents=True)
    rows = ["| Project | Visibility | Created | Last commit | Commits | Languages | Manifests |", "|---|---|---|---|---|---|---|"]
    for i in infos:
        rows.append(f"| [{i['name']}](repos/{i['name']}.md) | {i['visibility']} | {i['created'] or '-'} | {i['last_commit'] or '-'} "
                    f"| {i['commits']} | {', '.join(i['languages']) or '-'} | {', '.join(i['manifests']) or '-'} |")
    (out / "INDEX.md").write_text(
        "# Projects to show on the blog's projects page\n\n"
        "Generated by deploy/github-inventory.py from the owner's chosen list; read-only. Only these projects may appear on the page.\n"
        "Rank and describe them as project/guide/projects-page.md in the blog's project pack says (framework > app > plugin).\n"
        "Visibility `private`: a detail page and screenshots are fine (the product as a user sees it), but no GitHub link (it would be a 404)\n"
        "and nothing internal from the README, docs, code or images (hostnames, keys, customer or company names, real user data, unreleased plans).\n\n"
        + "\n".join(rows) + "\n" + (f"\nNot found on disk: {', '.join(missing)}\n" if missing else ""), encoding="utf-8")
    for i in infos:
        repo = next(r for r in repos if r.name == i["name"])
        img_dir = out / "repos" / i["name"] / "images"
        if i["images"]:
            img_dir.mkdir(parents=True)
        for im in i["images"]:
            im["bytes"] = copy_image(repo, im["source"], img_dir / im["file"])
        i["images"] = [im for im in i["images"] if im["bytes"]]
        shots = "\n".join(f"- `images/{im['file']}` (from `{im['source']}`, {im['bytes'] // 1024} KB)" + (f": {im['alt']}" if im["alt"] else "")
                          for im in i["images"]) or "(none found)"
        doc_md = "".join(f"\n\n## Document: {f} (first 200 lines)\n\n{t}" for f, t in i["docs"])
        (out / "repos" / f"{i['name']}.md").write_text(
            f"# {i['name']}\n\n- Visibility: {i['visibility']}; GitHub: {i['url'] or 'no public link'}\n"
            f"- Created: {i['created'] or '-'}; last commit: {i['last_commit'] or '-'} "
            f"({i['last_subject'] or '-'}); {i['commits']} commits; branch {i['branch'] or '-'}\n"
            f"- Languages (files): {', '.join(i['languages']) or '-'}\n- Manifests: {', '.join(i['manifests']) or '-'}\n"
            f"- Top level: {', '.join(i['top_level']) or '-'}\n\n"
            f"## Images (in {i['name']}/images/, copied from the repository)\n\n{shots}\n\n"
            f"## README\n\n{i['readme'] or '(none)'}{doc_md}"
            + (f"\n\n## CHANGELOG (head)\n\n{i['changelog']}" if i["changelog"] else "") + "\n", encoding="utf-8")
    (out / "inventory.json").write_text(json.dumps(infos, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(infos)} repositories -> {out}" + (f"; not found: {missing}" if missing else ""))


if __name__ == "__main__":
    main()
