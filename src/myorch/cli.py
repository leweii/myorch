from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

DEFAULT_ADDRESS = os.environ.get("MYORCH_TEMPORAL", "localhost:7233")
DEFAULT_VOLUME = os.environ.get("MYORCH_VOLUME", "myorch-artifacts")
DEFAULT_CONFIG = os.environ.get("MYORCH_CONFIG", ".")
# Knowledge bases and run data live in directories next to the project:
#   projects/myorch/  projects/myorch-knowledge/  projects/myorch-data/
DEFAULT_KNOWLEDGE = os.environ.get("MYORCH_KNOWLEDGE")
DEFAULT_DATA = os.environ.get("MYORCH_DATA")

ICONS = {
    "pending": "·", "running": "▶", "waiting_approval": "⏸", "waiting_input": "?",
    "succeeded": "✓", "failed": "✗", "rejected": "✗", "blocked": "-", "skipped": "↷", "cancelled": "⊘",
}


def _parse_inputs(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs:
        k, sep, v = p.partition("=")
        if not sep:
            raise SystemExit(f"--input expects KEY=VALUE, got {p!r}")
        out[k] = v
    return out


def _print_instance(state, inst, indent: str) -> None:
    line = f"{indent}{ICONS.get(inst.status, ' ')} {inst.id:<20} {inst.status:<17}"
    if inst.attempts > 1:
        line += f" attempts={inst.attempts}"
    if inst.turns > 1:
        line += f" turns={inst.turns}"
    if inst.result is not None and inst.result.exit_code not in (0, None):
        line += f" exit={inst.result.exit_code}"
    if inst.result is not None and inst.result.model:
        line += f" [{inst.result.model.removeprefix('claude-')}]"
    print(line)
    pad = indent + "    "
    if inst.manifest is not None and inst.manifest.summary and inst.status != "waiting_input":
        print(f"{pad}summary: {inst.manifest.summary[:200]}")
    if inst.manifest is not None and inst.manifest.outputs:
        print(f"{pad}outputs: {json.dumps(inst.manifest.outputs, ensure_ascii=False)[:200]}")
    err = inst.error or (inst.result.error if inst.result and inst.result.error else None)
    if err:
        print(f"{pad}error: {err[:300]}")
    if inst.status == "waiting_approval":
        missing = state.approval_missing(inst.id, set(inst.approvals)) or f"需要 {inst.approvals_needed} 人批准"
        print(f"{pad}{missing} → myorch approve {state.run_id} {inst.id}")
        approval = state.step_of(inst.id).approval
        if approval is not None and approval.changes is not None:
            print(f'{pad}要修改 → myorch changes {state.run_id} {inst.id} "<修改意见>"')
    if inst.question is not None:
        print(f"{pad}question: " + inst.question.question.replace("\n", f"\n{pad}          "))
        for i, opt in enumerate(inst.question.options, 1):
            print(f"{pad}  {i}. {opt}")
        if inst.status == "waiting_input":
            print(f'{pad}→ myorch reply {state.run_id} {inst.id} "<回复>"')


def _print_state(state) -> None:
    if state is None:
        print("(workflow has not initialised yet)")
        return
    from myorch.core.flow import Loop

    print(f"run {state.run_id}  flow={state.flow.flow}  status={state.status}")
    if state.mandate is not None:
        m = state.mandate
        proj = f"{m.project['name']}@{m.project.get('version', '').removeprefix('sha256:')[:8]}" if m.project else "-"
        print(f"  mandate: 发起人 {m.initiator} · {m.requested_at} · flow {m.flow['sha256'][:8]} · project {proj}")
    for step_id, a in state.approvals.items():
        who = (f"驳回：{a.rejected_by}" if a.rejected_by else "还没有决定" if a.at is None
               else f"批准：{', '.join(a.approvers) or '无需人工'}")
        subject = f" · subject {a.subject[:12]}" if a.subject else ""
        print(f"  approval {step_id}: {who}{subject}")
        for c in a.changes:
            print(f"    修改意见 {c.by} · {c.at} → 第 {c.round} 轮：{c.text.splitlines()[0][:80] if c.text else ''}")
    for item in state.flow.steps:
        if isinstance(item, Loop):
            lr = state.loops[item.id]
            print(f"  ⟳ {item.id:<20} {lr.status:<17} round {lr.round}/{lr.max_rounds}  until: {item.until}")
            if lr.error:
                print(f"      error: {lr.error}")
            if lr.question is not None:
                print("      question: " + lr.question.question.replace("\n", "\n                "))
                for i, opt in enumerate(lr.question.options, 1):
                    print(f"        {i}. {opt}")
                print(f'      → myorch reply {state.run_id} {item.id} "<编号>"')
            for inst in state.instances.values():
                if inst.loop_id == item.id:
                    _print_instance(state, inst, "      ")
        else:
            for inst in state.instances.values():
                if inst.step_id == item.id:
                    _print_instance(state, inst, "  ")


async def _run_records(client, store) -> list:
    """Every run with a Mandate, newest first, with its engine status and PR (myorch.runs)."""
    import asyncio

    from myorch.engines.temporal import client as tc
    from myorch.runs import RunRecord

    raw, statuses = await asyncio.gather(asyncio.to_thread(store.run_records), tc.list_runs(client, 1000))
    status = dict(statuses)
    out = []
    for run_id, mandate, pr in raw:
        try:
            m = json.loads(mandate)
        except ValueError:
            continue
        try:
            pr_url = str((json.loads(pr).get("outputs") or {}).get("pr_url") or "") if pr else ""
        except ValueError:
            pr_url = ""
        rec = RunRecord.from_mandate(run_id, m, pr_url)
        rec.status = status.get(run_id, "UNKNOWN")
        out.append(rec)
    return sorted(out, key=lambda r: r.requested_at, reverse=True)


def _with_labels(records: list, flows_dir: Path, kb_root: Path | None = None) -> list:
    """Each record with its labels as the run list computes them (myorch/runs.py): stored, from its
    inputs, and from the run its inputs name."""
    from myorch.runs import flow_inputs_of, label_index

    index = label_index({r.run_id: {"flow": r.flow, "project": r.project, "inputs": r.inputs, "labels": r.labels}
                         for r in records}, flow_inputs_of(flows_dir, kb_root))
    return [r.model_copy(update={"labels": index[r.run_id]}) for r in records]


def _local_time(iso: str) -> str:
    from datetime import datetime

    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%m-%d %H:%M")
    except ValueError:
        return "?" * 11


def _auto_runs(records, project: str | None, role: str | None = "learn") -> str:
    """`-i runs=auto` (the default of flows with a `runs` input). learn: the project's finished runs
    since its last learn. postmortem: the project's newest runs (locate keeps those that matter)."""
    from myorch.runs import select_for_learn, select_for_postmortem

    if not project:
        raise ValueError("runs=auto needs --project (which project to learn from)")
    if role == "postmortem":
        return ",".join(r.run_id for r in select_for_postmortem(records, project))
    picked, last = select_for_learn(records, project)
    since = f"上次 learn {last.run_id}（{_local_time(last.requested_at)}）之后" if last else "还没有 learn 过，全部"
    if not picked:
        raise ValueError(f"project {project}: {since}没有新的已结束的 development / review run")
    print(f"learn 的输入：{project} {since}的 {len(picked)} 个 run：", file=sys.stderr)
    for r in picked:
        print(f"  {_local_time(r.requested_at)}  {r.flow:<12} {r.run_id}  {r.pr_url}", file=sys.stderr)
    return ",".join(r.run_id for r in picked)


def _learn_files(log, inputs: dict[str, str], data_root: Path | None = None, project: str | None = None) -> dict[str, str]:
    """learn: what people said and what failed in the runs it learns from (myorch/trail.py), and the
    runs whose takeaways it already learned from (myorch/takeaways.py, kept in myorch-data)."""
    from myorch import takeaways
    from myorch.trail import trail

    runs = [r.strip() for r in (inputs.get("runs") or "").split(",") if r.strip()]
    files = {"docs/learn/trail.json": json.dumps(trail(log, runs), ensure_ascii=False, indent=1)} if runs else {}
    return {**files, **takeaways.learn_files(data_root, project)}


def _continue_files(log, inputs: dict[str, str], declared: dict, data_root: Path | None) -> dict[str, str]:
    """Continuing a run (development's from_run): what people said to it (questions and answers,
    change requests, approval comments) as docs/previous/conversation.md. CLI, console, Slack and
    schedules all start through here or relay.start."""
    from myorch.core import inputs as fi
    from myorch.trail import CONVERSATION_FILE, previous_conversation

    for name in fi.run_inputs(declared):
        rid = (inputs.get(name) or "").strip()
        if rid:
            return {CONVERSATION_FILE: previous_conversation(log, rid, data_root)}
    return {}


def _continue_from(log, inputs: dict[str, str], project: str | None, declared: dict) -> None:
    """Inputs of type run (development's from_run): the chosen run must be finished, of the same
    project and of the spec's role. The inputs the spec lists in `carry` are copied from it when
    not given (development: the ticket, which the branch name may carry)."""
    from myorch.core import inputs as fi
    from myorch.core.flow import role_of
    from myorch.core.types import check_id

    for name, spec in fi.run_inputs(declared).items():
        rid = (inputs.get(name) or "").strip()
        if not rid:
            continue
        events = log.read(run_id=check_id(rid, "run id"), limit=5000)
        started = next((e.data for e in events if e.kind == "run_started"), None)
        if started is None:
            raise ValueError(f"没有这个运行：{rid}")
        role = spec.get("role")
        if role and role_of(started.get("flow") or "", started.get("role")) != role:
            raise ValueError(f"{rid} 不是 {role} 运行，不能接着它做")
        if (started.get("project") or None) != project:
            raise ValueError(f"{rid} 属于项目 {started.get('project')}，不是 {project}")
        last = [e.kind for e in events if e.kind in ("run_finished", "run_resumed")]
        if not last or last[-1] != "run_finished":
            raise ValueError(f"{rid} 还没有结束。等它结束，或者先取消它")
        old = started.get("inputs") or {}
        for k in spec.get("carry") or []:
            if not inputs.get(k) and old.get(k):
                inputs[k] = old[k]


async def _review(client, store, kb_root: Path, data_root: Path, args: argparse.Namespace) -> int:
    """Everything the approval gate needs, on one local page (myorch.review_page)."""
    import asyncio
    import html
    import subprocess

    from myorch import project as projects
    from myorch import review_page
    from myorch.engines.temporal import client as tc

    mandate = json.loads(await asyncio.to_thread(store.read_file, f"runs/{args.run_id}/.myorch/mandate.json") or "{}")
    if not mandate:
        print(f"error: run {args.run_id} not found (myorch list)", file=sys.stderr)
        return 1
    name = (mandate.get("project") or {}).get("name")
    base = projects.load_project(kb_root, name).base if name else "main"
    files = await asyncio.to_thread(store.review_bundle, args.run_id, base)
    waiting, hint = "", ""
    state = await tc.get_status(client, args.run_id)
    for inst in (state.instances.values() if state else []):
        if inst.status == "waiting_approval":
            waiting = f"{inst.id}：" + (state.approval_missing(inst.id, set(inst.approvals)) or f"需要 {inst.approvals_needed} 人批准")
            source = mandate.get("source") or ""
            where = f'，或在 <a href="{html.escape(source)}">{html.escape(source)}</a> 回复 <code>/approve</code> / <code>/reject</code>' if source.startswith("http") else ""
            hint = (f'<p>在本机：<code class="cmd">uv run myorch approve {args.run_id} {inst.id}</code>'
                    f'<code class="cmd">uv run myorch reject {args.run_id} {inst.id}</code>{where}</p>')
    out = data_root / "reviews" / f"{args.run_id}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(review_page.build(args.run_id, files, waiting, hint), encoding="utf-8")
    print(out)
    if not args.no_open:
        subprocess.run(["open", str(out)], check=False)
    return 0


def _knowledge_cmd(store, root: Path, args: argparse.Namespace) -> int:
    from myorch import knowledge as kb

    try:
        if args.knowledge_cmd == "check":
            return _knowledge_check(store, root, args)
        if args.knowledge_cmd == "sync":
            r = kb.sync(store, root, args.name, args.project)
            state = "unchanged" if r["unchanged"] else "stored"
            print(f"{r['name']}: {r['version']} {state}, {r['files']} files, {r['bytes']} bytes")
            return 0
        rows = kb.catalog(store, root, args.project)
        if not rows:
            print("no knowledge sources registered (myorch-knowledge/_sources/<name>.yaml, <project>/sources/<name>.yaml)")
        for r in rows:
            latest = r["latest"]
            snap = f"{latest['version']} {latest['synced_at']}" if latest else ("-" if r["kind"] == "mcp" else "never synced")
            print(f"{r['name']:<20} {r['kind']:<4} {r['level']:<8} {snap:<40} {r['expanded'] or r['where']}")
            for p in r["problems"]:
                print(f"{'':<20} ! {p}")
        return 0
    except ValueError as e:  # KnowledgeError, a broken registry file
        print(f"error: {e}", file=sys.stderr)
        return 1


def _knowledge_check(store, root: Path, args: argparse.Namespace) -> int:
    """An MCP source, tried the way a step uses it: a sandbox with the project's image, the source's
    provider and /sandbox/bin, running `myorch-mcp --check`. The token never leaves the gateway."""
    import base64
    import json
    import secrets
    import subprocess

    from myorch import knowledge as kb
    from myorch import project as projects
    from myorch.runner.openshell import Mount, docker_driver_config

    s = kb.load_sources(root, args.project).get(args.name)
    if s is None:
        raise ValueError(f"知识源 {args.name} 没有登记" + (f"，或者项目 {args.project} 不能用它" if args.project else "（项目的知识源要加 --project）"))
    if s.kind != "mcp":
        raise ValueError(f"{args.name} 是 {s.kind}，只有 mcp 知识源能检查连接。dir 和 git 用 myorch knowledge sync")
    for problem in s.problems():
        print(f"! {problem}（运行时不能挂载；检查照样做）")
    image = args.image or (projects.load_project(root, args.project).image if args.project else "")
    if not image:
        raise ValueError("要知道用哪个镜像：加 --project <项目> 或 --image <镜像>")
    spec = {"servers": [{"name": s.name, "url": s.url, "token_env": s.token_env, "auth": s.auth, "email": s.email,
                         "tools": list(s.tools)}]}
    check = "python3 /sandbox/bin/myorch-mcp --check /tmp/spec.json"
    if args.call:
        json.loads(args.args)  # a bad JSON fails here, not in the sandbox
        check += f" --call {args.call} \"$(echo {base64.b64encode(args.args.encode()).decode()} | base64 -d)\""
    script = f"echo {base64.b64encode(json.dumps(spec).encode()).decode()} | base64 -d > /tmp/spec.json && {check}"
    name = f"kcheck-{secrets.token_hex(4)}"
    mounts = [Mount(source=store.tools_source(), target="/sandbox/bin", read_only=True)]
    print(f"在 sandbox {name} 里检查（镜像 {image}，provider {s.provider}）……", flush=True)
    try:
        r = subprocess.run(["openshell", "sandbox", "create", "--name", name, "--from", image, "--no-tty", "--no-auto-providers",
                            "--provider", s.provider, "--driver-config-json", docker_driver_config(mounts), "--", "sh", "-c", script],
                           capture_output=True, text=True, timeout=300)
    finally:
        subprocess.run(["openshell", "sandbox", "delete", name], capture_output=True)
    out = r.stdout + r.stderr
    lines = out.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("== ") or line.startswith("✗ ")), None)
    print("\n".join(lines[start:] if start is not None else lines[-30:]))
    return 0 if "RESULT: ok" in out else 1


