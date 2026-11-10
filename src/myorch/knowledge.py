"""Knowledge sources a run can read: registered once, snapshotted, mounted read-only.

Design: docs/notes/2026-11-03-knowledge-mounts.md. A source is one YAML file, registered at

    myorch-knowledge/_sources/<name>.yaml                 every project may use it (unless `projects` limits it)
    myorch-knowledge/_templates/<t>/sources/<name>.yaml   projects that extend template t
    myorch-knowledge/<project>/sources/<name>.yaml        only this project; wins over the others

Kinds:
    dir   a local folder (paths may use ${ENV}, ~); filtered, secret-scanned, snapshotted
    git   a GitHub repository, cloned in a one-off sandbox job with an OpenShell provider (the
          token stays at the gateway); filtered and scanned in the job, snapshotted on the volume
    mcp   a remote MCP server, reached from the sandbox through an OpenShell provider whose
          token is read-only; no snapshot (the content changes), the queries stay in the
          agent's session record

Snapshots are knowledge/<name>/<sha256:16>/ on the volume, immutable. A run fixes the version
of each source when it starts (Mandate.knowledge) and mounts it at /sandbox/workspace/knowledge/<name>/.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from myorch.core.run import KnowledgeRef
from myorch.core.types import check_id

SOURCES = "_sources"
MOUNT_ROOT = "/sandbox/workspace/knowledge"
IGNORE_FILE = ".myorchignore"
DEFAULT_MAX = 50 * 1024 * 1024
KEEP_VERSIONS = 5


class KnowledgeError(ValueError):
    pass


class Source(BaseModel):
    """One registered knowledge source (the YAML file, plus where it was found)."""

    kind: Literal["dir", "git", "mcp"]
    path: str = ""  # dir: the folder; ${ENV} and ~ are expanded
    repo: str = ""  # git: owner/name on GitHub
    ref: str = "main"  # git: branch or tag
    include: list[str] = Field(default_factory=list)  # globs (** allowed); empty: every file
    exclude: list[str] = Field(default_factory=list)
    index: str = ""  # what the agent reads first (a file), or how to use the tools (mcp)
    projects: list[str] = Field(default_factory=list)  # who may use a shared source; empty: everyone
    max_size: int = DEFAULT_MAX  # bytes after filtering
    provider: str = ""  # git: OpenShell provider that may clone the repo; mcp: adds the read-only token
    image: str = ""  # git: sandbox image with git and python3 (default: the project's image)
    url: str = ""  # mcp: the server
    token_env: str = ""  # mcp: the provider's placeholder variable (the gateway swaps in the token)
    auth: Literal["bearer", "basic"] = "bearer"  # mcp: "Bearer <token>", or Basic base64(email:<token>)
    email: str = ""  # mcp, basic: the token owner's email (e.g. an Atlassian personal API token)
    tools: list[str] = Field(default_factory=list)  # mcp: the only tools agents may call (others are denied)
    token_read_only: bool = False  # mcp: the registrant confirms the provider's token can only read
    allow_findings: list[str] = Field(default_factory=list)  # "file:line" secret-scan hits accepted as false positives
    # filled in by load_sources
    name: str = ""
    level: Literal["global", "template", "project"] = "global"
    file: str = ""

    @field_validator("max_size", mode="before")
    @classmethod
    def _size(cls, v: Any) -> int:
        if isinstance(v, str):
            m = re.fullmatch(r"\s*(\d+)\s*([KMG]?)B?\s*", v.upper())
            if not m:
                raise ValueError(f"max_size {v!r}: use bytes or e.g. 50MB")
            return int(m.group(1)) * {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}[m.group(2)]
        return int(v)

    def problems(self) -> list[str]:
        """What keeps this source from being used, in the console's words (empty: fine)."""
        out = []
        if self.kind == "dir":
            if not self.path:
                out.append("没有写 path")
            else:
                try:
                    p = expand_path(self.path)
                    root = os.environ.get("MYORCH_KB_ROOT")  # set only in the containers
                    if not p.is_dir() and root and not p.is_relative_to(Path(root).resolve()):
                        out.append(f"控制台和 worker 只能读宿主机 {root} 下的目录（deploy/compose.yaml 只读挂载了它），"
                                   f"{p} 不在里面：把文件夹放到 {root} 下，或者从宿主机的命令行发起")
                    elif not p.is_dir():
                        out.append(f"目录不存在：{p}")
                    elif (p / ".obsidian").is_dir():  # a vault holds secrets: only a curated folder is mounted
                        out.append(f"{p} 是一个 Obsidian vault（有 .obsidian/），不能直接挂。挂一个你整理过的文件夹")
                except KnowledgeError as e:
                    out.append(str(e))
        elif self.kind == "git":
            if not self.repo or not re.fullmatch(r"[\w.-]+/[\w.-]+", self.repo):
                out.append("repo 要写成 owner/name")
            if not self.provider:
                out.append("没有写 provider（能克隆这个仓库的 OpenShell provider）")
        else:
            if not self.url.startswith("https://"):
                out.append("url 要以 https:// 开头")
            if not self.provider or not self.token_env:
                out.append("没有写 provider 和 token_env")
            if not self.tools:
                out.append("没有写 tools（允许 agent 调用的工具，其余一律禁用）")
            if self.auth == "basic" and not self.email:
                out.append("auth: basic 要写 email")
            if not self.token_read_only:
                out.append("没有确认 token 只读（token_read_only: true），不能挂载")
        return out


