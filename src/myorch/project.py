"""Project packs: everything project-specific, in one directory per project.

    myorch-knowledge/<name>/
      project.yaml        repo, base branch, identity, provider roles, image, branch name, conventions
      guide/              knowledge maintained by myorch (learn / onboard edit it)
      checks.sh           the project's checks; gets the changed files, decides what to run
      setup.sh            optional: prepare the checkout (link baked deps, write build config)
      risk-rules.yaml     which paths need how many human approvals
      security.md         what the security review should focus on
      policy.yaml         optional OpenShell policy for steps that need network (e.g. registries)
      prompts/<slot>.md   optional: project-specific text the flows put into a step's prompt
      image/              Dockerfile for the project's dev image (not part of the snapshot)
      providers/          OpenShell provider profiles the project needs (not part of the snapshot)

Flows stay generic: one set for every project. They use {{project.x}} (filled in at submit time) and
read the pack, which is snapshotted to the volume and mounted read-only at /sandbox/workspace/project/.
What differs between projects is in the pack: settings that shape a flow (`flows:` in project.yaml,
e.g. how many reviewers) and text for its prompts (prompts/<slot>.md, {{project.prompts.<slot>}}).

The snapshot is what a sandbox sees, and it differs from the pack: project.yaml is not in it.
project.json holds the merged config (Project.public(), the {{project.x}} values); risk-rules.yaml
and security.md are the layers combined, plus risk-rules.json; image/, providers/ and sources/ are
left out. Each sync writes a new version (projects/<name>/<hash>/); a run mounts its own version.

Projects built on the same stack share a template: `extends: core-ng` in project.yaml inherits
myorch-knowledge/_templates/core-ng/. project.yaml is deep-merged (the project wins); files are
overlaid (a project file replaces the template's file of the same name, so guide/ merges);
risk-rules.yaml rules and security.md are concatenated, template first.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from myorch.core.types import check_id

# Files in a pack that agents may propose changes to, per action.
EDITABLE = {
    "learn": ("guide/", "checks.sh", "checks.env"),  # knowledge, and the checks every run runs (a person adopts them)
    "onboard": ("guide/", "checks.sh", "setup.sh", "risk-rules.yaml", "security.md"),
    # A development run's review loop, when a blocking problem is in the pack (e.g. a broken check).
    # Not risk-rules.yaml or security.md: the run must not change how its own change is approved.
    "fix": ("guide/", "checks.sh", "checks.env", "setup.sh"),
}


class Identity(BaseModel):
    """Who acts on GitHub for this project: a dedicated bot, or the operator's own account."""

    account: str
    git_name: str
    git_email: str
    # The account is the operator themselves: myorch's own messages are recognised by their hidden
    # markers rather than by author, and the trigger is a command instead of an @mention.
    self_is_operator: bool = False
    trigger: str | None = None  # default: "@<account>" for a bot, "/myorch" for the operator

    @property
    def trigger_word(self) -> str:
        return self.trigger or ("/myorch" if self.self_is_operator else f"@{self.account}")


TEMPLATES = "_templates"
CONCATENATED = ("risk-rules.yaml", "security.md")  # template + project, rather than overlaid


class E2E(BaseModel):
    """How the project's end-to-end tests run. The flow block (flows/lib/e2e.yaml) is the same for
    every project; the technology (browser, app emulator, device service) lives in the pack's e2e/
    directory and in these settings."""

    image: str = ""  # sandbox image with the test tools; empty: the project has no end-to-end tests
    # Sandbox access for the test steps. dev-read: repo read-only, no credentials, no internet. A
    # project that hands tests to an external device service needs a profile with its provider.
    profile: str = "dev-read"
    acceptance: bool = True  # an agent also writes and runs test cases for each change's acceptance criteria


class Jira(BaseModel):
    """Where the project's tickets live. development reads the ticket (read only) before the analysis
    through the project's `jira` provider (openshell/providers/jira-read.yaml)."""

    url: str = ""  # e.g. https://example.atlassian.net; empty: the project has no Jira
    key: str = ""  # project key put before a bare ticket number: 18700 -> MD-18700
    auth: Literal["basic", "bearer"] = "basic"  # basic: Atlassian Cloud (email + API token); bearer: a Data Center PAT
    email: str = ""  # the token owner's email, for basic


