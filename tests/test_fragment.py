from pathlib import Path

import pytest
import yaml

from myorch.core.flow import Loop, load_flow
from myorch.core.fragment import FragmentError, expand_flow
from myorch.flowfile import read_flow_file

REPO = Path(__file__).resolve().parent.parent

FRAGMENT = """
fragment: mini
params:
  base:      {type: string, required: true}
  reviewers: {type: int, default: 2, min: 1, max: 4}
  fix:       {type: bool, default: true}
  mode:      {type: string, default: ask, enum: [ask, accept]}
item:
  loop: _
  until: "steps.verify.outputs.blocking == 0"
  on_exhausted: "{{params.mode}}"
  steps:
    - {id: review, harness: script, profile: p, fanout: "{{params.reviewers}}",
       prompt: "diff origin/{{params.base}} round {{loop.round}} fix={{params.fix}}"}
    - {id: verify, harness: script, profile: p, needs: [review, fix-prep]}
    - {id: fix-prep, if: "params.fix", harness: script, profile: p, needs: [review]}
    - {id: fix, if: "params.fix", harness: script, profile: p, needs: [verify]}
"""


def flow_with(with_: dict) -> str:
    return yaml.safe_dump({
        "flow": "f",
        "inputs": {"base": "string"},
        "steps": [
            {"id": "start", "harness": "script", "profile": "p"},
            {"use": "lib/mini.yaml", "id": "loop1", "needs": ["start"], "with": with_},
            {"id": "after", "harness": "script", "profile": "p", "needs": ["loop1"]},
        ],
    })


def resolve(rel: str) -> str:
    assert rel == "lib/mini.yaml"
    return FRAGMENT


def loop_of(flow) -> Loop:
    return next(i for i in flow.steps if isinstance(i, Loop))


def test_substitution_is_typed_and_runtime_templates_survive():
    f = load_flow(flow_with({"base": "{{inputs.base}}", "reviewers": 3}), resolve)
    lp = loop_of(f)
    assert lp.id == "loop1" and lp.needs == ["start"] and lp.on_exhausted == "ask"
    review = lp.steps[0]
    assert review.fanout == 3  # "{{params.reviewers}}" became an int
    # params are filled in; run-time templates are left for later
    assert review.prompt == "diff origin/{{inputs.base}} round {{loop.round}} fix=true"


def test_if_drops_steps_and_prunes_needs():
    f = load_flow(flow_with({"base": "main", "fix": False, "mode": "accept"}), resolve)
    lp = loop_of(f)
    assert [s.id for s in lp.steps] == ["review", "verify"]
    assert lp.steps[1].needs == ["review"]
    assert lp.on_exhausted == "accept" and lp.steps[0].fanout == 2  # default


@pytest.mark.parametrize(
    "with_,msg",
    [
        ({}, "needs param 'base'"),
        ({"base": "m", "colour": "red"}, "no params"),
        ({"base": "m", "reviewers": "3"}, "must be int"),
        ({"base": "m", "reviewers": True}, "must be int"),
        ({"base": "m", "reviewers": 9}, "<= 4"),
        ({"base": "m", "mode": "fail"}, "one of"),
    ],
)
def test_param_errors(with_, msg):
    with pytest.raises(FragmentError, match=msg):
        load_flow(flow_with(with_), resolve)


def test_entry_and_fragment_errors():
    with pytest.raises(FragmentError, match="unsupported keys"):
        expand_flow({"steps": [{"use": "x", "id": "a", "harness": "script"}]}, resolve)
    with pytest.raises(FragmentError, match="needs an id"):
        expand_flow({"steps": [{"use": "lib/mini.yaml"}]}, resolve)
    with pytest.raises(FragmentError, match="undeclared param"):
        bad = FRAGMENT.replace("{{params.mode}}", "{{params.nope}}")
        load_flow(flow_with({"base": "m"}), lambda rel: bad)
    with pytest.raises(FragmentError, match="load it from its file"):
        load_flow(flow_with({"base": "m"}))


