"""Read flow files from disk, expanding `use:` fragments relative to the flow's directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from myorch.core.flow import Flow, role_of
from myorch.core.fragment import FragmentError, dump_flow, expand_flow, resolve_inputs
from myorch.project import with_defaults


def read_flow_file(path: Path, inputs: dict[str, Any] | None = None, project: dict[str, Any] | None = None) -> str:
    """Return the YAML to submit: the file as-is, or the expanded document if it uses fragments.

    Fragments are resolved relative to the flow file and must stay inside its directory.
    `inputs` select presets (`presets.by`), so they are needed when the flow declares presets.
    `project` (Project.public()) fills in {{project.x}}.
    """
    path = path.resolve()
    root = path.parent
    text = path.read_text()
    data = yaml.safe_load(text)

    def resolve(rel: str) -> str:
        target = (root / rel).resolve()
        if root not in target.parents:
            raise FragmentError(f"fragment {rel!r} is outside {root}")
        if not target.is_file():
            raise FragmentError(f"fragment {rel!r} not found at {target}")
        return target.read_text()

    expanded = expand_flow(data, resolve, inputs, with_defaults(project))
    Flow.model_validate(expanded)  # fail before submitting
    return text if expanded is data else dump_flow(expanded)


def flow_inputs(path: Path, project: dict[str, Any] | None = None) -> dict[str, Any]:
    """The flow's input specs as the project (Project.public()) sees them: inputs that do not apply to
    it are hidden, {{project.x}} filled in (core/fragment.py resolve_inputs)."""
    doc = yaml.safe_load(path.read_text()) or {}
    return resolve_inputs(doc.get("inputs") or {}, with_defaults(project))


def flow_meta(path: Path) -> dict[str, Any]:
    """A flow file's name, role and projects, from its top level (no fragment expansion)."""
    doc = yaml.safe_load(path.read_text()) or {}
    name = str(doc.get("flow") or path.stem)
    return {"flow": name, "role": role_of(name, doc.get("role")), "projects": list(doc.get("projects") or []),
            "file": path.name}


def find_flow(flows_dir: Path, role: str, project: str | None) -> Path:
    """The flow file with this role for the project: one listing the project, else one for any
    project, else one for any project. Flows are shared (eval.yaml serves every project); a flow that
    lists its projects is for a project that needs its own."""
    general = None
    for p in sorted(flows_dir.glob("*.yaml")):
        m = flow_meta(p)
        if m["role"] != role:
            continue
        if project and project in m["projects"]:
            return p
        if not m["projects"] and general is None:
            general = p
    if general is None:
        raise ValueError(f"没有给项目 {project or '（无）'} 用的 {role} 流程")
    return general
