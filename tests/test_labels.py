"""Run labels: every run is found by its Jira ticket (core/inputs.py, myorch/runs.py)."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from myorch import cli
from myorch.core import inputs as fi
from myorch.core.events import derive_events
from myorch.core.run import Mandate, RunState
from myorch.runs import RunRecord, inherited_labels, label_index, run_labels
from myorch.storage.events import EventLog

DECLARED = {"ticket": {"type": "ticket", "prefix": "MD-"}, "task": {"type": "message"},
            "from_run": {"type": "run"}, "mode": {"type": "select", "options": ["MD-1"]}}


def test_a_ticket_input_becomes_a_label_with_its_prefix():
    assert fi.input_labels(DECLARED, {"ticket": "18700"}) == ["MD-18700"]
    assert fi.input_labels(DECLARED, {"ticket": "md-18700"}) == ["MD-18700"]  # not normalized yet
    assert fi.input_labels({"t": {"type": "ticket"}}, {"t": "abc"}) == ["abc"]  # no prefix: as given


def test_jira_keys_in_plain_strings_count_but_not_free_text_or_run_ids():
    labels = fi.input_labels(DECLARED, {"tickets": "MD-1,MD-2", "task": "像 MD-9 那样改",
                                        "mode": "MD-1", "run": "ck-development-1f29e33a",
                                        "branch": "MD-18700-myorch-fix", "hash": "SHA-256 and UTF-8"})
    assert labels == ["MD-1", "MD-2", "MD-18700"]
    assert fi.jira_keys("xMD-1 MD-2b md-3 (MD-4)") == ["MD-4"]


def test_start_labels_put_the_callers_first_dedupe_and_cap():
    assert fi.start_labels(DECLARED, {"ticket": "18700"}, [" md-18700 ", "eval", "", "EVAL"]) == ["MD-18700", "eval"]
    many = fi.clean_labels([f"X-{i}" for i in range(100)] + ["y" * 200])
    assert len(many) == fi.MAX_LABELS and all(len(x) <= fi.MAX_LABEL_CHARS for x in many)


def test_a_search_matches_a_label_a_bare_number_or_part_of_the_run_id():
    m = fi.label_matches
    assert m("md-18700", "eval-1", ["MD-18700"]) and m("18700", "eval-1", ["MD-18700"])
    assert m("1f29e33a", "ck-development-1f29e33a", []) and m("", "x", [])
    assert not m("MD-1870", "eval-1", ["MD-18700"]) and not m("870", "eval-1", ["MD-18700"])


def flows(name: str, project: str | None = None) -> dict:
    return DECLARED if name == "ck-development" else {}


def test_old_runs_get_labels_from_their_inputs_and_from_the_run_they_name():
    started = {
        "ck-development-1f29e33a": {"flow": "ck-development", "inputs": {"ticket": "18700"}},  # before labels
        "ck-development-2b": {"flow": "ck-development", "inputs": {"ticket": "18800", "from_run": "ck-development-1f29e33a"}},
        "eval-hidden-tests-aa": {"flow": "eval-hidden-tests", "inputs": {"run": "ck-development-2b", "base": "abc"}},
        "loop-a-11111111": {"flow": "x", "inputs": {"of": "loop-b-22222222"}, "labels": ["A"]},
        "loop-b-22222222": {"flow": "x", "inputs": {"of": "loop-a-11111111"}, "labels": ["B"]},
        "ck-learn-cc": {"flow": "ck-learn", "inputs": {"runs": "ck-development-1f29e33a,ck-development-2b"}},
    }
    idx = label_index(started, flows)
    assert idx["ck-development-1f29e33a"] == ["MD-18700"]
    assert idx["ck-development-2b"] == ["MD-18800", "MD-18700"]
    assert idx["eval-hidden-tests-aa"] == ["MD-18800"]  # one level: 2b's own labels, not what 2b inherits
    assert idx["loop-a-11111111"] == ["A", "B"] and idx["loop-b-22222222"] == ["B", "A"]  # no cycle
    assert idx["ck-learn-cc"] == []  # a batch of runs is not what a run is about
    assert run_labels("eval-hidden-tests-aa", started["eval-hidden-tests-aa"], started.get, flows) == idx["eval-hidden-tests-aa"]


def test_at_start_the_labels_of_a_named_run_come_from_the_event_log(tmp_path: Path):
    log = EventLog(tmp_path / "events.db")
    log.append("ck-development-1f29e33a", [("started", "run_started", "", {"flow": "ck-development",
                                                                          "inputs": {"ticket": "18700"}})])
    assert inherited_labels(log, {"run": "ck-development-1f29e33a", "base": "abc"}, flows) == ["MD-18700"]
    assert inherited_labels(log, {"run": "ck-development-ffffffff"}, flows) == []

    class Down:
        def read(self, **kw):
            raise RuntimeError("console down")

    assert inherited_labels(Down(), {"run": "ck-development-1f29e33a"}, flows) == []  # never blocks a start


FLOW = """
flow: dev
inputs:
  ticket: {type: ticket, prefix: MD-}
