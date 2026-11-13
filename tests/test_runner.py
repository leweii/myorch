import json
import re
from pathlib import Path

import pytest

from myorch.core.flow import Step
from myorch.core.manifest import Manifest
from myorch.core.types import NullRunContext, StepKey
from myorch.runner.harnesses import default_harnesses
from myorch.runner.openshell import Mount, SandboxStatus, docker_driver_config
from myorch.runner.profiles import load_profile
from myorch.runner.runner import StepRequest, StepRunner, attempt_turn_of, sandbox_name, session_id
from myorch.storage.base import MountSource, project_snapshot_dir, run_dir, shared_dir, step_dir


class FakeStore:
    volume = "vol"

    def _s(self, p):
        return MountSource(volume=self.volume, subpath=p)

    def workspace_source(self, run_id):
        return self._s(run_dir(run_id))

    def repo_source(self, run_id):
        return self._s(f"{run_dir(run_id)}/repo")

    def out_source(self, run_id, iid):
        return self._s(f"{step_dir(run_id, iid)}/out")

    def logs_source(self, run_id, iid):
        return self._s(f"{step_dir(run_id, iid)}/logs")

    def project_source(self, name, version=""):
        return self._s(project_snapshot_dir(name, version))

    def shared_source(self, project, name):
        return self._s(shared_dir(project, name))

    def ensure_shared(self, project, name):
        pass

    def tools_source(self):
        return self._s("tools")

    def read_log_tail(self, run_id, iid, max_bytes=4000, file=""):
        self.tail_of = file
        return "log tail"

    def session_files(self, run_id, iid, names):
        return set()

    def set_aside_session_file(self, run_id, iid, name):
        pass


class FakeOpenShell:
    def __init__(self, phases):
        self.phases = list(phases)
        self.created = None
        self.deleted = []

    async def create_detached(self, **kw):
        self.created = kw

    async def status(self, name):
        return self.phases.pop(0) if len(self.phases) > 1 else self.phases[0]

    async def delete(self, name):
        self.deleted.append(name)


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "p.yaml").write_text("providers: [github]\n")
    (tmp_path / "profiles" / "ro.yaml").write_text("repo: ro\nimage: dev:latest\n")
    return tmp_path


def make_runner(config_dir, phases):
    os_ = FakeOpenShell(phases)
    return StepRunner(FakeStore(), default_harnesses(), config_dir, openshell=os_, poll_interval=0), os_


KEY = StepKey(run_id="r1", step_id="verify.r2", attempt=2)
STEP = Step(id="verify", harness="claude-code", profile="p", prompt="round {{loop.round}}", needs=["review"],
            outputs={"blocking": "number of blocking findings"})
CTX = {"inputs": {"repo": "o/r"}, "run": {"id": "r1", "branch": "myorch/r1"}, "loop": {"round": 2}, "steps": {},
       "project": {"name": "blog", "providers": {"github": "github-bot"}, "repo_docs": ["CLAUDE.md"]}}


def req(**kw):
    base = dict(key=KEY, step=STEP, context=CTX, upstream={"review.r2.x1": Manifest(step_id="review.r2.x1")},
                project="blog", default_image="flow:img")
    base.update(kw)
    return StepRequest(**base)


