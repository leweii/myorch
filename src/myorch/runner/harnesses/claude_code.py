from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

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
# Vertex: the provider (openshell/providers/claude-code-vertex.yaml) puts a placeholder for the
# gateway-minted access token in GCP_SA_ACCESS_TOKEN. Claude Code skips its own Google auth and
# sends the placeholder as the bearer token; the supervisor swaps in the real token at egress.
VERTEX_SH = 'export ANTHROPIC_CUSTOM_HEADERS="Authorization: Bearer $GCP_SA_ACCESS_TOKEN"; exec "$@"'


@dataclass(frozen=True)
class Vertex:
    """Claude on Google Vertex AI instead of api.anthropic.com (provider type claude-code-vertex)."""

    project: str
    region: str = "global"

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Vertex | None:
        """MYORCH_VERTEX_PROJECT (and MYORCH_VERTEX_REGION, default global) on the worker; unset: None."""
        project = env.get("MYORCH_VERTEX_PROJECT", "").strip()
        return cls(project, env.get("MYORCH_VERTEX_REGION", "").strip() or "global") if project else None

    def env(self) -> list[str]:
        return [
            "CLAUDE_CODE_USE_VERTEX=1",
            "CLAUDE_CODE_SKIP_VERTEX_AUTH=1",  # no Google auth in the sandbox: see VERTEX_SH
            f"ANTHROPIC_VERTEX_PROJECT_ID={self.project}",
            f"CLOUD_ML_REGION={self.region}",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1",  # the provider allows only aiplatform hosts
        ]


class ClaudeCodeAdapter:
    name = "claude-code"
    default_image = "openshell-claude-code:latest"  # built from images/claude-code
    supports_resume = True

    def __init__(self, provider: str = "claude", vertex: Vertex | None = None) -> None:
        self.provider = provider
        self.vertex = vertex

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
        vertex = [*self.vertex.env(), "bash", "-c", VERTEX_SH, "myorch-vertex"] if self.vertex else []
        return [
            "env", f"CLAUDE_CONFIG_DIR={task.session_dir}",
            *vertex,
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