def test_fragment_path_must_stay_inside_flow_dir(tmp_path: Path):
    (tmp_path / "flows").mkdir()
    (tmp_path / "evil.yaml").write_text(FRAGMENT)
    flow = tmp_path / "flows" / "f.yaml"
    flow.write_text(flow_with({"base": "m"}).replace("lib/mini.yaml", "../evil.yaml"))
    with pytest.raises(FragmentError, match="outside"):
        read_flow_file(flow)


PROJECT = {"name": "blog", "repo": "o/r", "base": "master", "image": "img", "e2e": {"image": "", "profile": "dev-read", "acceptance": True}, "jira": {"url": "", "key": "", "auth": "basic", "email": ""}, "branch": "myorch/{{run.id}}",
           "identity": {"account": "bot", "git_name": "bot", "git_email": "b@x", "trigger": "@bot"},
           "providers": {"github": "gh"}, "repo_docs": [], "conventions": ""}


def test_development_uses_the_review_loop():
    f = load_flow(read_flow_file(REPO / "flows" / "development.yaml", {"ticket": "1"}, PROJECT))
    lp = loop_of(f)
    assert lp.id == "review-fix" and lp.needs == ["develop"] and lp.max_rounds == 3
    assert [s.id for s in lp.steps] == ["ci", "review", "security", "verify", "fix", "pack-choose", "pack-apply"]
    assert lp.steps[1].fanout == 3 and lp.on_exhausted == "ask"  # the defaults of flows.development
    assert "origin/master" in lp.steps[0].prompt and f.image == "img"  # {{project.x}} filled in (CI computes the range)
    assert "{{steps.ci.outputs.range}}" in lp.steps[1].prompt  # reviewers get the range from this round's CI
    with pytest.raises(FragmentError, match="--project"):
        read_flow_file(REPO / "flows" / "development.yaml", {"ticket": "1"})


def test_the_project_pack_shapes_development():
    """One development flow: the pack's flows.development, jira and prompts decide its shape."""
    light = {**PROJECT, "flows": {"development": {"reviewers": 1, "max_rounds": 1, "security": False,
                                                   "on_exhausted": "accept"}},
             "prompts": {"develop": "## 博客\n这是一个个人博客。", "review": "## 博客还要看\n- 链接"}}
    f = load_flow(read_flow_file(REPO / "flows" / "development.yaml", {"task": "x"}, light))
    assert [i.id for i in f.steps] == ["sync-master", "checkout-branch", "develop", "review-fix", "risk-rules",
                                       "classify-risk", "human-review", "deliver"]  # no Jira, no takeaway
    assert next(i for i in f.steps if i.id == "develop").needs == ["checkout-branch"]
    assert next(i for i in f.steps if i.id == "risk-rules").needs == ["review-fix"]  # takes over takeaway-commit's
    lp = loop_of(f)
    assert lp.max_rounds == 1 and lp.on_exhausted == "accept" and lp.steps[1].fanout == 1
    assert "security" not in [s.id for s in lp.steps] and "## 博客还要看\n- 链接" in lp.steps[1].prompt
    assert "## 博客\n这是一个个人博客。" in next(i for i in f.steps if i.id == "develop").prompt
    assert f.inputs["ticket"] == {"type": "hidden"} and f.inputs["incident"] == {"type": "hidden"}

    full = {**PROJECT, "jira": {**PROJECT["jira"], "url": "https://j", "key": "MD"},
            "flows": {"development": {"takeaway": True, "incident": True}}}
    g = load_flow(read_flow_file(REPO / "flows" / "development.yaml", {"ticket": "1"}, full))
    ids = [i.id for i in g.steps]
    assert ids[:4] == ["sync-master", "checkout-branch", "fetch-ticket", "develop"]
    assert ids[5:9] == ["takeaway-trail", "takeaway", "takeaway-redact", "takeaway-commit"]
    assert next(i for i in g.steps if i.id == "risk-rules").needs == ["takeaway-commit"]
    assert g.inputs["ticket"]["prefix"] == "MD-" and g.inputs["incident"]["type"] == "string"
    assert loop_of(g).steps[1].fanout == 3 and "{{project." not in read_flow_file(
        REPO / "flows" / "development.yaml", {"ticket": "1"}, full)


