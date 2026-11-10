"""The console's view of runs, and what the user does there — all through the channel relay.

The console is the web adapter of the channel layer (docs/notes/2026-11-02-channel-layer.md):
- reads: the relay's events (a run is folded from them, ui/runview.py) and content (files, diff,
  logs, the run's flow snapshot); the engine's status of each run comes with relay.runs();
- writes: the relay's commands, made by the local user (`via: web`).
Runs started before the event-detail patch have only lifecycle events. For those, and only during
the migration, the console still reads the engine's state (Temporal query) — see `_engine_view`.

The console has no agent of its own: a run's conversation is the run. The user's first message is
the request (saved verbatim in the run's workspace by the relay); later messages go verbatim to the
step that is waiting for them. Approval and rejection notes, which the engine does not keep, are
kept in myorch-data/ui/runs/<run>.jsonl.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from myorch.core.flow import role_of
from myorch.core.inputs import role_view

from myorch.core.flow import Flow, Loop, load_flow
from myorch.core.run import RunState
from myorch.storage.base import run_dir, step_dir
from myorch.storage.events import LoggedEvent
from myorch.ui import runview

WAITING = ("waiting_input", "waiting_approval")
MAX_FILE_CHARS = 200_000
VIA = "web"  # the channel name of the console (channels.yaml identities)
SEARCH_LIMIT = 500  # a search in the run list returns at most this many runs (before the project filter)

RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def check_run_id(run_id: str) -> str:
    if not RUN_ID.match(run_id or ""):
        raise ValueError(f"invalid run id: {run_id!r}")
    return run_id


def _safe_rel(path: str) -> str:
    """A path inside a run's workspace: relative, no `..`."""
    p = path.strip().lstrip("/")
    if not p or any(part in ("..", "") for part in p.split("/")):
        raise ValueError(f"invalid path: {path!r}")
    return p


REQUEST_FILE = "docs/external/request.md"

# Images the console shows from a run's workspace (ticket screenshots, attachments). No SVG: it can
# carry script, and these files come from untrusted sources.
IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
MAX_IMAGE_BYTES = 10_000_000


