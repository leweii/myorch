from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from myorch import project as projects
from myorch.core.flow import Step, render_template
from myorch.core.manifest import Manifest
from myorch.core.run import KnowledgeRef
from myorch.core.prompt import (
    IN_DIR, OUT_DIR, WORKSPACE, build_interrupted_prompt, build_prompt, build_reply_prompt, build_retry_prompt,
)
from myorch.core.types import RunContext, StepKey, StepResult
from myorch.runner.harnesses import HarnessAdapter, HarnessTask
from myorch.runner.openshell import Mount, OpenShellCLI, OpenShellError
from myorch.runner.profiles import Profile, load_profile
from myorch.storage.base import ArtifactStore, MountSource, turn_log

LOGS_DIR = "/sandbox/logs"
TOOLS_DIR = "/sandbox/bin"
log = logging.getLogger("myorch.runner")
_SESSION_NS = uuid.UUID("6f1c2a5e-3b7d-4c1e-9a0f-6d2b8e4f7a10")


class StepRequest(BaseModel):
    """Everything needed to run one turn of one step instance, independent of the engine."""

    key: StepKey
    step: Step
    context: dict[str, Any]  # template/expression context (inputs, run, loop, replica, steps)
    upstream: dict[str, Manifest] = Field(default_factory=dict)  # instance id -> manifest
    project: str | None = None  # project name: its snapshot is mounted at workspace/project
    project_version: str = ""  # the snapshot version the run uses ("": the newest)
    default_image: str | None = None  # the flow's image
    default_model: str | None = None  # the flow's model
    reply: str | None = None  # the user's answer (or hint, after a failure) on continuation turns
    failure: str | None = None  # why the previous turn failed, when this turn retries it with a hint
    knowledge: list[KnowledgeRef] = Field(default_factory=list)  # the run's knowledge sources (Mandate)
    # A retry after the platform stopped the previous attempt (core.run.continued_session): the first
    # of the interrupted attempts in a row. The runner continues the newest of their agent sessions
    # that exists (or starts a new one), and tells the agent why. None: an ordinary attempt.
    continues: int | None = None
    interrupted: str | None = None  # why the platform stopped the previous attempt
    session_id: str | None = None  # later turns of a continuing attempt: the session turn 1 used (StepResult)


def wrapper(attempt: int, turn: int) -> str:
    # Run from the repo once it is checked out (so CLAUDE.md and repo skills apply), else from
    # the workspace. Output goes to the shared volume: `openshell logs` only carries supervisor
    # logs. stdin is closed so harnesses do not wait for piped input (claude -p waits 3s).
    # The log is named per attempt and turn, so a retry never overwrites an earlier attempt's log.
    return (
        f'if [ -d {WORKSPACE}/repo/.git ]; then cd {WORKSPACE}/repo; else cd {WORKSPACE}; fi; '
        f'exec "$@" < /dev/null > {LOGS_DIR}/{turn_log(attempt, turn)} 2>&1'
    )


# Flow roles in sandbox names: development -> dev, and the second part of an older flow id (ck-development -> dev).
_ROLE_CODES = {"development": "dev", "review": "rev", "learn": "lrn", "postmortem": "pm", "sweep": "swp",
               "eval": "evl", "onboard": "onb"}
_RUN_SUFFIX = re.compile(r"-[0-9a-f]{8}$")


def _flow_code(run_id: str) -> str:
    """The run's flow, shortened for a sandbox name: development -> dev, onboard -> onb,
    eval-validate-checks -> eval-val; runs of the older per-project flows: ck-development -> ck-dev.
    Flows are shared, so the name no longer says the project: the sandbox labels do."""
    flow = _RUN_SUFFIX.sub("", run_id)
    parts = [p for p in re.split(r"[^a-z0-9]+", flow.lower()) if p]
    if not parts:
        return "run"
    if len(parts) == 1:
        return _ROLE_CODES.get(parts[0], parts[0][:4])
    return f"{parts[0][:4]}-{_ROLE_CODES.get(parts[1], parts[1][:3])}"