def test_review_loop_without_fix_and_security(tmp_path: Path):
    lib = REPO / "flows" / "lib" / "review-loop.yaml"
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "review-loop.yaml").write_text(lib.read_text())
    flow = tmp_path / "review.yaml"
    flow.write_text(yaml.safe_dump({
        "flow": "review", "inputs": {"base": "string"},
        "steps": [{"use": "lib/review-loop.yaml", "id": "review-round", "with": {
            "base": "{{inputs.base}}", "reviewers": 2, "max_rounds": 1,
            "security": False, "fix": False, "on_exhausted": "accept"}}],
    }))
    lp = loop_of(load_flow(read_flow_file(flow, None, PROJECT)))  # the loop has the project's prompt slots
    assert [s.id for s in lp.steps] == ["ci", "review", "verify"]
    assert lp.steps[2].needs == ["review"] and lp.steps[1].fanout == 2
    assert lp.max_rounds == 1 and lp.on_exhausted == "accept"


def test_loop_id_must_not_clash_with_fragment_steps():
    bad = flow_with({"base": "m"}).replace("id: loop1", "id: review").replace("[loop1]", "[review]")
    with pytest.raises(ValueError, match="duplicate step/loop ids"):
        load_flow(bad, resolve)


PRESET_FLOW = """
flow: f
inputs: {depth: string}
defaults: {depth: standard}
presets:
  by: depth
  quick:    {reviewers: 1, fix: false}
  standard: {reviewers: 2, fix: true}
steps:
  - use: lib/mini.yaml
    id: loop1
    when: "inputs.depth != 'skip'"
    with: {base: main, reviewers: "{{preset.reviewers}}", fix: "{{preset.fix}}"}
"""


def test_presets_select_values_by_input():
    lp = loop_of(load_flow(PRESET_FLOW, resolve))  # no inputs: falls back to defaults.depth
    assert lp.steps[0].fanout == 2 and [s.id for s in lp.steps][-1] == "fix"
    data = expand_flow(yaml.safe_load(PRESET_FLOW), resolve, {"depth": "quick"})
    assert "presets" not in data
    lp = next(s for s in data["steps"] if "loop" in s)
    assert lp["steps"][0]["fanout"] == 1 and [s["id"] for s in lp["steps"]] == ["review", "verify"]
    assert lp["when"] == "inputs.depth != 'skip'"  # use-level when lands on the loop


def test_preset_errors():
    with pytest.raises(FragmentError, match="not a preset"):
        expand_flow(yaml.safe_load(PRESET_FLOW), resolve, {"depth": "huge"})
    with pytest.raises(FragmentError, match="undefined preset value"):
        expand_flow(yaml.safe_load(PRESET_FLOW.replace("{{preset.fix}}", "{{preset.nope}}")), resolve, {})
    with pytest.raises(FragmentError, match="no value or default"):
        expand_flow(yaml.safe_load(PRESET_FLOW.replace("defaults: {depth: standard}\n", "")), resolve, {})


E2E_ON = {"image": "e2e:img", "profile": "e2e-prof", "acceptance": True}


def e2e_flow(tmp_path: Path, e2e: dict, fix: bool = True) -> str:
    """A flow that uses the e2e block with a fix step (no project flow does today: blog-review tests only)."""
    (tmp_path / "lib").mkdir(exist_ok=True)
    (tmp_path / "lib" / "e2e.yaml").write_text((REPO / "flows" / "lib" / "e2e.yaml").read_text())
    flow = tmp_path / "dev.yaml"
    flow.write_text(yaml.safe_dump({
        "flow": "dev", "inputs": {"task": "string"},
        "steps": [{"id": "review-fix", "harness": "script", "profile": "p"},
                  {"use": "lib/e2e.yaml", "id": "e2e", "needs": ["review-fix"],
                   "when": "'{{project.e2e.image}}' != ''",
                   "with": {"image": "{{project.e2e.image}}", "profile": "{{project.e2e.profile}}",
                            "acceptance": "{{project.e2e.acceptance}}", "base": "{{project.base}}", "fix": fix}},
                  {"id": "after", "harness": "script", "profile": "p", "needs": ["e2e"]}],
    }, allow_unicode=True))
    return read_flow_file(flow, {"task": "x"}, {**PROJECT, "e2e": e2e})


