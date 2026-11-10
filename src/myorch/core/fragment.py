"""Reusable flow fragments: `use:` entries expanded at load time.

    - use: lib/review-loop.yaml
      id: review-fix
      needs: [implement]
      with: {base: "{{inputs.base}}", reviewers: 3}

Expansion happens before a flow is submitted, so the workflow only ever sees plain steps
and loops. Template phases share one syntax, told apart by namespace:

    {{preset.x}}   replaced first, from the preset chosen by an input (see below)
    {{params.x}}   replaced when a fragment is expanded
    {{anything}}   left alone, rendered at run time ({{inputs.base}}, {{loop.round}}, ...)

Presets pick a bundle of values by one input, at submit time:

    presets:
      by: depth
      quick: {reviewers: 1, security: false}
      deep:  {reviewers: 3, security: true}
    steps:
      - use: lib/review-loop.yaml
        with: {reviewers: "{{preset.reviewers}}"}

A string that is exactly "{{params.x}}" becomes the typed value (int, bool), so
`fanout: "{{params.reviewers}}"` expands to an int. A fragment step with `if:` is dropped
when the expression (over `params`) is false, and dropped ids are pruned from `needs`.

The flow's own steps, `use:` entries and inputs may have `if:` too, over `project` (the project
pack's settings): one flow serves every project, and the pack picks its shape.

    - id: takeaway
      if: "project.flows.development.takeaway"
    - use: lib/review-loop.yaml
      with: {reviewers: "{{project.flows.development.reviewers}}"}   # an int: filled in before binding

Pure: reading fragment files is the caller's job, passed in as `resolve`.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Iterable
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from myorch.core.expr import evaluate

_USE_KEYS = {"use", "id", "needs", "with", "when"}


def _patterns(ns: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    return (
        re.compile(r"^\{\{\s*" + ns + r"\.([A-Za-z0-9_]+)\s*\}\}$"),
        re.compile(r"\{\{\s*" + ns + r"\.([A-Za-z0-9_]+)\s*\}\}"),
    )


class FragmentError(ValueError):
    pass


class ParamSpec(BaseModel):
    type: Literal["string", "int", "bool"]
    required: bool = False
    default: Any = None
    min: int | None = None
    max: int | None = None
    enum: list[Any] | None = None

    @model_validator(mode="after")
    def _default_fits(self) -> ParamSpec:
        if self.required and self.default is not None:
            raise ValueError("a required param cannot have a default")
        return self

    def check(self, name: str, value: Any) -> Any:
        expected = {"string": str, "int": int, "bool": bool}[self.type]
        # bool is a subclass of int in Python; do not let True pass as an int.
        if not isinstance(value, expected) or (self.type == "int" and isinstance(value, bool)):
            raise FragmentError(f"param {name!r} must be {self.type}, got {value!r}")
        if self.min is not None and value < self.min:
            raise FragmentError(f"param {name!r} must be >= {self.min}, got {value}")
        if self.max is not None and value > self.max:
            raise FragmentError(f"param {name!r} must be <= {self.max}, got {value}")
        if self.enum is not None and value not in self.enum:
            raise FragmentError(f"param {name!r} must be one of {self.enum}, got {value!r}")
        return value


class Fragment(BaseModel):
    fragment: str
    description: str = ""
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    item: dict[str, Any]  # one step (has "id") or one loop (has "loop")

    def bind(self, given: dict[str, Any]) -> dict[str, Any]:
        unknown = set(given) - set(self.params)
        if unknown:
            raise FragmentError(f"fragment {self.fragment!r} has no params {sorted(unknown)}")
        values: dict[str, Any] = {}
        for name, spec in self.params.items():
            if name in given:
                values[name] = spec.check(name, given[name])
            elif spec.required:
                raise FragmentError(f"fragment {self.fragment!r} needs param {name!r}")
            else:
                values[name] = spec.default
        return values


def _substitute(node: Any, values: dict[str, Any], ns: str = "params") -> Any:
    whole_re, part_re = _patterns(ns)
    if isinstance(node, dict):
        return {k: _substitute(v, values, ns) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(v, values, ns) for v in node]
    if not isinstance(node, str):
        return node

    def lookup(name: str) -> Any:
        if name not in values:
            what = "fragment references undeclared param" if ns == "params" else "flow references undefined preset value"
            raise FragmentError(f"{what} {name!r}")
        return values[name]

    whole = whole_re.match(node)
    if whole:
        return lookup(whole.group(1))

    def part(m: re.Match[str]) -> str:
        v = lookup(m.group(1))
        return ("true" if v else "false") if isinstance(v, bool) else str(v)

    return part_re.sub(part, node)


def _apply_if(steps: list[dict], values: dict[str, Any]) -> list[dict]:
    ctx = {"params": values}
    kept, dropped = [], set()
    for step in steps:
        cond = step.pop("if", None)
        if cond is None or evaluate(str(cond), ctx):
            kept.append(step)
        else:
            dropped.add(step.get("id"))
    for step in kept:
        if "needs" in step:
            step["needs"] = [n for n in step["needs"] if n not in dropped]
    return kept


def expand_use(entry: dict[str, Any], resolve: Callable[[str], str]) -> dict[str, Any] | None:
    """Expand one `use:` entry into a plain step or loop (None if its `if` dropped it)."""
    extra = set(entry) - _USE_KEYS
    if extra:
        raise FragmentError(f"use entry has unsupported keys {sorted(extra)}; allowed: {sorted(_USE_KEYS)}")
    if "id" not in entry:
        raise FragmentError(f"use {entry['use']!r} needs an id")
    try:
        fragment = Fragment.model_validate(yaml.safe_load(resolve(str(entry["use"]))))
    except ValidationError as e:
        raise FragmentError(f"invalid fragment {entry['use']!r}: {e}") from e
    values = fragment.bind(dict(entry.get("with") or {}))
    item = _substitute(copy.deepcopy(fragment.item), values)

    if "loop" in item:
        item["steps"] = _apply_if(list(item.get("steps") or []), values)
        item["loop"] = entry["id"]
    else:
        kept = _apply_if([item], values)
        if not kept:
            return None
        item = kept[0]
        item["id"] = entry["id"]
    item["needs"] = list(entry.get("needs") or [])
    if "when" in entry:
        item["when"] = entry["when"]
    return item


def apply_presets(data: dict[str, Any], inputs: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve {{preset.x}} from the variant selected by `presets.by`; drop the presets block."""
    presets = data.get("presets")
    if presets is None:
        return data
    if not isinstance(presets, dict) or "by" not in presets:
        raise FragmentError("presets needs a 'by' key naming the input that selects a variant")
    by = presets["by"]
    variants = {k: v for k, v in presets.items() if k != "by"}
    choice = (inputs or {}).get(by, (data.get("defaults") or {}).get(by))
    if choice is None:
        raise FragmentError(f"presets are selected by input {by!r}, which has no value or default")
    if choice not in variants:
        raise FragmentError(f"input {by}={choice!r} is not a preset; choose one of {sorted(variants)}")
    out = {k: v for k, v in data.items() if k != "presets"}
    out["steps"] = _substitute(copy.deepcopy(data.get("steps") or []), dict(variants[choice]), ns="preset")
    return out


