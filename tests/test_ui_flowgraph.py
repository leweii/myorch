"""The console's flow definitions (ui/flowgraph.py): same expansion as `myorch run`, live version."""

from __future__ import annotations

import os
from pathlib import Path

from myorch.ui.flowgraph import FlowDefs

FRAGMENT = """\
# Review rounds.
fragment: rounds
params:
  reviewers: {type: int, default: 1}
item:
  loop: placeholder
  until: "steps.check.outputs.ok"
  max_rounds: 2
  steps:
    - id: check
      harness: script
      profile: read-only
      prompt: "true"
      fanout: "{{params.reviewers}}"
"""

FLOW = """\
# Demo flow for the console.
flow: demo
inputs: {depth: string}
defaults: {depth: quick}
presets:
  by: depth
  quick: {reviewers: 1}
  deep: {reviewers: 3}
steps:
  - id: prepare
    harness: script
    profile: read-only
    prompt: "true"
  - use: lib/rounds.yaml
    id: review
    needs: [prepare]
    with: {reviewers: "{{preset.reviewers}}"}
"""


def make(tmp_path: Path) -> FlowDefs:
    (tmp_path / "flows" / "lib").mkdir(parents=True)
    (tmp_path / "flows" / "lib" / "rounds.yaml").write_text(FRAGMENT)
    (tmp_path / "flows" / "demo.yaml").write_text(FLOW)
    return FlowDefs(tmp_path, tmp_path / "kb")


def test_graph_expands_presets_and_fragments(tmp_path: Path) -> None:
    defs = make(tmp_path)
    g = defs.graph("demo", None, {})
    assert "error" not in g
    prepare, review = g["tree"]
    assert (prepare["id"], prepare["stage"], prepare["exec"]) == ("prepare", 0, "script")
    assert (review["kind"], review["id"], review["stage"], review["use"]) == ("loop", "review", 1, "lib/rounds.yaml")
    assert review["steps"][0]["fanout"] == 1
    assert defs.graph("demo", None, {"depth": "deep"})["tree"][1]["steps"][0]["fanout"] == 3


def test_overview_links_flows_and_fragments(tmp_path: Path) -> None:
    o = make(tmp_path).overview()
    (flow,) = o["flows"]
    assert flow["description"] == "Demo flow for the console."
    assert (flow["role"], flow["label"], flow["projects"]) == (None, "demo", [])  # the list groups by project, shows the role
    assert (flow["presets_by"], flow["presets"], flow["uses"]) == ("depth", ["quick", "deep"], ["lib/rounds.yaml"])
    (frag,) = o["fragments"]
    assert (frag["name"], frag["kind"], frag["used_by"], frag["params"]) == ("rounds", "loop", ["demo"], ["reviewers"])


def test_version_changes_when_a_file_changes(tmp_path: Path) -> None:
    defs = make(tmp_path)
    v1 = defs.version()
    assert defs.version() == v1
    p = tmp_path / "flows" / "lib" / "rounds.yaml"
    p.write_text(FRAGMENT.replace("max_rounds: 2", "max_rounds: 4"))
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))  # coarse clocks: force a new mtime
    assert defs.version() != v1
    assert defs.graph("demo", None, {})["tree"][1]["max_rounds"] == 4


def test_errors_are_reported_not_raised(tmp_path: Path) -> None:
    defs = make(tmp_path)
    assert "没有这个流程" in defs.graph("../etc", None, {})["error"]
    assert "preset" in defs.graph("demo", None, {"depth": "huge"})["error"]
    (tmp_path / "flows" / "demo.yaml").write_text(FLOW.replace("lib/rounds.yaml", "lib/missing.yaml"))
    assert "missing" in defs.graph("demo", None, {})["error"]
    (tmp_path / "flows" / "demo.yaml").write_text(FLOW + "  - {id: x, needs: [nope], harness: script, profile: read-only}\n")
    assert defs.graph("demo", None, {})["error"]


def test_overview_has_role_label_and_projects_for_the_list(tmp_path: Path) -> None:
    defs = make(tmp_path)
    (tmp_path / "flows" / "x-review.yaml").write_text("# Review.\nflow: x-review\nrole: review\nprojects: [x]\nsteps: []\n")
    flow = next(f for f in defs.overview()["flows"] if f["flow"] == "x-review")
    assert (flow["role"], flow["label"], flow["projects"], flow["order"]) == ("review", "评审 PR", ["x"], 1)