def test_e2e_block_runs_after_review_in_the_browser_image(tmp_path: Path):
    """review -> e2e block (smoke + acceptance in the e2e image, then fix) -> the next step."""
    from myorch.core.expr import evaluate
    f = load_flow(e2e_flow(tmp_path, E2E_ON))
    e2e = next(i for i in f.steps if i.id == "e2e")
    assert e2e.needs == ["review-fix"] and e2e.max_rounds == 2
    assert [s.id for s in e2e.steps] == ["e2e-smoke", "e2e-accept", "e2e-fix"]
    smoke, accept, fix = e2e.steps
    assert smoke.image == accept.image == "e2e:img" and fix.image is None  # fix has no test tools
    assert smoke.profile == accept.profile == "e2e-prof" and fix.profile == "dev-write"
    assert evaluate(accept.when, {}) is True
    assert smoke.needs == accept.needs == [] and fix.needs == ["e2e-smoke", "e2e-accept"]
    assert evaluate(e2e.when, {}) is True
    # Projects without an e2e image skip the whole block.
    g = load_flow(e2e_flow(tmp_path, PROJECT["e2e"]))
    assert evaluate(next(i for i in g.steps if i.id == "e2e").when, {}) is False


def test_review_flow_runs_the_e2e_block_test_only():
    f = load_flow(read_flow_file(REPO / "flows" / "review.yaml", {"pr": "1"}, {**PROJECT, "e2e": E2E_ON}))
    e2e = next(i for i in f.steps if i.id == "e2e")
    assert e2e.needs == ["review-round"] and e2e.max_rounds == 1 and e2e.on_exhausted == "accept"
    assert [s.id for s in e2e.steps] == ["e2e-smoke", "e2e-accept"]  # fix: false
    assert "docs/pr.md" in e2e.steps[1].prompt


def test_e2e_acceptance_off_skips_the_agent_and_still_ends(tmp_path: Path):
    """acceptance: false -> e2e-accept is skipped; its missing output neither blocks `until` nor triggers fix."""
    from myorch.core.expr import evaluate
    f = load_flow(e2e_flow(tmp_path, {**E2E_ON, "acceptance": False}))
    e2e = next(i for i in f.steps if i.id == "e2e")
    smoke, accept, fix = e2e.steps
    assert evaluate(accept.when, {}) is False
    ok = {"steps": {"e2e-smoke": {"outputs": {"passed": True}}, "e2e-accept": {"status": "skipped"}}}
    assert evaluate(e2e.until, ok) is True and evaluate(fix.when, ok) is False
    bad = {"steps": {"e2e-smoke": {"outputs": {"passed": False, "failed": ["首页"], "environment": []}},
                     "e2e-accept": {"status": "skipped"}}}
    assert evaluate(e2e.until, bad) is False and evaluate(fix.when, bad) is True
    # Only environment problems: the block ends without a fix (risk-rules asks a person instead).
    env = {"steps": {"e2e-smoke": {"outputs": {"passed": False, "failed": [], "environment": ["Chromium"]}},
                     "e2e-accept": {"status": "skipped"}}}
    assert evaluate(e2e.until, env) is True and evaluate(fix.when, env) is False
    failed_accept = {"steps": {"e2e-smoke": {"outputs": {"passed": True}}, "e2e-accept": {"outputs": {"passed": False}}}}
    assert evaluate(e2e.until, failed_accept) is False and evaluate(fix.when, failed_accept) is True