_PROJECT_WHOLE = re.compile(r"^\{\{\s*project\.([A-Za-z0-9_.]+)\s*\}\}$")
_PROJECT_PART = re.compile(r"\{\{\s*project\.([A-Za-z0-9_.]+)\s*\}\}")


def uses_project(data: Any) -> bool:
    if isinstance(data, dict):
        return any(uses_project(v) for v in data.values())
    if isinstance(data, list):
        return any(uses_project(v) for v in data)
    return isinstance(data, str) and _PROJECT_PART.search(data) is not None


def apply_project(data: Any, project: dict[str, Any]) -> Any:
    """Fill in {{project.a.b}} (nested). A whole-value reference keeps its type."""

    def lookup(path: str) -> Any:
        cur: Any = project
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                raise FragmentError(f"flow references project.{path}, which the project does not define")
            cur = cur[part]
        return cur

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if not isinstance(node, str):
            return node
        whole = _PROJECT_WHOLE.match(node)
        if whole:
            return lookup(whole.group(1))

        def part(m: re.Match[str]) -> str:
            v = lookup(m.group(1))
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, list):
                return " ".join(map(str, v))
            return "" if v is None else str(v)

        return _PROJECT_PART.sub(part, node)

    return walk(data)


def _project_if(cond: Any, project: dict[str, Any] | None, what: str) -> bool:
    if project is None:
        raise FragmentError(f"{what} has `if: {cond}`, which depends on the project: run it with --project <name>")
    try:
        return bool(evaluate(str(cond), {"project": project}))
    except ValueError as e:
        raise FragmentError(f"{what}: bad `if: {cond}`: {e}") from e


