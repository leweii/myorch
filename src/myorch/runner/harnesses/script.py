from __future__ import annotations

from myorch.runner.harnesses.base import HarnessTask


class ScriptAdapter:
    """Runs the step's prompt as a bash script. For deterministic steps: git, CI, rules.

    The script sees the MYORCH_* environment (run id, branch, inputs, ...) plus
    MYORCH_TURN / MYORCH_REPLY, and may keep state in $MYORCH_SESSION_DIR across turns.
    """

    default_image = "openshell-claude-code:latest"
    supports_resume = True

    def __init__(self, name: str = "script") -> None:
        self.name = name

    def build_command(self, task: HarnessTask) -> list[str]:
        return [
            "env",
            f"MYORCH_TURN={task.turn}",
            f"MYORCH_REPLY={task.reply or ''}",
            f"MYORCH_SESSION={task.session_id}",
            f"MYORCH_SESSION_DIR={task.session_dir}",
            "bash", "-c", task.instruction,
        ]

    def model_providers(self) -> list[str]:
        return []

    def session_file(self, session_id: str) -> str | None:
        return None  # scripts keep their own state in $MYORCH_SESSION_DIR, there is no transcript

    def session_entries(self, session_id: str) -> list[str]:
        return []
