"""Continuing a finished development run on its branch (development's from_run): the branch name
stays, and the CLI / relay check the previous run and copy what the run input's `carry` lists
(cli._continue_from)."""

from __future__ import annotations

import pytest

from myorch.cli import _continue_from
from myorch.core.run import RunState
from myorch.storage.events import EventLog

FLOW = """
flow: development
inputs: {task: string, ticket: string, from_run: string}
defaults: {ticket: "", from_run: ""}
steps:
  - {id: a, harness: script, profile: p}
"""
DECLARED = {"task": {"type": "message"}, "ticket": "string",
            "from_run": {"type": "run", "role": "development", "carry": ["ticket"]}}
MDM = {"name": "mdm", "branch": "MD-{{inputs.ticket}}-myorch-{{run.id}}"}


def test_a_continuing_run_works_on_the_previous_runs_branch():
    new = RunState.create("development-new", FLOW, {"task": "t", "ticket": "18700"}, project=MDM)
    assert new.branch() == "MD-18700-myorch-development-new"
    cont = RunState.create("development-2", FLOW, {"task": "t", "ticket": "18700", "from_run": "development-1"}, project=MDM)
    assert cont.branch() == "MD-18700-myorch-development-1"  # same branch, so the same PR


def log_with(tmp_path, *events):
    log = EventLog(tmp_path / "events.db")
    log.append("development-1", [(f"k{i}", kind, "", data) for i, (kind, data) in enumerate(events)])
    return log


STARTED = ("run_started", {"flow": "development", "project": "mdm", "inputs": {"task": "x", "ticket": "18700"}})


def test_the_previous_ticket_is_copied(tmp_path):
    log = log_with(tmp_path, STARTED, ("run_finished", {"status": "succeeded"}))
    inputs = {"task": "fix it", "from_run": "development-1"}
    _continue_from(log, inputs, "mdm", DECLARED)
    assert inputs["ticket"] == "18700"
    inputs = {"task": "fix it", "from_run": "development-1", "ticket": "18701"}
    _continue_from(log, inputs, "mdm", DECLARED)
    assert inputs["ticket"] == "18701"  # a given ticket wins


@pytest.mark.parametrize("events,project,message", [
    ([], "mdm", "No such run"),
    ([STARTED], "mdm", "has not finished yet"),
    ([STARTED, ("run_finished", {}), ("run_resumed", {})], "mdm", "has not finished yet"),
    ([STARTED, ("run_finished", {})], "blog", "belongs to project mdm"),
    ([("run_started", {"flow": "review", "project": "mdm"}), ("run_finished", {})], "mdm", "is not a development run"),
])
def test_only_a_finished_development_run_of_the_project_can_be_continued(tmp_path, events, project, message):
    log = log_with(tmp_path, *events) if events else EventLog(tmp_path / "events.db")
    with pytest.raises(ValueError, match=message):
        _continue_from(log, {"task": "t", "from_run": "development-1"}, project, DECLARED)


def test_nothing_is_carried_unless_the_spec_says_so(tmp_path):
    log = log_with(tmp_path, STARTED, ("run_finished", {"status": "succeeded"}))
    inputs = {"from_run": "development-1"}
    _continue_from(log, inputs, "mdm", {"from_run": {"type": "run", "role": "development"}})
    assert "ticket" not in inputs
