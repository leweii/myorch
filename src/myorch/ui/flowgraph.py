"""Flow definitions for the console, read live from flows/.

The graph uses the same expansion as `myorch run` (presets, `use:` fragments, project values,
then Flow validation), so the console shows what a start with these inputs would run. Files are
read on every call; `version()` changes when any flow or fragment file changes, and the page
polls it to redraw after an edit. The tree has the shape of ConsoleService.describe() without
instances, so the page draws both with the same CSS.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from myorch.core.flow import Flow, Loop, Step
from myorch.core.fragment import FragmentError, expand_flow, uses_project
from myorch.core.inputs import role_view
from myorch.flowfile import flow_meta

FLOW_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def stages(items: list[tuple[str, list[str]]]) -> dict[str, int]:
    from myorch.ui.service import stages as run_stages  # one definition of execution order

    return run_stages(items)


def head_comment(text: str) -> str:
    """The comment block at the top of a YAML file, without the `#` marks."""
    out: list[str] = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        out.append(line[1:].removeprefix(" ").rstrip())
    return "\n".join(out).strip()


def _uses(node: Any) -> list[str]:
    """Fragment files referenced by `use:` entries anywhere in a document, in order."""
    found: list[str] = []
    if isinstance(node, dict):
        if isinstance(node.get("use"), str):
            found.append(node["use"])
        for v in node.values():
            found += _uses(v)
    elif isinstance(node, list):
        for v in node:
            found += _uses(v)
    return list(dict.fromkeys(found))


def _use_ids(node: Any) -> dict[str, str]:
    """id -> fragment file, for the items a `use:` entry produced."""
    out: dict[str, str] = {}
    if isinstance(node, dict):
        if isinstance(node.get("use"), str) and isinstance(node.get("id"), str):
            out[node["id"]] = node["use"]
        for v in node.values():
            out.update(_use_ids(v))
    elif isinstance(node, list):
        for v in node:
            out.update(_use_ids(v))
    return out


def exec_kind(step: Step) -> str:
    if step.action:
        return "host"
    if step.harness is None:
        return "approval"
    return "script" if step.harness in ("script", "shell") else "agent"


def step_view(step: Step, flow: Flow) -> dict[str, Any]:
    kind = exec_kind(step)
    a = step.approval
    return {
        "id": step.id, "exec": kind, "harness": step.harness, "action": step.action, "profile": step.profile,
        "model": (step.model or flow.model) if kind == "agent" else None, "image": step.image,
        "when": step.when, "needs": step.needs, "fanout": step.fanout, "interaction": step.interaction,
        "outputs": list(step.outputs), "on_failed": step.on_failed or flow.on_failed,
        "retries": step.limits.retries, "timeout": step.limits.timeout,
        "approval": {"count": a.count, "when": a.when, "require_initiator": a.require_initiator,
                     "changes": a.changes.loop if a.changes else None} if a else None,
    }


class FlowDefs:
    def __init__(self, config: Path, kb_root: Path) -> None:
        self.root = config / "flows"
        self.kb_root = kb_root

    def _files(self) -> list[Path]:
        return sorted([*self.root.glob("*.yaml"), *self.root.glob("lib/*.yaml")])

    def version(self) -> str:
        h = hashlib.sha1()
        for p in self._files():
            try:
                s = p.stat()
            except FileNotFoundError:  # deleted between glob and stat
                continue
            h.update(f"{p.relative_to(self.root)}:{s.st_mtime_ns}:{s.st_size};".encode())
        return h.hexdigest()[:12]

    def overview(self) -> dict[str, Any]:
        flows, frags = [], []
        used_by: dict[str, list[str]] = {}
        for p in sorted(self.root.glob("*.yaml")):
            text = p.read_text()
            item: dict[str, Any] = {"flow": p.stem, "file": f"flows/{p.name}", "description": head_comment(text)}
            try:
                doc = yaml.safe_load(text) or {}
                presets = doc.get("presets") or {}
                meta = flow_meta(p)  # the list groups by project and shows the role
                item.update({
                    "flow": meta["flow"], "role": meta["role"], "projects": meta["projects"],
                    **{k: v for k, v in role_view(meta["role"], doc).items() if k in ("label", "order")},
                    "inputs": doc.get("inputs") or {}, "defaults": doc.get("defaults") or {},
                    "presets_by": presets.get("by") if isinstance(presets, dict) else None,
                    "presets": [k for k in presets if k != "by"] if isinstance(presets, dict) else [],
                    "uses_project": uses_project(doc), "uses": _uses(doc),
                    "doc": f"flows/{p.stem}.md" if (self.root / f"{p.stem}.md").is_file() else None,
                })
            except yaml.YAMLError as e:
                item["error"] = f"YAML 解析失败：{e}"
            for u in item.get("uses", []):
                used_by.setdefault(u, []).append(item["flow"])
            flows.append(item)
        for p in sorted(self.root.glob("lib/*.yaml")):
            rel = f"lib/{p.name}"
            text = p.read_text()
            item = {"name": p.stem, "file": f"flows/{rel}", "description": head_comment(text).split("\n\n")[0],
                    "used_by": used_by.get(rel, [])}
            try:
                doc = yaml.safe_load(text) or {}
                body = doc.get("item") or {}
                if isinstance(doc.get("description"), str) and doc["description"].strip():
                    item["description"] = doc["description"].strip()
                item["kind"] = "loop" if isinstance(body, dict) and "loop" in body else "step"
                item["params"] = list((doc.get("params") or {}).keys())
                item["uses"] = _uses(body)
            except yaml.YAMLError as e:
                item["error"] = f"YAML 解析失败：{e}"
            frags.append(item)
        return {"version": self.version(), "flows": flows, "fragments": frags}

    def graph(self, flow: str, project: str | None, inputs: dict[str, str]) -> dict[str, Any]:
        """The expanded flow as a tree; `error` instead when it does not load with these values."""
        out: dict[str, Any] = {"version": self.version(), "flow": flow, "project": project or None, "inputs": inputs}
        if not FLOW_NAME.match(flow) or not (self.root / f"{flow}.yaml").is_file():
            return {**out, "error": f"没有这个流程：{flow}"}
        path = (self.root / f"{flow}.yaml").resolve()
        root = path.parent

        def resolve(rel: str) -> str:
            target = (root / rel).resolve()
            if root not in target.parents:
                raise FragmentError(f"fragment {rel!r} is outside {root}")
            if not target.is_file():
                raise FragmentError(f"片段 {rel} 不存在")
            return target.read_text()

        try:
            data = yaml.safe_load(path.read_text()) or {}
            pub = None
            if project:
                from myorch import project as projects

                pub = projects.load_project(self.kb_root, project).public()
            elif uses_project(data):
                return {**out, "error": "这个流程用到项目参数，先选一个项目。", "needs_project": True}
            values = {**(data.get("defaults") or {}), **{k: v for k, v in inputs.items() if v != ""}}
            f = Flow.model_validate(expand_flow(data, resolve, values, pub))
        except (yaml.YAMLError, FragmentError, ValidationError, ValueError, FileNotFoundError) as e:
            return {**out, "error": str(e)}
        from_use = _use_ids(data)
        tree: list[dict[str, Any]] = []
        for item in f.steps:
            if isinstance(item, Loop):
                tree.append({
                    "kind": "loop", "id": item.id, "needs": item.needs, "when": item.when, "max_rounds": item.max_rounds,
                    "until": item.until, "on_exhausted": item.on_exhausted, "use": from_use.get(item.id),
                    "steps": [step_view(s, f) for s in item.steps],
                    "reflect": step_view(item.reflect, f) if item.reflect else None,
                    "stages": stages([(s.id, s.needs) for s in item.steps]),
                })
            else:
                tree.append({"kind": "step", "use": from_use.get(item.id), **step_view(item, f)})
        top = stages([(t["id"], t["needs"]) for t in tree])
        for t in tree:
            t["stage"] = top[t["id"]]
        return {**out, "tree": tree, "values": values}
