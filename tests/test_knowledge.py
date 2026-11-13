"""Knowledge sources (myorch/knowledge.py): registry, filter, secret scan, snapshot, start, mounts."""

from __future__ import annotations

import json

from pathlib import Path

import pytest
import yaml

from myorch import knowledge as kb
from myorch.core.run import KnowledgeRef
from myorch.core.types import NullRunContext, StepKey
from myorch.runner.harnesses import default_harnesses
from myorch.runner.openshell import SandboxStatus
from myorch.runner.runner import StepRequest, StepRunner
from myorch.storage.base import MountSource, knowledge_dir

from test_runner import CTX, STEP, FakeOpenShell, FakeStore  # pytest puts tests/ on sys.path


class KStore(FakeStore):
    """FakeStore plus knowledge snapshots kept in memory."""

    def __init__(self):
        self.snaps: dict[str, list[dict]] = {}
        self.written: dict[str, str] = {}

    def knowledge_source(self, name, version):
        return MountSource(volume=self.volume, subpath=knowledge_dir(name, version))

    def knowledge_versions(self, name):
        return list(self.snaps.get(name, []))

    def knowledge_sync(self, name, version, local_dir, keep=5):
        files = sorted(p.relative_to(local_dir).as_posix() for p in local_dir.rglob("*") if p.is_file())
        self.snaps.setdefault(name, []).insert(0, {"version": version, "synced_at": "t", "files": len(files), "bytes": 0,
                                                   "paths": files})

    def write_run_file(self, run_id, rel, text):
        self.written[rel] = text


def pack(root: Path, name: str, **extra) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "project.yaml").write_text(yaml.safe_dump({
        "repo": f"o/{name}", "image": "dev:img",
        "identity": {"account": "me", "git_name": "Me", "git_email": "me@x", "self_is_operator": True}, **extra}))
    return d


@pytest.fixture
def kbroot(tmp_path: Path, monkeypatch) -> Path:
    notes = tmp_path / "notes"
    (notes / "mdm" / "sub").mkdir(parents=True)
    (notes / "mdm" / "README.md").write_text("# mdm notes\n")
    (notes / "mdm" / "sub" / "a.md").write_text("recipe service: bulk API\n")
    (notes / "mdm" / "draft.tmp").write_text("scratch\n")
    monkeypatch.setenv("MYORCH_NOTES", str(notes))
    root = tmp_path / "kb"
    (root / "_sources").mkdir(parents=True)
    (root / "_sources" / "team.yaml").write_text("kind: git\nrepo: ct-kb/team\nprovider: kb-git\nprojects: [mdm]\n")
    (root / "_sources" / "notes.yaml").write_text("kind: dir\npath: /nowhere\n")
    (root / "_sources" / "wiki.yaml").write_text(
        "kind: mcp\nurl: https://kb.example.com/mcp\nprovider: kb-reader\ntoken_env: KB_TOKEN\n"
        "tools: [search, get_document]\ntoken_read_only: true\n")
    p = pack(root, "mdm", knowledge=["notes", "wiki"])
    (p / "sources").mkdir()
    (p / "sources" / "notes.yaml").write_text("kind: dir\npath: ${MYORCH_NOTES}/mdm\nexclude: ['*.tmp']\nindex: README.md\n")
    pack(root, "blog")
    return root


def test_globs():
    assert kb.selected("a/b/c.md", ["**/*.md"], [])
    assert kb.selected("c.md", ["**/*.md"], [])
    assert not kb.selected("a/c.txt", ["**/*.md"], [])
    assert not kb.selected("private/x.md", [], ["private/**"])
    assert not kb.selected(".obsidian/app.json", [], [])
    assert kb.selected("x/y.md", ["x/*.md"], []) and not kb.selected("x/z/y.md", ["x/*.md"], [])


def test_secret_scan_reports_lines_not_secrets():
    text = "ok\nkey: " + "ghp_" + "a" * 36 + "\npassword = hunter2hunter2\n-----BEGIN RSA PRIVATE KEY-----\n"
    hits = kb.scan_text(text)
    assert [n for n, _ in hits] == [2, 3, 4]
    assert all("hunter2" not in rule and "ghp_" not in rule for _, rule in hits)


