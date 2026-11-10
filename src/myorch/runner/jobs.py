"""One-off sandbox jobs: work that needs a credential outside a run (open a PR, clone a knowledge repo).

Each job runs a script in a fresh OpenShell sandbox with only the providers it needs, reads
inputs from /sandbox/job/in/ and writes /sandbox/job/out/result.json. Credentials stay in the
gateway: the worker never sees a GitHub or Claude token, the sandbox only gets placeholders.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import json
import time
from collections.abc import Callable
from typing import Any

from myorch.runner.openshell import Mount, OpenShellCLI, OpenShellError

JOB_DIR = "/sandbox/job"


def job_sandbox_name(key: str) -> str:
    """kb-<source>-<hash> for a knowledge sync job (key knowledge-<source>-<spec hash>), job-<hash>
    otherwise, so `docker ps` shows what the job is for. OpenShell caps names at 19 characters."""
    digest = hashlib.sha1(key.encode()).hexdigest()[:5]
    m = re.fullmatch(r"knowledge-(.+)-[0-9a-f]{8}", key)
    if m:
        slug = re.sub(r"[^a-z0-9]+", "-", m.group(1).lower()).strip("-")[:10].strip("-") or "src"
        return f"kb-{slug}-{digest}"
    return f"job-{hashlib.sha1(key.encode()).hexdigest()[:12]}"


class JobError(RuntimeError):
    pass


class JobRunner:
    def __init__(self, store, image: str, openshell: OpenShellCLI | None = None, poll_interval: float = 2.0) -> None:
        self.store = store
        self.image = image
        self.openshell = openshell or OpenShellCLI()
        self.poll_interval = poll_interval

    async def run(
        self,
        key: str,
        script: str,
        providers: list[str],
        files: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 300,
        heartbeat: Callable[[], None] = lambda: None,
    ) -> dict[str, Any]:
        await asyncio.to_thread(self.store.prepare_job, key, files or {})
        name = job_sandbox_name(key)
        envs = [f"{k}={v}" for k, v in {"JOB_DIR": JOB_DIR, **(env or {})}.items()]
        # The script may end with a heredoc terminator, which must stand alone on its line.
        argv = ["env", *envs, "bash", "-c", f"cd {JOB_DIR} && {{\n{script}\n}} > {JOB_DIR}/out/log.txt 2>&1 < /dev/null"]
        await self.openshell.delete(name)
        try:
            await self.openshell.create_detached(
                name=name, image=self.image, argv=argv,
                mounts=[Mount(source=self.store.job_source(key), target=JOB_DIR, read_only=False)],
                providers=providers, labels={"myorch.job": key[:60]},
            )
        except OpenShellError as e:
            raise JobError(str(e)) from e
        deadline = time.monotonic() + timeout
        try:
            while True:
                status = await self.openshell.status(name)
                heartbeat()
                if status and status.finished:
                    break
                if time.monotonic() > deadline:
                    raise JobError(f"job {key} timed out after {timeout}s")
                await asyncio.sleep(self.poll_interval)
        finally:
            await self.openshell.delete(name)
        raw = await asyncio.to_thread(self.store.read_job_result, key)
        if raw is None:
            raise JobError(f"job {key} wrote no result (exit {status.exit_code})")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise JobError(f"job {key} wrote invalid JSON: {e}") from e
