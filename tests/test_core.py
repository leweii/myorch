import pytest

from myorch.core.flow import Step, load_flow, parse_duration, render_template
from myorch.core.manifest import Manifest, ManifestFile, Question, fallback_manifest, parse_manifest, resolve_outcome
from myorch.core.prompt import build_prompt, build_reply_prompt
from myorch.core.types import StepKey

FLOW = """
flow: demo
inputs: {task: string}
steps:
  - {id: a, harness: shell, profile: p, prompt: "do {{inputs.task}}"}
  - {id: b, harness: shell, profile: p, prompt: x, needs: [a]}
  - {id: c, harness: shell, profile: p, prompt: x, needs: [a]}
  - {id: d, harness: shell, profile: p, prompt: x, needs: [b, c]}
"""


@pytest.mark.parametrize(
    "bad,msg",
    [
        ("steps: [{id: a, harness: h, profile: p, prompt: x, needs: [zz]}]", "unknown"),
        ("steps: [{id: a, harness: h, profile: p, prompt: x, needs: [a]}]", "cycle"),
        ("steps: [{id: A_1, harness: h, profile: p, prompt: x}]", "must match"),
    ],
)
def test_flow_validation(bad, msg):
    with pytest.raises(ValueError, match=msg):
        load_flow("flow: f\n" + bad)


def test_templates_and_durations():
    assert render_template("x {{ inputs.a }} y", {"inputs": {"a": 1}}) == "x 1 y"
    with pytest.raises(ValueError):
        render_template("{{inputs.nope}}", {})
    assert parse_duration("45m") == 2700


def test_step_key_rejects_path_tricks():
    with pytest.raises(ValueError):
        StepKey(run_id="../etc", step_id="a", attempt=1)


def test_manifest_parse_and_fallback():
    m = parse_manifest('{"step_id": "spoof", "summary": "s", "files": [{"path": "a"}]}', "real")
    assert m is not None and m.step_id == "real" and m.files[0].path == "a"
    assert parse_manifest("not json", "x") is None
    assert parse_manifest(None, "x") is None
    fb = fallback_manifest("x", ["b.txt", "manifest.json", "a.txt"])
    assert fb.status == "manifest_missing" and [f.path for f in fb.files] == ["a.txt", "b.txt"]


def test_prompt_includes_upstream_and_contract():
    step = Step(id="b", harness="h", profile="p", prompt="use {{inputs.t}}", needs=["a"])
    up = {"a": Manifest(step_id="a", summary="did a", files=[ManifestFile(path="plan.md", description="plan")])}
    text = build_prompt(step, {"inputs": {"t": "it"}, "run": {"branch": "myorch/r"}}, up)
    assert text.startswith("use it")
    assert "/sandbox/in/a/manifest.json" in text and "plan.md — plan" in text and "did a" in text
    assert "myorch/r" in text and "MEMORY.md" in text
    assert "/sandbox/out/manifest.json" in text and '"step_id": "b"' in text


def test_outcome_precedence():
    q = Question(question="Which colour?", options=["red", "blue"])
    ok = Manifest(step_id="s", summary="done")
    # myorch-ask wins even if the agent also wrote an ok manifest
    assert resolve_outcome("s", ok, q, []).status == "needs_input"
    assert resolve_outcome("s", None, q, []).question == q
    assert resolve_outcome("s", ok, None, []).status == "ok"
    # needs_input in the manifest without a question is not actionable
    assert resolve_outcome("s", Manifest(step_id="s", status="needs_input"), None, []).status == "manifest_missing"
    fb = resolve_outcome("s", None, None, ["a.txt", ".myorch/question.json"])
    assert fb.status == "manifest_missing" and [f.path for f in fb.files] == ["a.txt"]


def test_interaction_modes_in_prompt():
    never = Step(id="a", harness="h", profile="p", prompt="x")
    allowed = never.model_copy(update={"interaction": "allowed"})
    assert "Do not ask the user questions" in build_prompt(never, {}, {}) and "myorch-ask" not in build_prompt(never, {}, {})
    assert "/sandbox/bin/myorch-ask" in build_prompt(allowed, {}, {})
    assert "read-only in this step" in build_prompt(never, {}, {}, repo_writable=False)
    assert "use blue" in build_reply_prompt(allowed, "use blue")


def test_escalate_forces_question_when_outputs_say_so():
    from myorch.core.manifest import escalate
    m = Manifest(step_id="a", summary="done", outputs={"requires_design_decision": True,
                 "design_question": "Replace the reading time or show both?", "design_options": ["replace", "both"]})
    q = escalate(m, "outputs.requires_design_decision")
    assert q.status == "needs_input" and q.question.question.startswith("Replace") and q.question.options == ["replace", "both"]
    assert escalate(m.model_copy(update={"outputs": {"requires_design_decision": False}}), "outputs.requires_design_decision").status == "ok"
    asked = m.model_copy(update={"status": "needs_input", "question": Question(question="x")})
    assert escalate(asked, "outputs.requires_design_decision").question.question == "x"  # agent already asked
    assert escalate(m, None).status == "ok"
    with pytest.raises(ValueError, match="interaction: allowed"):
        Step(id="a", harness="h", profile="p", escalate_when="outputs.x")