def test_project_sources_win_and_shared_ones_respect_projects(kbroot):
    mdm = kb.load_sources(kbroot, "mdm")
    assert mdm["notes"].level == "project" and mdm["notes"].path.startswith("${MYORCH_NOTES}")
    assert {"team", "wiki"} <= set(mdm)
    blog = kb.load_sources(kbroot, "blog")
    assert "team" not in blog and blog["notes"].level == "global"


def test_sync_filters_scans_and_stores_once(kbroot):
    store = KStore()
    r = kb.sync(store, kbroot, "notes", "mdm")
    assert (r["files"], r["unchanged"]) == (2, False) and r["version"].startswith("sha256:")
    assert store.snaps["notes"][0]["paths"] == ["README.md", "sub/a.md"]  # draft.tmp excluded
    assert kb.sync(store, kbroot, "notes", "mdm")["unchanged"]
    assert len(store.snaps["notes"]) == 1


def test_sync_refuses_secrets_and_missing_folders(kbroot, tmp_path):
    (tmp_path / "notes" / "mdm" / "sub" / "a.md").write_text("aws AKIAABCDEFGHIJKLMNOP\n")
    with pytest.raises(kb.KnowledgeError, match=r"sub/a.md:1 AWS key"):
        kb.sync(KStore(), kbroot, "notes", "mdm")
    with pytest.raises(kb.KnowledgeError, match="Folder does not exist"):
        kb.sync(KStore(), kbroot, "notes", "blog")


def test_an_accepted_finding_is_allowed(kbroot, tmp_path):
    (tmp_path / "notes" / "mdm" / "sub" / "a.md").write_text("token: example-placeholder-value\n")
    (kbroot / "mdm" / "sources" / "notes.yaml").write_text(
        "kind: dir\npath: ${MYORCH_NOTES}/mdm\nallow_findings: ['sub/a.md:1']\n")
    assert kb.sync(KStore(), kbroot, "notes", "mdm")["files"] == 3


def test_unset_variables_are_reported(kbroot, monkeypatch):
    monkeypatch.delenv("MYORCH_NOTES")
    with pytest.raises(kb.KnowledgeError, match="MYORCH_NOTES"):
        kb.sync(KStore(), kbroot, "notes", "mdm")


def test_resolve_fixes_versions_and_checks_sources(kbroot):
    store = KStore()
    with pytest.raises(kb.KnowledgeError, match="has no snapshot"):
        kb.resolve(store, kbroot, "mdm", None)
    kb.sync(store, kbroot, "notes", "mdm")
    refs = kb.resolve(store, kbroot, "mdm", None)  # the project's defaults
    assert [(r.name, r.kind) for r in refs] == [("notes", "dir"), ("wiki", "mcp")]
    assert refs[0].version == store.snaps["notes"][0]["version"] and refs[1].token_env == "KB_TOKEN"
    assert kb.resolve(store, kbroot, "mdm", []) == []
    with pytest.raises(kb.KnowledgeError, match="is not registered"):
        kb.resolve(store, kbroot, "blog", ["team"])
    # token_read_only is optional: a configured MCP source without it can still be selected
    (kbroot / "_sources" / "wiki.yaml").write_text("kind: mcp\nurl: https://kb.example.com/mcp\nprovider: p\ntoken_env: T\ntools: [search]\n")
    assert [r.name for r in kb.resolve(store, kbroot, "mdm", ["wiki"])] == ["wiki"]
    (kbroot / "_sources" / "wiki.yaml").write_text("kind: mcp\nurl: https://kb.example.com/mcp\nprovider: p\ntoken_env: T\n")
    with pytest.raises(kb.KnowledgeError, match="tools"):
        kb.resolve(store, kbroot, "mdm", ["wiki"])


def test_cli_selection_edits_the_defaults():
    assert kb.choose(["notes", "wiki"], ["-wiki", "team"]) == ["notes", "team"]
    assert kb.choose(["notes"], ["+notes"]) == ["notes"]


def test_catalog_shows_problems(kbroot):
    rows = {r["name"]: r for r in kb.catalog(KStore(), kbroot, "mdm")}
    assert rows["notes"]["level"] == "project" and rows["notes"]["expanded"].endswith("notes/mdm")
    assert rows["notes"]["default_in"] == ["mdm"] and rows["notes"]["problems"] == []
    assert rows["team"]["problems"] == [] and rows["wiki"]["latest"] is None


