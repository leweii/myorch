"""A retry after a platform interruption continues the interrupted agent session (2026-11-06,
ck-development-56059024: the develop step's heartbeat timed out, and the retry started a fresh session
on the half-edited repo, unaware the changes were its own).

- prepare_step keeps the session dir for a new attempt (each attempt has its own session id).
- core.run decides when a retry continues (interrupted_by_platform, continued_session).
- the runner continues the newest transcript of the interrupted attempts, or starts its own session
  with the same note in the prompt.
- a change request at a gate keeps counting attempts (attempts_before), so turn logs and session ids
  of the earlier visit are not reused.
"""

import os
import subprocess

import pytest

from myorch.core.flow import Step
from myorch.core.manifest import Manifest
from myorch.core.prompt import build_interrupted_prompt, build_prompt
from myorch.core.run import Instance, RunState, continued_session, interrupted_by_platform, key_attempt
from myorch.core.types import NullRunContext, StepKey, StepResult
from myorch.runner.harnesses import HarnessTask, default_harnesses
from myorch.runner.harnesses.claude_code import VERTEX_SH, ClaudeCodeAdapter, Vertex
from myorch.runner.openshell import SandboxStatus
from myorch.runner.runner import StepRequest, StepRunner, sandbox_name, session_id
from myorch.storage.base import run_dir, step_dir
from myorch.storage.docker_volume import DockerVolumeStore

from test_approval_changes import at_gate, finish, started
from test_runner import STEP, CTX, FakeOpenShell, FakeStore, config_dir  # noqa: F401 - config_dir is a fixture


# ---- storage: the session dir survives a new attempt ------------------------------------------------

@pytest.fixture
def store(tmp_path):
    """A DockerVolumeStore whose helper scripts run on this machine, with tmp_path as the volume."""
    vol = tmp_path / "vol"
    vol.mkdir()
    shims = tmp_path / "shims"
    shims.mkdir()
    (shims / "chown").write_text("#!/bin/sh\nexit 0\n")
    (shims / "chown").chmod(0o755)
    s = DockerVolumeStore()

    def sh(script, stdin=None):
        res = subprocess.run(["sh", "-c", script.replace("/v/", f"{vol}/")], input=stdin, capture_output=True,
                             text=True, env={**os.environ, "PATH": f"{shims}:{os.environ['PATH']}"})
        if res.returncode:
            raise RuntimeError(res.stderr)
        return res.stdout

    s._sh = sh
    s.root = vol
    return s


def transcript(store, run, iid, sid, slug="-sandbox-workspace-repo"):
    d = store.root / run_dir(run) / ".claude" / iid / "projects" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.jsonl").write_text("{}\n")
    return d / f"{sid}.jsonl"


def test_a_new_attempt_keeps_the_earlier_attempts_session(store):
    t = transcript(store, "r", "develop", "s1")
    (store.root / run_dir("r") / ".claude" / "develop" / ".claude.json").write_text("{}")
    out = store.root / step_dir("r", "develop") / "out"
    out.mkdir(parents=True)
    (out / "manifest.json").write_text("{}")
    store.prepare_step(StepKey(run_id="r", step_id="develop", attempt=2, turn=1))
    assert t.exists() and (t.parent.parent.parent / ".claude.json").exists()
    assert list(out.iterdir()) == []  # the attempt's record is still reset