def _project_cmd(store, root: Path, args: argparse.Namespace) -> int:
    from myorch import project as projects

    try:
        if args.project_cmd == "sync":
            n, version = projects.snapshot(store, root, args.name)
            print(f"project {args.name}: {n} files synced ({version})")
        elif args.project_cmd == "github-profile":
            print(projects.github_provider_profile(projects.load_project(root, args.name), args.read_only), end="")
        elif args.project_cmd == "show":
            print(json.dumps(projects.load_project(root, args.name).public(), ensure_ascii=False, indent=2))
        else:
            synced = set(store.project_list())
            print(f"projects in {root}")
            for name in projects.list_projects(root):
                p = projects.load_project(root, name)
                base = f" extends {p.extends}" if p.extends else ""
                print(f"  {name:<14} {'synced' if name in synced else 'not synced':<11} {p.repo} ({p.base}){base}")
            templates = projects.list_templates(root)
            if templates:
                print(f"templates: {', '.join(templates)}")
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def _flow_doc(flow: Path) -> dict:
    return yaml.safe_load(flow.read_text()) or {}


def _flow_inputs(flow: Path, project: dict | None = None) -> dict:
    """The flow's input specs as the project (Project.public()) sees them (flowfile.flow_inputs)."""
    from myorch.flowfile import flow_inputs

    return flow_inputs(flow, project)