def sandbox_name(key: StepKey) -> str:
    """<flow>-<step>-<hash>, e.g. dev-deve-3f2a9 for development's develop step, so a person can
    tell from `docker ps` which flow and step a sandbox belongs to. OpenShell caps sandbox
    names at 19 characters, so flow and step are shortened; the full ids travel as sandbox labels
    (sandbox_labels). The hash covers run, step, attempt and turn: two live sandboxes never share a
    name (a start deletes a leftover of the same name first, so a clash would remove another run's)."""
    step = re.sub(r"[^a-z0-9]", "", key.step_id.split(".")[0].lower())[:4] or "step"
    digest = hashlib.sha1(f"{key.run_id}/{key.step_id}/{key.attempt}/{key.turn}".encode()).hexdigest()[:5]
    name = f"{_flow_code(key.run_id)}-{step}-{digest}"
    if len(name) > 19:
        raise ValueError(f"sandbox name {name!r} exceeds OpenShell's 19 character limit")
    return name


def attempt_turn_of(key: StepKey, name: str, max_turns: int = 20) -> tuple[int, int] | None:
    """Which attempt and turn of this step a sandbox name belongs to (names carry a hash, not the
    numbers): None when it is not one of this step's."""
    for attempt in range(1, key.attempt + 1):
        for turn in range(1, max_turns + 1):
            if sandbox_name(key.model_copy(update={"attempt": attempt, "turn": turn})) == name:
                return attempt, turn
    return None


def sandbox_labels(key: StepKey, model: str | None = None, project: str | None = None) -> dict[str, str]:
    """The full ids behind a sandbox's short name. The flows are shared, so the project is not in the
    name: it is the myorch.project label."""
    labels = {
        "myorch.run": key.run_id,
        "myorch.step": key.step_id,
        "myorch.attempt": str(key.attempt),
        "myorch.turn": str(key.turn),
    }
    if model:
        labels["myorch.model"] = model
    if project:
        labels["myorch.project"] = project
    return labels


def session_id(key: StepKey) -> str:
    """Same for every turn of an attempt, different across attempts. A retry after a platform
    interruption may continue an earlier attempt's session instead (StepRunner._session)."""
    return str(uuid.uuid5(_SESSION_NS, f"{key.run_id}/{key.step_id}/{key.attempt}"))


def session_dir(key: StepKey) -> str:
    return f"{WORKSPACE}/.claude/{key.step_id}"


def _sub(src: MountSource, rel: str) -> MountSource:
    """A directory inside a mount source (the run's .myorch/, .claude/<instance>/)."""
    return src.model_copy(update={"subpath": f"{src.subpath}/{rel}"})


MAX_OBSERVED = 20


def observed_runs(req: StepRequest) -> list[str]:
    """Run ids from step.observe_runs, validated: they become mount paths."""
    if not req.step.observe_runs:
        return []
    from myorch.core.types import check_id

    raw = render_template(req.step.observe_runs, req.context)
    ids = list(dict.fromkeys(r.strip() for r in raw.split(",") if r.strip()))
    if len(ids) > MAX_OBSERVED:
        raise ValueError(f"step {req.step.id!r} observes {len(ids)} runs; at most {MAX_OBSERVED}")
    if req.key.run_id in ids:
        raise ValueError(f"step {req.step.id!r} cannot observe its own run")
    return [check_id(r, "observed run id") for r in ids]


def harness_env(req: StepRequest, repo_writable: bool) -> list[str]:
    ctx = req.context
    env = {
        "MYORCH_RUN_ID": req.key.run_id,
        "MYORCH_INSTANCE": req.key.step_id,
        "MYORCH_STEP": req.step.id,
        "MYORCH_BRANCH": ctx.get("run", {}).get("branch", ""),
        "MYORCH_WORKSPACE": WORKSPACE,
        "MYORCH_ROUND": str(ctx.get("loop", {}).get("round", 0)),
        "MYORCH_REPLICA": str(ctx.get("replica", 0)),
        "MYORCH_ATTEMPT": str(req.key.attempt),
        "MYORCH_PROJECT": req.project or "",
        "MYORCH_INTERACTION": req.step.interaction,  # myorch-ask only works when "allowed"
    }
    for k, v in (ctx.get("inputs") or {}).items():
        env[f"MYORCH_INPUT_{k.upper()}"] = str(v)
    project = ctx.get("project") or {}
    if project:  # scripts read project facts from the environment rather than from templated text
        ident = project.get("identity") or {}
        env.update({
            "MYORCH_PROJECT_REPO": project.get("repo", ""),
            "MYORCH_PROJECT_BASE": project.get("base", ""),
            "MYORCH_PROJECT_SUBMODULES": " ".join(project.get("submodules") or []),
            "MYORCH_PROJECT_PR_TEMPLATE": project.get("pr_template", ""),
            "MYORCH_GIT_NAME": ident.get("git_name", ""),
            "MYORCH_GIT_EMAIL": ident.get("git_email", ""),
        })
    if not repo_writable:
        env["GIT_OPTIONAL_LOCKS"] = "0"  # read-only repo: git must not try to refresh the index
    return [f"{k}={v}" for k, v in env.items()]