def test_session_files_and_setting_one_aside(store):
    assert store.session_files("r", "develop", ["s1.jsonl"]) == set()  # no session dir yet
    t1 = transcript(store, "r", "develop", "s1")
    transcript(store, "r", "develop", "s2", slug="-sandbox-workspace")
    assert store.session_files("r", "develop", ["s1.jsonl", "s2.jsonl", "s3.jsonl"]) == {"s1.jsonl", "s2.jsonl"}
    assert store.session_files("r", "other", ["s1.jsonl"]) == set()
    side = t1.parent / "s1" / "subagents"
    side.mkdir(parents=True)
    (side / "a.jsonl").write_text("{}")
    store.set_aside_session_file("r", "develop", "s1.jsonl")
    store.set_aside_session_file("r", "develop", "s1")
    assert not t1.exists() and not side.exists()
    assert sorted(p.name.split("-")[0] for p in t1.parent.iterdir()) == ["s1.jsonl.replaced", "s1.replaced"]
    assert next(t1.parent.glob("s1.replaced-*/subagents/a.jsonl"))  # kept, with what it holds
    assert store.session_files("r", "develop", ["s1.jsonl"]) == set()
    with pytest.raises(ValueError):
        store.session_files("r", "develop", ["../x"])
    with pytest.raises(ValueError):
        store.set_aside_session_file("r", "develop", "*")


# ---- core: when a retry continues -------------------------------------------------------------------

def inst_with(result: StepResult | None, manifest: Manifest | None = None) -> Instance:
    return Instance(id="develop", step_id="develop", group="develop", result=result, manifest=manifest)


def res(ok=False, exit_code=None, error=None, stopped=None):
    return StepResult(step_id="develop", ok=ok, exit_code=exit_code, sandbox="mo-x", error=error, stopped=stopped)


def test_platform_interruptions_are_told_apart_from_failures():
    missing = Manifest(step_id="develop", status="manifest_missing")
    assert interrupted_by_platform(inst_with(res(error="activity Heartbeat timeout"), missing))
    assert interrupted_by_platform(inst_with(res(error="timed out after 2h")))  # a result from an old runner
    assert interrupted_by_platform(inst_with(res(error="heartbeat timeout", stopped="lost")))
    assert interrupted_by_platform(inst_with(res(error="no exit code", stopped="sandbox")))
    assert not interrupted_by_platform(inst_with(res(error="timed out after 2h", stopped="timeout")))  # its own limit
    assert not interrupted_by_platform(inst_with(res(error="cannot continue", stopped="harness")))
    assert interrupted_by_platform(inst_with(res(error="sandbox create mo-x failed: overloaded")))
    assert not interrupted_by_platform(inst_with(res(exit_code=1)))  # the harness ended with an error
    assert not interrupted_by_platform(inst_with(res(ok=True, exit_code=0), Manifest(step_id="develop", status="failed")))
    assert not interrupted_by_platform(inst_with(res(error="timed out"), Manifest(step_id="develop", status="failed")))
    assert not interrupted_by_platform(inst_with(None))


def test_a_retry_looks_back_to_the_first_interrupted_attempt():
    lost = inst_with(res(error="activity Heartbeat timeout"))
    assert continued_session(lost, 1, None) == 1
    assert continued_session(lost, 2, 1) == 1  # interrupted twice in a row
    assert continued_session(inst_with(res(exit_code=2)), 2, 1) is None  # ran to its end: a fresh start


# ---- runner: which session a turn uses --------------------------------------------------------------

class SessionStore(FakeStore):
    def __init__(self, sessions=(), fail=0):
        self.sessions = {f"{s}.jsonl" for s in sessions}
        self.fail = fail  # how many lookups fail before one works
        self.lookups = []
        self.set_aside = []

    def session_files(self, run_id, iid, names):
        self.lookups.append(sorted(names))
        if self.fail:
            self.fail -= 1
            raise RuntimeError("docker is down")
        return self.sessions & set(names)

    def set_aside_session_file(self, run_id, iid, name):
        self.set_aside.append(name)
        self.sessions.discard(name)


class SessionOpenShell(FakeOpenShell):
    """`sandbox list` answers from `listings` (one per call, the last repeats); None: the gateway did not
    answer. Deleting a sandbox removes it from later listings only when `deletes_work`."""

    def __init__(self, listings=((),), deletes_work=True):
        super().__init__([SandboxStatus(phase="Completed", exit_code=0)])
        self.listings = [None if x is None else list(x) for x in listings]
        self.deletes_work = deletes_work
        self.listed = []

    async def list_names(self, labels):
        self.listed.append(dict(labels))
        names = self.listings.pop(0) if len(self.listings) > 1 else self.listings[0]
        if names is not None and self.deletes_work:
            names = [n for n in names if n not in self.deleted]
        return names

    async def supervisor_log(self, name):
        return ""


