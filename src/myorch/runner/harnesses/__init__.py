"""Harness adapters: how each kind of agent is started in a sandbox (base.py is the interface).

Docs: https://leweii.github.io/myorch/#index--platform-features
"""
from myorch.runner.harnesses.base import HarnessAdapter, HarnessTask
from myorch.runner.harnesses.claude_code import ClaudeCodeAdapter, Vertex
from myorch.runner.harnesses.script import ScriptAdapter


def default_harnesses() -> dict[str, HarnessAdapter]:
    # "shell" is kept as an alias of "script" for the early demo flows.
    adapters: list[HarnessAdapter] = [ClaudeCodeAdapter(vertex=Vertex.from_env()), ScriptAdapter("script"),
                                      ScriptAdapter("shell")]
    return {a.name: a for a in adapters}


__all__ = ["HarnessAdapter", "HarnessTask", "default_harnesses"]