def _project_public(root: Path, name: str | None) -> dict | None:
    """Project.public() for a project name, or None; no checks on the inputs (see _load_project)."""
    if not name:
        return None
    from myorch import project as projects

    return projects.load_project(root, name).public()


def _normalize_inputs(declared: dict, inputs: dict[str, str]) -> None:
    from myorch.core.inputs import normalize

    normalize(declared, inputs)


def _load_project(root: Path, name: str | None, inputs: dict[str, str], flow: Path):
    """Project.public() for --project, after checking the inputs the project requires (of the
    flows that take them: development needs a ticket, onboard does not)."""
    if not name:
        return None
    from myorch import project as projects

    p = projects.load_project(root, name)
    declared = _flow_inputs(flow)
    missing = [k for k in p.required_inputs if k in declared and not inputs.get(k)]
    if missing:
        raise ValueError(f"project {name} needs inputs: {', '.join(missing)} (-i {missing[0]}=...)")
    return p.public()


def _flow_sets(project: dict | None, sets: list[str], inputs: dict[str, str], flow: Path,
               preview: bool = False) -> list[tuple[str, str]]:
    """Apply `--set flows.<key>=<value>` to the project dict this run is expanded with and recorded
    under (the Mandate keeps it), for evals: the same case with fewer reviewers, or without a prompt
    rule (flows.ablate). Only on a replay (-i at=...), which never asks for approval and never
    delivers, and only flow settings: nothing that decides access, approval or delivery."""
    if not sets:
        return []
    if project is None:
        raise ValueError("--set needs --project")
    if not inputs.get("at") and not preview:  # `myorch expand` only prints
        raise ValueError("--set is for replays only: give -i at=<commit> (a replay never asks for approval or delivers)")
    from myorch import project as projects
    from myorch.core.fragment import rule_off

    flows = json.loads(json.dumps(project.get("flows") or {}))
    done = []
    for item in sets:
        key, sep, raw = item.partition("=")
        parts = key.strip().split(".")
        if not sep or len(parts) < 2 or parts[0] != "flows" or not all(parts):
            raise ValueError(f"--set {item!r}: write flows.<key>=<value>, e.g. flows.development.reviewers=1")
        if parts[1:] == ["ablate"]:
            value: Any = [x.strip() for x in raw.split(",") if x.strip()]
            known = _rule_registry(flow)
            unknown = [x for x in value if not any(rule_off(r, [x]) for r in known)]
            if unknown:
                raise ValueError(f"--set flows.ablate: unknown rules {', '.join(unknown)} (flows/rules/registry.yaml: "
                                 f"{', '.join(sorted(known))})")
        else:
            value = yaml.safe_load(raw) if raw.strip() else ""
        cur = flows
        for k in parts[1:-1]:
            if not isinstance(cur.get(k), dict):
                raise ValueError(f"--set {item!r}: the project has no setting {key}")
            cur = cur[k]
        if parts[-1] not in cur:
            raise ValueError(f"--set {item!r}: the project has no setting {key}")
        cur[parts[-1]] = value
        done.append((key.strip(), raw.strip()))
    project["flows"] = projects.Flows.model_validate(flows).model_dump(mode="json")
    return done