async def test_workspace_mounts_env_and_session(config_dir):
    runner, os_ = make_runner(config_dir, [SandboxStatus(phase="Ready", exit_code=None), SandboxStatus(phase="Completed", exit_code=0)])
    res = await runner.run(req(), NullRunContext())
    assert res.ok and res.output_tail == "log tail"
    c = os_.created
    name = sandbox_name(KEY)
    assert c["name"] == name and len(name) <= 19
    assert c["labels"]["myorch.step"] == "verify.r2"
    assert c["image"] == "flow:img"  # profile has none -> flow image beats adapter default
    assert c["providers"] == ["github-bot", "claude"]  # role "github" mapped by the project
    targets = {m.target: (m.source.subpath, m.read_only) for m in c["mounts"]}
    assert targets == {
        "/sandbox/workspace": ("runs/r1", False),
        "/sandbox/workspace/.myorch": ("runs/r1/.myorch", True),  # other steps' records: read only
        "/sandbox/workspace/.claude": ("runs/r1/.claude", True),  # other instances' sessions: read only
        "/sandbox/workspace/.claude/verify.r2": ("runs/r1/.claude/verify.r2", False),  # its own session
        "/sandbox/out": ("runs/r1/.myorch/steps/verify.r2/out", False),
        "/sandbox/logs": ("runs/r1/.myorch/steps/verify.r2/logs", False),
        "/sandbox/workspace/project": ("projects/blog", True),
        "/sandbox/in/review.r2.x1": ("runs/r1/.myorch/steps/review.r2.x1/out", True),
        "/sandbox/bin": ("tools", True),  # every step gets the helpers (myorch-checks)
    }  # repo is rw (no overlay)
    argv = c["argv"]
    assert "MYORCH_INTERACTION=never" in argv  # so myorch-ask refuses in this step
    assert "MYORCH_INPUT_REPO=o/r" in argv and "MYORCH_ROUND=2" in argv and "MYORCH_BRANCH=myorch/r1" in argv
    assert f"CLAUDE_CONFIG_DIR=/sandbox/workspace/.claude/verify.r2" in argv
    assert argv[argv.index("--session-id") + 1] == session_id(KEY)
    prompt = argv[argv.index("-p") + 1]
    assert prompt.startswith("round 2") and "/sandbox/workspace/project/" in prompt and "repo/CLAUDE.md" in prompt
    assert "`blocking`" in prompt and "Do not ask the user questions" in prompt
    assert os_.deleted == [name, name]


async def test_read_only_profile_overlays_repo(config_dir):
    runner, os_ = make_runner(config_dir, [SandboxStatus(phase="Completed", exit_code=0)])
    await runner.run(req(step=STEP.model_copy(update={"profile": "ro"})), NullRunContext())
    targets = {m.target: m.read_only for m in os_.created["mounts"]}
    assert targets["/sandbox/workspace/repo"] is True and targets["/sandbox/workspace"] is False
    assert os_.created["image"] == "dev:latest"
    assert "GIT_OPTIONAL_LOCKS=0" in os_.created["argv"]


async def test_continuation_turn_resumes_session(config_dir):
    runner, os_ = make_runner(config_dir, [SandboxStatus(phase="Completed", exit_code=0)])
    step = STEP.model_copy(update={"interaction": "allowed"})
    turn2 = KEY.model_copy(update={"turn": 2})
    await runner.run(req(key=turn2, step=step, reply="use red"), NullRunContext())
    argv = os_.created["argv"]
    assert argv[argv.index("--resume") + 1] == session_id(KEY)
    assert argv[argv.index("-p") + 1].startswith("The user replied to your question")
    assert {m.target for m in os_.created["mounts"]} >= {"/sandbox/bin"}


async def test_failures(config_dir):
    runner, _ = make_runner(config_dir, [SandboxStatus(phase="Error", exit_code=3)])
    res = await runner.run(req(), NullRunContext())
    assert not res.ok and res.exit_code == 3
    runner, _ = make_runner(config_dir, [SandboxStatus(phase="Error", exit_code=None)])
    res = await runner.run(req(), NullRunContext())
    assert not res.ok and "provisioning" in res.error


def test_names_and_driver_config():
    cfg = json.loads(docker_driver_config([Mount(source=MountSource(volume="v", subpath="s"), target="/t", read_only=True)]))
    assert cfg == {"docker": {"mounts": [{"type": "volume", "source": "v", "subpath": "s", "target": "/t", "read_only": True}]}}
    worst = StepKey(run_id="r" * 60, step_id="s" * 20 + ".r10.x8", attempt=6, turn=99)
    assert len(sandbox_name(worst)) <= 19
    assert session_id(KEY) == session_id(KEY.model_copy(update={"turn": 5}))
    assert session_id(KEY) != session_id(KEY.model_copy(update={"attempt": 3}))