# ---- registry ---------------------------------------------------------------------------

def _load_dir(d: Path, level: str, root: Path) -> dict[str, Source]:
    out: dict[str, Source] = {}
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.yaml")):
        name = check_id(f.stem, "knowledge source name")
        try:
            data = yaml.safe_load(f.read_text()) or {}
            out[name] = Source.model_validate({**data, "name": name, "level": level, "file": str(f.relative_to(root))})
        except Exception as e:  # one broken file should not hide the rest; it is reported as a problem
            raise KnowledgeError(f"{f.relative_to(root)}：{e}") from e
    return out


def load_sources(kb_root: Path, project: str | None = None) -> dict[str, Source]:
    """The sources a project may use (all shared sources when project is None). Project-level
    files win over the template's, which win over the shared ones."""
    found = {n: s for n, s in _load_dir(kb_root / SOURCES, "global", kb_root).items()
             if project is None or not s.projects or project in s.projects}
    if project:
        from myorch import project as projects

        p = projects.load_project(kb_root, project)
        if p.extends:
            found.update(_load_dir(projects.template_dir(kb_root, p.extends) / "sources", "template", kb_root))
        found.update(_load_dir(projects.project_dir(kb_root, project) / "sources", "project", kb_root))
    return found


def expand_path(raw: str) -> Path:
    """A registered folder path, the same on the host and in the console / worker containers: there
    `~` is the host's home (MYORCH_HOST_HOME), not root's, and the host's knowledge root is mounted
    at the same absolute path (deploy/compose.yaml)."""
    host_home = os.environ.get("MYORCH_HOST_HOME")
    if host_home and (raw == "~" or raw.startswith("~/")):
        raw = host_home + raw[1:]
    expanded = os.path.expanduser(os.path.expandvars(raw))
    if "$" in expanded:
        missing = re.findall(r"\$\{?(\w+)", expanded)
        raise KnowledgeError(f"路径 {raw} 里的环境变量没有设置：{', '.join(missing)}")
    return Path(expanded).resolve()


# ---- filtering and the secret scan (also run inside git jobs: stdlib only) ----------------

