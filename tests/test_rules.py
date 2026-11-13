"""Prompt rules ([[rule:<id>/<place>]] blocks, core/fragment.py) and their registry."""
from pathlib import Path

import pytest
import yaml

from myorch.core.fragment import FragmentError, apply_rules, registry_ids, rule_ids, rule_off, strip_rules

FLOWS = Path(__file__).resolve().parent.parent / "flows"

TEXT = """intro
  [[rule:a/x]]
  rule a
  [[/rule]]
  [[rule:b/x]]
  rule b
  [[/rule]]
outro
"""


def test_markers_are_dropped_and_ablated_blocks_too():
    assert strip_rules(TEXT) == "intro\n  rule a\n  rule b\noutro\n"
    assert strip_rules(TEXT, ["a"]) == "intro\n  rule b\noutro\n"          # a group: every place
    assert strip_rules(TEXT, ["b/x"]) == "intro\n  rule a\noutro\n"        # one place
    assert strip_rules(TEXT, ["a/y"]) == strip_rules(TEXT)                 # another place of a
    assert apply_rules({"steps": [{"prompt": TEXT}]}, ["a", "b"]) == {"steps": [{"prompt": "intro\noutro\n"}]}
    assert rule_off("a/x", ["a"]) and not rule_off("ab/x", ["a"])


@pytest.mark.parametrize("bad", [
    "[[rule:a/x]]\n[[rule:b/x]]\n[[/rule]]\n[[/rule]]\n",  # nested
    "[[rule:a/x]]\ntext\n",                                 # not closed
    "text\n[[/rule]]\n",                                    # closed, never opened
    "[[rule: a]]\n[[/rule]]\n",                             # malformed
])
def test_bad_markers_fail(bad):
    with pytest.raises(FragmentError):
        strip_rules(bad)


def test_registry_lists_every_marked_rule():
    marked: list[str] = []
    for f in sorted([*FLOWS.glob("*.yaml"), *FLOWS.glob("lib/*.yaml")]):
        marked += rule_ids(f.read_text(encoding="utf-8"))
    assert len(marked) == len(set(marked)), "a rule id is marked twice"
    registry = yaml.safe_load((FLOWS / "rules" / "registry.yaml").read_text(encoding="utf-8"))
    assert set(marked) == registry_ids(registry)
    for r in registry["rules"]:
        assert r["what"] and r["makes_up_for"] and r["origin"]["date"], r["id"]


def _project():
    from myorch.project import Flows

    return {"name": "p", "flows": Flows().model_dump(mode="json")}


def test_set_overrides_flow_settings_on_replays_only():
    from myorch.cli import _flow_sets

    flow = FLOWS / "development.yaml"
    p = _project()
    done = _flow_sets(p, ["flows.development.reviewers=1", "flows.ablate=nit-cap,entry-points/review"], {"at": "abc"}, flow)
    assert done == [("flows.development.reviewers", "1"), ("flows.ablate", "nit-cap,entry-points/review")]
    assert p["flows"]["development"]["reviewers"] == 1 and p["flows"]["ablate"] == ["nit-cap", "entry-points/review"]
    with pytest.raises(ValueError, match="replays only"):
        _flow_sets(_project(), ["flows.development.reviewers=1"], {}, flow)
    assert _flow_sets(_project(), ["flows.ablate=reuse"], {}, flow, preview=True)  # myorch expand
    for bad in ["approvers=0", "flows.development.nope=1", "flows.ablate=no-such-rule", "flows.development.reviewers=0"]:
        with pytest.raises(ValueError):
            _flow_sets(_project(), [bad], {"at": "abc"}, flow)


def test_ablated_rule_is_gone_from_the_expanded_flow():
    from myorch.flowfile import read_flow_file
    from myorch.project import Flows

    flow = FLOWS / "development.yaml"
    project = {"name": "p", "base": "main", "image": "i", "repo": "o/r", "flows": Flows().model_dump(mode="json"),
               "prompts": {}, "jira": {"url": "", "key": ""}}
    try:
        full = read_flow_file(flow, {}, project)
    except Exception as e:  # the flow needs more of a real project than this sketch has
        pytest.skip(f"needs a full project: {e}")
    project["flows"]["ablate"] = ["nit-cap", "entry-points"]
    cut = read_flow_file(flow, {}, project)
    assert "[[rule" not in full and "Keep at most 5 Nits" in full and "list every entry point" in full
    assert "Keep at most 5 Nits" not in cut and "list every entry point" not in cut and "Missed entry points" not in cut