async def test_model_resolution(config_dir):
    (config_dir / "profiles" / "pm.yaml").write_text("model: claude-sonnet-5-5\n")
    # flow default only
    runner, os_ = make_runner(config_dir, [SandboxStatus(phase="Completed", exit_code=0)])
    res = await runner.run(req(default_model="claude-haiku-5-5"), NullRunContext())
    argv = os_.created["argv"]
    assert argv[argv.index("--model") + 1] == "claude-haiku-5-5" and res.model == "claude-haiku-5-5"
    assert os_.created["labels"]["myorch.model"] == "claude-haiku-5-5"
    # profile beats flow, step beats profile
    runner, os_ = make_runner(config_dir, [SandboxStatus(phase="Completed", exit_code=0)])
    await runner.run(req(step=STEP.model_copy(update={"profile": "pm"}), default_model="claude-haiku-5-5"), NullRunContext())
    assert "claude-sonnet-5-5" in os_.created["argv"]
    runner, os_ = make_runner(config_dir, [SandboxStatus(phase="Completed", exit_code=0)])
    await runner.run(req(step=STEP.model_copy(update={"profile": "pm", "model": "claude-opus-5-5"})), NullRunContext())
    assert "claude-opus-5-5" in os_.created["argv"] and "claude-sonnet-5-5" not in os_.created["argv"]
    # no model anywhere -> no flag; scripts never get one
    runner, os_ = make_runner(config_dir, [SandboxStatus(phase="Completed", exit_code=0)])
    await runner.run(req(), NullRunContext())
    assert "--model" not in os_.created["argv"]
    runner, os_ = make_runner(config_dir, [SandboxStatus(phase="Completed", exit_code=0)])
    res = await runner.run(req(step=STEP.model_copy(update={"harness": "script", "model": "claude-opus-5-5"})), NullRunContext())
    assert "--model" not in os_.created["argv"] and res.model is None


async def test_shared_dirs_keep_the_sandbox_name(config_dir):
    # Regression: the shared-dir loop once reused `name`, so every sandbox of a project with a
    # shared cache was called "gradle" and concurrent steps deleted each other's sandboxes.
    runner, os_ = make_runner(config_dir, [SandboxStatus(phase="Completed", exit_code=0)])
    ctx = {**CTX, "project": {**CTX["project"], "shared": ["gradle"]}}
    res = await runner.run(req(context=ctx), NullRunContext())
    assert os_.created["name"] == sandbox_name(KEY) == res.sandbox
    assert "/sandbox/workspace/shared/gradle" in {m.target for m in os_.created["mounts"]}


def test_ask_rules_say_replies_are_text_only():
    from myorch.core.prompt import _interaction_section

    text = _interaction_section(Step(id="a", harness="claude-code", profile="p", interaction="allowed"))
    assert "a reply does not trigger any action" in text and "paste" in text


async def test_hint_after_failure_resumes_session_with_retry_prompt(config_dir):
    runner, os_ = make_runner(config_dir, [SandboxStatus(phase="Completed", exit_code=0)])
    turn2 = KEY.model_copy(update={"turn": 2})
    await runner.run(req(key=turn2, reply="look at the test data first", failure="timed out after 30m"), NullRunContext())
    argv = os_.created["argv"]
    assert argv[argv.index("--resume") + 1] == session_id(KEY)
    prompt = argv[argv.index("-p") + 1]
    assert prompt.startswith("The previous turn failed: timed out after 30m") and "look at the test data first" in prompt


async def test_step_image_beats_profile_and_flow_image(config_dir):
    """A step's own image (e.g. a browser image for e2e) wins; an empty one falls back."""
    for image, expected in (("e2e:img", "e2e:img"), ("", "flow:img")):
        runner, os_ = make_runner(config_dir, [SandboxStatus(phase="Completed", exit_code=0)])
        await runner.run(req(step=STEP.model_copy(update={"image": image})), NullRunContext())
        assert os_.created["image"] == expected