def resolve_inputs(declared: dict[str, Any] | None, project: dict[str, Any] | None) -> dict[str, Any]:
    """A flow's input specs as one project sees them. An input with `if: <expr over project>` that is
    false for the project becomes `hidden`: forms leave it out, but it is still declared, so its
    default keeps {{inputs.x}} in prompts valid. `required` may be such an expression too.
    {{project.x}} in specs is filled in (a ticket's prefix). Without a project, conditional inputs
    are hidden, conditionally required ones are optional, and specs stay as written."""
    out: dict[str, Any] = {}
    for name, raw in (declared or {}).items():
        if not isinstance(raw, dict) or not ("if" in raw or isinstance(raw.get("required"), str)):
            out[name] = raw
            continue
        s = {k: v for k, v in raw.items() if k != "if"}
        if isinstance(s.get("required"), str):  # `required: "not project.jira.url"`: required for some projects
            s["required"] = project is not None and _project_if(s["required"], project, f"input {name!r} required")
        shown = "if" not in raw or (project is not None and _project_if(raw["if"], project, f"input {name!r}"))
        out[name] = s if shown else {"type": "hidden", **({"default": s["default"]} if "default" in s else {})}
    if project is not None:
        out = apply_project(out, project)
    return out


def apply_project_if(data: dict[str, Any], project: dict[str, Any] | None) -> dict[str, Any]:
    """Shape the flow for the project, before fragments are expanded: drop the steps (and `use:`
    entries) whose `if:` is false for the project, fill {{project.x}} into `with:` (so a project value
    can be an int or bool param), and resolve the inputs (resolve_inputs).

    A dropped step's dependents take over its `needs`, so a chain keeps its order: with
    takeaway-commit dropped, risk-rules (needs: [takeaway-commit]) waits for what takeaway-commit
    waited for, instead of starting at once."""
    steps = data.get("steps") or []
    conditional = any(isinstance(s, dict) and "if" in s for s in steps)
    has_with = any(isinstance(s, dict) and "use" in s and s.get("with") for s in steps)
    conditional_inputs = any(isinstance(v, dict) and ("if" in v or isinstance(v.get("required"), str))
                             for v in (data.get("inputs") or {}).values())
    if not conditional and not has_with and not conditional_inputs:
        return data
    out = dict(data)
    if "inputs" in data:
        out["inputs"] = resolve_inputs(data["inputs"], project)
    kept, dropped = [], {}
    for s in steps:
        s = dict(s) if isinstance(s, dict) else s
        if isinstance(s, dict) and "if" in s:
            sid = s.get("id") or s.get("loop")
            if not _project_if(s.pop("if"), project, f"step {sid!r}"):
                dropped[sid] = list(s.get("needs") or [])
                continue
        if isinstance(s, dict) and "use" in s and s.get("with") and project is not None:
            s["with"] = apply_project(s["with"], project)
        kept.append(s)

    def inherit(needs: list[str], seen: frozenset[str] = frozenset()) -> list[str]:
        res: list[str] = []
        for n in needs:
            if n in dropped and n not in seen:
                res += inherit(dropped[n], seen | {n})
            elif n not in dropped:
                res.append(n)
        return list(dict.fromkeys(res))

    for s in kept:
        if isinstance(s, dict) and s.get("needs"):
            s["needs"] = inherit(list(s["needs"]))
    out["steps"] = kept
    return out


