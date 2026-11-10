"""Built-in host actions: deterministic steps the worker runs outside any sandbox.

They exist only for effects a sandbox cannot have, such as writing to the host's project packs or
starting another run. Each action is registered here by name; flows cannot run arbitrary commands
on the host, and actions never execute agent-written code. Inputs written by agents (e.g. the
proposals to apply) are validated before anything is changed.
"""

from __future__ import annotations

import logging

import json
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from myorch import evals
from myorch import project as projects
from myorch import takeaways
from myorch.core.flow import Step, render_template
from myorch.core.manifest import MANIFEST_FILE, Manifest
from myorch.core.types import StepKey, StepResult
from myorch.storage.base import run_dir


class ActionRequest(BaseModel):
    key: StepKey
    step: Step
    context: dict[str, Any]
    trail: dict[str, Any] | None = None  # run-trail: the run's course (core/runtrail.py), from the workflow


class ActionOutcome(BaseModel):
    result: StepResult
    manifest: Manifest


class Edit(BaseModel):
    old: str
    new: str


class Change(BaseModel):
    file: str  # as seen in the sandbox: project/<path>, e.g. project/guide/review-guide.md
    content: str | None = None  # the whole new file (replaces it; applied before edits and append)
    edits: list[Edit] = Field(default_factory=list)  # each `old` must occur exactly once
    append: str | None = None  # text appended to the end of the file


class Proposal(BaseModel):
    id: int
    title: str
    changes: list[Change] = Field(default_factory=list)


class ActionError(RuntimeError):
    pass


class Finding(BaseModel):  # an expected finding of a postmortem case (docs/postmortem/case.json)
    severity: str = "Important"
    title: str
    file: str
    line: int | None = None
    locations: list[dict[str, Any]] = Field(default_factory=list)
    evidence: str = ""
    class_: str | None = Field(default=None, alias="class")


log = logging.getLogger(__name__)