# ---- live output ----------------------------------------------------------------------------------

class FollowStore(FakeStore):
    """follow_log yields the given chunks (with an idle tick between), then waits like `tail -F`."""

    def __init__(self, chunks):
        self.chunks = chunks
        self.followed = None
        self.stopped = False

    async def follow_log(self, run_id, iid, file, name, max_seconds, idle=0.5):
        import asyncio

        self.followed = (run_id, iid, file, name)
        try:
            for c in self.chunks:
                yield c
                yield ""
            while True:
                await asyncio.sleep(0.01)
                yield ""
        finally:
            self.stopped = True


class Sink:
    def __init__(self):
        self.rows = []

    def add_output(self, run_id, step, attempt, turn, text):
        self.rows.append((run_id, step, attempt, turn, text))


async def test_live_output_is_copied_while_the_step_runs(config_dir, monkeypatch):
    import myorch.runner.runner as rr

    monkeypatch.setattr(rr, "OUTPUT_FLUSH", 0)
    store, sink = FollowStore(["→ Bash: ls\n", "  ← a.txt\n"]), Sink()
    os_ = FakeOpenShell([SandboxStatus(phase="Ready", exit_code=None), SandboxStatus(phase="Completed", exit_code=0)])
    runner = StepRunner(store, default_harnesses(), config_dir, openshell=os_, poll_interval=0.02, output=sink,
                        drain_seconds=0.05)
    res = await runner.run(req(), NullRunContext())
    assert res.ok
    assert store.followed == ("r1", "verify.r2", "turn-2-1.log", f"myorch-follow-{sandbox_name(KEY)}")
    assert store.stopped  # the follower container is removed when the step ends
    assert "".join(r[4] for r in sink.rows) == "→ Bash: ls\n  ← a.txt\n"
    assert {r[:4] for r in sink.rows} == {("r1", "verify.r2", 2, 1)}


async def test_live_output_stops_at_the_cap(config_dir, monkeypatch):
    import myorch.runner.runner as rr

    monkeypatch.setattr(rr, "OUTPUT_FLUSH", 0)
    monkeypatch.setattr(rr, "OUTPUT_MAX", 10)
    store, sink = FollowStore(["0123456", "789abc", "never"]), Sink()
    os_ = FakeOpenShell([SandboxStatus(phase="Completed", exit_code=0)])
    runner = StepRunner(store, default_harnesses(), config_dir, openshell=os_, poll_interval=0, output=sink,
                        drain_seconds=0.05)
    await runner.run(req(), NullRunContext())
    text = "".join(r[4] for r in sink.rows)
    assert text.startswith("0123456789") and "never" not in text and "too much output" in text


async def test_claude_steps_stream_through_myorch_stream(config_dir):
    runner, os_ = make_runner(config_dir, [SandboxStatus(phase="Completed", exit_code=0)])
    await runner.run(req(), NullRunContext())
    argv = os_.created["argv"]
    i = argv.index("myorch-claude")
    assert argv[i - 4:i - 1] == ["-o", "pipefail", "-c"] and argv[i + 1] == "/sandbox/logs/turn-2-1.jsonl"
    assert "--output-format" not in argv  # the bash snippet adds it: stream-json with the tool, text without


async def test_a_log_tail_that_cannot_be_read_does_not_fail_the_step(config_dir):
    # Regression: tail -c cut a Chinese character in half, decoding raised, and a step whose agent
    # had succeeded was recorded as failed.
    runner, _ = make_runner(config_dir, [SandboxStatus(phase="Completed", exit_code=0)])

    def broken(*a, **kw):
        raise UnicodeDecodeError("utf-8", b"\xbc", 0, 1, "invalid start byte")

    runner.store.read_log_tail = broken
    res = await runner.run(req(), NullRunContext())
    assert res.ok and res.exit_code == 0 and res.output_tail == ""