def expand_flow(
    data: dict[str, Any], resolve: Callable[[str], str] | None, inputs: dict[str, Any] | None = None,
    project: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a copy of the flow document with presets, project conditions, fragments and project
    values resolved."""
    data = _expand_fragments(apply_project_if(apply_presets(data, inputs), project), resolve)
    if project is not None:
        data = apply_project(data, project)
    elif uses_project(data):
        raise FragmentError("this flow uses {{project.x}}: run it with --project <name>")
    return apply_rules(data, ablated(project))


# ---- rules: prompt text that makes up for something a model got wrong --------------------------
# A rule in a prompt is a block between two marker lines:
#     [[rule:entry-points/develop]]
#     ...the text...
#     [[/rule]]
# Expansion drops the marker lines, and drops the whole block when the rule is ablated: the project's
# flows.ablate lists rule ids, or a group (`entry-points` turns off entry-points/develop,
# entry-points/review, ...). Ablating is for evals: replay a case with and without the rule, to see
# whether today's model still needs it (flows/rules/registry.yaml says why each rule is there).

RULE_ID = r"[a-z0-9][a-z0-9-]*(?:/[a-z0-9][a-z0-9-]*)?"
_RULE_OPEN = re.compile(rf"\[\[rule:({RULE_ID})\]\]")
_RULE_CLOSE = "[[/rule]]"


def ablated(project: dict[str, Any] | None) -> list[str]:
    return list(((project or {}).get("flows") or {}).get("ablate") or [])


def rule_off(rule: str, off: Iterable[str]) -> bool:
    return any(rule == o or rule.startswith(o + "/") for o in off)


def registry_ids(doc: Any) -> set[str]:
    """<id>/<place> for every rule of flows/rules/registry.yaml."""
    rules = (doc or {}).get("rules") if isinstance(doc, dict) else None
    return {f"{r['id']}/{place}" for r in rules or [] if isinstance(r, dict) and r.get("id")
            for place in r.get("places") or []}


def rule_ids(text: str) -> list[str]:
    """The rule ids marked in a text (a flow or fragment file)."""
    return [m.group(1) for line in text.splitlines() if (m := _RULE_OPEN.fullmatch(line.strip()))]


def strip_rules(text: str, off: Iterable[str] = ()) -> str:
    """The text without rule markers, and without the blocks of ablated rules."""
    if "[[" not in text:
        return text
    off = list(off)
    out: list[str] = []
    current: str | None = None
    for line in text.splitlines(keepends=True):
        s = line.strip()
        m = _RULE_OPEN.fullmatch(s)
        if m:
            if current is not None:
                raise FragmentError(f"rule {m.group(1)!r} starts inside rule {current!r}: rules do not nest")
            current = m.group(1)
            continue
        if s == _RULE_CLOSE:
            if current is None:
                raise FragmentError("[[/rule]] without a [[rule:...]] before it")
            current = None
            continue
        if s.startswith("[[rule") or s.startswith("[[/rule"):
            raise FragmentError(f"bad rule marker {s!r}: [[rule:<id>]] and [[/rule]], each on its own line")
        if current is None or not rule_off(current, off):
            out.append(line)
    if current is not None:
        raise FragmentError(f"rule {current!r} has no [[/rule]]")
    return "".join(out)


def apply_rules(data: Any, off: Iterable[str] = ()) -> Any:
    off = list(off)

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return strip_rules(node, off) if isinstance(node, str) else node

    return walk(data)


def _expand_fragments(data: dict[str, Any], resolve: Callable[[str], str] | None) -> dict[str, Any]:
    steps = data.get("steps") or []
    if not any(isinstance(s, dict) and "use" in s for s in steps):
        return data
    if resolve is None:
        raise FragmentError("this flow uses fragments (use:), load it from its file so they can be found")
    out = dict(data)
    expanded, dropped = [], set()
    for s in steps:
        if isinstance(s, dict) and "use" in s:
            item = expand_use(s, resolve)
            if item is None:
                dropped.add(s["id"])
                continue
            expanded.append(item)
        else:
            expanded.append(s)
    for s in expanded:
        if isinstance(s, dict) and "needs" in s:
            s["needs"] = [n for n in s["needs"] if n not in dropped]
    out["steps"] = expanded
    return out


def dump_flow(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000)
