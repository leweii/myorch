from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class Profile(BaseModel):
    """Sandbox shape for a kind of step: image x policy x providers x repo access."""

    name: str
    image: str | None = None  # None: the flow's image, else the harness adapter's default
    policy: str | None = None  # path relative to the config dir; "project": the project's policy; None: gateway default
    providers: list[str] = Field(default_factory=list)
    repo: Literal["rw", "ro"] = "rw"  # ro overlays the run's repo/ read-only (reviewers)
    model: str | None = None  # default model for agent steps using this profile


def load_profile(config_dir: Path, name: str) -> Profile:
    path = config_dir / "profiles" / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"profile {name!r} not found at {path}")
    data = yaml.safe_load(path.read_text()) or {}
    profile = Profile.model_validate({"name": name, **data})
    if profile.policy and profile.policy != "project":
        policy_path = config_dir / profile.policy
        if not policy_path.is_file():
            raise FileNotFoundError(f"profile {name!r}: policy file {policy_path} not found")
        profile.policy = str(policy_path)
    return profile
