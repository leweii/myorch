from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

MANIFEST_FILE = "manifest.json"
QUESTION_FILE = ".myorch/question.json"  # written by myorch-ask, relative to out/


class ManifestFile(BaseModel):
    path: str
    description: str = ""


class Question(BaseModel):
    question: str
    options: list[str] = Field(default_factory=list)


class Manifest(BaseModel):
    step_id: str
    status: Literal["ok", "failed", "needs_input", "manifest_missing"] = "ok"
    summary: str = ""
    files: list[ManifestFile] = Field(default_factory=list)
    notes: str = ""
    outputs: dict[str, Any] = Field(default_factory=dict)  # structured values for conditions
    question: Question | None = None


# Shown to the agent so it knows what to write.
MANIFEST_SCHEMA_HINT = json.dumps(
    {
        "step_id": "<this step id>",
        "status": "ok | failed",
        "summary": "one or two sentences on what this step did",
        "files": [{"path": "path relative to /sandbox/out", "description": "what the file is"}],
        "notes": "things later steps should watch for",
        "outputs": {},
    },
    ensure_ascii=False,
    indent=2,
)


def parse_manifest(text: str | None, step_id: str) -> Manifest | None:
    """Parse what the agent wrote. Returns None if missing or malformed."""
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    data["step_id"] = step_id  # never trust the agent on identity
    try:
        return Manifest.model_validate(data)
    except ValidationError:
        return None


def parse_question(text: str | None) -> Question | None:
    if not text:
        return None
    try:
        return Question.model_validate_json(text)
    except ValidationError:
        return None


def resolve_outcome(step_id: str, manifest: Manifest | None, question: Question | None, files: list[str]) -> Manifest:
    """Decide how a turn ended. Precedence: myorch-ask question > manifest status > fallback."""
    if question is not None:
        base = manifest or Manifest(step_id=step_id)
        return base.model_copy(update={"status": "needs_input", "question": question})
    if manifest is not None:
        if manifest.status == "needs_input" and manifest.question is None:
            return manifest.model_copy(update={"status": "manifest_missing"})
        return manifest
    return fallback_manifest(step_id, files)


def escalate(manifest: Manifest, when: str | None) -> Manifest:
    """Force a question when the step's own outputs say a human must decide but the agent did not ask."""
    from myorch.core.expr import evaluate

    if not when or manifest.status not in ("ok", "manifest_missing"):
        return manifest
    if not evaluate(when, {"outputs": manifest.outputs}):
        return manifest
    out = manifest.outputs
    text = out.get("question") or out.get("design_question") or manifest.summary or "A decision from you is needed to continue."
    options = out.get("options") or out.get("design_options") or []
    question = Question(question=str(text), options=[str(o) for o in options] if isinstance(options, list) else [])
    return manifest.model_copy(update={"status": "needs_input", "question": question})


def fallback_manifest(step_id: str, files: list[str]) -> Manifest:
    """Built when the agent did not leave a usable manifest."""
    return Manifest(
        step_id=step_id,
        status="manifest_missing",
        summary="The agent did not write a valid manifest.json. The output files below were listed automatically",
        files=[ManifestFile(path=f) for f in sorted(files) if f != MANIFEST_FILE and not f.startswith(".myorch/")],
    )
