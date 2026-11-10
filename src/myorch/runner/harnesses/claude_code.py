from __future__ import annotations

from myorch.runner.harnesses.base import HarnessTask

STREAM = "/sandbox/bin/myorch-stream"
MCP_WRAPPER = "/sandbox/bin/myorch-mcp"
# Live output: stream-json goes through myorch-stream, which writes a readable line per event to the
# step's log as it happens and keeps the raw events in $1. pipefail keeps claude's exit code. Without
# the tool (tools/ not installed on the volume yet) claude writes plain text, as before.
STREAM_SH = (
    f'raw=$1; shift; if [ -x {STREAM} ]; then "$@" --output-format stream-json --verbose | {STREAM} "$raw"; '
    'else "$@" --output-format text; fi'
)


class ClaudeCodeAdapter:
    name = "claude-code"
    default_image = "openshell-claude-code:latest"  # built from images/claude-code
    supports_resume = True

    def __init__(self, provider: str = "claude") -> None:
        self.provider = provider

    def build_command(self, task: HarnessTask) -> list[str]:
        resume = task.resume or task.turn > 1
        session = ["--resume", task.session_id] if resume else ["--session-id", task.session_id]
        model = ["--model", task.model] if task.model else []
        # Only the run's knowledge servers, and only their registered tools: myorch-mcp builds the
        # config in the sandbox and adds --mcp-config, --strict-mcp-config (a repo's own .mcp.json is
        # not loaded) and --disallowedTools for every other tool.
        mcp = [MCP_WRAPPER, task.mcp_config, "--"] if task.mcp_config else []
        # CLAUDE_CONFIG_DIR puts transcripts *and* .claude.json in the persisted session dir;
        # by default .claude.json sits at ~/.claude.json and a resumed turn would lose it.
        # The sandbox is the isolation boundary, so in-agent permission prompts are skipped.
        return [
            "env", f"CLAUDE_CONFIG_DIR={task.session_dir}",
            "bash", "-o", "pipefail", "-c", STREAM_SH, "myorch-claude", task.raw_log or "/dev/null",
            *mcp,
            "claude",
            "-p", task.prompt,
            *session,
            *model,
            "--dangerously-skip-permissions",
        ]

    def model_providers(self) -> list[str]:
        return [self.provider]

    def session_file(self, session_id: str) -> str | None:
        # $CLAUDE_CONFIG_DIR/projects/<cwd, slugged>/<session id>.jsonl. `--session-id` refuses an id
        # that already has one ("Session ID ... is already in use").
        return f"{session_id}.jsonl"

    def session_entries(self, session_id: str) -> list[str]:
        # projects/<slug>/<id>.jsonl, and dirs named <id>: projects/<slug>/<id>/ (subagent transcripts,
        # tool results), file-history/<id>/, session-env/<id>/.
        return [f"{session_id}.jsonl", session_id]