def sid(attempt):
    return session_id(StepKey(run_id="r1", step_id="develop", attempt=attempt))


def box(attempt, turn=1):
    return sandbox_name(StepKey(run_id="r1", step_id="develop", attempt=attempt, turn=turn))


DEV = STEP.model_copy(update={"id": "develop", "outputs": {}, "prompt": "实现需求"})
ASKS = DEV.model_copy(update={"interaction": "allowed"})


class Turn:
    def __init__(self, result, os_):
        self.result, self.os = result, os_
        argv = os_.created["argv"] if os_.created else []
        self.session = ("--resume", argv[argv.index("--resume") + 1]) if "--resume" in argv else (
            ("--session-id", argv[argv.index("--session-id") + 1]) if "--session-id" in argv else None)
        self.prompt = argv[argv.index("-p") + 1] if "-p" in argv else ""


async def run_turn(config_dir, store, attempt, turn=1, step=DEV, os_=None, **kw):
    os_ = os_ or SessionOpenShell()
    runner = StepRunner(store, default_harnesses(), config_dir, openshell=os_, poll_interval=0, store_retry_delay=0)
    key = StepKey(run_id="r1", step_id="develop", attempt=attempt, turn=turn)
    r = StepRequest(key=key, step=step, context=CTX, project="blog", **kw)
    result = await runner.run(r, NullRunContext())
    assert result.ok
    return Turn(result, os_)


INTERRUPTED = dict(continues=1, interrupted="activity Heartbeat timeout")


async def test_a_retry_after_an_interruption_continues_the_interrupted_session(config_dir):
    store = SessionStore([sid(1)])
    t = await run_turn(config_dir, store, 2, **INTERRUPTED)
    assert t.session == ("--resume", sid(1)) and t.result.session_id == sid(1)
    assert t.prompt.startswith("上一次执行被平台中断了（activity Heartbeat timeout）")
    assert "git status" in t.prompt and "/sandbox/out 已经清空" in t.prompt
    assert "实现需求" not in t.prompt  # the session has the task already
    assert t.os.listed == [{"myorch.run": "r1", "myorch.step": "develop"}]  # confirmed nothing else runs
    # a later turn of the same attempt (after a question) goes on in the session turn 1 reported
    store.lookups.clear()
    t = await run_turn(config_dir, store, 2, turn=2, step=ASKS, reply="用红色", session_id=sid(1), **INTERRUPTED)
    assert t.session == ("--resume", sid(1)) and store.lookups == []


async def test_the_interrupted_sandbox_is_deleted_before_its_session_is_continued(config_dir):
    os_ = SessionOpenShell([[box(1, 2)]])  # attempt 1's turn 2 is still running
    t = await run_turn(config_dir, SessionStore([sid(1)]), 2, os_=os_, **INTERRUPTED)
    assert t.session == ("--resume", sid(1))
    assert os_.deleted[0] == box(1, 2) and len(os_.listed) == 2  # deleted, then confirmed gone


@pytest.mark.parametrize("os_", [
    SessionOpenShell([[box(1)]], deletes_work=False),  # it does not go away
    SessionOpenShell([None]),  # the gateway does not answer
], ids=["still-there", "unknown"])
async def test_without_confirming_the_old_sandbox_is_gone_a_new_session_starts(config_dir, os_):
    t = await run_turn(config_dir, SessionStore([sid(1)]), 2, os_=os_, **INTERRUPTED)
    assert t.session == ("--session-id", sid(2)) and t.result.session_id == sid(2)
    assert "## 这是一次重试" in t.prompt and "activity Heartbeat timeout" in t.prompt
    assert len(os_.listed) == 4  # STOP_CHECKS