def glob_re(pattern: str) -> re.Pattern[str]:
    """A glob over relative paths: ** spans directories, * and ? stay inside one."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(f"^{out}$")


def selected(rel: str, include: list, exclude: list) -> bool:
    if any(part in (".git", ".obsidian", ".DS_Store", ".trash") for part in rel.split("/")):
        return False
    if include and not any(glob_re(p).match(rel) for p in include):
        return False
    return not any(glob_re(p).match(rel) for p in exclude)


SECRET_RULES = [
    ("私钥", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("AWS key", r"\bA(?:KIA|SIA)[0-9A-Z]{16}\b"),
    ("GitHub token", r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    ("Anthropic key", r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    ("OpenAI key", r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}"),
    ("Slack token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    ("Google API key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("JWT", r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("密码或密钥赋值", r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|token)\b\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
    # An HTTP auth header with a literal credential (not "$TOKEN" or "<token>").
    ("Authorization 头", r"(?i)\bauthorization\s*[:=]\s*['\"]?(?:basic|bearer)\s+[A-Za-z0-9._~+/-]{12,}=*"),
    ("带凭证的地址", r"\b[a-z][a-z0-9+.-]*://[^/\s:@'\"<>]+:[^/\s@'\"<>]+@"),
    # Azure DevOps PAT (older format): 52 characters of lowercase base32, no prefix. Plain hex (hashes,
    # commit ids) never matches: at least one letter after f is required.
    ("Azure DevOps PAT", r"\b(?=[a-z2-7]{0,51}[g-z])[a-z2-7]{52}\b"),
]


def redact_text(text: str) -> tuple[str, int]:
    """Replace every line that looks like a secret (SECRET_RULES) with a note. Returns (text, lines
    replaced). Used for what agents write that leaves the sandbox (development's takeaway, its trail)."""
    hits = dict(scan_text(text))
    if not hits:
        return text, 0
    out = "\n".join(f"（这一行像{hits[n]}，已删除）" if n in hits else line for n, line in enumerate(text.splitlines(), 1))
    return out + ("\n" if text.endswith("\n") else ""), len(hits)


def redact_tree(value: Any) -> tuple[Any, int]:
    """redact_text over every string in a JSON-like value (dict keys stay as they are)."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out, n = {}, 0
        for k, v in value.items():
            out[k], m = redact_tree(v)
            n += m
        return out, n
    if isinstance(value, list):
        items = [redact_tree(v) for v in value]
        return [v for v, _ in items], sum(m for _, m in items)
    return value, 0


def scan_text(text: str) -> list[tuple[int, str]]:
    """(line number, rule name) of every line that looks like a secret. The secret itself is
    never returned, so reports can be shown and logged."""
    rules = [(name, re.compile(rx)) for name, rx in SECRET_RULES]
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for name, rx in rules:
            if rx.search(line):
                hits.append((n, name))
                break
    return hits


def collect(root: Path, include: list, exclude: list, allow: list, max_size: int) -> tuple[list[str], list[str], int]:
    """Files to snapshot (relative paths), secret-scan findings ("file:line 规则"), total bytes."""
    ignore = []
    if (root / ".myorchignore").is_file():
        ignore = [ln.strip() for ln in (root / ".myorchignore").read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    files, findings, total = [], [], 0
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(root).as_posix()
        if rel == ".myorchignore" or not selected(rel, include, exclude + ignore):
            continue
        data = p.read_bytes()
        total += len(data)
        if total > max_size:
            raise ValueError(f"过滤后超过 max_size（{max_size} 字节），收窄 include 或调大 max_size")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None  # binary files are copied but not scanned for text secrets
        if text is not None:
            for line, rule in scan_text(text):
                if f"{rel}:{line}" not in allow:
                    findings.append(f"{rel}:{line} {rule}")
        files.append(rel)
    return files, findings, total


def version_of(root: Path, files: list[str]) -> str:
    h = hashlib.sha256()
    for rel in files:
        h.update(rel.encode() + b"\0" + (root / rel).read_bytes() + b"\0")
    return f"sha256:{h.hexdigest()[:16]}"


# ---- sync --------------------------------------------------------------------------------

def _scan_failure(name: str, findings: list[str]) -> KnowledgeError:
    shown = "\n".join(f"  {f}" for f in findings[:30]) + ("\n  …" if len(findings) > 30 else "")
    return KnowledgeError(f"知识源 {name} 里有疑似密钥，没有同步。删掉这些内容，或者确认是误报后把「文件:行号」"
                          f"写进 allow_findings：\n{shown}")


def sync(store, kb_root: Path, name: str, project: str | None = None, job_runner=None) -> dict[str, Any]:
    """Snapshot one source. Returns {name, version, files, bytes, unchanged}. A source with
    secret-scan findings is not stored."""
    sources = load_sources(kb_root, project)
    if name not in sources:
        raise KnowledgeError(f"没有登记知识源 {name}" + (f"（项目 {project}）" if project else ""))
    s = sources[name]
    problems = [p for p in s.problems() if s.kind != "mcp"]
    if s.kind == "mcp":
        raise KnowledgeError(f"{name} 是 MCP 知识源，不需要同步")
    if problems:
        raise KnowledgeError(f"知识源 {name} 不能同步：{'；'.join(problems)}")
    known = {v["version"] for v in store.knowledge_versions(name)}
    if s.kind == "dir":
        root = expand_path(s.path)
        files, findings, total = collect(root, s.include, s.exclude, s.allow_findings, s.max_size)
        if findings:
            raise _scan_failure(name, findings)
        if not files:
            raise KnowledgeError(f"知识源 {name} 过滤后没有文件（{root}）")
        version = version_of(root, files)
        if version not in known:
            with tempfile.TemporaryDirectory() as tmp:
                for rel in files:
                    (Path(tmp) / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(root / rel, Path(tmp) / rel)
                store.knowledge_sync(name, version, Path(tmp), KEEP_VERSIONS)
        return {"name": name, "version": version, "files": len(files), "bytes": total, "unchanged": version in known}
    return _sync_git(store, kb_root, s, project, job_runner, known)


GIT_JOB = r'''python3 - <<'PY'
import base64, hashlib, json, os, re, shutil, subprocess, sys
from pathlib import Path
spec = json.load(open(os.path.join(os.environ["JOB_DIR"], "in", "spec.json")))
out = Path(os.environ["JOB_DIR"]) / "out"
src = out / "clone"
auth = base64.b64encode(("x-access-token:" + os.environ.get("GITHUB_TOKEN", "")).encode()).decode()
r = subprocess.run(["git", "-c", "http.extraHeader=Authorization: Basic " + auth, "clone", "--depth", "1",
                    "--branch", spec["ref"], f"https://github.com/{spec['repo']}.git", str(src)],
                   capture_output=True, text=True)
if r.returncode != 0:
    json.dump({"error": "git clone 失败：" + r.stderr[-800:]}, open(out / "result.json", "w")); sys.exit(0)
__HELPERS__
try:
    files, findings, total = collect(src, spec["include"], spec["exclude"], spec["allow"], spec["max_size"])
except ValueError as e:
    json.dump({"error": str(e)}, open(out / "result.json", "w")); sys.exit(0)
res = {"files": len(files), "bytes": total, "findings": findings}
if not findings and files:
    res["version"] = version_of(src, files)
    tree = out / "tree"
    for rel in files:
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, tree / rel)
shutil.rmtree(src, ignore_errors=True)
json.dump(res, open(out / "result.json", "w"))
PY'''


def _helpers_source() -> str:
    """The filter and scan functions, shipped into the job script so both sides agree."""
    parts = [f"SECRET_RULES = {SECRET_RULES!r}"]
    parts += [inspect.getsource(f) for f in (glob_re, selected, scan_text, collect, version_of)]
    return "\n".join(parts)


def _sync_git(store, kb_root: Path, s: Source, project: str | None, job_runner, known: set[str]) -> dict[str, Any]:
    from myorch.runner.jobs import JobRunner

    image = s.image
    if not image and project:
        from myorch import project as projects

        image = projects.load_project(kb_root, project).image or ""
    if not image:
        raise KnowledgeError(f"git 知识源 {s.name} 需要一个带 git 和 python3 的镜像：在登记文件里写 image，或者用 --project 指定项目")
    spec = {"repo": s.repo, "ref": s.ref, "include": s.include, "exclude": s.exclude,
            "allow": s.allow_findings, "max_size": s.max_size}
    key = f"knowledge-{s.name}-{hashlib.sha1(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:8]}"
    runner = job_runner or JobRunner(store, image)
    import asyncio

    res = asyncio.run(runner.run(key, GIT_JOB.replace("__HELPERS__", _helpers_source()), [s.provider],
                                 files={"spec.json": json.dumps(spec)}, timeout=600))
    if res.get("error"):
        raise KnowledgeError(f"知识源 {s.name}：{res['error']}")
    if res.get("findings"):
        raise _scan_failure(s.name, res["findings"])
    if not res.get("files"):
        raise KnowledgeError(f"知识源 {s.name} 过滤后没有文件（{s.repo}@{s.ref}）")
    version = res["version"]
    if version not in known:
        store.knowledge_adopt_job(key, s.name, version, KEEP_VERSIONS)
    return {"name": s.name, "version": version, "files": res["files"], "bytes": res["bytes"], "unchanged": version in known}


# ---- a run's knowledge -------------------------------------------------------------------

def defaults(kb_root: Path, project: str | None) -> list[str]:
    if not project:
        return []
    from myorch import project as projects

    return list(projects.load_project(kb_root, project).knowledge)


def choose(default: list[str], tokens: list[str]) -> list[str]:
    """CLI selection: `name` adds, `-name` removes, from the project's defaults."""
    out = list(default)
    for t in (t.strip() for t in tokens if t.strip()):
        if t.startswith("-"):
            out = [n for n in out if n != t[1:]]
        elif t.lstrip("+") not in out:
            out.append(t.lstrip("+"))
    return out


GIT_REFRESH = 3600  # seconds: a git source older than this is synced again when a run starts


def _age(synced_at: str) -> float:
    from datetime import datetime, timezone

    try:
        return (datetime.now(timezone.utc) - datetime.strptime(synced_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds()
    except ValueError:
        return float("inf")


def refresh(store, kb_root: Path, project: str | None, names: list[str] | None, job_runner=None) -> list[str]:
    """Before a run starts, bring the snapshots it will mount up to date (there is no sync button).

    dir: synced every time; an unchanged folder hashes to the version it already has, so nothing is
    copied. A failure (missing folder, a secret-scan hit) refuses the start: the person fixes it.
    git: synced when it never was or its newest snapshot is older than GIT_REFRESH. A failed clone
    falls back to the newest snapshot, if there is one. Returns notes for the person."""
    names = defaults(kb_root, project) if names is None else list(dict.fromkeys(names))
    sources = load_sources(kb_root, project) if names else {}
    notes = []
    for n in names:
        s = sources.get(n)
        if s is None or s.kind == "mcp":
            continue  # resolve() reports unknown names; mcp has no snapshot
        versions = store.knowledge_versions(n) if s.kind == "git" else []
        if s.kind == "git" and versions and _age(versions[0]["synced_at"]) < GIT_REFRESH:
            continue
        try:
            r = sync(store, kb_root, n, project, job_runner)
        except KnowledgeError as e:
            if s.kind == "git" and versions:
                notes.append(f"知识源 {n} 同步失败，用上一次的快照 {versions[0]['version']}：{e}")
                continue
            raise
        if not r.get("unchanged"):
            notes.append(f"知识源 {n} 有变化，已同步：{r['version']}（{r['files']} 个文件）")
    return notes


def resolve(store, kb_root: Path, project: str | None, names: list[str] | None) -> list[KnowledgeRef]:
    """What a starting run mounts: the given names (None: the project's defaults), each fixed to
    its newest snapshot. Unknown names, unsynced snapshots and mcp sources without a confirmed
    read-only token refuse the start."""
    names = defaults(kb_root, project) if names is None else list(dict.fromkeys(names))
    if not names:
        return []
    sources = load_sources(kb_root, project)
    refs = []
    for n in names:
        s = sources.get(n)
        if s is None:
            raise KnowledgeError(f"知识源 {n} 没有登记，或者项目 {project or '（无）'} 不能用它")
        if s.kind == "mcp":
            if s.problems():
                raise KnowledgeError(f"知识源 {n} 不能挂载：{'；'.join(s.problems())}")
            refs.append(KnowledgeRef(name=n, kind="mcp", index=s.index, url=s.url, provider=s.provider,
                                     token_env=s.token_env, auth=s.auth, email=s.email, tools=s.tools))
            continue
        versions = store.knowledge_versions(n)
        if not versions:
            raise KnowledgeError(f"知识源 {n} 没有快照（同步没有成功）：myorch knowledge sync {n}"
                                 + (f" --project {project}" if s.level != "global" else "") + " 可以看到原因")
        refs.append(KnowledgeRef(name=n, kind=s.kind, version=versions[0]["version"], index=s.index))
    return refs


def mcp_spec(refs: list[KnowledgeRef]) -> dict[str, Any] | None:
    """What tools/myorch-mcp needs to start the agent: each server's address, how to build its
    Authorization header from the provider's placeholder (inside the sandbox), and the only tools
    the agent may call."""
    servers = [{"name": r.name, "url": r.url, "token_env": r.token_env, "auth": r.auth, "email": r.email,
                "tools": list(r.tools)} for r in refs if r.kind == "mcp"]
    return {"servers": servers} if servers else None


def catalog(store, kb_root: Path, project: str | None = None) -> list[dict[str, Any]]:
    """Every source the console can show (for a project: the ones it may use), with its newest
    snapshot and what keeps it from being used."""
    from myorch import project as projects

    sources = load_sources(kb_root, project)
    default_of: dict[str, list[str]] = {}
    for p in projects.list_projects(kb_root):
        try:
            for n in projects.load_project(kb_root, p).knowledge:
                default_of.setdefault(n, []).append(p)
        except Exception:  # a broken pack is reported by the project list
            continue
    out = []
    for n, s in sorted(sources.items()):
        where = (s.path if s.kind == "dir" else f"{s.repo}@{s.ref}" if s.kind == "git" else s.url)
        expanded = ""
        if s.kind == "dir" and s.path:
            try:
                expanded = str(expand_path(s.path))
            except KnowledgeError:
                pass
        try:
            versions = store.knowledge_versions(n) if s.kind != "mcp" else []
        except RuntimeError:  # Docker down
            versions = []
        out.append({"name": n, "kind": s.kind, "level": s.level, "file": s.file, "where": where, "expanded": expanded,
                    "index": s.index, "tools": s.tools, "projects": s.projects, "default_in": default_of.get(n, []),
                    "fields": registered_fields(s),
                    "latest": versions[0] if versions else None, "versions": len(versions), "problems": s.problems()})
    return out


# ---- registering from the console ---------------------------------------------------------

FIELDS = {  # what a registration may set, per kind (the rest of Source is filled in by load_sources)
    "dir": ("path", "include", "exclude", "index", "projects", "max_size", "allow_findings"),
    "git": ("repo", "ref", "provider", "image", "include", "exclude", "index", "projects", "max_size", "allow_findings"),
    "mcp": ("url", "provider", "token_env", "auth", "email", "tools", "token_read_only", "index", "projects"),
}


def registered_fields(s: Source) -> dict[str, Any]:
    """The registration as written (defaults left out), for editing it in the console."""
    defaults = Source(kind=s.kind)
    return {k: getattr(s, k) for k in FIELDS[s.kind] if getattr(s, k) != getattr(defaults, k)}


def registry_file(kb_root: Path, name: str, project: str | None) -> Path:
    from myorch import project as projects

    check_id(name, "knowledge source name")
    if project is None:
        return kb_root / SOURCES / f"{name}.yaml"
    d = projects.project_dir(kb_root, project)
    if not (d / "project.yaml").is_file():
        raise KnowledgeError(f"没有项目 {project}")
    return d / "sources" / f"{name}.yaml"


def _clean(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    if kind not in FIELDS:
        raise KnowledgeError(f"类型只能是 dir、git 或 mcp，不是 {kind!r}")
    out: dict[str, Any] = {"kind": kind}
    for k in FIELDS[kind]:
        v = data.get(k)
        if isinstance(v, str):
            v = v.strip()
            if k in ("include", "exclude", "projects", "tools", "allow_findings"):
                v = [x.strip() for x in re.split(r"[,\n]", v) if x.strip()]
        if isinstance(v, list):
            v = [str(x).strip() for x in v if str(x).strip()]
        if v not in (None, "", [], False):
            out[k] = v
    return out


def check(kb_root: Path, name: str, project: str | None, data: dict[str, Any]) -> dict[str, Any]:
    """Validate a registration without saving: its problems, and for a folder what a snapshot would
    take (files, size, secret-scan findings). Nothing is copied."""
    body = _clean(str(data.get("kind") or ""), data)
    try:
        s = Source.model_validate({**body, "name": check_id(name, "knowledge source name")})
    except ValueError as e:
        raise KnowledgeError(str(e)) from e
    registry_file(kb_root, name, project)  # the name and the project are valid
    out: dict[str, Any] = {"problems": s.problems(), "yaml": yaml.safe_dump(body, allow_unicode=True, sort_keys=False)}
    if s.kind == "dir" and not out["problems"]:
        files, findings, total = collect(expand_path(s.path), s.include, s.exclude, s.allow_findings, s.max_size)
        out.update(files=len(files), bytes=total, findings=findings[:30], sample=files[:15])
        if findings:
            out["problems"].append(f"扫描到 {len(findings)} 处疑似密钥，同步会失败。删掉这些内容，或者把误报的「文件:行号」写进 allow_findings")
        elif not files:
            out["problems"].append("过滤后没有文件")
    return out


def _commit(kb_root: Path, rel: str, message: str, by: str) -> str:
    import subprocess

    if not (kb_root / ".git").is_dir():
        return ""
    git = ["git", "-C", str(kb_root)]
    subprocess.run([*git, "add", "-A", "--", rel], check=True, capture_output=True)
    if subprocess.run([*git, "diff", "--cached", "--quiet"]).returncode == 0:
        return ""  # nothing changed
    subprocess.run([*git, "-c", "user.name=myorch", "-c", "user.email=myorch@localhost", "commit", "-q", "-m", message,
                    "-m", f"by: {by} (console)"], check=True, capture_output=True)
    return subprocess.run([*git, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()


def register(kb_root: Path, name: str, project: str | None, data: dict[str, Any], by: str,
             replace: bool = False) -> dict[str, Any]:
    """Write the registration file (myorch-knowledge/_sources/ or <project>/sources/) and commit it.
    It is saved even with problems (a folder that does not exist yet); the console shows them and a
    run cannot mount the source until they are fixed."""
    result = check(kb_root, name, project, data)
    f = registry_file(kb_root, name, project)
    if f.exists() and not replace:
        raise KnowledgeError(f"{f.relative_to(kb_root)} 已经存在。要改它，在它的页面上点「编辑」")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"# Registered in the console by {by}. Fields: myorch/knowledge.py Source.\n" + result["yaml"])
    rel = str(f.relative_to(kb_root))
    commit = _commit(kb_root, rel, f"knowledge: {'update' if replace else 'register'} {name}" + (f" ({project})" if project else ""), by)
    return {**result, "file": rel, "commit": commit}


def unregister(kb_root: Path, name: str, project: str | None, by: str) -> dict[str, Any]:
    """Delete the registration file and commit. Snapshots on the volume stay (runs that used them
    keep their record); new runs can no longer mount the source."""
    f = registry_file(kb_root, name, project)
    if not f.is_file():
        raise KnowledgeError(f"没有登记 {f.relative_to(kb_root)}")
    rel = str(f.relative_to(kb_root))
    f.unlink()
    return {"file": rel, "commit": _commit(kb_root, rel, f"knowledge: remove {name}" + (f" ({project})" if project else ""), by)}
