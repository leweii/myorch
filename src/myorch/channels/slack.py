"""The Slack adapter: a platform adapter on the channel relay, in the `myorch serve` process.

Connection: Socket Mode (an app-level token, xapp-) plus the Web API (the bot token, xoxb-). myorch
connects out to Slack, so the machine needs no open port. Both tokens are channel credentials:
they stay in this process (deploy/secrets/slack.env), never in a sandbox.

Out (events -> Slack), in the initiator's direct messages, one thread per run:
    run_started       the thread's root message
    step_* / loops    one progress message in the thread, edited in place
    question          a message with the options as buttons; a reply in the thread answers it
    approval          approve / reject / request changes buttons, with the risk conclusion
    approval_*        the approval message is edited; a reminder is a new message
    run_finished      the result, and the branch the run pushed (myorch opens no PR)
Messages that need a person are also sent to the conversation (reply_broadcast), so they notify.

In (Slack -> commands): a reply in a run's thread, a button, or /myorch (the start form, drawn from
the flow's input specs like the console's). Only people listed in channels.yaml identities with a
`slack` account may act; anyone else is told their Slack user id and nothing runs.
Design: docs/notes/2026-11-05-slack-adapter.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("myorch.slack")

VIA = "slack"
STATUS_MARK = {"succeeded": "✅", "failed": "❌", "rejected": "🚫", "cancelled": "⏹", "skipped": "⏭",
               "blocked": "⛔", "running": "▶️", "waiting_input": "❓", "waiting_approval": "🖐"}
TEXT_LIMIT = 2800  # a section block holds 3000 characters
STEP_LINES = 40    # progress message: the last steps shown


def clip(text: str, limit: int = TEXT_LIMIT) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": clip(text) or " "}}


def context(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": clip(text, 1900)}]}


def button(text: str, action_id: str, value: str, style: str | None = None, url: str | None = None) -> dict[str, Any]:
    b: dict[str, Any] = {"type": "button", "text": {"type": "plain_text", "text": text[:75]}, "action_id": action_id,
                         "value": value[:2000]}
    if style:
        b["style"] = style
    if url:
        b["url"] = url
    return b


# ---- message bodies (pure: tested without Slack) ---------------------------------------------

def root_message(e: dict[str, Any], console: str) -> tuple[str, list[dict[str, Any]]]:
    d = e["data"]
    task = (d.get("inputs") or {}).get("task", "")
    head = f"*{d.get('flow', '')}*" + (f" · {d['project']}" if d.get("project") else "") + f" · `{e['run_id']}`"
    blocks = [section(head + (f"\n{task}" if task else "")),
              {"type": "actions", "elements": [button("在控制台打开", "open_console", e["run_id"],
                                                     url=f"{console}#run={e['run_id']}")]}]
    return f"{d.get('flow', '')} {e['run_id']}", blocks


def progress_message(steps: dict[str, dict[str, Any]], loops: dict[str, dict[str, Any]]) -> tuple[str, list]:
    lines = []
    for sid, s in list(steps.items())[-STEP_LINES:]:
        mark = STATUS_MARK.get(s["status"], "•")
        summary = (s.get("summary") or "").strip().splitlines()[0][:140] if s.get("summary") else ""
        lines.append(f"{mark} `{sid}`" + (f" {summary}" if summary else ""))
    for lid, lr in loops.items():
        lines.append(f"🔁 `{lid}` 第 {lr.get('round', 0)}/{lr.get('max_rounds', '?')} 轮 · {lr.get('status', '')}")
    text = "\n".join(lines) or "还没有步骤开始。"
    return "进度", [section("*进度*\n" + text)]


def question_message(e: dict[str, Any], answered: str | None = None) -> tuple[str, list[dict[str, Any]]]:
    d = e["data"]
    q = d.get("question", "")
    blocks = [section(f"❓ *`{e['step']}` 在问你*\n{q}")]
    if answered is not None:
        blocks.append(context(f"已回复：{clip(answered, 300)}"))
    else:
        opts = [o for o in d.get("options") or [] if str(o).strip()][:5]
        if opts:
            blocks.append({"type": "actions", "elements": [
                button(f"{i + 1}. {o}"[:75], f"answer_{i}", json.dumps({"run": e["run_id"], "step": e["step"], "text": str(o)},
                                                                     ensure_ascii=False)) for i, o in enumerate(opts)]})
        blocks.append(context("选一个选项，或者在这个线程里直接回复。"))
    return f"{e['step']} 在问你：{clip(q, 150)}", blocks


def approval_message(e: dict[str, Any], risk: str, console: str, progress: dict[str, Any] | None = None,
                     closed: str | None = None) -> tuple[str, list[dict[str, Any]]]:
    d = e["data"]
    rid, step = e["run_id"], e["step"]
    needed = (progress or {}).get("needed", d.get("needed", 1))
    approvers = (progress or {}).get("approvers") or []
    missing = (progress or {}).get("missing", d.get("missing", ""))
    lines = [f"🖐 *等你审批* `{step}` · 风险 {d.get('risk', '?')} · 需要 {needed} 人"]
    if approvers:
        lines.append("已批准：" + "、".join(approvers))
    if missing:
        lines.append(f"还差：{missing}")
    blocks = [section("\n".join(lines))]
    if risk:
        blocks.append(section(clip(risk, 1500)))
    value = json.dumps({"run": rid, "step": step})
    if closed:
        blocks.append(context(closed))
    else:
        blocks.append({"type": "actions", "elements": [
            button("批准", "approve", value, "primary"),
            button("要求修改", "request_changes", value),
            button("驳回", "reject", value, "danger"),
            button("在控制台看", "open_changes", rid, url=f"{console}#run={rid}"),
        ]})
    return f"等你审批：{rid}", blocks


def finished_message(e: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    d = e["data"]
    status = d.get("status", "")
    lines = [f"{STATUS_MARK.get(status, '•')} *运行结束：{status}*"]
    if d.get("stopped"):
        lines.append(f"没有改动：{clip(d['stopped'], 600)}")
    if d.get("failure"):
        lines.append(clip(str(d["failure"]), 1200))
    if d.get("pr_url"):
        lines.append(f"PR：{d['pr_url']}")
    elif d.get("delivery"):
        dl = d["delivery"]
        lines.append(f"已推送分支 `{dl.get('branch', '')}`（{str(dl.get('commit', ''))[:12]}），PR 请自己开。")
    return f"{e['run_id']} 结束：{status}", [section("\n".join(lines))]


def first_section(markdown: str, title: str) -> str:
    """The text under the first heading that contains `title` (e.g. 结论 in docs/risk.md)."""
    out, inside = [], False
    for line in (markdown or "").splitlines():
        if line.startswith("#"):
            if inside:
                break
            inside = title in line
            continue
        if inside:
            out.append(line)
    return "\n".join(out).strip()


# ---- the start form, from the flow's input specs ---------------------------------------------

def catalog(config_dir: Path, kb_root: Path) -> list[dict[str, Any]]:
    """Every (project, flow) pair a person can start: a flow lists its projects, or takes any project."""
    from myorch import project as projects
    from myorch.core.fragment import resolve_inputs
    from myorch.core.inputs import role_view, specs
    from myorch.flowfile import flow_meta

    import yaml

    names = projects.list_projects(kb_root)
    public: dict[str, dict[str, Any] | None] = {}
    for n in names:
        try:
            public[n] = projects.load_project(kb_root, n).public()
        except Exception:  # noqa: BLE001 - a broken pack should not hide the others
            public[n] = None
    out = []
    for p in sorted((config_dir / "flows").glob("*.yaml")):
        meta = flow_meta(p)
        doc = yaml.safe_load(p.read_text()) or {}
        if meta["role"] in ("eval",):  # started by postmortem and the CLI, not by people
            continue
        uses_project = "{{project." in p.read_text()
        for proj in (meta["projects"] or (names if uses_project else [""])):
            try:  # the inputs as this project sees them: some apply to some projects only
                declared = resolve_inputs(doc.get("inputs") or {}, public.get(proj))
            except ValueError:
                continue
            out.append({"project": proj, "file": f"flows/{p.name}", "flow": meta["flow"], "role": meta["role"],
                        "label": role_view(meta["role"], doc)["label"], "specs": specs(declared)})
    return out


def start_view(choices: list[dict[str, Any]], chosen: dict[str, Any] | None, runs: list[dict[str, Any]],
               error: str = "", project: str | None = None) -> dict[str, Any]:
    """Project first, then that project's flows, then the chosen flow's fields. project "" is 无项目."""
    if chosen:
        project = chosen["project"]
    names = sorted({c["project"] for c in choices} - {""}) + ([""] if any(not c["project"] for c in choices) else [])
    popts = [{"text": {"type": "plain_text", "text": (n or "无项目")[:75]}, "value": f"p:{n}"} for n in names][:100]
    proj: dict[str, Any] = {"type": "static_select", "action_id": "project", "options": popts,
                            "placeholder": {"type": "plain_text", "text": "选项目"}}
    pinitial = next((o for o in popts if project is not None and o["value"] == f"p:{project}"), None)
    if pinitial:
        proj["initial_option"] = pinitial
    blocks: list[dict[str, Any]] = [{"type": "input", "block_id": "project", "dispatch_action": True,
                                     "label": {"type": "plain_text", "text": "项目"}, "element": proj}]
    if project is not None:
        opts = [{"text": {"type": "plain_text", "text": f"{c['label']}（{c['flow']}）"[:75]},
                 "value": f"{c['project']}|{c['file']}"} for c in choices if c["project"] == project][:100]
        pick: dict[str, Any] = {"type": "static_select", "action_id": "pick", "options": opts,
                                "placeholder": {"type": "plain_text", "text": "选任务"}}
        initial = next((o for o in opts if chosen and o["value"] == f"{chosen['project']}|{chosen['file']}"), None)
        if initial:
            pick["initial_option"] = initial
        # The block id carries the project, so Slack drops the old task when the project changes.
        blocks.append({"type": "input", "block_id": f"pick:{project}", "dispatch_action": True,
                       "label": {"type": "plain_text", "text": "任务"}, "element": pick})
    if chosen:
        for name, s in chosen["specs"].items():
            t = s.get("type", "string")
            if t == "hidden":
                continue
            label = {"type": "plain_text", "text": (s.get("label") or name)[:150]}
            hint = s.get("hint") or ""
            if t == "message":
                el: dict[str, Any] = {"type": "plain_text_input", "action_id": "v", "multiline": True}
            elif t == "text":
                el = {"type": "plain_text_input", "action_id": "v", "multiline": True}
            elif t == "select":
                o = [x if isinstance(x, dict) else {"value": str(x), "label": str(x)} for x in s.get("options") or []]
                el = {"type": "static_select", "action_id": "v",
                      "options": [{"text": {"type": "plain_text", "text": str(x.get("label", x["value"]))[:75]},
                                   "value": str(x["value"])} for x in o][:100]}
            elif t == "run":
                rs = [r for r in runs if r.get("project") == chosen["project"]
                      and r.get("role") == (s.get("role") or chosen["role"]) and r.get("status") != "RUNNING"][:100]
                if not rs:
                    continue
                el = {"type": "static_select", "action_id": "v", "options": [
                    {"text": {"type": "plain_text", "text": f"{r['run_id']} · {r.get('status', '')}"[:75]},
                     "value": r["run_id"]} for r in rs]}
            else:  # string, ticket
                el = {"type": "plain_text_input", "action_id": "v"}
            if hint and el["type"] == "plain_text_input":
                el["placeholder"] = {"type": "plain_text", "text": hint[:150]}
            blocks.append({"type": "input", "block_id": f"in:{name}", "label": label, "element": el,
                           "optional": not (s.get("required") or t == "message")})
    if error:
        blocks.append(context(f"⚠️ {error}"))
    return {"type": "modal", "callback_id": "start", "title": {"type": "plain_text", "text": "新运行"},
            "submit": {"type": "plain_text", "text": "启动"}, "close": {"type": "plain_text", "text": "取消"},
            "private_metadata": json.dumps(({"project": project} if project is not None else {})
                                           | ({"chosen": f"{chosen['project']}|{chosen['file']}"} if chosen else {})),
            "blocks": blocks}


def task_block(view: dict[str, Any]) -> str | None:
    """The task block's id in a start form: "pick:{project}", or "pick" in a form opened before the upgrade."""
    ids = [b.get("block_id", "") for b in view.get("blocks") or []] or list(view.get("state", {}).get("values", {}))
    return next((i for i in ids if i == "pick" or i.startswith("pick:")), None)