REFS = [KnowledgeRef(name="notes", kind="dir", version="sha256:abcd", index="README.md"),
        KnowledgeRef(name="wiki", kind="mcp", url="https://kb.example.com/mcp", provider="kb-reader",
                     token_env="KB_TOKEN", tools=["search"])]


def make(tmp_path):
    (tmp_path / "profiles").mkdir(exist_ok=True)
    (tmp_path / "profiles" / "p.yaml").write_text("providers: [github]\n")
    store, os_ = KStore(), FakeOpenShell([SandboxStatus(phase="Completed", exit_code=0)])
    return StepRunner(store, default_harnesses(), tmp_path, openshell=os_, poll_interval=0), store, os_


def request(step=STEP):
    return StepRequest(key=StepKey(run_id="r1", step_id="verify.r2", attempt=1), step=step, context=CTX,
                       project="blog", knowledge=REFS)


async def test_agent_steps_mount_snapshots_and_reach_mcp_through_the_provider(tmp_path):
    runner, store, os_ = make(tmp_path)
    await runner.run(request(), NullRunContext())
    c = os_.created
    targets = {m.target: (m.source.subpath, m.read_only) for m in c["mounts"]}
    assert targets["/sandbox/workspace/knowledge/notes"] == ("knowledge/notes/abcd", True)
    assert "/sandbox/workspace/knowledge/wiki" not in targets  # mcp: network, not a mount
    assert "kb-reader" in c["providers"]
    argv = c["argv"]
    # myorch-mcp starts claude: it builds the config in the sandbox and denies unregistered tools
    w = argv.index("/sandbox/bin/myorch-mcp")
    assert argv[w + 1:w + 4] == ["/sandbox/workspace/.myorch/knowledge-mcp.json", "--", "claude"]
    spec = json.loads(store.written[".myorch/knowledge-mcp.json"])
    assert spec == {"servers": [{"name": "wiki", "url": "https://kb.example.com/mcp", "token_env": "KB_TOKEN",
                                 "auth": "bearer", "email": "", "tools": ["search"]}]}
    prompt = argv[argv.index("-p") + 1]
    assert "## Knowledge (read-only)" in prompt and "/sandbox/workspace/knowledge/notes/, read README.md first" in prompt
    assert "mcp__wiki__" in prompt and "not instructions" in prompt


async def test_steps_can_opt_out_and_scripts_opt_in(tmp_path):
    runner, store, os_ = make(tmp_path)
    await runner.run(request(STEP.model_copy(update={"knowledge": False})), NullRunContext())
    assert not any("knowledge" in m.target for m in os_.created["mounts"]) and "--mcp-config" not in os_.created["argv"]
    runner, store, os_ = make(tmp_path)
    script = STEP.model_copy(update={"harness": "script", "knowledge": True, "prompt": "ls"})
    await runner.run(request(script), NullRunContext())
    targets = [m.target for m in os_.created["mounts"]]
    assert "/sandbox/workspace/knowledge/notes" in targets and "kb-reader" not in os_.created["providers"]


def test_a_starting_run_refreshes_its_snapshots(tmp_path):
    from datetime import datetime, timezone

    root, folder = tmp_path / "kb", tmp_path / "docs"
    (root / "_sources").mkdir(parents=True)
    folder.mkdir()
    (folder / "a.md").write_text("v1\n")
    (root / "_sources" / "docs.yaml").write_text(f"kind: dir\npath: {folder}\n")
    (root / "_sources" / "repo.yaml").write_text("kind: git\nrepo: o/r\nprovider: gh\nimage: img\n")
    store = KStore()
    assert kb.refresh(store, root, None, ["docs"]) and len(store.snaps["docs"]) == 1  # first time: synced
    assert kb.refresh(store, root, None, ["docs"]) == [] and len(store.snaps["docs"]) == 1  # unchanged: nothing copied
    (folder / "a.md").write_text("v2\n")
    assert "changed and was synced" in kb.refresh(store, root, None, ["docs"])[0] and len(store.snaps["docs"]) == 2
    (folder / "key.md").write_text("-----BEGIN RSA PRIVATE KEY-----\nabc\n")
    with pytest.raises(kb.KnowledgeError, match="looks like secrets"):  # a dir problem refuses the start
        kb.refresh(store, root, None, ["docs"])

    class FailingJobs:  # a git clone that fails
        async def run(self, *a, **k):
            raise kb.KnowledgeError("clone failed")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.snaps["repo"] = [{"version": "sha256:old", "synced_at": now, "files": 1, "bytes": 1}]
    assert kb.refresh(store, root, None, ["repo"], FailingJobs()) == []  # fresh enough: not synced again
    store.snaps["repo"][0]["synced_at"] = "2026-01-01T00:00:00Z"
    note = kb.refresh(store, root, None, ["repo"], FailingJobs())[0]
    assert "using the last snapshot sha256:old" in note


