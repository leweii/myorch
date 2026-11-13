"""Postmortem (flows/postmortem.yaml): its host action writes the eval case and starts the replays
(runner/actions.py postmortem-replay); the CLI picks the candidate runs (runs.select_for_postmortem)."""

from __future__ import annotations

import json
from pathlib import Path

from myorch import evals
from myorch.core.flow import Step
from myorch.core.types import StepKey
from myorch.runner.actions import ActionRequest, ActionRunner
from myorch.runs import RunRecord, select_for_postmortem

from test_actions import FakeStore  # pytest puts tests/ on sys.path

RUN = "postmortem-1a2b3c4d"
TARGET = {"repo": "o/r", "base_ref": "main", "bad_sha": "b" * 40, "base_sha": "a" * 40, "fix_sha": "f" * 40,
          "bad_subject": "add analytics", "requirement": "add analytics"}
FOUND = [{"severity": "Important", "title": "Password is sent to a third party in the URL fragment", "file": "_includes/ga.html", "line": 12,
          "evidence": "page_location contains #pw="}]


def act(tmp_path: Path, files: dict[str, str], phase: str):
    launched: list[dict] = []
    store = FakeStore({f"runs/{RUN}/{k}": v for k, v in {
        "docs/postmortem/target.json": json.dumps(TARGET), ".myorch/mandate.json": json.dumps({"initiator": "jakobhe"}),
        **files}.items()})

    def launch(req):
        launched.append(req)
        return req["run_id"]
    runner = ActionRunner(store, tmp_path / "kb", config_dir=tmp_path, data_root=tmp_path, launch=launch)
    step = Step(id=f"replay-{phase}", action="postmortem-replay", args={"phase": phase, "depth": "standard"})
    out = runner.run(ActionRequest(key=StepKey(run_id=RUN, step_id=step.id, attempt=1), step=step,
                                   context={"project": {"name": "blog"}}))
    assert out.result.ok, out.result.error
    return out, launched


def test_before_writes_a_draft_and_replays_the_change_only(tmp_path):
    out, launched = act(tmp_path, {}, "before")
    case = evals.load_case(tmp_path, RUN)
    assert (case["status"], case["origin"], case["head_sha"], case["base_sha"]) == ("draft", "git", "b" * 40, "a" * 40)
    assert [r["run_id"] for r in launched] == ["eval-1a2b3c4d-before"] and launched[0]["by"] == "jakobhe"
    seen = evals.visible(launched[0]["case"])
    assert "findings" not in seen and "fix_sha" not in seen  # the replay never sees the answer or the fix
    assert evals.list_cases(tmp_path, "pending") == []  # a draft is not offered for confirming
    _, again = act(tmp_path, {}, "before")  # a retry does not start a second replay
    assert again == []


def test_after_adds_the_findings_and_replays_only_when_proposals_were_adopted(tmp_path):
    act(tmp_path, {}, "before")
    out, launched = act(tmp_path, {"docs/postmortem/case.json": json.dumps(FOUND, ensure_ascii=False)}, "after")
    case = evals.load_case(tmp_path, RUN)
    assert (case["status"], case["findings"][0]["file"], launched) == ("pending", "_includes/ga.html", [])
    assert "no proposal adopted" in out.manifest.summary
    out, launched = act(tmp_path, {"docs/postmortem/case.json": json.dumps(FOUND, ensure_ascii=False),
                                   "docs/postmortem/accepted.json": json.dumps({"accepted": [1]})}, "after")
    assert [r["run_id"] for r in launched] == ["eval-1a2b3c4d-after"]
    assert set(evals.load_case(tmp_path, RUN)["replays"]) == {"before", "after"}


def test_nothing_detectable_rejects_the_case(tmp_path):
    out, launched = act(tmp_path, {"docs/postmortem/case.json": "[]", "docs/postmortem/accepted.json": '{"accepted": [1]}'}, "after")
    assert evals.load_case(tmp_path, RUN)["status"] == "rejected" and launched == []


def test_candidates_are_the_projects_newest_development_and_review_runs():
    recs = [RunRecord(run_id=f"development-{i}", flow="development", project="blog", status="COMPLETED",
                      requested_at=f"2026-11-{i:02d}") for i in range(1, 25)]
    recs += [RunRecord(run_id="learn-1", flow="learn", project="blog", status="COMPLETED", requested_at="2026-11-30"),
             RunRecord(run_id="review-9", flow="review", project="mdm", status="COMPLETED", requested_at="2026-11-30"),
             RunRecord(run_id="development-run", flow="development", project="blog", status="RUNNING", requested_at="2026-11-30")]
    got = [r.run_id for r in select_for_postmortem(recs, "blog")]
    assert len(got) == 20 and got[-1] == "development-24" and "learn-1" not in got and "development-run" not in got