def _rule_registry(flow: Path) -> set[str]:
    """The marked rule ids (<id>/<place>) of flows/rules/registry.yaml, next to the flow."""
    from myorch.core.fragment import registry_ids

    path = flow.resolve().parent / "rules" / "registry.yaml"
    return registry_ids(yaml.safe_load(path.read_text(encoding="utf-8"))) if path.is_file() else set()


async def _main(args: argparse.Namespace) -> int:
    # Engine-specific imports stay inside the commands that need them.
    from myorch.storage.base import run_dir
    from myorch.storage.docker_volume import DockerVolumeStore

    store = DockerVolumeStore(args.volume)
    config = Path(args.config).resolve()
    kb_root = Path(args.knowledge).expanduser().resolve() if args.knowledge else config.parent / "myorch-knowledge"
    data_root = Path(args.data).expanduser().resolve() if args.data else config.parent / "myorch-data"

    if args.cmd == "init":
        store.ensure_volume()
        store.install_tools(Path(args.tools).resolve())
        print(f"volume {args.volume} ready, tools installed from {args.tools}")
        return 0

    if args.cmd == "project":
        return _project_cmd(store, kb_root, args)

    if args.cmd == "knowledge":
        return _knowledge_cmd(store, kb_root, args)

    if args.cmd == "expand":
        from myorch.flowfile import read_flow_file

        try:
            inputs = _parse_inputs(args.input)
            project = _load_project(kb_root, args.project, inputs, Path(args.flow))
            _flow_sets(project, args.sets, inputs, Path(args.flow), preview=True)
            print(read_flow_file(Path(args.flow), inputs, project), end="")
        except ValueError as e:  # FragmentError and flow validation errors
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0

    if args.cmd == "logs":
        print(store.read_log_tail(args.run_id, args.step_id, max_bytes=args.bytes))
        return 0

    if args.cmd == "out":
        manifest = store.read_manifest(args.run_id, args.step_id)
        print("manifest:", manifest.model_dump_json(indent=2) if manifest else "(none)")
        print("files:", json.dumps(store.list_outputs(args.run_id, args.step_id), ensure_ascii=False))
        return 0

    if args.cmd == "export":
        dest = Path(args.dest).expanduser() if args.dest else data_root / "runs" / args.run_id
        try:
            n = store.export_run(args.run_id, dest)
        except RuntimeError as e:
            print(e, file=sys.stderr)
            return 1
        print(f"{n} files exported to {dest}")
        return 0

    if args.cmd == "ws":
        base = run_dir(args.run_id)
        if args.path:
            text = store.read_file(f"{base}/{args.path}")
            print(text if text is not None else f"(no such file: {args.path})")
        else:
            for f in store.list_files(base, max_depth=args.depth):
                print(f)
        return 0

    from myorch.engines.temporal import client as tc
    from myorch.engines.temporal.worker import connect, run_worker

    if args.cmd == "worker":
        # kb_root holds the project packs; the event log lives in the data dir (--data / MYORCH_DATA), which
        # is not next to the code in a container (deploy/compose.yaml mounts it at /data).
        await run_worker(args.address, config, args.volume, kb_root, events_path=data_root / "events.db")
        return 0

    client = await connect(args.address)
    if args.cmd == "ui":
        return await _ui(client, store, config, kb_root, data_root, args)
    if args.cmd == "serve":
        return await _serve(client, store, config, kb_root, data_root, args)
    if args.cmd == "review":
        return await _review(client, store, kb_root, data_root, args)
    if args.cmd == "schedule":
        return await _schedule_cmd(client, config, args)
    if args.cmd == "resume":
        try:
            reset = await tc.resume_run(client, args.run_id, args.step or None, args.as_user)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"{args.run_id}: running again {', '.join(reset)}")
        if args.wait:
            _print_state(await tc.wait_result(client, args.run_id))
        return 0
    if args.cmd == "eval":
        return await _eval_cmd(client, store, config, kb_root, data_root, args)
    try:
        if args.cmd == "run":
            from myorch.flowfile import flow_meta, read_flow_file
            from myorch.runs import flow_inputs_of, inherited_labels, referenced_runs

            inputs = _parse_inputs(args.input)
            meta = flow_meta(Path(args.flow))
            if meta["projects"] and args.project not in meta["projects"]:
                raise ValueError(f"流程 {meta['flow']} 只给项目 {', '.join(meta['projects'])} 用，--project 要写其中一个")
            declared = _flow_inputs(Path(args.flow), _project_public(kb_root, args.project))
            _normalize_inputs(declared, inputs)
            if inputs.get("runs") == "auto" or ("runs" in declared and "runs" not in inputs):
                inputs["runs"] = _auto_runs(await _run_records(client, store), args.project, meta["role"])
            from myorch.core import inputs as fi

            if any(inputs.get(n) for n in fi.run_inputs(declared)):
                from myorch.storage.events import open_event_log

                _continue_from(open_event_log(data_root), inputs, args.project, declared)
            fi.check(declared, inputs, _flow_doc(Path(args.flow)).get("require_one_of"))
            project = _load_project(kb_root, args.project, inputs, Path(args.flow))
            sets = _flow_sets(project, args.sets, inputs, Path(args.flow))
            from myorch.channels.model import load_config

            aliases = load_config(config / "channels.yaml").aliases(args.as_user)
            from myorch import knowledge as kb

            names = kb.choose(kb.defaults(kb_root, args.project), args.kb_select.split(",")) if args.kb_select else None
            for note in kb.refresh(store, kb_root, args.project, names):
                print(note, file=sys.stderr)
            refs = kb.resolve(store, kb_root, args.project, names)
            files = {}
            if flow_meta(Path(args.flow))["role"] == "learn":
                from myorch.storage.events import open_event_log

                files = _learn_files(open_event_log(data_root), inputs, data_root, args.project)
            if fi.run_inputs(declared) and any(inputs.get(n) for n in fi.run_inputs(declared)):
                from myorch.storage.events import open_event_log

                files.update(_continue_files(open_event_log(data_root), inputs, declared, data_root))
            labels = list(args.label) + [f"set:{k}={v}" for k, v in sets]  # find the variant's runs by label
            if referenced_runs("", inputs):  # e.g. -i run=ck-development-1f29e33a: that run's ticket too
                from myorch.storage.events import open_event_log

                labels += inherited_labels(open_event_log(data_root), inputs, flow_inputs_of(config / "flows", kb_root))
            run_id = await tc.start_run(client, read_flow_file(Path(args.flow), inputs, project), inputs, args.as_user,
                                        aliases, project, knowledge=refs, files=files, labels=labels)
            print(run_id)
            if refs:
                print("knowledge: " + ", ".join(f"{k.name}" + (f" {k.version}" if k.version else " (mcp)") for k in refs),
                      file=sys.stderr)
            if args.wait:
                _print_state(await tc.wait_result(client, run_id))
        elif args.cmd in ("list", "runs"):
            from myorch.core.inputs import label_matches

            records = _with_labels(await _run_records(client, store), config / "flows", kb_root)
            records = [r for r in records
                       if (not args.project or r.project == args.project) and (not args.flow or r.flow == args.flow)
                       and all(label_matches(q, r.run_id, r.labels) for q in args.label)]
            for r in records[: args.limit]:
                hint = r.pr_url or (r.source if r.source.startswith("http") else "")
                if r.status == "RUNNING":
                    st = await tc.get_status(client, r.run_id)
                    if st is not None:
                        waiting = [i.id for i in st.instances.values() if i.status in ("waiting_input", "waiting_approval")]
                        waiting += [lp.id for lp in st.loops.values() if lp.status == "waiting_input"]
                        hint = f"等你处理: {', '.join(waiting)}" if waiting else hint
                when = _local_time(r.requested_at)
                tags = ",".join(r.labels) or "-"
                print(f"{when}  {(r.project or '-'):<9} {r.flow:<12} {r.status:<10} {r.run_id:<34} {tags:<12} {hint}")
        elif args.cmd == "status":
            _print_state(await tc.get_status(client, args.run_id))
        elif args.cmd == "approve":
            await tc.approve(client, args.run_id, args.step_id, args.as_user)
        elif args.cmd == "reject":
            await tc.reject(client, args.run_id, args.step_id, args.as_user)
        elif args.cmd == "changes":
            await tc.request_changes(client, args.run_id, args.step_id, args.as_user, args.text)
        elif args.cmd == "reply":
            await tc.reply(client, args.run_id, args.step_id, args.text)
        elif args.cmd == "cancel":
            await tc.cancel(client, args.run_id)
    except ValueError as e:  # bad flow/fragment/preset, or a step not waiting for this
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