class Development(BaseModel):
    """How the development flow (flows/development.yaml) runs for this project. The flow is the same
    for every project; these settings pick its shape: how hard the review is, what happens after it."""

    reviewers: int = Field(default=3, ge=1, le=8)  # parallel reviewers per round
    max_rounds: int = Field(default=3, ge=1, le=10)  # review rounds before on_exhausted
    security: bool = True  # a separate security reviewer each round
    on_exhausted: Literal["ask", "fail", "accept"] = "ask"  # blocking findings left after the last round
    # Commit the run's task information and takeaway to repo/myorch/<date>-<run_id>/ (learn reads them
    # from the base branch). Only for a repository where such a directory is welcome.
    takeaway: bool = False
    # Offer the `incident` input: a run where something went wrong, mounted read-only for develop.
    incident: bool = False


class Flows(BaseModel):
    """Per-flow settings ({{project.flows.<flow>.x}}); the flows stay the same for every project."""

    development: Development = Field(default_factory=lambda: Development())
    # Prompt rules left out of every flow this project runs ([[rule:<id>]] blocks, core/fragment.py;
    # ids and why each rule exists: flows/rules/registry.yaml). For evals: `myorch run --set
    # flows.ablate=<id>,...` on a replay compares a case with and without them. A pack can set it too.
    ablate: list[str] = Field(default_factory=list)


# Project-specific text the flows put into a step's prompt, from the pack's prompts/<slot>.md
# ({{project.prompts.<slot>}}). A template's file comes first, then the project's.
PROMPT_SLOTS = ("develop", "review", "verify", "fix", "risk")


class Sweep(BaseModel):
    """What the project's sweep flow (flows/<prefix>-sweep.yaml) looks at: the repository's files, in a fixed order, a chunk per run."""

    paths: list[str] = Field(default_factory=list)  # directories (or files) to sweep; empty: the whole repository
    include: list[str] = Field(default_factory=list)  # globs (** allowed); empty: every file under paths
    exclude: list[str] = Field(default_factory=list)
    chunk: int = Field(default=20, ge=1, le=200)  # files per run


class Project(BaseModel):
    name: str
    extends: str | None = None  # template under _templates/ this project builds on
    repo: str  # owner/name on GitHub
    base: str = "main"
    submodules: list[str] = Field(default_factory=list)  # other repos the checkout needs (owner/name)
    identity: Identity
    providers: dict[str, str | None] = Field(default_factory=dict)  # role -> provider instance (None: not used)
    image: str
    sweep: Sweep = Field(default_factory=lambda: Sweep())  # the sweep flow: which code to look at, a chunk per run
    e2e: E2E = Field(default_factory=lambda: E2E())  # end-to-end tests (flows/lib/e2e.yaml); none by default
    jira: Jira = Field(default_factory=lambda: Jira())  # where tickets are read from; none by default
    flows: Flows = Field(default_factory=lambda: Flows())  # how the shared flows run for this project
    # prompts/<slot>.md of the template and the project (read by load_project, not from project.yaml)
    prompts: dict[str, str] = Field(default_factory=lambda: {s: "" for s in PROMPT_SLOTS})
    branch:str = "myorch/{{run.id}}"  # rendered at run time
    conventions: str = ""  # commit / PR conventions, shown to agents
    pr_template: str = ""  # path in the repo, appended to PR bodies
    repo_docs: list[str] = Field(default_factory=list)  # knowledge that lives in the repo itself
    required_inputs: list[str] = Field(default_factory=list)  # e.g. a ticket number
    # Knowledge sources mounted into this project's runs unless the start says otherwise
    # (names registered under _sources/ or this pack's sources/; myorch/knowledge.py).
    knowledge: list[str] = Field(default_factory=list)
    policy: str | None = None  # file in the pack
    shared: list[str] = Field(default_factory=list)  # shared dirs mounted in every step (caches), see storage
    # How a change is reviewed before the approval gate. Only "local": nothing leaves the sandbox
    # before approval; the change is reviewed in the console (myorch ui) or with `myorch review <run>`.
    # People no longer talk to myorch on GitHub, so the draft-PR mode is gone.
    review: Literal["local"] = "local"
    allow: list[str] = Field(default_factory=list)  # GitHub logins that may instruct myorch (channels)
    # People who can approve this project's changes. The approval gate never asks for more people
    # than are listed here (empty: no cap).
    approvers: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return check_id(v, "project name")

    def public(self) -> dict[str, Any]:
        """What flows and agents may see: {{project.x}} values and the run context."""
        d = self.model_dump(mode="json", by_alias=True)
        d["identity"]["trigger"] = self.identity.trigger_word
        d["repo_docs_list"] = "\n".join(f"- repo/{p}" for p in self.repo_docs) or "（无）"
        d["submodules_list"] = " ".join(self.submodules)
        return d