KNOWLEDGE_ROOT = f"{WORKSPACE}/knowledge"  # /sandbox/workspace/knowledge/<name>/ (myorch/knowledge.py)
MCP_CONFIG = ".myorch/knowledge-mcp.json"  # in the run's workspace; written per run, no secrets in it


def step_knowledge(req: StepRequest, is_agent: bool) -> list[KnowledgeRef]:
    """The run's knowledge sources this step sees: agent steps by default; `knowledge: true`
    adds scripts, `false` removes the step. MCP sources only reach agents (they need a client)."""
    wanted = req.step.knowledge if req.step.knowledge is not None else is_agent
    if not wanted:
        return []
    return [k for k in req.knowledge if k.kind != "mcp" or is_agent]


def shared_dirs(req: StepRequest) -> list[str]:
    """The step's own shared dirs plus the project's (e.g. a build cache every step may use)."""
    return list(dict.fromkeys([*req.step.shared, *((req.context.get("project") or {}).get("shared") or [])]))


OPTIONAL_ROLES: dict[str, str | None] = {"artifacts": None, "jira": None}  # used only when the project maps them


def resolve_providers(roles: list[str], project: Mapping[str, Any] | None) -> list[str]:
    """Profiles name provider *roles* (github, artifacts, jira, claude); a project maps them to instances.

    A role mapped to null is not used by that project. Optional roles (artifacts, jira) are dropped
    unless the project maps them; any other unmapped role is taken as the instance name.
    """
    mapping = {**OPTIONAL_ROLES, **((project or {}).get("providers") or {})}
    out = []
    for role in roles:
        inst = mapping[role] if role in mapping else role
        if inst:
            out.append(inst)
    return list(dict.fromkeys(out))


class OutputSink(Protocol):
    def add_output(self, run_id: str, step: str, attempt: int, turn: int, text: str) -> None: ...


OUTPUT_FLUSH = 0.5  # seconds: live output is written in batches at most this often
OUTPUT_MAX = 2_000_000  # characters per turn; the rest is only in the log file
OUTPUT_CUT = "\n… (too much output: the rest is not shown live. Read the full log when the step has ended.)\n"


def follower_name(sandbox: str) -> str:
    return f"myorch-follow-{sandbox}"


HEARTBEAT_INTERVAL = 10.0  # seconds; the activity's heartbeat_timeout is 60 s
STORE_TRIES = 3  # reading the session files before a turn
STOP_CHECKS = 4  # listing the step's sandboxes before continuing an interrupted attempt's session
POLL_BACKOFF_MAX = 30.0  # seconds between status polls at most, while the phase cannot be read