async def _eval_cmd(client, store, config: Path, kb_root: Path, data_root: Path, args: argparse.Namespace) -> int:
    """Reviewer eval (myorch/evals.py): collect candidates, start runs, compare variants."""
    import secrets as _secrets

    from myorch import evals
    from myorch.engines.temporal.replay import start_replay

    if args.eval_cmd == "collect":
        new = evals.collect(store, kb_root, data_root)
        for c in new:
            print(f"{c['id']:<34} {c['project']:<9} {len(c['findings'])} findings  head {c['head_sha'][:8]}"
                  + ("（head 是推断的）" if c["head_guessed"] else ""))
        print(f"{len(new)} new candidates; confirm them in the console (流程 → 评测集)")
        return 0
    if args.eval_cmd == "list":
        for c in evals.list_cases(data_root):
            print(f"{c['id']:<34} {c['status']:<10} {c['project']:<9} kept {len(c.get('keep') or [])}/{len(c['findings'])}")
        return 0
    if args.eval_cmd == "run":
        cases = [c for c in evals.list_cases(data_root, "confirmed") if not args.case or c["id"] in args.case]
        if not cases:
            print("no confirmed cases (myorch eval collect, then confirm them in the console)", file=sys.stderr)
            return 1
        batch = _secrets.token_hex(3)
        for c in cases:
            for depth in args.depth or ["standard"]:
                rid = await start_replay(client, config, kb_root, c, depth, args.as_user, f"eval:{batch}")
                print(f"{rid}  {c['id']}  {depth}")
        print(f"batch {batch}: myorch eval report --batch {batch}")
        return 0
    if args.eval_cmd == "report":
        from myorch.storage.events import open_event_log

        log = open_event_log(data_root)
        started, finished, after = {}, {}, 0
        while batch_ := log.read(after=after, limit=2000):
            for e in batch_:
                src = str(e.data.get("source", "")) if e.kind == "run_started" else ""
                if src.startswith(("eval:", "postmortem:")):  # postmortem:<run>:before|after (myorch/evals.py)
                    if not args.batch or src in (f"eval:{args.batch}", f"postmortem:{args.batch}:before",
                                                 f"postmortem:{args.batch}:after"):
                        started[e.run_id] = e.data
                elif e.kind == "run_finished":
                    finished[e.run_id] = e.data.get("status")
            after = batch_[-1].seq
        cases = {c["id"]: c for c in evals.list_cases(data_root)}
        rows, pending = [], 0
        for rid, d in sorted(started.items()):
            case = cases.get((d.get("inputs") or {}).get("case", ""))
            if finished.get(rid) != "succeeded" or case is None:
                pending += finished.get(rid) is None
                continue
            found = json.loads(store.read_file(f"runs/{rid}/docs/review/round-1/verified.json") or "[]")
            o = evals.score(found if isinstance(found, list) else [], evals.expected(case))
            phase = d["source"].rsplit(":", 1)[1] if d["source"].startswith("postmortem:") else ""
            rows.append({"variant": d["inputs"].get("depth", "?") + (f"·{phase}" if phase else ""), "case": case["id"],
                         "run": rid, "outputs": o})
        for r in rows:
            o = r["outputs"]
            print(f"{r['variant']:<9} {r['case']:<34} caught {o['caught']}/{o['expected']}  extra {o['extra']}  ({r['run']})")
        print()
        print(f"{'variant':<9} {'cases':>5} {'expected':>8} {'caught':>6} {'missed':>6} {'recall':>6} {'extra':>5} {'extra Important':>15}")
        for v in evals.report(rows):
            print(f"{v['variant']:<9} {v['cases']:>5} {v['expected']:>8} {v['caught']:>6} {v['missed']:>6} "
                  f"{v['recall'] if v['recall'] is not None else '-':>6} {v['extra']:>5} {v['extra_important']:>15}")
        if pending:
            print(f"\n{pending} eval runs still running")
        return 0
    return 1