async def test_without_the_old_session_a_new_one_starts_with_the_same_note(config_dir):
    store = SessionStore()  # attempt 1 never got its sandbox running
    t = await run_turn(config_dir, store, 2, **INTERRUPTED)
    assert t.session == ("--session-id", sid(2))
    assert t.prompt.startswith("实现需求") and "## 这是一次重试" in t.prompt and "git status" in t.prompt
    assert "activity Heartbeat timeout" in t.prompt


async def test_a_later_turn_without_a_reported_session_looks_it_up(config_dir):
    store = SessionStore([sid(1), sid(2)])  # e.g. a hint after turn 1's activity was lost
    t = await run_turn(config_dir, store, 2, turn=2, step=ASKS, reply="好", **INTERRUPTED)
    assert t.session == ("--resume", sid(2))
    t = await run_turn(config_dir, SessionStore([sid(1)]), 2, turn=2, step=ASKS, reply="好", **INTERRUPTED)
    assert t.session == ("--resume", sid(1))


async def test_after_interruptions_in_a_row_the_newest_session_is_continued(config_dir):
    store = SessionStore([sid(1)])  # attempt 2 continued sid(1) and was interrupted too
    assert (await run_turn(config_dir, store, 3, **INTERRUPTED)).session == ("--resume", sid(1))
    store = SessionStore([sid(1), sid(2)])  # attempt 2 had to start its own
    assert (await run_turn(config_dir, store, 3, **INTERRUPTED)).session == ("--resume", sid(2))


async def test_an_ordinary_attempt_starts_its_own_session_and_sets_a_stale_one_aside(config_dir):
    store = SessionStore([sid(1)])  # left from an earlier visit that also numbered this attempt 1
    t = await run_turn(config_dir, store, 1)
    assert t.session == ("--session-id", sid(1)) and store.set_aside == [f"{sid(1)}.jsonl", sid(1)]
    assert "这是一次重试" not in t.prompt and t.os.listed == []  # an ordinary attempt lists nothing
    store = SessionStore([sid(2), sid(1)])  # stale own id beside the one to continue
    assert (await run_turn(config_dir, store, 2, **INTERRUPTED)).session == ("--resume", sid(1))
    assert store.set_aside == [f"{sid(2)}.jsonl", sid(2)]


async def test_an_ordinary_later_turn_looks_nothing_up(config_dir):
    store = SessionStore()
    assert (await run_turn(config_dir, store, 1, turn=2, step=ASKS, reply="好")).session == ("--resume", sid(1))
    assert store.lookups == []


async def test_a_lookup_is_tried_again_then_fails_the_activity_instead_of_guessing(config_dir):
    store = SessionStore([sid(1)], fail=2)
    assert (await run_turn(config_dir, store, 2, **INTERRUPTED)).session == ("--resume", sid(1))
    assert len(store.lookups) == 3
    for turn, kw in [(1, {}), (2, dict(step=ASKS, reply="好", **INTERRUPTED))]:
        os_ = SessionOpenShell()
        runner = StepRunner(SessionStore([sid(1)], fail=3), default_harnesses(), config_dir, openshell=os_,
                            poll_interval=0, store_retry_delay=0)
        step = kw.pop("step", DEV)
        r = StepRequest(key=StepKey(run_id="r1", step_id="develop", attempt=1, turn=turn), step=step, context=CTX,
                        project="blog", **kw)
        with pytest.raises(RuntimeError, match="reading the agent sessions failed"):
            await runner.run(r, NullRunContext())  # the activity fails: an interruption, not a wrong session
        assert os_.created is None


async def test_a_script_step_has_no_session_to_continue(config_dir):
    store = SessionStore([sid(1)])
    step = Step(id="develop", harness="script", profile="p", prompt="echo hi")
    await run_turn(config_dir, store, 2, step=step, **INTERRUPTED)
    assert store.lookups == []