class StepRunner:
    def __init__(
        self,
        store: ArtifactStore,
        harnesses: Mapping[str, HarnessAdapter],
        config_dir: Path,
        openshell: OpenShellCLI | None = None,
        poll_interval: float = 3.0,
        projects_root: Path | None = None,
        output: OutputSink | None = None,
        drain_seconds: float = 1.5,
        heartbeat_interval: float = HEARTBEAT_INTERVAL,
        store_retry_delay: float = 1.0,
    ) -> None:
        self.store = store
        self.heartbeat_interval = heartbeat_interval
        self.store_retry_delay = store_retry_delay
        self.output = output  # live output of running steps (EventLog's output table); None: off
        self.drain_seconds = drain_seconds
        self.harnesses = harnesses
        self.config_dir = config_dir
        self.projects_root = projects_root
        self.openshell = openshell or OpenShellCLI()
        self.poll_interval = poll_interval

    def mounts_for(self, req: StepRequest, profile: Profile, knowledge: list[KnowledgeRef] = (),
                   project_src: MountSource | None = None) -> list[Mount]:
        run, iid = req.key.run_id, req.key.step_id
        ws = self.store.workspace_source(run)
        mounts = [
            Mount(source=ws, target=WORKSPACE, read_only=False),
            # The run's records stay readable but no step can change another's: .myorch/ (flow and
            # mandate snapshots, every step's manifest and logs) and .claude/ (every instance's
            # sessions) are read-only overlays; a step writes its own out/ and logs/ through the
            # mounts below and its own session dir through its own overlay. A session or manifest
            # another step rewrote would be resumed or read as if it were true.
            Mount(source=_sub(ws, ".myorch"), target=f"{WORKSPACE}/.myorch", read_only=True),
            Mount(source=_sub(ws, ".claude"), target=f"{WORKSPACE}/.claude", read_only=True),
            Mount(source=_sub(ws, f".claude/{iid}"), target=session_dir(req.key), read_only=False),
            Mount(source=self.store.out_source(run, iid), target=OUT_DIR, read_only=False),
            Mount(source=self.store.logs_source(run, iid), target=LOGS_DIR, read_only=False),
        ]
        if profile.repo == "ro":
            mounts.append(Mount(source=self.store.repo_source(run), target=f"{WORKSPACE}/repo", read_only=True))
        if req.project:
            src = project_src or self.store.project_source(req.project, req.project_version)
            mounts.append(Mount(source=src, target=f"{WORKSPACE}/project", read_only=True))
        for name in shared_dirs(req):
            if not req.project:
                raise ValueError(f"step {req.step.id!r} uses shared dirs, which need a project")
            mounts.append(Mount(source=self.store.shared_source(req.project, name), target=f"{WORKSPACE}/shared/{name}",
                                read_only=False))
        for up in req.upstream:
            mounts.append(Mount(source=self.store.out_source(run, up), target=f"{IN_DIR}/{up}", read_only=True))
        for other in observed_runs(req):
            mounts.append(Mount(source=self.store.workspace_source(other), target=f"{WORKSPACE}/observed/{other}",
                                read_only=True))
        for k in knowledge:  # snapshots, read-only; mcp sources are reached over the network instead
            if k.kind != "mcp":
                mounts.append(Mount(source=self.store.knowledge_source(k.name, k.version),
                                    target=f"{KNOWLEDGE_ROOT}/{k.name}", read_only=True))
        # Helper commands for every step (myorch-checks); myorch-ask refuses unless the step may ask
        # (MYORCH_INTERACTION, see harness_env).
        mounts.append(Mount(source=self.store.tools_source(), target=TOOLS_DIR, read_only=True))
        return mounts

    async def run(self, req: StepRequest, ctx: RunContext) -> StepResult:
        """Run one turn. Turn 1 sends the full prompt; later turns resume the session with the reply.

        Heartbeats come from a timer for the whole turn, not from the steps that wait on OpenShell or
        storage: a slow call must not look like a dead worker (2026-11-06, ck-development-56059024: a hung
        `sandbox get` stopped the heartbeats and Temporal cancelled a healthy step). A really stuck turn
        still ends: every OpenShell call has a timeout, and the activity has start_to_close."""
        beat: dict[str, Any] = {"sandbox": sandbox_name(req.key), "phase": "Preparing"}
        heart = asyncio.create_task(self._heartbeat(ctx, beat))
        try:
            return await self._turn(req, ctx, beat)
        finally:
            heart.cancel()

    async def _heartbeat(self, ctx: RunContext, beat: dict[str, Any]) -> None:
        while True:
            ctx.heartbeat(beat)
            await asyncio.sleep(self.heartbeat_interval)

    async def _turn(self, req: StepRequest, ctx: RunContext, beat: dict[str, Any]) -> StepResult:
        key, step = req.key, req.step
        if step.harness is None:
            raise ValueError(f"step {step.id!r} has no harness to run")
        adapter = self.harnesses.get(step.harness)
        if adapter is None:
            raise ValueError(f"unknown harness {step.harness!r}")
        name = sandbox_name(key)
        if key.turn > 1 and not adapter.supports_resume:
            return StepResult(
                step_id=key.step_id, ok=False, exit_code=None, sandbox=name, stopped="harness",
                error=f"harness {adapter.name!r} does not support continuing a session",
            )
        profile = load_profile(self.config_dir, step.profile or "")
        repo_writable = profile.repo == "rw"
        # Script harnesses have no model; for agents: step > profile > flow > harness default.
        model = (step.model or profile.model or req.default_model) if adapter.model_providers() else None
        knowledge = step_knowledge(req, bool(adapter.model_providers()))
        mcp = [k for k in knowledge if k.kind == "mcp"]
        if mcp:
            from myorch.knowledge import mcp_spec

            await asyncio.to_thread(self.store.write_run_file, key.run_id, MCP_CONFIG, json.dumps(mcp_spec(mcp), indent=1))

        sid, resume = await self._session(req, adapter)
        if key.turn > 1:
            prompt = (build_retry_prompt(step, req.failure, req.reply or "") if req.failure is not None
                      else build_reply_prompt(step, req.reply or ""))
        elif resume:  # continues the session of an attempt the platform stopped
            prompt = build_interrupted_prompt(step, req.interrupted or "reason unknown")
        else:
            prompt = build_prompt(step, req.context, req.upstream, repo_writable, knowledge, interrupted=req.interrupted)
        task = HarnessTask(
            prompt=prompt,
            instruction=render_template(step.prompt, req.context),
            turn=key.turn,
            session_id=sid,
            resume=resume,
            session_dir=session_dir(key),
            reply=req.reply,
            model=model,
            mcp_config=f"{WORKSPACE}/{MCP_CONFIG}" if mcp else None,
            raw_log=f"{LOGS_DIR}/{turn_log(key.attempt, key.turn, 'jsonl')}",
        )
        argv = [
            "env", *harness_env(req, repo_writable),
            "sh", "-c", wrapper(key.attempt, key.turn), "myorch", *adapter.build_command(task),
        ]
        providers = resolve_providers([*profile.providers, *adapter.model_providers()], req.context.get("project"))
        providers += [k.provider for k in mcp if k.provider not in providers]  # adds the read-only token at the gateway
        policy = profile.policy
        if policy == "project":
            project = req.context.get("project") or {}
            if not project.get("policy") or self.projects_root is None:
                policy = None  # the project needs no network policy (gateway default)
            else:
                policy = str(projects.pack_file(self.projects_root, project["name"], project["policy"]))
        for shared in shared_dirs(req):  # not `name`: that is the sandbox's
            await asyncio.to_thread(self.store.ensure_shared, req.project or "", shared)

        project_src = (await asyncio.to_thread(self.store.project_source, req.project, req.project_version)
                       if req.project else None)  # resolving the version reads the volume

        await self.openshell.delete(name)  # leftover from a crashed worker, if any
        try:
            await self.openshell.create_detached(
                name=name,
                image=step.image or profile.image or req.default_image or adapter.default_image,
                argv=argv,
                mounts=self.mounts_for(req, profile, knowledge, project_src),
                providers=providers,
                policy=policy,
                labels=sandbox_labels(key, model, req.project),
            )
        except OpenShellError as e:
            # A create that timed out may still have made the sandbox: it must not run on beside a retry.
            await self.openshell.delete(name)
            return StepResult(step_id=key.step_id, ok=False, exit_code=None, sandbox=name, model=model, error=str(e),
                              stopped="sandbox", session_id=sid)

        follow = asyncio.create_task(self._follow(key, name, step.limits.timeout_seconds)) if self.output else None
        try:
            status = await self._wait(name, step.limits.timeout_seconds, ctx, beat)
        except asyncio.CancelledError:
            await self._stop_follow(follow, drain=False)
            await self._remove(key, name)
            raise
        except TimeoutError:
            await self._stop_follow(follow, drain=False)
            await self._remove(key, name)
            return StepResult(
                step_id=key.step_id, ok=False, exit_code=None, sandbox=name, model=model,
                error=f"timed out after {step.limits.timeout}", stopped="timeout", session_id=sid,
                output_tail=await self._tail(key),
            )

        await self._stop_follow(follow, drain=True)
        await self._remove(key, name)
        tail = await self._tail(key)
        if status.exit_code is None:
            return StepResult(
                step_id=key.step_id, ok=False, exit_code=None, sandbox=name, model=model, output_tail=tail,
                error=f"sandbox ended in phase {status.phase} without an exit code (provisioning failure?)",
                stopped="sandbox", session_id=sid,
            )
        return StepResult(
            step_id=key.step_id, ok=status.exit_code == 0, exit_code=status.exit_code,
            sandbox=name, model=model, output_tail=tail, session_id=sid,
        )

    async def _session(self, req: StepRequest, adapter: HarnessAdapter) -> tuple[str, bool]:
        """The session id this turn uses, and whether it continues an existing session.

        Ordinarily: the attempt's own session, created on turn 1 and continued on later turns. A
        retry after a platform interruption (req.continues) continues, on turn 1, the newest session
        among the interrupted attempts that has a transcript, once their sandboxes are confirmed gone
        (a sandbox still running would keep writing the same transcript); otherwise it starts its own,
        and the prompt still says what happened. Later turns use the session turn 1 reported
        (req.session_id). Turn 1 sets aside a session that already has the attempt's own id: it is left
        from an earlier visit that numbered its attempts the same (runs started before
        Instance.attempts_before), and the harness would refuse to create the id again.

        When the session files cannot be read, this raises (after a few tries) instead of guessing: the
        activity fails, which the engine treats as an interruption, rather than a turn that fails on a
        session that does not exist, or collides with one that does."""
        key = req.key
        own = session_id(key)
        if key.turn > 1 and req.session_id:
            return req.session_id, True  # what turn 1 used
        if key.turn > 1 and req.continues is None:
            return own, True  # the usual case: nothing to look up
        name_of = getattr(adapter, "session_file", None)
        earlier: list[str] = []
        if req.continues is not None and adapter.supports_resume:
            earlier = [session_id(key.model_copy(update={"attempt": a}))
                       for a in range(key.attempt - 1, req.continues - 1, -1)]  # newest first
        files = {sid: name_of(sid) for sid in [own, *earlier]} if name_of else {}
        files = {sid: f for sid, f in files.items() if f}
        found: set[str] = set()
        if files:
            found = await self._store_call(self.store.session_files, key.run_id, key.step_id, list(files.values()))
        if key.turn > 1:  # a continuing attempt whose turn-1 session is not known (e.g. a hint after a lost activity)
            for sid in [own, *earlier]:
                if files.get(sid) in found:
                    return sid, True
            return own, True
        if files.get(own) in found:
            log.warning("%s attempt %d: a session with its id exists already; setting it aside", key.step_id, key.attempt)
            found.discard(files[own])
            entries = getattr(adapter, "session_entries", None)
            for entry in (entries(own) if entries else [files[own]]):
                await self._store_call(self.store.set_aside_session_file, key.run_id, key.step_id, entry)
        if req.continues is None:
            return own, False
        gone = await self._stop_earlier(key)
        for sid in earlier:
            if files.get(sid) in found:
                if not gone:
                    log.warning("%s attempt %d: the interrupted attempt's sandbox may still run; starting a new "
                                "session instead of continuing %s", key.step_id, key.attempt, sid)
                    break
                log.info("%s attempt %d continues the session %s of an interrupted attempt", key.step_id,
                         key.attempt, sid)
                return sid, True
        return own, False

    async def _store_call(self, fn, *args):
        """A storage call that decides which session to use: retried a few times, then raised."""
        for n in range(STORE_TRIES):
            try:
                return await asyncio.to_thread(fn, *args)
            except Exception as e:  # noqa: BLE001
                if n == STORE_TRIES - 1:
                    raise RuntimeError(f"reading the agent sessions failed: {e}") from e
                log.warning("reading the agent sessions failed (%s); trying again", e)
                await asyncio.sleep(self.store_retry_delay * 2 ** n)

    async def _stop_earlier(self, key: StepKey) -> bool:
        """Delete every other sandbox of this step instance (an interrupted attempt's may still run: the
        activity was lost, and its cleanup is best effort) and confirm none is left. False when that
        cannot be confirmed: the gateway does not answer, or a sandbox does not go away."""
        labels = {"myorch.run": key.run_id, "myorch.step": key.step_id}
        own = sandbox_name(key)
        for n in range(STOP_CHECKS):
            names = await self.openshell.list_names(labels)
            if names is not None:
                stale = [x for x in names if x != own]
                if not stale:
                    return True
                for x in stale:
                    at = attempt_turn_of(key, x)
                    if at:  # keep its supervisor log, like a normal cleanup
                        await self._remove(key.model_copy(update={"attempt": at[0], "turn": at[1]}), x)
                    else:
                        await self.openshell.delete(x)
            if n < STOP_CHECKS - 1:
                await asyncio.sleep(min(self.poll_interval * 2 ** n, POLL_BACKOFF_MAX))
        return False

    async def _follow(self, key: StepKey, sandbox: str, timeout_s: int) -> None:
        """Copy the turn's log to the live output while the step runs, in batches. Best effort: a
        failure here is logged and never fails the step."""
        assert self.output is not None
        buf: list[str] = []
        sent, last = 0, time.monotonic()

        async def flush() -> None:
            nonlocal buf, last
            text, buf, last = "".join(buf), [], time.monotonic()
            if text:
                await asyncio.to_thread(self.output.add_output, key.run_id, key.step_id, key.attempt, key.turn, text)

        try:
            async for chunk in self.store.follow_log(key.run_id, key.step_id, turn_log(key.attempt, key.turn),
                                                     follower_name(sandbox), timeout_s + 120, idle=OUTPUT_FLUSH):
                if sent >= OUTPUT_MAX:
                    continue  # keep reading so the container is not blocked; drop the rest
                if chunk:
                    chunk = chunk[: OUTPUT_MAX - sent]
                    sent += len(chunk)
                    buf.append(chunk + (OUTPUT_CUT if sent >= OUTPUT_MAX else ""))
                if buf and (time.monotonic() - last >= OUTPUT_FLUSH or sum(map(len, buf)) > 64_000):
                    await flush()
        except asyncio.CancelledError:
            await asyncio.shield(flush())
            raise
        except Exception:
            log.exception("live output of %s/%s stopped", key.run_id, key.step_id)
        await flush()

    async def _stop_follow(self, task: asyncio.Task | None, drain: bool) -> None:
        """Stop the follower. With `drain`, first give it a moment to read the last lines."""
        if task is None:
            return
        if drain and not task.done():
            await asyncio.sleep(self.drain_seconds)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("live output follower failed")

    async def _wait(self, name: str, timeout_s: int, ctx: RunContext, beat: dict[str, Any] | None = None):
        """Poll until the sandbox finishes. A poll that fails or times out (status None) means "unknown,
        ask again", never a failed step; while it stays unknown the polls back off up to POLL_BACKOFF_MAX,
        so an overloaded gateway is not hammered. The step's own timeout still bounds the wait."""
        deadline = time.monotonic() + timeout_s
        unknown = 0  # polls in a row that could not read the phase
        while True:
            status = await self.openshell.status(name)
            unknown = 0 if status else unknown + 1
            phase = status.phase if status else "Unknown"
            if beat is not None:
                beat.update(sandbox=name, phase=phase)
            ctx.heartbeat({"sandbox": name, "phase": phase})
            if status and status.finished:
                return status
            if ctx.is_cancelled():
                raise asyncio.CancelledError()
            if time.monotonic() > deadline:
                raise TimeoutError
            if unknown:
                log.info("status of sandbox %s unknown (%d in a row); polling again", name, unknown)
            await asyncio.sleep(min(self.poll_interval * 2 ** min(unknown, 4), POLL_BACKOFF_MAX))

    async def _tail(self, key: StepKey) -> str:
        """The end of this turn's log, for the record. Reading it must never fail the step."""
        try:
            return await asyncio.to_thread(self.store.read_log_tail, key.run_id, key.step_id,
                                           file=turn_log(key.attempt, key.turn))
        except Exception as e:  # noqa: BLE001
            log.warning("reading the log tail of %s failed: %s", key.step_id, e)
            return ""

    async def cleanup(self, key: StepKey) -> None:
        await self._remove(key, sandbox_name(key))

    async def _remove(self, key: StepKey, name: str) -> None:
        """Delete a step's sandbox, keeping its supervisor log first (the sandbox takes it along)."""
        await self._keep_supervisor_log(key, name)
        await self.openshell.delete(name)

    async def _keep_supervisor_log(self, key: StepKey, name: str) -> None:
        """Save the sandbox supervisor's log next to the step's turn logs before the sandbox is deleted:
        it is the evidence for blocked egress, DNS and credential problems. Never fails the cleanup."""
        try:
            text = await self.openshell.supervisor_log(name)
            if text:
                rel = f".myorch/steps/{key.step_id}/logs/supervisor-{key.attempt}-{key.turn}.log"
                await asyncio.to_thread(self.store.write_run_file, key.run_id, rel, text)
        except Exception as e:  # noqa: BLE001
            log.warning("keeping the supervisor log of %s failed: %s", key.step_id, e)