def test_the_project_snapshot_mounted_is_the_runs_version(tmp_path):
    runner = StepRunner(FakeStore(), default_harnesses(), tmp_path, openshell=FakeOpenShell([]), poll_interval=0)
    profile = load_profile(Path(__file__).parent.parent, "dev-read")
    req = StepRequest(key=StepKey(run_id="r1", step_id="s", attempt=1), step=STEP, context=CTX,
                      project="blog", project_version="sha256:0123456789abcdef")
    [m] = [m for m in runner.mounts_for(req, profile) if m.target.endswith("/project")]
    assert m.source.subpath == "projects/blog/0123456789abcdef" and m.read_only


# ---- a slow or hung OpenShell must not fail the step (2026-11-06, ck-development-56059024) ------------

class RecordingContext:
    def __init__(self):
        self.beats = []  # (time, detail)

    def heartbeat(self, detail=None):
        import time

        self.beats.append((time.monotonic(), dict(detail or {})))

    def is_cancelled(self):
        return False


def fake_openshell_binary(tmp_path):
    """A stand-in for the `openshell` CLI: the first `sandbox get` hangs, later ones say Completed."""
    script = tmp_path / "openshell"
    script.write_text(
        "#!/bin/sh\n"
        'case "$2" in\n'
        '  get) n=$(cat "$0.count" 2>/dev/null || echo 0); echo $((n + 1)) > "$0.count"\n'
        '       [ "$n" = 0 ] && exec sleep 30\n'
        "       echo '{\"phase\": \"Completed\", \"exit_code\": 0}' ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    script.chmod(0o755)
    return script


async def test_a_status_poll_that_times_out_is_retried_and_the_step_succeeds(config_dir, tmp_path, monkeypatch):
    import myorch.runner.openshell as osh
    from myorch.runner.openshell import OpenShellCLI

    monkeypatch.setattr(osh, "STATUS_TIMEOUT", 0.3)
    binary = fake_openshell_binary(tmp_path)
    cli = OpenShellCLI(str(binary))

    async def no_supervisor_log(name):
        return ""

    cli.supervisor_log = no_supervisor_log  # the real one asks docker
    runner = StepRunner(FakeStore(), default_harnesses(), config_dir, openshell=cli, poll_interval=0)
    ctx = RecordingContext()
    res = await runner.run(req(), ctx)
    assert res.ok and res.exit_code == 0 and res.error is None
    assert (tmp_path / "openshell.count").read_text().strip() == "2"  # polled again after the hung call was killed
    phases = [d["phase"] for _, d in ctx.beats if "phase" in d]
    assert "Unknown" in phases and phases[-1] == "Completed"


async def test_heartbeats_continue_while_a_status_call_hangs(config_dir):
    import asyncio
    import time

    class HangingOpenShell(FakeOpenShell):
        returned_at = None

        async def status(self, name):
            await asyncio.sleep(0.5)  # a hung `sandbox get`, as seen by the runner
            self.returned_at = time.monotonic()
            return SandboxStatus(phase="Completed", exit_code=0)

    os_ = HangingOpenShell([])
    runner = StepRunner(FakeStore(), default_harnesses(), config_dir, openshell=os_, poll_interval=0,
                        heartbeat_interval=0.02)
    ctx = RecordingContext()
    started = time.monotonic()
    res = await runner.run(req(), ctx)
    assert res.ok
    during = [t for t, _ in ctx.beats if started < t < os_.returned_at]
    assert len(during) >= 10  # the timer kept beating; before, the first beat came after status returned
    assert max(b - a for a, b in zip(during, during[1:])) < 0.2


async def test_unknown_status_backs_off_up_to_a_bound(config_dir, monkeypatch):
    import myorch.runner.runner as rr

    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    os_ = FakeOpenShell([None] * 6 + [SandboxStatus(phase="Completed", exit_code=0)])
    runner = StepRunner(FakeStore(), default_harnesses(), config_dir, openshell=os_, poll_interval=3)
    monkeypatch.setattr(rr.asyncio, "sleep", fake_sleep)
    status = await runner._wait("sb", 3600, RecordingContext())
    assert status.finished
    assert sleeps == [6, 12, 24, 30, 30, 30]


async def test_a_create_that_fails_or_times_out_deletes_the_sandbox(config_dir):
    from myorch.runner.openshell import OpenShellTimeout

    class SlowCreate(FakeOpenShell):
        async def create_detached(self, **kw):
            raise OpenShellTimeout("openshell sandbox create --name did not return within 120s; killed")

    os_ = SlowCreate([])
    runner = StepRunner(FakeStore(), default_harnesses(), config_dir, openshell=os_, poll_interval=0)
    res = await runner.run(req(), NullRunContext())
    assert not res.ok and "did not return" in res.error
    assert os_.deleted == [sandbox_name(KEY), sandbox_name(KEY)]  # the leftover check, then the cleanup


# ---- every attempt has its own turn logs -------------------------------------------------------------

async def test_a_retry_writes_its_own_turn_logs(config_dir):
    store = FakeStore()
    seen = []
    for attempt in (1, 2):
        os_ = FakeOpenShell([SandboxStatus(phase="Completed", exit_code=0)])
        runner = StepRunner(store, default_harnesses(), config_dir, openshell=os_, poll_interval=0)
        await runner.run(req(key=KEY.model_copy(update={"attempt": attempt})), NullRunContext())
        argv = os_.created["argv"]
        wrapper_ = argv[argv.index("-c") + 1]
        seen.append((wrapper_.rsplit("> ", 1)[1].split()[0], argv[argv.index("myorch-claude") + 1], store.tail_of))
    assert seen == [
        ("/sandbox/logs/turn-1-1.log", "/sandbox/logs/turn-1-1.jsonl", "turn-1-1.log"),
        ("/sandbox/logs/turn-2-1.log", "/sandbox/logs/turn-2-1.jsonl", "turn-2-1.log"),
    ]


def test_sandbox_names_say_flow_and_step():
    def name(run, step, attempt=1, turn=1):
        return sandbox_name(StepKey(run_id=run, step_id=step, attempt=attempt, turn=turn))

    assert name("ck-development-19c43a9b", "develop").startswith("ck-dev-deve-")
    assert name("ck-development-19c43a9b", "review.r1.x2").startswith("ck-dev-revi-")
    assert name("blog-development-0a1b2c3d", "fix").startswith("blog-dev-fix-")
    assert name("ck-learn-11112222", "analyze").startswith("ck-lrn-anal-")
    assert name("mo-development-abcdef12", "verify").startswith("mo-dev-veri-")
    assert name("onboard-12345678", "analyze").startswith("onb-anal-")
    # unique per run, step replica, attempt and turn: a start deletes a leftover of the same name first
    names = {name(r, s, a, t) for r in ("ck-development-11111111", "ck-development-22222222")
             for s in ("review.r1.x1", "review.r1.x2", "review.r2.x1") for a in (1, 2) for t in (1, 2, 3)}
    assert len(names) == 2 * 3 * 2 * 3
    assert all(len(n) <= 19 and re.fullmatch(r"[a-z0-9-]+", n) for n in names)


def test_attempt_and_turn_are_recovered_from_a_name():
    key = StepKey(run_id="ck-development-19c43a9b", step_id="develop", attempt=3, turn=1)
    older = key.model_copy(update={"attempt": 1, "turn": 2})
    assert attempt_turn_of(key, sandbox_name(older)) == (1, 2)
    assert attempt_turn_of(key, "ck-dev-deve-00000") is None


def test_sandbox_labels_say_the_project():
    """Flows are shared, so the sandbox name no longer says the project: the label does."""
    from myorch.runner.runner import sandbox_labels
    key = StepKey(run_id="development-1a2b3c4d", step_id="develop", attempt=1, turn=2)
    assert sandbox_labels(key, "m", "mdm")["myorch.project"] == "mdm"
    assert "myorch.project" not in sandbox_labels(key)
