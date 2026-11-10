"""Flow input specs (core/inputs.py): the rules the CLI, the relay and the console share."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from myorch.core import inputs as fi
from myorch.core.flow import ROLES

FLOWS = Path(__file__).resolve().parent.parent / "flows"


def test_every_role_has_a_label_and_a_hint():
    assert set(fi.ROLE_INFO) == set(ROLES)
    assert all(v["label"] and v["hint"] for v in fi.ROLE_INFO.values())


def test_a_flow_may_override_the_roles_label():
    v = fi.role_view("development", {"flow": "x", "label": "改代码"})
    assert v["label"] == "改代码" and v["hint"] == fi.ROLE_INFO["development"]["hint"] and v["default"] and v["order"] == 0
    assert fi.role_view(None, {"flow": "odd"})["label"] == "odd"


def test_the_message_fills_the_message_input_by_its_spec():
    first, whole = {"task": {"type": "message", "from_message": "first_line"}}, {"focus": {"type": "message"}}
    a, b = {}, {}
    fi.fill_from_message(first, a, "第一行\n第二行")
    fi.fill_from_message(whole, b, "第一行\n第二行")
    assert a == {"task": "第一行"} and b == {"focus": "第一行\n第二行"}
    given = {"task": "已给"}
    fi.fill_from_message(first, given, "别的")
    assert given == {"task": "已给"}
    plain = {}
    fi.fill_from_message({"task": "string"}, plain, "x")  # no message input: names mean nothing
    assert plain == {}


def test_required_inputs_and_one_of_groups():
    declared = {"pr": {"type": "string", "label": "PR 编号", "required": True}, "commit": "string", "bad": "string"}
    with pytest.raises(ValueError, match="PR 编号"):
        fi.check(declared, {})
    fi.check(declared, {"pr": "25"})
    with pytest.raises(ValueError, match="commit 或 bad"):
        fi.check(declared, {"pr": "25"}, [["commit", "bad"]])
    fi.check(declared, {"pr": "25", "bad": "abc"}, [["commit", "bad"]])


def test_a_ticket_keeps_only_its_number():
    inputs = {"ticket": "md-18700"}
    fi.normalize({"ticket": {"type": "ticket", "prefix": "MD-"}}, inputs)
    assert inputs == {"ticket": "18700"}


@pytest.mark.parametrize("path", sorted(FLOWS.glob("*.yaml")), ids=lambda p: p.name)
def test_the_flow_files_use_only_known_spec_types(path):
    doc = yaml.safe_load(path.read_text()) or {}
    types = {fi.spec(v).get("type") for v in (doc.get("inputs") or {}).values()}
    assert types <= {"string", "text", "select", "ticket", "run", "message", "hidden"}
    assert sum(t == "message" for t in (fi.spec(v).get("type") for v in (doc.get("inputs") or {}).values())) <= 1
    for group in doc.get("require_one_of") or []:
        assert set(group) <= set(doc.get("inputs") or {})