def test_claude_code_resumes_on_turn_one_when_told():
    a = ClaudeCodeAdapter()
    base = dict(prompt="p", instruction="i", turn=1, session_id="s", session_dir="/d")
    argv = a.build_command(HarnessTask(**base))
    assert argv[argv.index("--session-id") + 1] == "s" and "--resume" not in argv
    argv = a.build_command(HarnessTask(**base, resume=True))
    assert argv[argv.index("--resume") + 1] == "s" and "--session-id" not in argv
    assert a.session_file("s") == "s.jsonl" and a.session_entries("s") == ["s.jsonl", "s"]


def test_claude_code_on_vertex_sends_the_token_placeholder_itself():
    assert Vertex.from_env({}) is None and Vertex.from_env({"MYORCH_VERTEX_PROJECT": " "}) is None
    v = Vertex.from_env({"MYORCH_VERTEX_PROJECT": "proj"})
    assert v == Vertex("proj", "global")
    task = HarnessTask(prompt="p", instruction="i", turn=1, session_id="s", session_dir="/d")
    plain = ClaudeCodeAdapter().build_command(task)
    argv = ClaudeCodeAdapter(vertex=Vertex("proj", "europe-west1")).build_command(task)
    assert "CLAUDE_CODE_USE_VERTEX=1" not in plain and not any("GCP_SA_ACCESS_TOKEN" in a for a in plain)
    head = argv[:argv.index("myorch-vertex") + 1]
    assert {"CLAUDE_CODE_USE_VERTEX=1", "CLAUDE_CODE_SKIP_VERTEX_AUTH=1", "ANTHROPIC_VERTEX_PROJECT_ID=proj",
            "CLOUD_ML_REGION=europe-west1"} <= set(head)
    assert head[-3:] == ["-c", VERTEX_SH, "myorch-vertex"] and "$GCP_SA_ACCESS_TOKEN" in VERTEX_SH
    assert argv[len(head):] == plain[2:]  # the rest (stream wrapper, claude ...) is unchanged


def test_prompts_name_the_interruption():
    p = build_interrupted_prompt(DEV, "timed out\nafter 2h")
    assert "（timed out after 2h）" in p and "myorch-ask" not in p  # interaction never: no asking
    p = build_prompt(DEV, CTX, {}, interrupted="x" * 400)
    assert p.index("## 这是一次重试") < p.index("## 输出要求") and "x" * 300 + "…" in p
    assert "这是一次重试" not in build_prompt(DEV, CTX, {})


# ---- change requests keep counting attempts ---------------------------------------------------------

def test_a_change_request_keeps_counting_the_attempts_of_the_steps_it_resets():
    s = at_gate()
    s.instances["risk"].attempts = 2  # risk was retried once on its first visit
    s.request_changes("human", "ann", "改一下", "t", keep_attempts=True)
    risk = s.instances["risk"]
    assert risk.attempts == 0 and risk.attempts_before == 2  # people still see attempt 1 of this visit
    assert key_attempt(risk, 1) == 3  # but its logs and session are turn-3-*, not turn-1-* again

    for iid in ("verify.r2", "smoke.r2"):
        assert started(s.plan()) == [iid]
        finish(s, iid, blocking=0, passed=True)
    assert started(s.plan()) == ["risk"]
    risk.attempts = 1
    finish(s, "risk", head="bbb")
    assert started(s.plan()) == ["human"]
    s.instances["human"].status = "waiting_approval"
    s.request_changes("human", "ann", "再改", "t2", keep_attempts=True)
    assert risk.attempts_before == 3


def test_runs_from_before_number_attempts_from_one_again():
    s = at_gate()
    s.instances["risk"].attempts = 2
    s.request_changes("human", "ann", "改一下", "t")
    assert s.instances["risk"].attempts == 0 and key_attempt(s.instances["risk"], 1) == 1