def with_defaults(public: dict[str, Any] | None) -> dict[str, Any] | None:
    """A Project.public() dict with the settings flows rely on (flows, prompts) filled in where it
    lacks them: one recorded before they existed, or one built by hand."""
    if public is None:
        return None
    out = dict(public)
    out["flows"] = _deep_merge(Flows().model_dump(mode="json"), dict(public.get("flows") or {}))
    out["prompts"] = {**{s: "" for s in PROMPT_SLOTS}, **dict(public.get("prompts") or {})}
    return out


def project_dir(root: Path, name: str) -> Path:
    return root / check_id(name, "project name")


def template_dir(root: Path, name: str) -> Path:
    return root / TEMPLATES / check_id(name, "template name")


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _read_yaml(p: Path) -> dict[str, Any]:
    return (yaml.safe_load(p.read_text()) or {}) if p.is_file() else {}


def load_project(root: Path, name: str) -> Project:
    p = project_dir(root, name) / "project.yaml"
    if not p.is_file():
        raise FileNotFoundError(f"project {name!r} not found: create {p}")
    own = _read_yaml(p)
    own.pop("prompts", None)  # only from prompts/*.md
    base: dict[str, Any] = {}
    layers = [project_dir(root, name)]
    if own.get("extends"):
        t = template_dir(root, own["extends"])
        if not t.is_dir():
            raise FileNotFoundError(f"project {name!r} extends {own['extends']!r}, but {t} does not exist")
        base = _read_yaml(t / "project.yaml")
        base.pop("prompts", None)
        layers.insert(0, t)
    return Project.model_validate({**_deep_merge(base, own), "name": name, "prompts": _read_prompts(name, layers)})


def _read_prompts(name: str, layers: list[Path]) -> dict[str, str]:
    """prompts/<slot>.md of each layer (template first), joined per slot. An unknown file name is an
    error: a typo would otherwise drop the text silently."""
    out = {s: [] for s in PROMPT_SLOTS}
    for layer in layers:
        d = layer / "prompts"
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if f.name.startswith(".") or f.is_dir():
                continue
            slot = f.name.removesuffix(".md")
            if not f.name.endswith(".md") or slot not in out:
                raise ValueError(f"project {name!r}: {f} is not a prompt slot; "
                                 f"use prompts/<slot>.md with slot one of {', '.join(PROMPT_SLOTS)}")
            text = f.read_text(encoding="utf-8").strip()
            if text:
                out[slot].append(text)
    return {s: "\n\n".join(parts) for s, parts in out.items()}


def list_projects(root: Path) -> list[str]:
    return sorted(d.name for d in root.iterdir() if (d / "project.yaml").is_file()) if root.is_dir() else []


def list_templates(root: Path) -> list[str]:
    t = root / TEMPLATES
    return sorted(d.name for d in t.iterdir() if d.is_dir()) if t.is_dir() else []


def snapshot(store, root: Path, name: str) -> tuple[int, str]:
    """Copy the pack to the volume. Returns (files, version). Knowledge sources are snapshotted
    on their own (myorch/knowledge.py); their registry (sources/) stays out of the pack snapshot."""
    project = load_project(root, name)
    src = project_dir(root, name)
    layers = ([template_dir(root, project.extends)] if project.extends else []) + [src]
    ignore = shutil.ignore_patterns(".git", ".DS_Store", "image", "providers", "sources", "project.yaml")
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        for layer in layers:  # template first, then the project overlays it
            shutil.copytree(layer, dest, dirs_exist_ok=True, ignore=ignore)
        # Concatenated rather than replaced: rules and security focus add up across layers.
        rules = [r for layer in layers for r in (_read_yaml(layer / "risk-rules.yaml").get("rules") or [])]
        defaults = [_read_yaml(layer / "risk-rules.yaml").get("default") for layer in layers]
        merged_rules = {"rules": rules, "default": next((d for d in reversed(defaults) if d is not None), 0)}
        if any((layer / "risk-rules.yaml").is_file() for layer in layers):
            (dest / "risk-rules.yaml").write_text(yaml.safe_dump(merged_rules, allow_unicode=True, sort_keys=False))
            (dest / "risk-rules.json").write_text(json.dumps(merged_rules, ensure_ascii=False))  # no YAML parser needed
        security = [(layer / "security.md").read_text() for layer in layers if (layer / "security.md").is_file()]
        if security:
            (dest / "security.md").write_text("\n\n".join(security))
        (dest / "project.json").write_text(json.dumps(project.public(), ensure_ascii=False, indent=1))
        files = sorted(p for p in dest.rglob("*") if p.is_file())
        h = hashlib.sha256()
        for f in files:
            h.update(str(f.relative_to(dest)).encode() + b"\0" + f.read_bytes() + b"\0")
        version = f"sha256:{h.hexdigest()[:16]}"
        store.project_sync(name, dest, version)
    return len(files), version


