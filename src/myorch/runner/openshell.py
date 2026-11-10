from __future__ import annotations

import asyncio
import logging
import re
import json

from pydantic import BaseModel

from myorch.storage.base import MountSource

log = logging.getLogger("myorch.openshell")

# How long one CLI call may take before it is killed. A loaded gateway can leave `sandbox get` hanging
# (2026-11-06: slow statements, seconds to get a database connection), and a call without a limit blocked
# the step until Temporal cancelled it. Creating returns once the sandbox is registered (--detach; cold
# start to Ready is about 2.4 s), so its limit only guards against a stuck gateway.
STATUS_TIMEOUT = 30.0  # sandbox get, docker ps/logs
DELETE_TIMEOUT = 60.0
CREATE_TIMEOUT = 120.0


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Token shapes that must never reach a run's workspace, where later steps' agents can read them. The
# supervisor logs placeholders, not secrets; this is a second line in case a log line ever carries one.
SECRETS = [
    (re.compile(r"\b(gh[opsu]_|github_pat_)[A-Za-z0-9_]{20,}"), r"\1***"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "xox***"),
    (re.compile(r"\bxapp-[A-Za-z0-9-]{10,}"), "xapp-***"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"), "sk-ant-***"),
    (re.compile(r"(?i)\b(authorization:?\s*(?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1***"),
    (re.compile(r"(?i)([?&](?:token|access_token|api_key|apikey|key|password|secret)=)[^&\s\"']+"), r"\1***"),
]


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def redact(text: str) -> str:
    for pattern, repl in SECRETS:
        text = pattern.sub(repl, text)
    return text


class Mount(BaseModel, frozen=True):
    source: MountSource
    target: str
    read_only: bool


class SandboxStatus(BaseModel):
    phase: str  # observed: Provisioning, Ready, Completed, Error
    exit_code: int | None

    @property
    def finished(self) -> bool:
        return self.phase in ("Completed", "Error")


def parse_status(name: str, out: str) -> SandboxStatus | None:
    """`sandbox get -o json` output as a status. None ("unknown, ask again") when it is not the JSON
    object we expect: an overloaded gateway can answer exit 0 with an error text or a cut-off body, and
    that must not fail the step any more than a call that timed out."""
    try:
        data = json.loads(out)
        if not isinstance(data, dict):
            raise ValueError(f"expected a JSON object, got {type(data).__name__}")
        code = data.get("exit_code")
        return SandboxStatus(phase=str(data.get("phase") or ""), exit_code=None if code is None else int(code))
    except (ValueError, TypeError) as e:  # json.JSONDecodeError and pydantic's ValidationError are ValueErrors
        log.warning("sandbox get %s: unreadable status %r (%s); polling again", name, out[:200], e)
        return None


class OpenShellError(RuntimeError):
    pass


class OpenShellTimeout(OpenShellError):
    """The CLI call did not return in time and was killed. Whether it took effect is unknown."""


def docker_driver_config(mounts: list[Mount]) -> str:
    """Mount syntax is driver specific; this is the only place that knows the docker shape."""
    return json.dumps(
        {
            "docker": {
                "mounts": [
                    {
                        "type": "volume",
                        "source": m.source.volume,
                        "subpath": m.source.subpath,
                        "target": m.target,
                        "read_only": m.read_only,
                    }
                    for m in mounts
                ]
            }
        }
    )


class OpenShellCLI:
    """Thin async wrapper over the `openshell` CLI."""

    def __init__(self, binary: str = "openshell") -> None:
        self.binary = binary

    async def create_detached(
        self,
        name: str,
        image: str,
        argv: list[str],
        mounts: list[Mount],
        providers: list[str],
        policy: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> None:
        args = [
            "sandbox", "create",
            "--name", name,
            "--from", image,
            "--detach", "--no-tty", "--no-auto-providers",
            "--driver-config-json", docker_driver_config(mounts),
            "-o", "json",
        ]
        for p in providers:
            args += ["--provider", p]
        if policy:
            args += ["--policy", policy]
        for k, v in (labels or {}).items():
            args += ["--label", f"{k}={v}"]
        args += ["--", *argv]
        code, out, err = await self._run(args, CREATE_TIMEOUT)
        if code != 0:
            raise OpenShellError(f"sandbox create {name} failed: {(err or out).strip()[-2000:]}")

    async def status(self, name: str) -> SandboxStatus | None:
        """None when the phase cannot be read right now (the call failed or timed out): poll again."""
        try:
            code, out, _ = await self._run(["sandbox", "get", name, "-o", "json"], STATUS_TIMEOUT)
        except OpenShellTimeout as e:
            log.warning("%s", e)
            return None
        if code != 0:
            return None
        return parse_status(name, out)

    async def list_names(self, labels: dict[str, str]) -> list[str] | None:
        """Names of the sandboxes carrying all these labels. None when the gateway cannot say right now
        (the call failed or timed out): unknown, not "none"."""
        selector = ",".join(f"{k}={v}" for k, v in labels.items())
        try:
            code, out, _ = await self._run(["sandbox", "list", "--selector", selector, "--names"], STATUS_TIMEOUT)
        except OpenShellTimeout as e:
            log.warning("%s", e)
            return None
        if code != 0:
            return None
        return [line.strip() for line in out.splitlines() if line.strip()]

    async def delete(self, name: str) -> None:
        """Best effort, like before: the result is not checked, and a call that hangs is given up."""
        try:
            await self._run(["sandbox", "delete", name], DELETE_TIMEOUT)
        except OpenShellTimeout as e:
            log.warning("%s", e)

    async def supervisor_log(self, name: str, tail: int = 4000) -> str:
        """The supervisor container's log (policy decisions, egress, DNS, credential injection) of a
        sandbox, for the record: deleting the sandbox deletes it. Docker driver only (the container
        is named openshell-<workspace>--<name>-<id>-supervisor); "" when it cannot be read."""
        code, out, _ = await self._exec("docker", "ps", "-a", "--filter", f"name=--{name}-", "--format", "{{.Names}}")
        names = [n for n in out.split() if f"--{name}-" in n and n.endswith("-supervisor")] if code == 0 else []
        if not names:
            return ""
        code, out, err = await self._exec("docker", "logs", "--tail", str(tail), names[0])
        return redact(strip_ansi(out + err)) if code == 0 else ""

    async def _exec(self, *cmd: str, timeout: float = STATUS_TIMEOUT) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        return await _communicate(proc, cmd, timeout)

    async def _run(self, args: list[str], timeout: float) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            self.binary, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=None,
        )
        return await _communicate(proc, [self.binary, *args[:3]], timeout)


async def _communicate(proc: asyncio.subprocess.Process, what: list[str] | tuple[str, ...],
                       timeout: float) -> tuple[int, str, str]:
    """Wait for the process at most `timeout` seconds. On a timeout, or when the caller is cancelled,
    the process is killed: it must not outlive the call that started it."""
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        _kill(proc)
        await proc.wait()
        raise OpenShellTimeout(f"{' '.join(what)} did not return within {timeout:g}s; killed") from None
    except asyncio.CancelledError:
        _kill(proc)
        raise
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
