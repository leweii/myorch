from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class HarnessTask(BaseModel):
    prompt: str  # what to send this turn: full first-turn prompt, or the reply prompt
    instruction: str  # the step's own prompt, templates rendered
    turn: int
    session_id: str  # stable per attempt; first turn creates it, later turns resume it
    resume: bool = False  # continue session_id even on turn 1 (a retry after a platform interruption)
    session_dir: str  # where the harness keeps config and sessions; persists across turns
    reply: str | None = None  # the user's reply, on continuation turns
    model: str | None = None  # None: the harness's own default
    mcp_config: str | None = None  # path (in the sandbox) of the run's MCP spec: knowledge sources only (tools/myorch-mcp)
    raw_log: str | None = None  # where a harness with structured output keeps it unchanged (turn-<attempt>-<turn>.jsonl)


class HarnessAdapter(Protocol):
    name: str
    default_image: str
    supports_resume: bool  # can a later turn in a new sandbox continue the session?

    def build_command(self, task: HarnessTask) -> list[str]:
        """Non-interactive argv; runs with cwd at the repo (or the workspace before checkout)."""
        ...

    def model_providers(self) -> list[str]:
        """OpenShell providers this harness needs for model access."""
        ...

    def session_file(self, session_id: str) -> str | None:
        """Name of the file that holds the session's transcript, somewhere under session_dir; the
        runner checks it exists before continuing a session from an earlier attempt. None: the
        harness keeps no transcript, so a retry never continues an earlier attempt's session."""
        ...

    def session_entries(self, session_id: str) -> list[str]:
        """Names of every file and directory under session_dir that belong to the session (the
        transcript and its side files), set aside together when the id must be created anew."""
        ...
