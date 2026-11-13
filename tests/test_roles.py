"""Flows are told apart by role, not by name (core.flow.role_of, flowfile.find_flow)."""

from __future__ import annotations

from pathlib import Path

import pytest

from myorch.core.flow import load_flow, role_of
from myorch.flowfile import find_flow, flow_meta


def test_the_role_comes_from_the_declaration_or_the_name():
    assert role_of("development") == "development"  # old runs
    assert (role_of("ck-development"), role_of("blog-review"), role_of("ck-eval")) == ("development", "review", "eval")
    assert role_of("my-thing") is None and role_of("ship", "development") == "development"


def test_a_flow_for_one_project_refuses_another():
    f = load_flow("flow: ck-development\nrole: development\nprojects: [mdm]\nsteps:\n  - {id: a, harness: script, profile: p}\n")
    assert f.kind == "development"
    f.check_project("mdm")
    with pytest.raises(ValueError, match="only for projects mdm"):
        f.check_project("blog")


def test_the_projects_own_flow_is_found_by_role(tmp_path: Path):
    for name, extra in (("ck-eval", "projects: [mdm]\n"), ("demo-eval", "projects: [demo]\n"), ("eval", "")):
        (tmp_path / f"{name}.yaml").write_text(f"flow: {name}\nrole: eval\n{extra}steps: []\n")
    assert find_flow(tmp_path, "eval", "mdm").name == "ck-eval.yaml"
    assert find_flow(tmp_path, "eval", "blog").name == "eval.yaml"  # nothing of its own: the general one
    assert flow_meta(tmp_path / "demo-eval.yaml") == {"flow": "demo-eval", "role": "eval", "projects": ["demo"], "file": "demo-eval.yaml"}
    with pytest.raises(ValueError, match="No .* flow for project"):
        find_flow(tmp_path, "sweep", "mdm")
