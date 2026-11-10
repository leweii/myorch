"""What has run: records read from each run's Mandate, for listing runs and choosing what to learn from.

Users think in projects and time ("everything mdm did since we last learned"), not in run
ids. The Mandate (`.myorch/mandate.json`, written once when a run starts) already records the
project, flow, initiator and start time, so it is the source; the engine only adds the status.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from myorch.core import inputs as fi
from myorch.core.flow import role_of

LEARNABLE_ROLES = ("development", "review", "postmortem")  # runs with findings, decisions or lessons
CHANGE_ROLES = ("development", "review")  # runs that changed or reviewed code (postmortem candidates)
LEARNABLE_STATUSES = ("COMPLETED", "FAILED")  # finished; cancelled runs are left out
MAX_LEARN_RUNS = 20  # observe_runs mounts at most 20 workspaces


class RunRecord(BaseModel):
    run_id: str
    flow: str = "?"
    role: str | None = None  # what the flow does (core.flow.role_of): development, review, learn …
    project: str | None = None
    initiator: str = ""
    source: str = ""  # where the request came from (an issue URL), if any
    requested_at: str = ""  # ISO time from the Mandate
    status: str = "UNKNOWN"  # engine status: RUNNING, COMPLETED, FAILED, CANCELED …
    pr_url: str = ""  # the pull request a development run opened
    inputs: dict[str, Any] = Field(default_factory=dict)  # the flow inputs as given (Mandate.request)
    labels: list[str] = Field(default_factory=list)  # stored at start; run_labels() adds the derived ones

    @model_validator(mode="after")
    def _role(self) -> RunRecord:
        if self.role is None:
            self.role = role_of(self.flow)
        return self

    @classmethod
    def from_mandate(cls, run_id: str, mandate: dict, pr_url: str = "") -> RunRecord:
        return cls(
            run_id=run_id,
            flow=(mandate.get("flow") or {}).get("name") or "?",
            role=role_of((mandate.get("flow") or {}).get("name") or "", (mandate.get("flow") or {}).get("role")),
            project=(mandate.get("project") or {}).get("name"),
            initiator=mandate.get("initiator") or "",
            source=mandate.get("source") or "",
            requested_at=mandate.get("requested_at") or "",
            pr_url=pr_url,
            inputs=dict(mandate.get("request") or {}),
            labels=[str(x) for x in mandate.get("labels") or []],
        )


def select_for_learn(records: list[RunRecord], project: str) -> tuple[list[RunRecord], RunRecord | None]:
    """The project's finished development/review runs since its last completed learn run (newest
    MAX_LEARN_RUNS). Returns (runs, the last learn run or None)."""
    mine = [r for r in records if r.project == project]
    learns = sorted((r for r in mine if r.role == "learn" and r.status == "COMPLETED"), key=lambda r: r.requested_at)
    last = learns[-1] if learns else None
    since = last.requested_at if last else ""
    picked = [r for r in mine if r.role in LEARNABLE_ROLES and r.status in LEARNABLE_STATUSES and r.requested_at > since]
    picked.sort(key=lambda r: r.requested_at)
    return picked[-MAX_LEARN_RUNS:], last


def select_for_postmortem(records: list[RunRecord], project: str) -> list[RunRecord]:
    """Candidates for a postmortem: the project's newest development/review runs, finished or
    cancelled (MAX_LEARN_RUNS). Its locate step keeps the ones that touched the bad change."""
    picked = [r for r in records if r.project == project and r.role in CHANGE_ROLES
              and r.status in (*LEARNABLE_STATUSES, "CANCELED", "TERMINATED")]
    picked.sort(key=lambda r: r.requested_at)
    return picked[-MAX_LEARN_RUNS:]


# ---- labels: what a run is found by --------------------------------------------------------------
# A run's labels are those stored at start (Mandate.labels, run_started "labels") plus what its inputs
# give by the rules of core/inputs.py, plus the labels of a run named in its inputs (from_run, an
# eval's run=ck-development-1f29e33a), one level deep. Recomputed when runs are listed, so runs
# started before labels existed are found too. `started` maps run id -> {flow, inputs, labels}
# (run_started data, or the same read from a Mandate).

RUN_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._]*(?:-[A-Za-z0-9._]+)+$")  # could be a run id: worth a lookup


_FLOW_INPUTS: dict[Path, tuple[float, dict[str, Any]]] = {}  # flows/<name>.yaml -> (mtime, its input specs)
_PROJECTS: dict[tuple[Path, str], tuple[float, dict[str, Any] | None]] = {}  # (kb root, name) -> (mtime, public)

DeclaredOf = Callable[[str, "str | None"], dict[str, Any]]  # (flow name, project) -> input specs


def flow_inputs_of(flows_dir: Path, kb_root: Path | None = None) -> DeclaredOf:
    """(flow name, project) -> the input specs of flows/<name>.yaml as it is now, as that project sees
    them (a ticket's prefix comes from the project's Jira key). Parsed again only when a file changes:
    the console lists runs every few seconds. A run of a flow that was renamed to the shared one
    (ck-development before 2026-11-10) reads the shared flow of its role (development.yaml). A flow
    that is not there (a tool's own flow file, a deleted flow) has no specs: its inputs count as plain
    strings."""
    import yaml

    from myorch.core.fragment import FragmentError, resolve_inputs

    def raw(name: str) -> dict[str, Any] | None:
        p = flows_dir / f"{name}.yaml"
        try:
            mtime = p.stat().st_mtime
        except OSError:
            return None
        hit = _FLOW_INPUTS.get(p)
        if hit is None or hit[0] != mtime:
            hit = (mtime, (yaml.safe_load(p.read_text()) or {}).get("inputs") or {})
            _FLOW_INPUTS[p] = hit
        return hit[1] if isinstance(hit[1], dict) else {}

    def project_of(name: str | None) -> dict[str, Any] | None:
        if not name or kb_root is None:
            return None
        from myorch import project as projects

        try:
            p = projects.project_dir(kb_root, name) / "project.yaml"
            key = (kb_root, name)
            hit = _PROJECTS.get(key)
            if hit is None or hit[0] != p.stat().st_mtime:
                hit = (p.stat().st_mtime, projects.load_project(kb_root, name).public())
                _PROJECTS[key] = hit
            return hit[1]
        except (OSError, ValueError):
            return None

    def get(name: str, project: str | None = None) -> dict[str, Any]:
        try:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name or ""):
                return {}
            specs = raw(name)
            if specs is None and role_of(name) and role_of(name) != name:
                specs = raw(str(role_of(name)))
            return resolve_inputs(specs, project_of(project)) if specs else {}
        except (OSError, yaml.YAMLError, AttributeError, FragmentError):
            return {}

    return get


def own_labels(started: dict[str, Any], declared_of: DeclaredOf) -> list[str]:
    """A run's stored labels and those its own inputs give."""
    inputs = started.get("inputs") or {}
    declared = declared_of(str(started.get("flow") or ""), started.get("project") or None)
    return fi.clean_labels([*(started.get("labels") or []), *fi.input_labels(declared, inputs)])


def referenced_runs(run_id: str, inputs: dict[str, Any]) -> list[str]:
    """Input values that could name another run (whole values only: learn's comma-separated `runs`
    is a batch, not what the run is about)."""
    vals = (str(v or "").strip() for v in (inputs or {}).values())
    return list(dict.fromkeys(v for v in vals if v != run_id and len(v) <= 100 and RUN_REF.match(v)))


def label_index(started: dict[str, dict[str, Any]], declared_of: DeclaredOf) -> dict[str, list[str]]:
    """Every run's labels: its own, then those of the runs its inputs name (one level, so no cycles)."""
    own = {rid: own_labels(d, declared_of) for rid, d in started.items()}
    return {rid: fi.clean_labels([*own[rid], *(x for ref in referenced_runs(rid, d.get("inputs") or {})
                                               for x in own.get(ref, []))])
            for rid, d in started.items()}


def _inherited(run_id: str, inputs: dict[str, Any], lookup: Callable[[str], dict[str, Any] | None],
               declared_of: DeclaredOf) -> list[str]:
    out: list[str] = []
    for ref in referenced_runs(run_id, inputs):
        other = lookup(ref)
        if other:
            out += own_labels(other, declared_of)
    return out


def run_labels(run_id: str, started: dict[str, Any], lookup: Callable[[str], dict[str, Any] | None],
               declared_of: DeclaredOf) -> list[str]:
    """One run's labels, reading the runs its inputs name through `lookup` (run id -> run_started data
    or None)."""
    return fi.clean_labels([*own_labels(started, declared_of),
                            *_inherited(run_id, started.get("inputs") or {}, lookup, declared_of)])


def started_lookup(log) -> Callable[[str], dict[str, Any] | None]:
    """run id -> its run_started data, from an event log (EventLog or HttpEventLog)."""
    def get(run_id: str) -> dict[str, Any] | None:
        e = next((e for e in log.read(run_id=run_id, limit=5) if e.kind == "run_started"), None)
        return e.data if e is not None else None

    return get


def inherited_labels(log, inputs: dict[str, Any], declared_of: DeclaredOf) -> list[str]:
    """At start: the labels of the runs a new run's inputs name, so the Mandate records them too.
    Best effort: when the log cannot be read they are still found when runs are listed."""
    try:
        return fi.clean_labels(_inherited("", inputs, started_lookup(log), declared_of))
    except Exception:  # noqa: BLE001 - the console is down, SQLite is busy …: never block a start for this
        return []