async def _schedule_cmd(client, config: Path, args: argparse.Namespace) -> int:
    """Schedules that start a flow on a cron or interval (engines/temporal/schedules.py)."""
    from myorch.engines.temporal import schedules as sch
    from myorch.engines.temporal.models import LaunchInput

    try:
        if args.schedule_cmd == "add":
            flow = args.flow.removeprefix("flows/").removesuffix(".yaml")
            if not (config / "flows" / f"{flow}.yaml").is_file():
                raise ValueError(f"no flow {args.flow!r} in {config / 'flows'}")
            inp = LaunchInput(name=args.name, flow=flow, project=args.project, inputs=_parse_inputs(args.input),
                              knowledge=[k for k in args.kb.split(",") if k] if args.kb is not None else None,
                              initiator=args.as_user, skip_if_running=not args.allow_overlap)
            await sch.create(client, inp, cron=args.cron, every=args.every, paused=args.paused)
            print(f"schedule {args.name}: {args.cron or 'every ' + args.every} -> flows/{flow}.yaml"
                  + (f" --project {args.project}" if args.project else "") + (" (paused)" if args.paused else ""))
        elif args.schedule_cmd == "list":
            rows = await sch.list_schedules(client)
            if not rows:
                print("no schedules (myorch schedule add)")
            for r in rows:
                state = "paused" if r["paused"] else ("next " + r["next"][0] if r["next"] else "-")
                print(f"{r['name']:<20} {r['when']:<34} {r['flow']:<12} {r['project'] or '-':<10} {state}")
        elif args.schedule_cmd in ("pause", "unpause"):
            await sch.pause(client, args.name, args.schedule_cmd == "pause")
            print(f"schedule {args.name}: {args.schedule_cmd}d")
        elif args.schedule_cmd == "delete":
            await sch.delete(client, args.name)
            print(f"schedule {args.name}: deleted")
        elif args.schedule_cmd == "trigger":
            await sch.trigger(client, args.name)
            print(f"schedule {args.name}: triggered")
        return 0
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