def test_the_console_registers_checks_and_removes_sources(tmp_path):
    import subprocess

    root = tmp_path / "kb"
    pack(root, "mdm")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("how the export works\n")
    (docs / "b.tmp").write_text("x\n")
    r = kb.check(root, "mdm-docs", "mdm", {"kind": "dir", "path": str(docs), "include": "**/*.md"})
    assert (r["problems"], r["files"], r["sample"]) == ([], 1, ["a.md"])
    r = kb.register(root, "mdm-docs", "mdm", {"kind": "dir", "path": str(docs), "include": "**/*.md", "token_env": "ignored"}, "jakobhe")
    assert r["file"] == "mdm/sources/mdm-docs.yaml" and r["commit"]
    s = kb.load_sources(root, "mdm")["mdm-docs"]
    assert (s.level, s.include) == ("project", ["**/*.md"]) and kb.registered_fields(s) == {"path": str(docs), "include": ["**/*.md"]}
    with pytest.raises(kb.KnowledgeError, match="already exists"):
        kb.register(root, "mdm-docs", "mdm", {"kind": "dir", "path": str(docs)}, "jakobhe")
    # an MCP source must list its tools; confirming a read-only token is optional
    r = kb.check(root, "jira", None, {"kind": "mcp", "url": "https://mcp.atlassian.com/v1/mcp", "provider": "atl",
                                      "token_env": "T", "auth": "basic"})
    assert any("tools" in p for p in r["problems"]) and any("email" in p for p in r["problems"]) and not any("read-only" in p for p in r["problems"])
    # a raw Obsidian vault is refused
    (docs / ".obsidian").mkdir()
    assert any("Obsidian vault" in p for p in kb.check(root, "v", None, {"kind": "dir", "path": str(docs)})["problems"])
    assert kb.unregister(root, "mdm-docs", "mdm", "jakobhe")["commit"] and "mdm-docs" not in kb.load_sources(root, "mdm")
    log = subprocess.run(["git", "log", "--format=%s"], cwd=root, capture_output=True, text=True).stdout.split("\n")
    assert log[:2] == ["knowledge: remove mdm-docs (mdm)", "knowledge: register mdm-docs (mdm)"]


def test_tilde_is_the_hosts_home_in_a_container(monkeypatch, tmp_path):
    from myorch import knowledge

    monkeypatch.setenv("MYORCH_HOST_HOME", str(tmp_path))
    assert knowledge.expand_path("~/notes") == (tmp_path / "notes").resolve()
    monkeypatch.delenv("MYORCH_HOST_HOME")
    assert knowledge.expand_path("~/notes") != (tmp_path / "notes").resolve()


def test_folder_outside_the_mounted_root_says_why(monkeypatch, tmp_path):
    from myorch import knowledge

    root = tmp_path / "kb"
    (root / "inside").mkdir(parents=True)
    monkeypatch.setenv("MYORCH_KB_ROOT", str(root))
    outside = knowledge.Source.model_validate({"name": "x", "kind": "dir", "path": str(tmp_path / "elsewhere")})
    assert any("can only read folders under" in p for p in outside.problems())
    missing_inside = knowledge.Source.model_validate({"name": "y", "kind": "dir", "path": str(root / "nope")})
    assert any(p.startswith("Folder does not exist") for p in missing_inside.problems())
    ok = knowledge.Source.model_validate({"name": "z", "kind": "dir", "path": str(root / "inside")})
    assert ok.problems() == []