steps:
  - {id: a, harness: script, profile: p, prompt: "echo"}
"""


def test_the_mandate_and_run_started_carry_the_labels():
    assert Mandate(initiator="a", requested_at="t", request={}, flow={"name": "d", "sha256": "x"}).labels == []
    old = Mandate.model_validate_json('{"initiator": "a", "requested_at": "t", "request": {}, "flow": {"name": "d"}}')
    assert old.labels == []  # a mandate written before labels
    s = RunState.create("dev-1", FLOW, {"ticket": "18700"})
    s.mandate = Mandate(initiator="a", requested_at="t", request={}, flow={"name": "dev", "sha256": "x"}, labels=["MD-18700"])
    assert derive_events(s, detail=True)[0].data["labels"] == ["MD-18700"]


async def test_start_run_adds_the_ticket_to_the_callers_labels():
    from myorch.engines.temporal import client as tc

    class Client:
        async def start_workflow(self, fn, inp, **kw):
            self.inp = inp

    c = Client()
    await tc.start_run(c, FLOW, {"ticket": "18700"}, labels=["eval"])
    assert c.inp.labels == ["eval", "MD-18700"]


def parse(*argv: str) -> argparse.Namespace:
    seen = {}

    def grab(self, args=None, namespace=None):
        seen["ns"] = orig(self, args, namespace)
        raise SystemExit(0)

    orig = argparse.ArgumentParser.parse_args
    argparse.ArgumentParser.parse_args = grab
    try:
        import sys

        old, sys.argv = sys.argv, ["myorch", *argv]
        with pytest.raises(SystemExit):
            cli.main()
    finally:
        argparse.ArgumentParser.parse_args = orig
        sys.argv = old
    return seen["ns"]


def test_cli_label_flags():
    ns = parse("run", "flows/x.yaml", "--label", "MD-1", "--label", "eval")
    assert ns.label == ["MD-1", "eval"]
    ns = parse("runs", "--label", "MD-18700")
    assert ns.cmd == "runs" and ns.label == ["MD-18700"]
    assert parse("list").label == []


def test_runs_of_a_renamed_flow_read_the_shared_one_with_their_projects_prefix(tmp_path: Path):
    """ck-development became development (2026-11-10): its old runs keep their ticket labels, with the
    prefix from their project's Jira key."""
    from myorch.runs import flow_inputs_of
    flows_dir, kb = tmp_path / "flows", tmp_path / "kb"
    flows_dir.mkdir()
    (flows_dir / "development.yaml").write_text(
        'inputs:\n  ticket: {if: "project.jira.url", type: ticket, prefix: "{{project.jira.key}}-"}\n')
    (kb / "mdm").mkdir(parents=True)
    (kb / "mdm" / "project.yaml").write_text("repo: o/r\nimage: i\nidentity: {account: a, git_name: a, git_email: a@x}\n"
                                             "jira: {url: 'https://j', key: MD}\n")
    declared_of = flow_inputs_of(flows_dir, kb)
    assert declared_of("ck-development", "mdm")["ticket"]["prefix"] == "MD-"
    started = {"ck-development-1f29e33a": {"flow": "ck-development", "project": "mdm", "inputs": {"ticket": "18700"}}}
    assert label_index(started, declared_of)["ck-development-1f29e33a"] == ["MD-18700"]
    assert declared_of("development", None)["ticket"] == {"type": "hidden"}  # no project: not offered


def test_cli_list_derives_labels_from_mandates(tmp_path: Path):
    (tmp_path / "ck-development.yaml").write_text("inputs:\n  ticket: {type: ticket, prefix: MD-}\n")
    dev = RunRecord.from_mandate("ck-development-1f29e33a", {"flow": {"name": "ck-development"},
                                                             "request": {"ticket": "18700"}})
    ev = RunRecord.from_mandate("eval-hidden-tests-aa", {"flow": {"name": "eval-hidden-tests"},
                                                         "request": {"run": "ck-development-1f29e33a"}, "labels": ["x"]})
    out = {r.run_id: r.labels for r in cli._with_labels([dev, ev], tmp_path)}
    assert out == {"ck-development-1f29e33a": ["MD-18700"], "eval-hidden-tests-aa": ["x", "MD-18700"]}