def pack_file(root: Path, name: str, rel: str) -> Path:
    """A file of the pack as layered: the project's own, else its template's."""
    if Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise ValueError(f"{rel!r} is not a file in the project pack")
    own = project_dir(root, name) / rel
    if own.is_file():
        return own
    extends = load_project(root, name).extends
    if extends and (template_dir(root, extends) / rel).is_file():
        return template_dir(root, extends) / rel
    raise FileNotFoundError(f"project {name}: {rel} not found in the pack or its template")


def starting_text(root: Path, name: str, target: Path) -> str:
    """Current text of a pack file about to be edited. A file the project only inherits is copied
    from its template first (copy-on-write); concatenated files start from the project's own part."""
    if target.exists():
        return target.read_text(encoding="utf-8")
    rel = target.relative_to(project_dir(root, name).resolve())
    extends = load_project(root, name).extends
    inherited = template_dir(root, extends) / rel if extends else None
    if inherited is not None and inherited.is_file() and str(rel) not in CONCATENATED:
        return inherited.read_text(encoding="utf-8")
    return ""


def resolve_editable(root: Path, name: str, sandbox_path: str, action: str) -> Path:
    """Map 'project/<file>' (as seen in the sandbox) to the pack file, if the action may edit it."""
    rel = sandbox_path.removeprefix("/sandbox/workspace/").removeprefix("project/")
    parts = Path(rel).parts
    if not parts or ".." in parts or Path(rel).is_absolute():
        raise ValueError(f"{sandbox_path!r} is not a file in the project pack")
    allowed = EDITABLE.get(action, ())
    if not any(rel == a or (a.endswith("/") and rel.startswith(a)) for a in allowed):
        raise ValueError(f"{action} may not change {rel!r}; allowed: {', '.join(allowed)}")
    base = project_dir(root, name).resolve()
    target = (base / rel).resolve()
    if base not in target.parents:
        raise ValueError(f"{sandbox_path!r} escapes the project pack")
    return target


def github_provider_profile(project: Project, read_only: bool = False) -> str:
    """OpenShell provider profile for the project's GitHub provider: push and pull requests on the
    project's repository, read-only clone/fetch of its submodules, nothing else. read_only drops
    push, pull requests and comments (enough for onboard and for reading PRs)."""
    pid = f"github-{project.name}"
    git_rules = [f'      - allow: {{ method: GET, path: "/{r}.git/**" }}' for r in [project.repo, *project.submodules]]
    git_rules += [f'      - allow: {{ method: POST, path: "/{r}.git/git-upload-pack" }}' for r in [project.repo, *project.submodules]]
    r = project.repo
    api_rules = [f'      - allow: {{ method: GET, path: "/repos/{r}" }}', f'      - allow: {{ method: GET, path: "/repos/{r}/**" }}']
    if not read_only:
        git_rules.append(f'      - allow: {{ method: POST, path: "/{r}.git/git-receive-pack" }}')
        api_rules += [
            f'      - allow: {{ method: POST, path: "/repos/{r}/pulls" }}',
        ]
    mode = "READ-ONLY: clone/fetch and API reads only" if read_only else f"push branches and open pull requests on {r}"
    return f"""# Generated by `myorch project github-profile {project.name}{" --read-only" if read_only else ""}` from the project pack.
# GitHub provider for project {project.name}: {mode}; clone/fetch its submodules read-only;
# nothing else. See openshell/providers/github-push.yaml for the notes
# on git's Basic auth and on updating an imported profile (resource_version).
#
#   openshell provider profile lint   -f <this file>
#   openshell provider profile import -f <this file>
#   read -s GITHUB_TOKEN && export GITHUB_TOKEN     # in your own terminal; never paste it in chat
#   openshell provider create --name {project.providers.get("github") or pid} --type {pid} --credential GITHUB_TOKEN
#   unset GITHUB_TOKEN
id: {pid}
display_name: GitHub ({project.name})
description: {"Read" if read_only else "Push and open pull requests on"} {r}; read its submodules
category: source_control
credentials:
  - name: api_token
    description: GitHub token of {project.identity.account}
    env_vars: [GITHUB_TOKEN, GH_TOKEN]
    required: true
    auth_style: bearer
    header_name: authorization
discovery:
  credentials: [api_token]
endpoints:
  - host: github.com
    port: 443
    protocol: rest
    enforcement: enforce
    rules:
{chr(10).join(git_rules)}
  - host: api.github.com
    port: 443
    protocol: rest
    enforcement: enforce
    rules:
{chr(10).join(api_rules)}
binaries: [/usr/bin/gh, /usr/local/bin/gh, /usr/bin/git, /usr/local/bin/git]
"""
