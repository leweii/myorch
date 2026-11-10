from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal, Protocol

from pydantic import BaseModel, field_validator

# Identifiers end up in sandbox labels and volume subpaths, so keep them path-safe.
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
# Instance ids extend step ids with ".r<round>" (loops) and ".x<replica>" (fanout).
INSTANCE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}(\.r[0-9]{1,2})?(\.x[0-9])?$")


def check_id(value: str, what: str) -> str:
    if not ID_PATTERN.match(value):
        raise ValueError(f"{what} {value!r} must match {ID_PATTERN.pattern}")
    return value


class StepKey(BaseModel, frozen=True):
    """Identifies one turn of one attempt of one step instance. Supplied by the engine.

    step_id is the instance id: "implement", "review.r2.x1" (loop round 2, replica 1).

    attempt: a retry after failure, starts from a clean out/ and a fresh session.
    turn:    a continuation after the user replied, keeps out/ and resumes the session.
    """

    run_id: str
    step_id: str
    attempt: int
    turn: int = 1

    @field_validator("run_id")
    @classmethod
    def _path_safe(cls, v: str) -> str:
        return check_id(v, "run id")

    @field_validator("step_id")
    @classmethod
    def _instance_safe(cls, v: str) -> str:
        if not INSTANCE_PATTERN.match(v):
            raise ValueError(f"instance id {v!r} must match {INSTANCE_PATTERN.pattern}")
        return v


class StepResult(BaseModel):
    step_id: str
    ok: bool
    exit_code: int | None
    sandbox: str
    model: str | None = None  # model the agent harness was told to use (None: harness default)
    output_tail: str = ""
    error: str | None = None
    # Why a turn without an exit code ended: "timeout" (the step's own limits.timeout), "sandbox" (could
    # not be created, or ended without an exit code), "lost" (the activity failed: heartbeat timeout,
    # worker gone, cancelled), "harness" (cannot run this turn). None: ran to an exit code, or a result
    # from before 2026-11-06 (core.run.interrupted_by_platform).
    stopped: Literal["timeout", "sandbox", "lost", "harness"] | None = None
    # The agent session this turn used (runner._session); later turns of the attempt go on in it.
    session_id: str | None = None


class RunContext(Protocol):
    """What the core library needs from an engine while a step runs."""

    def heartbeat(self, detail: Mapping[str, Any] | None = None) -> None: ...

    def is_cancelled(self) -> bool: ...


class NullRunContext:
    """RunContext for running steps outside any engine (tests, local CLI)."""

    def heartbeat(self, detail: Mapping[str, Any] | None = None) -> None:
        pass

    def is_cancelled(self) -> bool:
        return False