async def _serve(client, store, config: Path, kb_root: Path, data_root: Path, args: argparse.Namespace) -> int:
    """The channel relay and its adapters in one process (docs/notes/2026-11-02-channel-layer.md)."""
    import logging

    import uvicorn

    from myorch.channels.serve import create_serve_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_serve_app(client=client, store=store, config=config, kb_root=kb_root, data_root=data_root,
                           port=args.port)
    print(f"myorch serve: console http://{args.host}:{args.port}/ · relay /channel/  (Ctrl-C to stop)")
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level="warning"))
    await server.serve()
    return 0


async def _ui(client, store, config: Path, kb_root: Path, data_root: Path, args: argparse.Namespace) -> int:
    """`myorch ui` is `myorch serve`: the console needs the relay (/channel), so it never runs on
    its own."""
    return await _serve(client, store, config, kb_root, data_root, args)


def main() -> None:
    p = argparse.ArgumentParser(prog="myorch")
    p.add_argument("--address", default=DEFAULT_ADDRESS, help="Temporal frontend address")
    p.add_argument("--volume", default=DEFAULT_VOLUME, help="artifacts docker volume")
    p.add_argument("--config", default=DEFAULT_CONFIG, help="project dir containing profiles/")
    p.add_argument("--knowledge", default=DEFAULT_KNOWLEDGE, help="project packs dir (default: ../myorch-knowledge)")
    p.add_argument("--data", default=DEFAULT_DATA, help="run data dir (default: ../myorch-data)")
    sub = p.add_subparsers(dest="cmd", required=True)

    ini = sub.add_parser("init", help="create the artifacts volume and install helper tools")
    ini.add_argument("--tools", default="tools", help="local dir with helper commands")
    sub.add_parser("worker", help="run the Temporal worker")

    pj = sub.add_parser("project", help="project packs (myorch-knowledge/<name>/)")
    pj_sub = pj.add_subparsers(dest="project_cmd", required=True)
    pj_sub.add_parser("list", help="projects, templates, and whether they are synced")
    for name, hlp in (("sync", "snapshot the pack into the volume"), ("show", "print the resolved config"),
                      ("github-profile", "print an OpenShell provider profile for the project's GitHub access")):
        sp = pj_sub.add_parser(name, help=hlp)
        sp.add_argument("name")
        if name == "github-profile":
            sp.add_argument("--read-only", action="store_true", help="no push, pull requests or comments")

    r = sub.add_parser("run", help="start a flow")
    r.add_argument("flow")
    r.add_argument("--input", "-i", action="append", default=[], metavar="KEY=VALUE")
    r.add_argument("--wait", action="store_true")
    r.add_argument("--project", help="project pack to run against (myorch-knowledge/<name>/)")
    r.add_argument("--label", action="append", default=[], metavar="LABEL",
                   help="a label to find the run by, e.g. MD-18700; repeatable (ticket inputs are added anyway)")
    r.add_argument("--as", dest="as_user", default=getpass.getuser(), help="initiator name (recorded in the mandate)")
    r.add_argument("--kb", dest="kb_select", metavar="NAMES",
                   help="knowledge sources, from the project's defaults: 'a,b' adds, '-a' removes (myorch knowledge list)")
    r.add_argument("--set", dest="sets", action="append", default=[], metavar="flows.KEY=VALUE",
                   help="replays only (-i at=...): override a project flow setting for this run, e.g. "
                        "flows.development.reviewers=1 or flows.ablate=nit-cap,entry-points (flows/rules/registry.yaml)")

    rs = sub.add_parser("resume", help="continue a failed or cancelled run from its failed steps (same run id)")
    rs.add_argument("run_id")
    rs.add_argument("--step", action="append", help="only this step (instance or step id); repeatable")
    rs.add_argument("--wait", action="store_true")
    rs.add_argument("--as", dest="as_user", default=getpass.getuser(), help="who continued it (recorded in the event)")

    ev = sub.add_parser("eval", help="score reviewers on confirmed past cases (myorch/evals.py)")
    ev_sub = ev.add_subparsers(dest="eval_cmd", required=True)
    ev_sub.add_parser("collect", help="candidate cases from past runs' round-1 findings")
    ev_sub.add_parser("list", help="cases and whether they are confirmed")
    er = ev_sub.add_parser("run", help="one eval run per confirmed case and depth")
    er.add_argument("--depth", action="append", choices=["quick", "standard", "deep"], help="variant(s); default standard")
    er.add_argument("--case", action="append", help="only these cases")
    er.add_argument("--as", dest="as_user", default=getpass.getuser())
    ep = ev_sub.add_parser("report", help="caught / missed / extra per variant")
    ep.add_argument("--batch", help="only the runs of this `eval run` batch, or of this postmortem run")

    sc = sub.add_parser("schedule", help="start a flow on a cron or interval (Temporal Schedules)")
    sc_sub = sc.add_subparsers(dest="schedule_cmd", required=True)
    a = sc_sub.add_parser("add", help="create a schedule")
    a.add_argument("name")
    a.add_argument("--flow", required=True, help="flows/<name>.yaml or <name>")
    a.add_argument("--project")
    a.add_argument("--input", "-i", action="append", default=[], metavar="KEY=VALUE")
    a.add_argument("--kb", default=None, metavar="NAMES", help="knowledge sources to mount (default: the project's)")
    when = a.add_mutually_exclusive_group(required=True)
    when.add_argument("--cron", help="cron expression in local time, e.g. '0 2 * * *'")
    when.add_argument("--every", help="interval, e.g. 1m, 6h, 1d")
    a.add_argument("--paused", action="store_true")
    a.add_argument("--allow-overlap", action="store_true", help="start even while the previous run still runs")
    a.add_argument("--as", dest="as_user", default=getpass.getuser(), help="initiator of the runs it starts")
    sc_sub.add_parser("list", help="schedules, when they fire next")
    for name, hlp in (("pause", "stop firing"), ("unpause", "fire again"), ("delete", "remove"), ("trigger", "fire once now")):
        sc_sub.add_parser(name, help=hlp).add_argument("name")

    kn = sub.add_parser("knowledge", help="knowledge sources runs can mount: list, check, sync a snapshot")
    kn_sub = kn.add_subparsers(dest="knowledge_cmd", required=True)
    for name, hlp in (("list", "registered sources, newest snapshot, problems"), ("sync", "snapshot one source into the volume"),
                      ("check", "an mcp source, from a sandbox like a step's: connection, tools against the allowlist, one call")):
        c = kn_sub.add_parser(name, help=hlp)
        if name in ("sync", "check"):
            c.add_argument("name")
        if name == "check":
            c.add_argument("--call", metavar="TOOL", help="also call this registered tool, e.g. getJiraIssue")
            c.add_argument("--args", default="{}", metavar="JSON", help='its arguments, e.g. \'{"issueIdOrKey": "MD-18785"}\'')
            c.add_argument("--image", help="sandbox image (default: the project's)")
        c.add_argument("--project", help="also the project's own sources (myorch-knowledge/<project>/sources/)")

    exp = sub.add_parser("expand", help="print a flow with its use: fragments expanded")
    exp.add_argument("flow")
    exp.add_argument("--input", "-i", action="append", default=[], metavar="KEY=VALUE", help="selects presets")
    exp.add_argument("--project", help="fill in {{project.x}} from this project pack")
    exp.add_argument("--set", dest="sets", action="append", default=[], metavar="flows.KEY=VALUE",
                     help="preview a run's --set, e.g. flows.ablate=nit-cap")

    ls = sub.add_parser("list", aliases=["runs"],
                        help="recent runs (time, project, flow, status, labels, PR) and what is waiting for you")
    ls.add_argument("--limit", type=int, default=20)
    ls.add_argument("--project", help="only this project's runs")
    ls.add_argument("--flow", help="only runs of this flow (development, review, learn, onboard …)")
    ls.add_argument("--label", action="append", default=[], metavar="LABEL",
                    help="only runs with this label (e.g. MD-18700, any case; a bare number works) or whose id "
                         "contains it; searches every run before --limit; repeatable (all must match)")
    for name in ("status", "cancel"):
        sub.add_parser(name).add_argument("run_id")
    for name in ("approve", "reject"):
        sp = sub.add_parser(name)
        sp.add_argument("run_id")
        sp.add_argument("step_id")
        sp.add_argument("--as", dest="as_user", default=getpass.getuser(), help="approver name")
    ch = sub.add_parser("changes", help="send a run back from its approval gate with change requests")
    ch.add_argument("run_id")
    ch.add_argument("step_id")
    ch.add_argument("text", help="the change requests, as the approver writes them")
    ch.add_argument("--as", dest="as_user", default=getpass.getuser(), help="approver name")
    out = sub.add_parser("out")
    out.add_argument("run_id")
    out.add_argument("step_id")
    rp = sub.add_parser("reply", help="answer a step (or an exhausted loop) that is waiting for input")
    rp.add_argument("run_id")
    rp.add_argument("step_id")
    rp.add_argument("text")
    lg = sub.add_parser("logs")
    lg.add_argument("run_id")
    lg.add_argument("step_id")
    lg.add_argument("--bytes", type=int, default=20000)
    rv = sub.add_parser("review", help="one local page with what a run asks you to approve, and open it")
    rv.add_argument("run_id")
    rv.add_argument("--no-open", action="store_true", help="only write the page (myorch-data/reviews/<run>.html)")
    ex = sub.add_parser("export", help="copy a run's workspace to a local directory")
    ex.add_argument("run_id")
    ex.add_argument("dest", nargs="?", help="default: ../myorch-data/runs/<run_id>")
    ws = sub.add_parser("ws", help="list or read files in a run's workspace")
    ws.add_argument("run_id")
    ws.add_argument("path", nargs="?")
    ws.add_argument("--depth", type=int, default=3)

    sv = sub.add_parser("serve", help="channel relay + console (and other adapters) in one process")
    sv.add_argument("--port", type=int, default=8800)
    sv.add_argument("--host", default=os.environ.get("MYORCH_UI_HOST", "127.0.0.1"), help="listen address; 0.0.0.0 only inside a container (deploy/compose.yaml)")
    ui = sub.add_parser("ui", help="same as `serve`: the console and the channel relay")
    ui.add_argument("--port", type=int, default=8800)
    ui.add_argument("--host", default=os.environ.get("MYORCH_UI_HOST", "127.0.0.1"), help="listen address; 0.0.0.0 only inside a container (deploy/compose.yaml)")

    sys.exit(asyncio.run(_main(p.parse_args())))


if __name__ == "__main__":
    main()