def image_type(path: str) -> str | None:
    """The media type of an image path the console shows, or None."""
    name = path.rsplit("/", 1)[-1].lower()
    return IMAGE_TYPES.get(name.rsplit(".", 1)[-1]) if "." in name else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Service:
    def __init__(self, relay, config: Path, kb_root: Path, user: str, log_dir: Path | None = None) -> None:
        self.relay = relay
        self.config = config
        self.kb_root = kb_root
        self.user = user
        self.log_dir = log_dir
        self._requests: dict[str, str | None] = {}  # run id -> request.md text (it never changes)
        self._events: dict[str, list[LoggedEvent]] = {}  # run id -> its events so far (appended by seq)
        self._flows: dict[str, Flow] = {}  # run id -> its flow snapshot (never changes)
        self._runs: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self._records: tuple[float, dict[str, Any]] = (0.0, {})
        self.volume_error: str | None = None  # set while the artifact volume cannot be read
        self.state_errors: dict[str, str] = {}  # run id -> why it could not be read
        self._knowledge: dict[str, list[dict[str, Any]]] = {}  # run id -> Mandate.knowledge
        self._catalog: dict[str, tuple[float, list[dict[str, Any]]]] = {}  # project -> knowledge catalog

    # ---- reading through the relay ------------------------------------------------------

    async def events(self, run_id: str) -> list[LoggedEvent]:
        """The run's events, read incrementally: only those after the last seq seen are fetched."""
        have = self._events.setdefault(check_run_id(run_id), [])
        while True:
            batch = await asyncio.to_thread(self.relay.events, have[-1].seq if have else 0, run_id, 2000)
            have += batch
            if len(batch) < 2000:
                return have

    async def flow(self, run_id: str) -> Flow | None:
        """The run's own flow, as it was expanded at start (the files in flows/ may have changed since)."""
        if run_id not in self._flows:
            text = await self.relay.read_file(run_id, runview.FLOW_FILE)
            if not text:
                return None
            self._flows[run_id] = load_flow(text)
        return self._flows[run_id]

    async def engine_runs(self, max_age: float = 2.0) -> list[dict[str, Any]]:
        """relay.runs(): run_started data with the engine's status of each run (briefly cached)."""
        at, cached = self._runs
        if time.monotonic() - at > max_age:
            cached = await self.relay.runs(200)
            self._runs = (time.monotonic(), cached)
        return cached

    async def _legacy_records(self) -> dict[str, Any]:
        """Mandates of runs whose run_started does not say who started them (before the event-detail
        patch), read from the volume. Migration only; cached for a minute."""
        from myorch.cli import _run_records

        at, cached = self._records
        if time.monotonic() - at > 60:
            try:
                cached = {r.run_id: r for r in await _run_records(self.relay.client, self.relay.store)}
                self.volume_error = None
            except RuntimeError as e:  # Docker is down
                self.volume_error = str(e).splitlines()[0][:300]
            self._records = (time.monotonic(), cached)
        return cached

    # ---- runs ---------------------------------------------------------------------------

    async def runs(self, limit: int = 40, project: str | None = None, label: str | None = None) -> dict[str, Any]:
        """Recent runs, plus everything that waits for the user across running runs.
        `label`: a search (relay.runs): every run with that label or whose id contains it, not only recent
        ones. The inbox stays that of the recent runs: a search does not hide what waits for the user."""
        recent = await self._rows(await self.engine_runs(), project, limit)
        rows = await self._rows(await self.relay.runs(SEARCH_LIMIT, label=label), project, limit) if label else recent
        running = list(dict.fromkeys(r["run_id"] for r in recent + rows if r["status"] == "RUNNING"))
        views = await asyncio.gather(*(self.run_detail(rid) for rid in running))
        inbox, prog = [], {}
        for rid, v in zip(running, views):
            if v is None:
                continue
            prog[rid] = runview.progress(v)
            if any(r["run_id"] == rid for r in recent):
                inbox += [{**w, "run_id": rid, "project": v.get("project"), "flow": v.get("flow")} for w in v["waiting"]]
        return {"runs": [{**r, **({"progress": prog[r["run_id"]]} if r["run_id"] in prog else {})} for r in rows],
                "inbox": inbox, "volume_error": self.volume_error}

    async def _rows(self, rows: list[dict[str, Any]], project: str | None, limit: int) -> list[dict[str, Any]]:
        """relay.runs() rows for the run list: the Mandate fills in what an old run_started lacks; the project
        filter; flow and role always set."""
        if any(not r.get("initiator") for r in rows):
            legacy = await self._legacy_records()
            for r in rows:
                rec = legacy.get(r["run_id"])
                if rec is not None:
                    for k in ("flow", "project", "initiator", "requested_at", "source", "pr_url"):
                        if not r.get(k):
                            r[k] = getattr(rec, k)
        rows = [{**r, "flow": r.get("flow") or r["run_id"].rsplit("-", 1)[0]} for r in rows
                if not project or r.get("project") == project][:limit]
        return [{**r, "role": role_of(r["flow"], r.get("role"))} for r in rows]

    async def run_labels(self, run_id: str) -> list[str]:
        """The run's labels: from the run list when it is there, else read for this run alone."""
        row = next((r for r in await self.engine_runs() if r["run_id"] == run_id), None)
        if row is not None and "labels" in row:
            return list(row["labels"])
        try:
            return await asyncio.to_thread(self.relay.run_labels, run_id)
        except (RuntimeError, ValueError):
            return []

    async def run_detail(self, run_id: str) -> dict[str, Any] | None:
        try:
            evs = await self.events(run_id)
            status = next((r["status"] for r in await self.engine_runs() if r["run_id"] == run_id), None)
            if runview.has_detail(evs):
                f = await self.flow(run_id)
                if f is not None:
                    return {"run_id": run_id, **runview.fold(f, evs, status), "knowledge": await self.mandate_knowledge(run_id)}
        except (RuntimeError, ValueError) as e:  # Docker down, a bad snapshot
            self.state_errors[run_id] = str(e).splitlines()[0][:300]
        return await self._engine_view(run_id)

    # ---- migration: runs without detail events --------------------------------------------

    async def _engine_view(self, run_id: str) -> dict[str, Any] | None:
        """The engine's state of a run started before the event-detail patch. Remove once those runs
        are gone (docs/notes/2026-11-02-channel-layer.md, 迁移)."""
        from myorch.engines.temporal import client as tc

        try:
            st = await tc.get_status(self.relay.client, run_id)
        except Exception as e:  # not started, gone from Temporal, or its replay fails
            self.state_errors[run_id] = str(e).splitlines()[0][:300]
            return None
        return self.describe(st)

    @staticmethod
    def _waiting(st: RunState) -> list[dict[str, Any]]:
        out = []
        for inst in st.instances.values():
            if inst.status == "waiting_input" and inst.question:
                out.append({"step": inst.id, "kind": "question", "text": inst.question.question, "options": inst.question.options})
            elif inst.status == "waiting_approval":
                missing = st.approval_missing(inst.id, set(inst.approvals))
                out.append({"step": inst.id, "kind": "approval", "text": missing or f"需要 {inst.approvals_needed} 人批准",
                            "approvals": inst.approvals})
        for lp in st.loops.values():
            if lp.status == "waiting_input" and lp.question:
                out.append({"step": lp.id, "kind": "loop", "text": lp.question.question, "options": lp.question.options})
        return out

    def describe(self, st: RunState) -> dict[str, Any]:
        """The engine's state in the shape of runview.fold()."""

        def inst_view(inst) -> dict[str, Any]:
            m, r = inst.manifest, inst.result
            return {
                "id": inst.id, "step": inst.step_id, "status": inst.status, "round": inst.round,
                "replica": inst.replica, "attempts": inst.attempts, "turns": inst.turns,
                "model": r.model if r else None, "exit_code": r.exit_code if r else None,
                "summary": m.summary if m else "", "outputs": m.outputs if m else {},
                "files": [f.model_dump() for f in m.files] if m else [], "notes": m.notes if m else "",
                "manifest_status": m.status if m else None,
                "question": inst.question.model_dump() if inst.question else None,
                "approvals_needed": inst.approvals_needed, "approvals": inst.approvals,
                "error": inst.error or (r.error if r and r.error else None),
            }

        tree = []
        for item in st.flow.steps:
            if isinstance(item, Loop):
                lp = st.loops[item.id]
                rounds: dict[int, list] = {}
                for inst in st.instances.values():
                    if inst.loop_id == item.id:
                        rounds.setdefault(inst.round, []).append(inst_view(inst))
                tree.append({"kind": "loop", "id": item.id, "status": lp.status, "round": lp.round,
                             "max_rounds": lp.max_rounds, "until": item.until, "error": lp.error,
                             "needs": item.needs, "when": getattr(item, "when", None),
                             "question": lp.question.model_dump() if lp.question else None,
                             "steps": [{"id": s.id, **runview.step_meta(s)} for s in item.steps],
                             "stages": stages([(s.id, s.needs) for s in item.steps]),
                             "rounds": [{"round": k, "instances": v} for k, v in sorted(rounds.items())]})
            else:
                insts = [inst_view(i) for i in st.instances.values() if i.step_id == item.id and not i.loop_id]
                tree.append({"kind": "step", "id": item.id, "instances": insts, **runview.step_meta(item)})
        top = stages([(t["id"], t["needs"]) for t in tree])
        for t in tree:
            t["stage"] = top[t["id"]]
        m = st.mandate
        return {
            "run_id": st.run_id, "flow": st.flow.flow, "status": st.status, "inputs": st.inputs,
            "project": (st.project or {}).get("name"), "mandate": m.model_dump() if m else None,
            "tree": tree, "waiting": self._waiting(st) if st.status == "running" else [], "said": None,
            "base": (st.project or {}).get("base"), "source": "engine",
            "knowledge": [k.model_dump() for k in (m.knowledge if m else [])],
        }

    # ---- knowledge ----------------------------------------------------------------------

    async def mandate_knowledge(self, run_id: str) -> list[dict[str, Any]]:
        """The knowledge sources the run mounts, from its mandate (fixed at start, so cached)."""
        if run_id not in self._knowledge:
            text = await self.relay.read_file(run_id, ".myorch/mandate.json")
            try:
                self._knowledge[run_id] = list((json.loads(text) if text else {}).get("knowledge") or [])
            except ValueError:
                self._knowledge[run_id] = []
        return self._knowledge[run_id]

    async def knowledge_catalog(self, project: str | None) -> list[dict[str, Any]]:
        """Registered sources (for a project: the ones it may use), briefly cached: each one
        costs a look at the volume."""
        from myorch import knowledge as kb

        key = project or ""
        at, rows = self._catalog.get(key, (0.0, []))
        if time.monotonic() - at > 20:
            rows = await asyncio.to_thread(kb.catalog, self.relay.store, self.kb_root, project)
            self._catalog[key] = (time.monotonic(), rows)
        return rows

    async def knowledge_all(self) -> list[dict[str, Any]]:
        """Every registered source, shared and per project, for the 流程 sheet. A project-level row
        carries `project` (where it is registered, so it can be edited there)."""
        from myorch import knowledge as kb
        from myorch import project as projects

        def rows() -> list[dict[str, Any]]:
            out = kb.catalog(self.relay.store, self.kb_root, None)
            seen = {r["file"] for r in out}
            for p in projects.list_projects(self.kb_root):
                try:
                    for r in kb.catalog(self.relay.store, self.kb_root, p):
                        if r["file"] not in seen:
                            seen.add(r["file"])
                            out.append({**r, "project": p if r["level"] == "project" else None})
                except Exception:  # a broken pack is reported by the project list
                    continue
            return out
        return await asyncio.to_thread(rows)

    async def knowledge_edit(self, op: str, name: str, project: str | None, data: dict[str, Any] | None = None,
                             replace: bool = False) -> dict[str, Any]:
        """Register, check or remove a knowledge source (myorch/knowledge.py); the registry is a git
        repository, so every change is a commit."""
        from myorch import knowledge as kb

        if op == "check":
            return await asyncio.to_thread(kb.check, self.kb_root, name, project, data or {})
        self._catalog.clear()
        if op == "register":
            return await asyncio.to_thread(kb.register, self.kb_root, name, project, data or {}, self.user, replace)
        if op == "remove":
            return await asyncio.to_thread(kb.unregister, self.kb_root, name, project, self.user)
        raise ValueError(f"unknown operation {op!r}")

    async def step_knowledge_use(self, run_id: str, instance: str) -> dict[str, Any]:
        """What the step did with the run's knowledge, from the agent's session record
        (.claude/<instance>/projects/**/*.jsonl): MCP queries with their answers, and the files it
        read under /sandbox/workspace/knowledge/."""
        check_run_id(run_id)
        check_run_id(instance)
        base = f".claude/{instance}"
        try:
            files = [f for f in await self.relay.list_files(run_id, base, 6) if f.endswith(".jsonl")]
        except (RuntimeError, ValueError):
            files = []
        calls: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for rel in sorted(files):
            text = await self.relay.read_file(run_id, f"{base}/{rel}") or ""
            for line in text.splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                content = ((rec.get("message") or {}).get("content")) if isinstance(rec, dict) else None
                if not isinstance(content, list):
                    continue
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "tool_use":
                        name, inp = str(c.get("name") or ""), c.get("input") or {}
                        target = next((str(inp.get(k)) for k in ("file_path", "path", "pattern") if inp.get(k)), "")
                        if name.startswith("mcp__") or "/sandbox/workspace/knowledge/" in json.dumps(inp, ensure_ascii=False):
                            calls[c.get("id") or f"{len(order)}"] = {"tool": name, "input": inp, "target": target,
                                                                     "at": rec.get("timestamp"), "result": ""}
                            order.append(c.get("id") or f"{len(order) - 1}")
                    elif c.get("type") == "tool_result" and c.get("tool_use_id") in calls:
                        res = c.get("content")
                        if isinstance(res, list):
                            res = "\n".join(str(x.get("text", "")) for x in res if isinstance(x, dict))
                        calls[c["tool_use_id"]]["result"] = str(res or "")[:6000]
        items = [calls[i] for i in order if i in calls]
        return {"queries": [i for i in items if i["tool"].startswith("mcp__")],
                "reads": [i for i in items if not i["tool"].startswith("mcp__")], "transcripts": len(files)}

    # ---- content ------------------------------------------------------------------------

    async def read_file(self, run_id: str, path: str) -> str | None:
        text = await self.relay.read_file(run_id, _safe_rel(path))
        if text is not None and len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS] + f"\n\n[已截断：文件超过 {MAX_FILE_CHARS} 个字符]"
        return text

    async def read_image(self, run_id: str, path: str) -> tuple[bytes, str] | None:
        """An image file of the run as (bytes, media type); None when there is no such file."""
        kind = image_type(path)
        if not kind:
            raise ValueError(f"只能预览图片（{'、'.join(IMAGE_TYPES)}）：{path}")
        data = await self.relay.read_bytes(run_id, _safe_rel(path), MAX_IMAGE_BYTES)
        if data is not None and len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"图片超过 {MAX_IMAGE_BYTES // 1_000_000} MB，不能预览：{path}")
        return None if data is None else (data, kind)

    async def list_files(self, run_id: str, sub: str = "docs", depth: int = 4) -> list[str]:
        files = await self.relay.list_files(run_id, _safe_rel(sub), depth)
        return [f"{sub}/{f}".replace("//", "/") for f in files]

    async def step_files(self, run_id: str, instance: str) -> dict[str, Any]:
        f = await self.relay.step_files(check_run_id(run_id), check_run_id(instance))
        rel = step_dir(run_id, instance).removeprefix(run_dir(run_id) + "/")
        return {"out_dir": f"{rel}/out", "outputs": f.get("files") or [], "log": f.get("log") or ""}

    async def _base(self, run_id: str) -> str:
        """The branch the run's change is compared against: its project's base."""
        view = await self.run_detail(run_id)
        if view and view.get("base"):
            return view["base"]
        name = (view or {}).get("project")
        if name:
            from myorch import project as projects

            try:
                return projects.load_project(self.kb_root, name).base or "main"
            except Exception:  # pack gone or broken: compare against main
                pass
        return "main"

    async def changes(self, run_id: str) -> dict[str, Any]:
        """What the run changed (git diff against the project's base) and what review concluded."""
        check_run_id(run_id)
        base = await self._base(run_id)
        bundle = await self.relay.changes(run_id, base)
        raw = bundle.get("git/diff", "")
        stat, _, diff = raw.partition("\ndiff --git ")
        diff = ("diff --git " + diff) if diff else ""
        files = []
        for chunk in re.split(r"(?m)^diff --git ", diff)[1:]:
            head = chunk.splitlines()[0] if chunk else ""
            m = re.match(r"a/(.+?) b/(.+)$", head)
            path = m.group(2) if m else head
            added = sum(1 for ln in chunk.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
            removed = sum(1 for ln in chunk.splitlines() if ln.startswith("-") and not ln.startswith("---"))
            files.append({"path": path, "added": added, "removed": removed, "diff": "diff --git " + chunk[:MAX_FILE_CHARS]})
        rounds = sorted((int(m.group(1)), k) for k in bundle
                        if (m := re.fullmatch(r"docs/review/round-(\d+)/verified\.json", k)))
        findings, summary, rnd = [], "", None
        if rounds:
            rnd, key = rounds[-1]
            try:
                findings = json.loads(bundle[key] or "[]")
            except ValueError:
                findings = []
            summary = bundle.get(f"docs/review/round-{rnd}/summary.md", "")
        no_repo = "not a git repository" in raw or "unknown revision" in raw
        return {"base": base, "stat": stat.strip() if not no_repo else "", "files": files if not no_repo else [],
                "log": bundle.get("git/log", "") if not no_repo else "", "no_diff_reason": raw.strip()[:300] if no_repo else "",
                "review_round": rnd, "findings": findings if isinstance(findings, list) else [], "summary": summary,
                "risk": bundle.get("docs/risk.md", "")}

    # ---- catalog: flows and projects (definitions, read in this process) -------------------

    def flows(self) -> list[dict[str, Any]]:
        """Every flow, with its inputs as written and as each project sees them (inputs_for: some
        inputs apply to some projects only, a ticket's prefix comes from the project)."""
        from myorch import project as projects
        from myorch.core.fragment import resolve_inputs

        public: dict[str, dict[str, Any]] = {}
        for name in projects.list_projects(self.kb_root):
            try:
                public[name] = projects.load_project(self.kb_root, name).public()
            except Exception:  # noqa: BLE001 - a broken pack is reported by projects()
                continue

        def inputs_for(doc: dict[str, Any]) -> dict[str, Any]:
            res = {}
            for name, pub in public.items():
                try:
                    res[name] = resolve_inputs(doc.get("inputs") or {}, pub)
                except ValueError:
                    continue
            return res

        out = []
        for p in sorted((self.config / "flows").glob("*.yaml")):
            text = p.read_text()
            doc = yaml.safe_load(text) or {}
            comment = []
            for line in text.splitlines():
                if not line.startswith("#"):
                    break
                comment.append(line.lstrip("# ").rstrip())
            role = role_of(str(doc.get("flow", p.stem)), doc.get("role"))
            view = role_view(role, doc)  # label, hint, order, default: the console decides nothing by name
            description = "\n".join(comment).strip()
            out.append({"file": f"flows/{p.name}", "flow": doc.get("flow", p.stem), "role": role,
                        "projects": list(doc.get("projects") or []), "inputs": doc.get("inputs") or {},
                        "inputs_for": inputs_for(doc),
                        "defaults": doc.get("defaults") or {}, "require_one_of": doc.get("require_one_of") or [],
                        "uses_project": "{{project." in text, "description": description,
                        **view, "hint": view["hint"] or description.split("\n")[0]})
        return sorted(out, key=lambda f: (f["order"], f["flow"]))

    def projects(self) -> list[dict[str, Any]]:
        from myorch import project as projects

        try:
            synced: set[str] | None = set(self.relay.store.project_list())
        except RuntimeError:  # Docker is down: sync state unknown
            synced = None
        out = []
        for name in projects.list_projects(self.kb_root):
            try:
                p = projects.load_project(self.kb_root, name)
            except Exception as e:  # a broken pack should not hide the others
                out.append({"name": name, "error": str(e)})
                continue
            out.append({"name": name, "repo": p.repo, "base": p.base, "extends": p.extends,
                        "required_inputs": p.required_inputs, "synced": (name in synced) if synced is not None else None,
                        "identity": p.identity.account})
        return out

    # ---- commands, through the relay (only called after the user confirmed) ----------------

    async def start(self, flow: str, project: str | None, inputs: dict[str, Any], message: str = "",
                    knowledge: list[str] | None = None, labels: list[str] | None = None) -> str:
        run_id = await self.relay.start(flow, project, inputs, message, via=VIA, account=self.user, source="console",
                                        knowledge=knowledge, labels=labels)
        self._runs = (0.0, [])
        self.log(run_id, {"kind": "request", "text": message.strip(), "inputs": inputs})
        return run_id

    async def reply(self, run_id: str, step: str, text: str) -> None:
        view = await self.run_detail(check_run_id(run_id))
        w = next((w for w in (view or {}).get("waiting") or [] if w["step"] == step), None)
        await self.relay.reply(run_id, step, text, via=VIA, account=self.user)  # refuses unless the step waits
        self.log(run_id, {"kind": "reply", "step": step, "text": text,
                          "question": w["text"] if w else "", "options": w.get("options", []) if w else []})

    async def approve(self, run_id: str, step: str, comment: str = "") -> None:
        await self.relay.approve(check_run_id(run_id), step, via=VIA, account=self.user)
        self.log(run_id, {"kind": "approve", "step": step, "text": comment})

    async def reject(self, run_id: str, step: str, comment: str = "") -> None:
        await self.relay.reject(check_run_id(run_id), step, via=VIA, account=self.user)
        self.log(run_id, {"kind": "reject", "step": step, "text": comment})

    can_request_changes = True  # the relay always has it

    async def request_changes(self, run_id: str, step: str, text: str) -> None:
        await self.relay.request_changes(check_run_id(run_id), step, text, via=VIA, account=self.user)
        self.log(run_id, {"kind": "changes", "step": step, "text": text})

    async def resume(self, run_id: str, steps: list[str] | None = None) -> list[str]:
        """Continue a failed or cancelled run from its failed steps (relay.resume)."""
        reset = await self.relay.resume(check_run_id(run_id), steps, via=VIA, account=self.user)
        self._runs = (0.0, [])
        self.log(run_id, {"kind": "resume", "steps": steps or [], "text": "、".join(reset)})
        return reset

    async def cancel(self, run_id: str) -> None:
        await self.relay.cancel(check_run_id(run_id), via=VIA, account=self.user)
        self.log(run_id, {"kind": "cancel"})

    # ---- the run's conversation -----------------------------------------------------------

    def log(self, run_id: str, entry: dict[str, Any]) -> None:
        if self.log_dir is None:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with open(self.log_dir / f"{check_run_id(run_id)}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": _now(), "by": self.user, **entry}, ensure_ascii=False) + "\n")

    def read_log(self, run_id: str) -> list[dict[str, Any]]:
        p = self.log_dir / f"{check_run_id(run_id)}.jsonl" if self.log_dir else None
        if p is None or not p.is_file():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    async def request_text(self, run_id: str) -> str | None:
        if run_id not in self._requests:
            try:
                self._requests[run_id] = await self.relay.read_file(run_id, REQUEST_FILE)
            except RuntimeError:  # Docker down: try again next time
                return None
        return self._requests[run_id]

    def _said(self, run_id: str, detail: dict[str, Any]) -> list[dict[str, Any]]:
        """What people said to the run, in order. From the engine's events when the run has them
        (replies and change requests from any channel, the CLI too), plus the console's own notes
        (approval comments, cancel); approvals made elsewhere show from the gate's approvers."""
        log = self.read_log(run_id)
        if detail.get("said") is None:  # engine view (migration): the console's log is all there is
            return [e for e in log if e.get("step") or e.get("kind") in ("cancel", "resume")]
        out = list(detail["said"])  # resumes come from the run's own events (run_resumed)
        out += [e for e in log if e.get("kind") in ("approve", "reject", "cancel")]
        noted = {(e["step"], e.get("by")) for e in log if e.get("kind") == "approve"}
        for t in detail["tree"]:
            for i in (t["instances"] if t["kind"] == "step" else []):
                for who in i.get("approvals") or []:
                    if (i["id"], who) not in noted and who != self.user:
                        out.append({"kind": "approve", "step": i["id"], "text": "", "by": who, "at": None})
                if i.get("rejected_by") and not any(e.get("kind") == "reject" and e.get("step") == i["id"] for e in log):
                    out.append({"kind": "reject", "step": i["id"], "text": "", "by": i["rejected_by"], "at": None})
        return out

    async def conversation(self, run_id: str, detail: dict[str, Any]) -> dict[str, Any]:
        """The run as a conversation: the request, each step's progress, questions and the replies,
        approvals, the PR and the end. Plus where the user's next message would go."""
        log = self.read_log(run_id)
        said: dict[str, list] = {}
        for e in self._said(run_id, detail):
            said.setdefault(e.get("step") or "", []).append(e)
        items: list[dict[str, Any]] = []
        req = next((e for e in log if e.get("kind") == "request" and e.get("text")), None)
        m = detail.get("mandate") or {}
        if req:
            items.append({"kind": "request", "text": req["text"], "by": req.get("by"), "at": req.get("at")})
        else:
            text = await self.request_text(run_id)
            if text:  # started elsewhere (CLI, a channel) with the original request saved
                text = text.split("\n\n", 2)[-1] if text.startswith("# 原始请求") else text
            items.append({"kind": "request", "by": m.get("initiator"), "at": m.get("requested_at"),
                          "text": text or "\n".join(f"{k}: {v}" for k, v in (detail.get("inputs") or {}).items() if v)})

        def said_items(sid: str) -> None:
            for e in said.get(sid, []):
                if e["kind"] == "reply":
                    items.append({"kind": "question", "step": sid, "text": e.get("question", ""),
                                  "options": e.get("options", []), "answered": True})
                items.append({"kind": "said", "step": sid, "what": e["kind"], "text": e.get("text", ""),
                              "by": e.get("by"), "at": e.get("at")})

        def step_items(sid: str, insts: list[dict], meta: dict, round_: int = 0) -> None:
            live = [i for i in insts if i["status"] not in ("pending",)]
            if not live:
                return
            items.append({"kind": "step", "step": sid, "round": round_, "exec": meta.get("exec"),
                          "instances": [{k: i.get(k) for k in ("id", "status", "model", "summary", "error", "replica")} for i in live]})
            for i in live:
                said_items(i["id"])
                if i["status"] == "waiting_input" and i.get("question"):
                    items.append({"kind": "question", "step": i["id"], "text": i["question"]["question"],
                                  "options": i["question"]["options"], "answered": False})
                if i["status"] == "waiting_approval":
                    items.append({"kind": "approval", "step": i["id"], "needed": i["approvals_needed"],
                                  "approvals": i["approvals"],
                                  "missing": next((w["text"] for w in detail["waiting"] if w["step"] == i["id"]), "")})
                pr = (i.get("outputs") or {}).get("pr_url")
                if pr:
                    items.append({"kind": "pr", "step": i["id"], "url": pr})

        for t in detail["tree"]:
            if t["kind"] == "step":
                step_items(t["id"], t["instances"], t)
            else:
                for r in t["rounds"]:
                    for sm in t["steps"]:
                        step_items(sm["id"], [i for i in r["instances"] if i["step"] == sm["id"]], sm, r["round"])
                said_items(t["id"])
                if t["status"] == "waiting_input" and t.get("question"):
                    items.append({"kind": "question", "step": t["id"], "text": t["question"]["question"],
                                  "options": t["question"]["options"], "answered": False})
        for e in said.get("", []):  # cancel: not tied to a step
            items.append({"kind": "said", "step": "", "what": e["kind"], "text": e.get("text", ""), "by": e.get("by"), "at": e.get("at")})
        if detail["status"] != "running":
            items.append({"kind": "end", "status": detail["status"]})
        return {"items": items, "target": self._target(detail)}

    @staticmethod
    def _target(detail: dict[str, Any]) -> dict[str, Any]:
        """Where the next message goes: the earliest question, else an approval, else nowhere."""
        waiting = detail.get("waiting") or []
        for w in waiting:
            if w["kind"] in ("question", "loop"):
                return {"mode": "reply", "step": w["step"], "question": w["text"], "options": w.get("options", []),
                        "others": [x["step"] for x in waiting if x is not w]}
        for w in waiting:
            if w["kind"] == "approval":
                return {"mode": "approve", "step": w["step"], "missing": w["text"]}
        if detail["status"] == "running":
            running = [i["id"] for t in detail["tree"]
                       for i in (t["instances"] if t["kind"] == "step" else [x for r in t["rounds"] for x in r["instances"]])
                       if i["status"] == "running"]
            return {"mode": "busy", "running": running}
        return {"mode": "done", "status": detail["status"]}


def stages(items: list[tuple[str, list[str]]]) -> dict[str, int]:
    """Execution order among siblings: 0 for items that need no sibling, else one more than the
    latest sibling they need. Items with the same stage can run in parallel. Needs outside the
    container (a loop step needing a top-level step) do not count."""
    needs = dict(items)
    out: dict[str, int] = {}

    def stage(i: str, seen: frozenset = frozenset()) -> int:
        if i in out:
            return out[i]
        deps = [n for n in needs.get(i, []) if n in needs and n not in seen]
        out[i] = 1 + max((stage(n, seen | {i}) for n in deps), default=-1)
        return out[i]

    for i, _ in items:
        stage(i)
    return out