class ActionRunner:
    def __init__(self, store, projects_root: Path, *, config_dir: Path | None = None, data_root: Path | None = None,
                 launch: Callable[[dict[str, Any]], str] | None = None) -> None:
        self.store = store
        self.kb_root = projects_root  # a git repository holding every project pack
        self.config_dir = config_dir  # flows/ (to start eval runs)
        self.data_root = data_root  # myorch-data (eval cases)
        self.launch = launch  # launch({case, depth, by, source, run_id}) -> run id: starts an eval replay (worker.py)

    def run(self, req: ActionRequest) -> ActionOutcome:
        outcome = self._run(req)
        # A host action has no sandbox, so nothing made its out/ directory, yet a later agent step
        # mounts every upstream step's out/ (read-only at /sandbox/in/<step>): a missing directory
        # failed that sandbox's creation (2026-11-09, takeaway after takeaway-trail). Write the
        # action's manifest there, which also lets the next step read the action's outputs.
        try:
            self.store.write_run_file(req.key.run_id, f".myorch/steps/{req.key.step_id}/out/{MANIFEST_FILE}",
                                      outcome.manifest.model_dump_json(by_alias=True, indent=1))
        except Exception:  # noqa: BLE001 - best effort; the outcome itself is what the workflow records
            log.exception("could not write the manifest of host action %s/%s", req.key.run_id, req.key.step_id)
        return outcome

    def _run(self, req: ActionRequest) -> ActionOutcome:
        args = {k: render_template(str(v), req.context) for k, v in req.step.args.items()}
        try:
            if req.step.action == "project-apply":
                project = (req.context.get("project") or {}).get("name")
                if not project:
                    raise ActionError("project-apply needs a run with a project")
                summary, outputs = self.project_apply(req.key, project, args)
            elif req.step.action == "postmortem-replay":
                project = (req.context.get("project") or {}).get("name")
                if not project:
                    raise ActionError("postmortem-replay needs a run with a project")
                summary, outputs = self.postmortem_replay(req.key, project, args)
            elif req.step.action == "run-trail":
                summary, outputs = self.run_trail(req, args)
            elif req.step.action == "takeaway-redact":
                summary, outputs = self.takeaway_redact(req.key, args)
            elif req.step.action == "takeaways-learned":
                project = (req.context.get("project") or {}).get("name")
                if not project:
                    raise ActionError("takeaways-learned needs a run with a project")
                summary, outputs = self.takeaways_learned(req.key, project, args)
            else:
                raise ActionError(f"unknown action {req.step.action!r}")
        except (ActionError, ValueError, FileNotFoundError) as e:
            return ActionOutcome(
                result=StepResult(step_id=req.key.step_id, ok=False, exit_code=1, sandbox="(host action)", error=str(e)),
                manifest=Manifest(step_id=req.key.step_id, status="failed", summary=str(e)[:300]),
            )
        return ActionOutcome(
            result=StepResult(step_id=req.key.step_id, ok=True, exit_code=0, sandbox="(host action)"),
            manifest=Manifest(step_id=req.key.step_id, summary=summary, outputs=outputs),
        )

    # ---- project-apply ------------------------------------------------------------------

    def _read_json(self, run_id: str, rel: str) -> Any:
        text = self.store.read_file(f"{run_dir(run_id)}/{rel}")
        if text is None:
            raise ActionError(f"{rel} not found in the run's workspace")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ActionError(f"{rel} is not valid JSON: {e}") from e

    def _git(self, *args: str, check: bool = True) -> str:
        res = subprocess.run(["git", "-C", str(self.kb_root), *args], capture_output=True, text=True)
        if check and res.returncode != 0:
            raise ActionError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
        return res.stdout.strip()

    def project_apply(self, key: StepKey, project: str, args: dict[str, str]) -> tuple[str, dict[str, Any]]:
        """Apply the accepted proposals to the project pack, commit, and re-sync the snapshot.

        args.scope decides which pack files may change (project.EDITABLE: learn, onboard, fix).
        The run's later steps mount the new snapshot (outputs.version); other runs keep theirs.
        A run may apply more than once (fix, once per review round): each step instance commits once.
        """
        scope = args.get("scope", "learn")
        if scope not in projects.EDITABLE:
            raise ActionError(f"unknown scope {scope!r}")
        accepted = self._read_json(key.run_id, args.get("accepted", "docs/learn/accepted.json"))
        proposals = [Proposal.model_validate(p) for p in self._read_json(key.run_id, args.get("proposals", "docs/learn/proposals.json"))]
        by_id = {p.id: p for p in proposals}
        ids = [int(i) for i in (accepted.get("accepted", []) if isinstance(accepted, dict) else accepted)]
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise ActionError(f"accepted proposals {missing} do not exist")
        if not ids:
            return "没有采纳任何建议，知识库未改动", {"applied": [], "commit": ""}

        if not (self.kb_root / ".git").is_dir():
            raise ActionError(f"{self.kb_root} is not a git repository; run `git init` there first")
        marker = f"myorch-run: {key.run_id} step: {key.step_id}"
        done = self._git("log", "--format=%H", f"--grep={marker}", "--fixed-strings")
        if done:  # idempotent: a retry after a successful commit must not apply twice
            version = (self.store.read_file(f"projects/{project}/.version") or "").strip()
            return (f"这一步的建议已经写回过（{done.split()[0][:7]}）",
                    {"applied": ids, "commit": done.split()[0], **({"version": version} if version else {})})

        # Validate everything in memory first; write nothing unless every edit applies cleanly.
        contents: dict[Path, str] = {}
        errors: list[str] = []
        for pid in ids:
            for ch in by_id[pid].changes:
                try:
                    target = projects.resolve_editable(self.kb_root, project, ch.file, scope)
                except ValueError as e:
                    errors.append(f"#{pid}: {e}")
                    continue
                text = contents.get(target)
                if text is None:
                    text = projects.starting_text(self.kb_root, project, target)
                if ch.content is not None:
                    text = ch.content.rstrip("\n") + "\n"
                for ed in ch.edits:
                    n = text.count(ed.old) if ed.old else 0
                    if n != 1:
                        errors.append(f"#{pid}: {ch.file}: 要替换的原文出现了 {n} 次（必须恰好 1 次）：{ed.old[:60]!r}")
                        continue
                    text = text.replace(ed.old, ed.new, 1)
                if ch.append:
                    text = text.rstrip("\n") + "\n\n" + ch.append.strip("\n") + "\n"
                contents[target] = text
        if errors:
            raise ActionError("建议无法干净地应用，知识库未改动：\n" + "\n".join(errors))

        for target, text in contents.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        rel = [str(t.relative_to(self.kb_root.resolve())) for t in contents]
        self._git("add", "--", *rel)
        titles = "; ".join(f"#{i} {by_id[i].title}" for i in ids)
        # Only these files (`-- <paths>`): whatever else someone staged in myorch-knowledge stays out.
        self._git("-c", "user.name=myorch", "-c", "user.email=myorch@localhost",
                  "commit", "-q", "-m", f"{scope}({project}): {titles}"[:200], "-m", f"{marker}\nproposals: {', '.join(map(str, ids))}",
                  "--", *rel)
        commit = self._git("rev-parse", "HEAD")
        n, version = projects.snapshot(self.store, self.kb_root, project)
        return (f"采纳 {len(ids)} 条建议，改动 {len(rel)} 个文件，提交 {commit[:7]}，已同步项目 {project}（{n} 个文件，{version[:15]}）",
                {"applied": ids, "files": rel, "commit": commit, "version": version})

    # ---- postmortem-replay -------------------------------------------------------------

    def postmortem_replay(self, key: StepKey, project: str, args: dict[str, str]) -> tuple[str, dict[str, Any]]:
        """The postmortem's eval case (myorch/evals.py, id = this run) and its replays.

        phase before: write the case as a draft (the change only) and replay it with today's knowledge.
        phase after: add the expected findings from docs/postmortem/case.json (the case then waits for
        the person in 流程 → 评测集) and, when proposals were adopted (docs/postmortem/accepted.json), replay again
        with the new knowledge. The replay is a separate eval run: the reviewers must not see this
        run's workspace (the incident, the fix, the analysis).
        """
        if self.data_root is None or self.config_dir is None or self.launch is None:
            raise ActionError("postmortem-replay needs the worker's data dir and a Temporal client")
        phase, depth = args.get("phase", "before"), args.get("depth", "standard")
        if phase not in ("before", "after"):
            raise ActionError(f"unknown phase {phase!r}")
        t = self._read_json(key.run_id, "docs/postmortem/target.json")
        case = evals.load_case(self.data_root, key.run_id) or {
            "id": key.run_id, "origin": "git", "flow": "postmortem", "project": project, "repo": t["repo"],
            "base_ref": t.get("base_ref", ""), "base_sha": t["base_sha"], "head_sha": t["bad_sha"],
            "commits": [f"{t['bad_sha'][:7]} {t.get('bad_subject', '')}"], "head_guessed": False,
            "requirement": t.get("requirement", ""), "fix_sha": t.get("fix_sha", ""),
            "findings": [], "status": "draft", "keep": [], "collected_at": time.time(), "replays": {}}
        notes = []
        if phase == "after":
            raw = self._read_json(key.run_id, "docs/postmortem/case.json")
            try:
                found = [Finding.model_validate(f).model_dump(by_alias=True, exclude_none=True)
                         for f in (raw if isinstance(raw, list) else raw.get("findings", []))]
            except ValueError as e:
                raise ActionError(f"docs/postmortem/case.json: {e}") from e
            if case["status"] == "draft":
                case.update(findings=found, status="pending" if found else "rejected")
            notes.append(f"期望的发现 {len(found)} 条，" + ("在「流程 → 评测集」里确认" if found else "没有可以事先发现的问题，案例不计分"))
        again = phase == "before"
        if phase == "after" and self.store.read_file(f"{run_dir(key.run_id)}/docs/postmortem/accepted.json"):
            acc = self._read_json(key.run_id, "docs/postmortem/accepted.json")
            again = bool(acc.get("accepted") if isinstance(acc, dict) else acc)  # proposals were adopted
        if again and case["status"] != "rejected":
            mandate = self._read_json(key.run_id, ".myorch/mandate.json")
            rid = case["replays"].get(phase) or self.launch({
                "case": case, "depth": depth, "by": mandate.get("initiator") or "myorch",
                "source": f"postmortem:{key.run_id}:{phase}", "run_id": f"eval-{key.run_id.rsplit('-', 1)[-1]}-{phase}"})
            case["replays"][phase] = rid
            notes.append(f"{'用现在的' if phase == 'before' else '用采纳后的'}知识库回放：{rid}")
        elif phase == "after":
            notes.append("没有采纳建议，不再回放")
        evals.save_case(self.data_root, case)
        return "；".join(notes) or "没有要做的", {"case": case["id"], "replay": case["replays"].get(phase, ""),
                                                  "expected": len(case["findings"])}

    # ---- run-trail, takeaways-learned (development's takeaway, learn reading it) ---------------

    def _myorch_commit(self) -> str:
        """The myorch code this worker runs: MYORCH_COMMIT (deploy/update.sh bakes it into the image), else git."""
        commit = os.environ.get("MYORCH_COMMIT", "").strip()
        if commit or self.config_dir is None:
            return commit[:12]
        res = subprocess.run(["git", "-C", str(self.config_dir), "rev-parse", "--short=12", "HEAD"],
                             capture_output=True, text=True)
        return res.stdout.strip() if res.returncode == 0 else ""

    @staticmethod
    def _workspace_path(rel: str, action: str) -> str:
        rel = rel.strip()
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            raise ActionError(f"{action}: path {rel!r} must stay inside the workspace")
        return rel

    def run_trail(self, req: ActionRequest, args: dict[str, str]) -> tuple[str, dict[str, Any]]:
        """Write the run's course so far (from the workflow, core/runtrail.py) into its workspace, with
        what only the host knows: the myorch commit and the pack version the run uses. Lines that look
        like secrets are dropped first (a step's error can carry a command line). args.clear: files
        emptied here, so the agent that follows cannot leave an earlier visit's text behind."""
        from myorch.knowledge import redact_tree

        if req.trail is None:
            raise ActionError("run-trail got no trail from the workflow")
        rel = self._workspace_path(args.get("path", "docs/takeaway/trail.json"), "run-trail")
        clear = [self._workspace_path(c, "run-trail") for c in args.get("clear", "").split()]
        mandate_text = self.store.read_file(f"{run_dir(req.key.run_id)}/.myorch/mandate.json")
        try:
            mandate = json.loads(mandate_text) if mandate_text else {}
        except ValueError:
            mandate = {}
        t, redacted = redact_tree({**req.trail, "myorch_commit": self._myorch_commit(),
                                   "pack_version": ((mandate.get("project") or {}).get("version", "") if isinstance(mandate, dict) else "")})
        for c in clear:
            self.store.write_run_file(req.key.run_id, c, "")
        self.store.write_run_file(req.key.run_id, rel, json.dumps(t, ensure_ascii=False, indent=1))
        failed = list(t.get("failed_so_far") or [])
        return (f"{len(t.get('steps') or [])} 个步骤" + (f"，失败过：{', '.join(map(str, failed))}" if failed else "")
                + f"，{len(t.get('qa') or [])} 次问答，写入 {rel}" + (f"，删掉 {redacted} 行疑似密钥" if redacted else ""),
                {"path": rel, "failed": len(failed), "redacted": redacted})

    def takeaway_redact(self, key: StepKey, args: dict[str, str]) -> tuple[str, dict[str, Any]]:
        """Before what an agent wrote leaves the sandbox (development commits it to the repository):
        replace lines that look like secrets (knowledge.SECRET_RULES, the one list). Fails when none of
        the files has content, so the commit that depends on outputs.files does not run."""
        from myorch.knowledge import redact_text

        files = [self._workspace_path(f, "takeaway-redact") for f in args.get("files", "").split()]
        kept, redacted = [], 0
        for rel in files:
            text = self.store.read_file(f"{run_dir(key.run_id)}/{rel}")
            if not text or not text.strip():
                continue
            clean, n = redact_text(text)
            if n:
                self.store.write_run_file(key.run_id, rel, clean)
            kept.append(rel)
            redacted += n
        if not kept:
            raise ActionError(f"没有内容：{' '.join(files) or '（没有给 files）'}")
        return (f"检查了 {len(kept)} 个文件" + (f"，删掉 {redacted} 行疑似密钥" if redacted else "，没有疑似密钥"),
                {"files": len(kept), "redacted": redacted})

    def takeaways_learned(self, key: StepKey, project: str, args: dict[str, str]) -> tuple[str, dict[str, Any]]:
        """learn took in the takeaways listed in docs/learn/takeaways.json (its collect step): record their
        run ids in myorch-data (myorch/takeaways.py), so the next learn skips them. Idempotent."""
        if self.data_root is None:
            raise ActionError("takeaways-learned needs the worker's data dir")
        data = self._read_json(key.run_id, args.get("picked", takeaways.PICKED_FILE))
        picked = data.get("picked") if isinstance(data, dict) else None
        runs = [str(p.get("run")) for p in picked or [] if isinstance(p, dict) and p.get("run")]
        new = takeaways.record(self.data_root, project, runs, key.run_id)
        return (f"记下 {len(new)} 条 takeaway 已学过（共读了 {len(runs)} 条）", {"recorded": len(new), "read": len(runs)})