def form_values(view: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """(pick value, inputs) from a submitted start form."""
    values = view.get("state", {}).get("values", {})
    block = task_block(view)
    pick = (values.get(block or "", {}).get("pick", {}).get("selected_option") or {}).get("value", "")
    inputs: dict[str, str] = {}
    for block_id, v in values.items():
        if not block_id.startswith("in:"):
            continue
        el = v.get("v", {})
        val = el.get("value") if "value" in el else (el.get("selected_option") or {}).get("value")
        if val:
            inputs[block_id[3:]] = str(val)
    return pick, inputs


def changes_view(run_id: str, step: str) -> dict[str, Any]:
    return {"type": "modal", "callback_id": "request_changes", "title": {"type": "plain_text", "text": "要求修改"},
            "submit": {"type": "plain_text", "text": "提交"}, "close": {"type": "plain_text", "text": "取消"},
            "private_metadata": json.dumps({"run": run_id, "step": step}),
            "blocks": [{"type": "input", "block_id": "text", "label": {"type": "plain_text", "text": "要改什么"},
                        "element": {"type": "plain_text_input", "action_id": "v", "multiline": True}}]}


# ---- state ----------------------------------------------------------------------------------

class SlackState:
    """myorch-data/slack/state.json: the event cursor and, per run, where its thread is."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {"cursor": None, "runs": {}}
        if path.is_file():
            try:
                self.data = json.loads(path.read_text())
            except ValueError:
                log.warning("slack state %s is unreadable; starting from the newest event", path)

    def run(self, run_id: str) -> dict[str, Any] | None:
        return self.data["runs"].get(run_id)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1))
        tmp.replace(self.path)


# ---- the adapter ----------------------------------------------------------------------------

class SlackAdapter:
    def __init__(self, relay, data_root: Path, config_dir: Path, kb_root: Path, *,
                 bot_token: str, app_token: str, console_url: str, web=None) -> None:
        from slack_sdk.web.async_client import AsyncWebClient

        self.relay = relay
        self.config_dir = config_dir
        self.kb_root = kb_root
        self.state = SlackState(data_root / "slack" / "state.json")
        self.web = web or AsyncWebClient(token=bot_token)
        self.app_token = app_token
        self.console = console_url.rstrip("/") + "/"
        self._lock = asyncio.Lock()

    # ---- who ----------------------------------------------------------------------------

    def slack_user(self, person: str) -> str | None:
        return (self.relay.cfg.identities.get(person) or {}).get("slack")

    def person_of(self, user_id: str) -> str | None:
        """The registered person behind a Slack user, or None: unknown users may not act."""
        for person, accounts in self.relay.cfg.identities.items():
            if accounts.get("slack") == user_id:
                return person
        return None

    # ---- loops --------------------------------------------------------------------------

    async def run(self) -> None:
        await asyncio.gather(self._socket(), self._events())

    async def _socket(self) -> None:
        from slack_sdk.socket_mode.aiohttp import SocketModeClient

        client = SocketModeClient(app_token=self.app_token, web_client=self.web)
        client.socket_mode_request_listeners.append(self._on_request)
        await client.connect()
        log.info("slack: connected (Socket Mode)")
        await asyncio.Event().wait()

    async def _events(self) -> None:
        if self.state.data.get("cursor") is None:  # first start: from now on, not the whole history
            latest = await asyncio.to_thread(self._latest_seq)
            self.state.data["cursor"] = latest
            self.state.save()
        async for e in self.relay.follow(after=self.state.data["cursor"]):
            try:
                await self.handle_event(e.model_dump())
            except Exception:  # one bad event must not stop the adapter
                log.exception("slack: event %s %s failed", e.run_id, e.kind)
            self.state.data["cursor"] = e.seq
            self.state.save()

    def _latest_seq(self) -> int:
        after = 0
        while batch := self.relay.events(after=after, limit=2000):
            after = batch[-1].seq
        return after

    # ---- events -> Slack ----------------------------------------------------------------

    async def handle_event(self, e: dict[str, Any]) -> None:
        kind, rid = e["kind"], e["run_id"]
        if kind == "run_started":
            user = self.slack_user(e["data"].get("initiator", ""))
            if not user:
                return  # the initiator has no Slack account: this run stays out of Slack
            conv = await self.web.conversations_open(users=user)
            channel = conv["channel"]["id"]
            text, blocks = root_message(e, self.console)
            root = await self.web.chat_postMessage(channel=channel, text=text, blocks=blocks)
            self.state.data["runs"][rid] = {"channel": channel, "ts": root["ts"], "user": user,
                                            "steps": {}, "loops": {}, "questions": {}, "approvals": {}}
            return
        r = self.state.run(rid)
        if r is None:
            return
        if kind == "step_started":
            r["steps"][e["step"]] = {"status": "running", "summary": ""}
            await self._progress(rid)
        elif kind == "step_finished":
            d = e["data"]
            r["steps"][e["step"]] = {"status": d.get("status", ""), "summary": d.get("summary") or d.get("error") or ""}
            await self._progress(rid)
        elif kind == "loop_round":
            r["loops"][e["data"].get("loop", e["step"])] = e["data"]
            await self._progress(rid)
        elif kind == "question":
            text, blocks = question_message(e)
            ts = await self._post(rid, text, blocks, broadcast=True)
            r["questions"][e["step"]] = {"ts": ts, "event": e}
        elif kind == "replied":
            q = r["questions"].pop(e["step"], None)
            if q:
                text, blocks = question_message(q["event"], answered=e["data"].get("text", ""))
                await self.web.chat_update(channel=r["channel"], ts=q["ts"], text=text, blocks=blocks)
        elif kind == "approval":
            risk = first_section(await self.relay.read_file(rid, "docs/risk.md") or "", "结论")
            text, blocks = approval_message(e, risk, self.console)
            ts = await self._post(rid, text, blocks, broadcast=True)
            r["approvals"][e["step"]] = {"ts": ts, "event": e, "risk": risk}
        elif kind == "approval_progress":
            a = r["approvals"].get(e["step"])
            if a:
                d = e["data"]
                closed = (f"已驳回：{d['rejected_by']}" if d.get("rejected_by")
                          else "已批准：" + "、".join(d.get("approvers") or [])
                          if len(d.get("approvers") or []) >= int(d.get("needed") or 1) else None)
                text, blocks = approval_message(a["event"], a["risk"], self.console, d, closed)
                await self.web.chat_update(channel=r["channel"], ts=a["ts"], text=text, blocks=blocks)
        elif kind == "approval_reminder":
            await self._post(rid, "审批提醒", [section(f"⏰ `{e['step']}` 还在等审批（已等 {e['data'].get('waited', '')}）。")],
                             broadcast=True)
        elif kind == "changes_requested":
            d = e["data"]
            await self._post(rid, "要求修改", [section(f"✏️ {d.get('by', '')} 要求修改：{clip(d.get('text', ''), 1500)}")])
        elif kind == "run_resumed":
            await self._post(rid, "继续运行", [section("▶️ 运行从失败的步骤继续。")])
        elif kind == "pr_opened":
            await self._post(rid, "PR", [section(f"🔗 PR：{e['data'].get('url', '')}")], broadcast=True)
        elif kind == "run_finished":
            text, blocks = finished_message(e)
            await self._post(rid, text, blocks, broadcast=True)

    async def _post(self, rid: str, text: str, blocks: list, broadcast: bool = False) -> str:
        r = self.state.data["runs"][rid]
        res = await self.web.chat_postMessage(channel=r["channel"], thread_ts=r["ts"], text=text, blocks=blocks,
                                              reply_broadcast=broadcast)
        return res["ts"]

    async def _progress(self, rid: str) -> None:
        r = self.state.data["runs"][rid]
        text, blocks = progress_message(r["steps"], r["loops"])
        if r.get("progress_ts"):
            await self.web.chat_update(channel=r["channel"], ts=r["progress_ts"], text=text, blocks=blocks)
        else:
            r["progress_ts"] = await self._post(rid, text, blocks)

    # ---- Slack -> commands --------------------------------------------------------------

    async def _on_request(self, client, req) -> None:
        from slack_sdk.socket_mode.response import SocketModeResponse

        payload = req.payload or {}
        ack: dict[str, Any] | None = None
        ev = payload.get("event") or {}
        if not (ev.get("bot_id") or ev.get("subtype")):  # myorch's own messages come back as events too
            log.info("slack: %s %s from %s", req.type,
                   payload.get("command") or payload.get("type") or ev.get("type", ""),
                   payload.get("user_id") or (payload.get("user") or {}).get("id") or ev.get("user", ""))
        try:
            if req.type == "events_api":
                asyncio.create_task(self._guard(self.on_message(payload.get("event") or {})))
            elif req.type == "slash_commands":
                asyncio.create_task(self._guard(self.open_start(payload["user_id"], payload["trigger_id"])))
            elif req.type == "interactive":
                ack = await self.on_interactive(payload)
        finally:
            await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id, payload=ack))

    async def _guard(self, coro) -> None:
        try:
            await coro
        except Exception:
            log.exception("slack: handling a request failed")

    async def _refuse(self, user: str, channel: str | None = None) -> None:
        ch = channel or (await self.web.conversations_open(users=user))["channel"]["id"]
        await self.web.chat_postMessage(channel=ch, text=(
            f"你的 Slack 账号（`{user}`）没有登记，myorch 不执行你的操作。"
            f"要使用 myorch，请管理员在 channels.yaml 的 identities 里给你加上 `slack: {user}`。"))

    async def on_message(self, ev: dict[str, Any]) -> None:
        if ev.get("type") != "message" or ev.get("subtype") or ev.get("bot_id") or ev.get("channel_type") != "im":
            return
        user, channel, text = ev.get("user", ""), ev["channel"], (ev.get("text") or "").strip()
        if not self.person_of(user):
            await self._refuse(user, channel)
            return
        thread = ev.get("thread_ts")
        rid = next((rid for rid, r in self.state.data["runs"].items()
                    if thread and r["channel"] == channel and r["ts"] == thread), None)
        if rid is None:
            await self.web.chat_postMessage(channel=channel, text="用 /myorch 发起一个运行。", blocks=[
                section("用 `/myorch` 发起一个运行，或者点下面的按钮。运行开始以后，回复、审批都在它的线程里做。"),
                {"type": "actions", "elements": [button("新运行", "open_start", "start", "primary")]}])
            return
        pending = list(self.state.data["runs"][rid]["questions"])
        if not pending:
            await self.web.chat_postMessage(channel=channel, thread_ts=thread,
                                            text="这个运行现在没有在等回复。要改已经完成的运行，在控制台里「接着改」。")
            return
        await self._reply(rid, pending[-1], text, user, channel, thread)

    async def _reply(self, rid: str, step: str, text: str, user: str, channel: str, thread: str) -> None:
        try:
            await self.relay.reply(rid, step, text, via=VIA, account=user)
        except Exception as ex:
            await self.web.chat_postMessage(channel=channel, thread_ts=thread, text=f"回复没有送达：{ex}")

    async def on_interactive(self, p: dict[str, Any]) -> dict[str, Any] | None:
        user = (p.get("user") or {}).get("id", "")
        if not self.person_of(user):
            asyncio.create_task(self._guard(self._refuse(user)))
            return None
        if p.get("type") == "block_actions":
            for a in p.get("actions") or []:
                asyncio.create_task(self._guard(self.on_action(a, p, user)))
            return None
        if p.get("type") == "view_submission":
            return await self.on_submit(p["view"], user)
        return None

    async def on_action(self, a: dict[str, Any], p: dict[str, Any], user: str) -> None:
        aid = a.get("action_id", "")
        channel = (p.get("channel") or {}).get("id") or (p.get("container") or {}).get("channel_id")
        thread = (p.get("message") or {}).get("thread_ts") or (p.get("message") or {}).get("ts")
        if aid.startswith("answer_"):
            v = json.loads(a["value"])
            await self._reply(v["run"], v["step"], v["text"], user, channel, thread)
        elif aid in ("approve", "reject"):
            v = json.loads(a["value"])
            try:
                if aid == "approve":
                    await self.relay.approve(v["run"], v["step"], via=VIA, account=user)
                else:
                    await self.relay.reject(v["run"], v["step"], via=VIA, account=user)
            except Exception as ex:
                await self.web.chat_postMessage(channel=channel, thread_ts=thread, text=f"操作没有送达：{ex}")
        elif aid == "request_changes":
            v = json.loads(a["value"])
            await self.web.views_open(trigger_id=p["trigger_id"], view=changes_view(v["run"], v["step"]))
        elif aid == "open_start":
            await self.open_start(user, p["trigger_id"])
        elif aid == "project":  # the start form: the project decides the tasks offered, no fields yet
            project = a["selected_option"]["value"].removeprefix("p:")
            try:
                await self.web.views_update(view_id=p["view"]["id"], hash=p["view"].get("hash"),
                                            view=start_view(self._choices(), None, [], project=project))
            except Exception as ex:  # the form was submitted or closed meanwhile; on_submit handles it
                log.info("slack: start form not updated (%s)", getattr(getattr(ex, "response", None), "data", ex))
        elif aid == "pick":  # the start form: the chosen flow decides the fields
            chosen = self._choice(a["selected_option"]["value"])
            runs = await self._runs()
            try:
                await self.web.views_update(view_id=p["view"]["id"], hash=p["view"].get("hash"),
                                            view=start_view(self._choices(), chosen, runs))
            except Exception as ex:  # the form was submitted or closed meanwhile; on_submit handles it
                log.info("slack: start form not updated (%s)", getattr(getattr(ex, "response", None), "data", ex))

    async def on_submit(self, view: dict[str, Any], user: str) -> dict[str, Any] | None:
        cb = view.get("callback_id")
        if cb == "request_changes":
            meta = json.loads(view.get("private_metadata") or "{}")
            text = view["state"]["values"]["text"]["v"].get("value") or ""
            asyncio.create_task(self._guard(self.relay.request_changes(meta["run"], meta["step"], text,
                                                                       via=VIA, account=user)))
            return None
        if cb == "start":
            pick, inputs = form_values(view)
            chosen = self._choice(pick) if pick else None
            if chosen is None:  # errors must name a block the form has
                block = task_block(view)
                return {"response_action": "errors",
                        "errors": {block: "先选任务"} if block else {"project": "先选项目"}}
            drawn = json.loads(view.get("private_metadata") or "{}").get("chosen")
            if drawn != pick and any(sp.get("type") != "hidden" for sp in chosen["specs"].values()):
                # Submitted before the form showed this flow's fields: show them instead of starting.
                return {"response_action": "update",
                        "view": start_view(self._choices(), chosen, await self._runs(), error="先填这个任务的字段，再点启动。")}
            message_name = next((n for n, s in chosen["specs"].items() if s.get("type") == "message"), None)
            message = inputs.pop(message_name, "") if message_name else ""
            try:
                run_id = await self.relay.start(chosen["file"], chosen["project"] or None, inputs, message,
                                                via=VIA, account=user, source="slack")
            except Exception as ex:
                log.exception("slack: start %s for %s failed", chosen["file"], user)
                return {"response_action": "errors", "errors": {task_block(view) or "pick": clip(f"没有启动：{ex}", 150)}}
            log.info("slack: %s started %s", user, run_id)
            return None
        return None

    async def open_start(self, user: str, trigger_id: str) -> None:
        if not self.person_of(user):
            await self._refuse(user)
            return
        await self.web.views_open(trigger_id=trigger_id, view=start_view(self._choices(), None, []))

    def _choices(self) -> list[dict[str, Any]]:
        return catalog(self.config_dir, self.kb_root)

    def _choice(self, value: str) -> dict[str, Any] | None:
        project, _, file = value.partition("|")
        return next((c for c in self._choices() if c["project"] == project and c["file"] == file), None)

    async def _runs(self) -> list[dict[str, Any]]:
        try:
            return await self.relay.runs(limit=60)
        except Exception:
            return []


def tokens_from_env() -> tuple[str, str] | None:
    import os

    bot, app = os.environ.get("SLACK_BOT_TOKEN", ""), os.environ.get("SLACK_APP_TOKEN", "")
    if bot.startswith("xoxb-") and app.startswith("xapp-"):
        return bot, app
    if bot or app:
        log.warning("slack: SLACK_BOT_TOKEN must start with xoxb- and SLACK_APP_TOKEN with xapp-; Slack stays off")
    return None
