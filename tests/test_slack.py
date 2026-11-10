"""The Slack adapter (channels/slack.py) against a fake Slack Web API and a fake relay."""

import json
from pathlib import Path

import pytest

from myorch.channels.model import ChannelsConfig
from myorch.channels.slack import SlackAdapter, catalog, first_section, form_values, start_view

REPO = Path(__file__).resolve().parent.parent
ME = "U0ME"


class FakeWeb:
    def __init__(self):
        self.calls, self.n = [], 0

    async def _call(self, name, **kw):
        self.calls.append((name, kw))
        self.n += 1
        return {"ts": f"1.{self.n}", "channel": {"id": "D1"}, "ok": True}

    def __getattr__(self, name):
        async def f(**kw):
            return await self._call(name, **kw)
        return f

    def named(self, name):
        return [kw for n, kw in self.calls if n == name]


class FakeRelay:
    def __init__(self):
        self.cfg = ChannelsConfig(identities={"jakobhe": {"github": "leweii", "slack": ME}})
        self.done = []

    async def read_file(self, run_id, rel):
        return "# 风险说明\n\n## 结论\n中风险，1 人审批。\n\n## 影响范围\n只改文档。\n" if rel == "docs/risk.md" else None

    async def reply(self, run_id, step, text, *, via, account):
        self.done.append(("reply", run_id, step, text, via, account))

    async def approve(self, run_id, step, *, via, account):
        self.done.append(("approve", run_id, step, via, account))

    async def reject(self, run_id, step, *, via, account):
        self.done.append(("reject", run_id, step, via, account))

    async def request_changes(self, run_id, step, text, *, via, account):
        self.done.append(("request_changes", run_id, step, text))

    async def start(self, flow, project, inputs, message, *, via, account, source=""):
        self.done.append(("start", flow, project, inputs, message, via, account))
        return "mo-development-12345678"

    async def runs(self, limit=60):
        return []


def make_kb(root: Path) -> Path:
    """Two project packs: blog, and myorch, which offers development's `incident` input."""
    for name, extra in (("blog", ""), ("myorch", "flows:\n  development: {incident: true}\n")):
        (root / name).mkdir(parents=True, exist_ok=True)
        (root / name / "project.yaml").write_text(
            f"repo: o/{name}\nimage: img\nidentity: {{account: a, git_name: a, git_email: a@x}}\n{extra}")
    return root


@pytest.fixture
def adapter(tmp_path):
    a = SlackAdapter(FakeRelay(), tmp_path, REPO, make_kb(tmp_path / "kb"), bot_token="xoxb-x", app_token="xapp-x",
                     console_url="http://127.0.0.1:8800/", web=FakeWeb())
    return a


def ev(kind, step="", **data):
    return {"kind": kind, "run_id": "blog-development-1", "step": step, "data": data}


async def start_run(a, initiator="jakobhe"):
    await a.handle_event(ev("run_started", flow="blog-development", project="blog", initiator=initiator,
                            inputs={"task": "给文章页加字数统计"}))


async def test_a_run_of_a_registered_person_opens_a_thread_in_their_dms(adapter):
    await start_run(adapter)
    assert adapter.web.named("conversations_open") == [{"users": ME}]
    root = adapter.web.named("chat_postMessage")[0]
    assert "给文章页加字数统计" in json.dumps(root["blocks"], ensure_ascii=False)
    assert adapter.state.run("blog-development-1")["ts"] == "1.2"


async def test_a_run_of_someone_without_a_slack_account_stays_out_of_slack(adapter):
    await start_run(adapter, initiator="someone")
    await adapter.handle_event(ev("question", "analyze", question="?", options=[]))
    assert adapter.web.calls == []


async def test_progress_is_one_message_edited_in_place(adapter):
    await start_run(adapter)
    await adapter.handle_event(ev("step_started", "sync-master"))
    await adapter.handle_event(ev("step_finished", "sync-master", status="succeeded", summary="同步到 master"))
    posts = [p for p in adapter.web.named("chat_postMessage") if p.get("thread_ts")]
    assert len(posts) == 1 and posts[0]["reply_broadcast"] is False
    update = adapter.web.named("chat_update")[-1]
    assert "同步到 master" in json.dumps(update["blocks"], ensure_ascii=False)


async def test_a_question_has_option_buttons_and_a_thread_reply_answers_it(adapter):
    await start_run(adapter)
    await adapter.handle_event(ev("question", "analyze-requirement", question="放在哪里？", options=["标题下面", "文末"]))
    q = adapter.web.named("chat_postMessage")[-1]
    assert q["reply_broadcast"] is True
    buttons = [b for blk in q["blocks"] if blk["type"] == "actions" for b in blk["elements"]]
    assert [json.loads(b["value"])["text"] for b in buttons] == ["标题下面", "文末"]
    root = adapter.state.run("blog-development-1")
    await adapter.on_message({"type": "message", "channel_type": "im", "user": ME, "channel": root["channel"],
                              "thread_ts": root["ts"], "text": "标题下面"})
    assert adapter.relay.done == [("reply", "blog-development-1", "analyze-requirement", "标题下面", "slack", ME)]
    await adapter.handle_event(ev("replied", "analyze-requirement", text="标题下面"))
    assert "已回复" in json.dumps(adapter.web.named("chat_update")[-1]["blocks"], ensure_ascii=False)


async def test_an_unregistered_user_is_told_their_id_and_nothing_runs(adapter):
    await adapter.on_message({"type": "message", "channel_type": "im", "user": "U0OTHER", "channel": "D9", "text": "hi"})
    msg = adapter.web.named("chat_postMessage")[-1]
    assert "U0OTHER" in msg["text"] and adapter.relay.done == []
    assert await adapter.on_interactive({"type": "block_actions", "user": {"id": "U0OTHER"}, "actions": [
        {"action_id": "approve", "value": json.dumps({"run": "r", "step": "s"})}]}) is None


async def test_approval_shows_the_risk_conclusion_and_closes_when_approved(adapter):
    await start_run(adapter)
    await adapter.handle_event(ev("approval", "human-review", needed=1, risk="medium", actions=[], missing=""))
    msg = adapter.web.named("chat_postMessage")[-1]
    body = json.dumps(msg["blocks"], ensure_ascii=False)
    assert "中风险，1 人审批" in body and "只改文档" not in body and '"approve"' in body
    await adapter.on_action({"action_id": "approve", "value": json.dumps({"run": "blog-development-1", "step": "human-review"})},
                            {"channel": {"id": "D1"}, "message": {"ts": "1.9"}}, ME)
    assert adapter.relay.done[-1] == ("approve", "blog-development-1", "human-review", "slack", ME)
    await adapter.handle_event(ev("approval_progress", "human-review", needed=1, approvers=["jakobhe"], missing=""))
    closed = json.dumps(adapter.web.named("chat_update")[-1]["blocks"], ensure_ascii=False)
    assert "已批准：jakobhe" in closed and '"approve"' not in closed


async def test_a_finished_run_with_a_delivery_names_the_pushed_branch(adapter):
    await start_run(adapter)
    await adapter.handle_event(ev("run_finished", status="succeeded", delivery={"branch": "myorch/x", "commit": "a" * 40}))
    assert "pr_wait" not in adapter.state.run("blog-development-1")
    body = json.dumps(adapter.web.named("chat_postMessage")[-1]["blocks"], ensure_ascii=False)
    assert "myorch/x" in body and "aaaaaaaaaaaa" in body and "正在开 PR" not in body


def test_the_start_form_offers_each_project_flow_pair_and_draws_the_flows_fields(tmp_path):
    choices = catalog(REPO, make_kb(tmp_path / "kb"))
    mo = next(c for c in choices if c["flow"] == "development" and c["project"] == "myorch")
    assert mo["project"] == "myorch" and not any(c["role"] == "eval" for c in choices)
    view = start_view(choices, mo, [])
    fields = {b["block_id"]: b for b in view["blocks"] if b["block_id"].startswith("in:")}
    assert set(fields) == {"in:task", "in:incident"}  # from_run has no earlier run to offer yet
    assert fields["in:task"]["optional"] is False and fields["in:incident"]["optional"] is True
    blocks = {b["block_id"]: b for b in view["blocks"] if b["type"] == "input"}
    assert blocks["project"]["element"]["initial_option"]["value"] == "p:myorch"
    assert blocks["pick:myorch"]["element"]["initial_option"]["value"] == "myorch|flows/development.yaml"


def test_the_start_form_opens_with_only_the_project_list(tmp_path):
    choices = catalog(REPO, make_kb(tmp_path / "kb"))
    view = start_view(choices, None, [])
    inputs = [b for b in view["blocks"] if b["type"] == "input"]
    assert [b["block_id"] for b in inputs] == ["project"]
    names = sorted({c["project"] for c in choices})
    assert [o["text"]["text"] for o in inputs[0]["element"]["options"]] == names
    assert "initial_option" not in inputs[0]["element"]


def test_a_chosen_project_offers_only_its_flows(tmp_path):
    choices = catalog(REPO, make_kb(tmp_path / "kb"))
    view = start_view(choices, None, [], project="blog")
    blocks = {b["block_id"]: b for b in view["blocks"] if b["type"] == "input"}
    assert set(blocks) == {"project", "pick:blog"}
    assert blocks["project"]["element"]["initial_option"]["value"] == "p:blog"
    mine = [c for c in choices if c["project"] == "blog"]
    assert len(mine) > 1
    assert [(o["text"]["text"], o["value"]) for o in blocks["pick:blog"]["element"]["options"]] == [
        (f"{c['label']}（{c['flow']}）", f"blog|{c['file']}") for c in mine]  # no "blog · " prefix
    assert "initial_option" not in blocks["pick:blog"]["element"]
    assert json.loads(view["private_metadata"]) == {"project": "blog"}


async def test_changing_the_project_redraws_the_form_with_that_projects_tasks(adapter, tmp_path):
    await adapter.on_action({"action_id": "project", "selected_option": {"value": "p:blog"}},
                            {"view": {"id": "V1", "hash": "h"}}, ME)
    call = adapter.web.named("views_update")[-1]
    assert call["view_id"] == "V1"
    blocks = {b["block_id"]: b for b in call["view"]["blocks"] if b["type"] == "input"}
    assert set(blocks) == {"project", "pick:blog"}  # a new block id: Slack keeps no task from the old project
    assert "initial_option" not in blocks["pick:blog"]["element"] and blocks["pick:blog"]["element"]["options"]
    assert {o["value"] for o in blocks["pick:blog"]["element"]["options"]} == {
        f"blog|{c['file']}" for c in adapter._choices() if c["project"] == "blog"}


def test_flows_without_a_project_are_listed_under_no_project(tmp_path):
    choices = [{"project": "myorch", "file": "flows/a.yaml", "flow": "a", "role": "development", "label": "开发",
                "specs": {}},
               {"project": "", "file": "flows/x.yaml", "flow": "x", "role": "chore", "label": "杂事", "specs": {}}]
    view = start_view(choices, None, [])
    opts = view["blocks"][0]["element"]["options"]
    assert [(o["text"]["text"], o["value"]) for o in opts] == [("myorch", "p:myorch"), ("无项目", "p:")]
    view = start_view(choices, None, [], project="")
    task = next(b for b in view["blocks"] if b["block_id"] == "pick:")
    assert [o["value"] for o in task["element"]["options"]] == ["|flows/x.yaml"]


async def test_a_form_submitted_before_its_fields_were_drawn_shows_them_instead_of_starting(adapter):
    view = {"callback_id": "start", "private_metadata": "{}", "state": {"values": {
        "pick": {"pick": {"selected_option": {"value": "myorch|flows/development.yaml"}}}}}}
    res = await adapter.on_submit(view, ME)
    assert res["response_action"] == "update" and adapter.relay.done == []
    assert any(b.get("block_id") == "in:task" for b in res["view"]["blocks"])


async def test_submitting_the_start_form_starts_the_run_with_the_message(adapter):
    view = {"callback_id": "start", "private_metadata": json.dumps({"chosen": "myorch|flows/development.yaml"}),
            "state": {"values": {
        "project": {"project": {"selected_option": {"value": "p:myorch"}}},
        "pick:myorch": {"pick": {"selected_option": {"value": "myorch|flows/development.yaml"}}},
        "in:task": {"v": {"type": "plain_text_input", "value": "worker 镜像缺 ssh"}},
        "in:incident": {"v": {"type": "plain_text_input", "value": "blog-development-1"}}}}}
    assert form_values(view) == ("myorch|flows/development.yaml",
                                 {"task": "worker 镜像缺 ssh", "incident": "blog-development-1"})
    assert await adapter.on_submit(view, ME) is None
    assert adapter.relay.done[-1] == ("start", "flows/development.yaml", "myorch", {"incident": "blog-development-1"},
                                      "worker 镜像缺 ssh", "slack", ME)


async def test_a_failed_start_is_shown_in_the_form(adapter):
    async def boom(*a, **k):
        raise ValueError("项目 myorch 没有同步")
    adapter.relay.start = boom
    view = {"callback_id": "start", "private_metadata": json.dumps({"chosen": "myorch|flows/development.yaml"}),
            "blocks": [{"block_id": "project"}, {"block_id": "pick:myorch"}, {"block_id": "in:task"}],
            "state": {"values": {
        "pick:myorch": {"pick": {"selected_option": {"value": "myorch|flows/development.yaml"}}},
        "in:task": {"v": {"value": "x"}}}}}
    res = await adapter.on_submit(view, ME)
    assert res["response_action"] == "errors" and "没有同步" in res["errors"]["pick:myorch"]


async def test_a_form_opened_before_the_upgrade_still_starts(adapter):
    view = {"callback_id": "start", "private_metadata": json.dumps({"chosen": "myorch|flows/development.yaml"}),
            "state": {"values": {
        "pick": {"pick": {"selected_option": {"value": "myorch|flows/development.yaml"}}},
        "in:task": {"v": {"value": "x"}}}}}
    assert await adapter.on_submit(view, ME) is None
    assert adapter.relay.done[-1][:3] == ("start", "flows/development.yaml", "myorch")


async def test_submitting_without_a_project_asks_for_one(adapter):
    view = {"callback_id": "start", "private_metadata": "{}", "blocks": [{"block_id": "project"}],
            "state": {"values": {"project": {"project": {"selected_option": None}}}}}
    res = await adapter.on_submit(view, ME)
    assert res == {"response_action": "errors", "errors": {"project": "先选项目"}} and adapter.relay.done == []


async def test_submitting_without_a_task_asks_for_one_on_the_task_list(adapter):
    view = {"callback_id": "start", "private_metadata": json.dumps({"project": "myorch"}),
            "blocks": [{"block_id": "project"}, {"block_id": "pick:myorch"}],
            "state": {"values": {"project": {"project": {"selected_option": {"value": "p:myorch"}}},
                                 "pick:myorch": {"pick": {"selected_option": None}}}}}
    res = await adapter.on_submit(view, ME)
    assert res == {"response_action": "errors", "errors": {"pick:myorch": "先选任务"}} and adapter.relay.done == []


async def test_a_flow_without_a_project_starts_with_no_project(adapter, monkeypatch):
    x = {"project": "", "file": "flows/x.yaml", "flow": "x", "role": "chore", "label": "杂事", "specs": {}}
    monkeypatch.setattr(adapter, "_choices", lambda: [x])
    view = {"callback_id": "start", "private_metadata": json.dumps({"project": "", "chosen": "|flows/x.yaml"}),
            "blocks": [{"block_id": "project"}, {"block_id": "pick:"}],
            "state": {"values": {"project": {"project": {"selected_option": {"value": "p:"}}},
                                 "pick:": {"pick": {"selected_option": {"value": "|flows/x.yaml"}}}}}}
    assert await adapter.on_submit(view, ME) is None
    assert adapter.relay.done[-1] == ("start", "flows/x.yaml", None, {}, "", "slack", ME)


def test_first_section_takes_the_text_under_a_heading():
    assert first_section("# A\n\n## 结论\n一\n二\n## 下一节\n三", "结论") == "一\n二"
    assert first_section("没有标题", "结论") == ""


def test_a_restarted_adapter_keeps_its_threads(tmp_path):
    a = SlackAdapter(FakeRelay(), tmp_path, REPO, tmp_path, bot_token="xoxb-x", app_token="xapp-x",
                     console_url="http://x/", web=FakeWeb())
    a.state.data["runs"]["r"] = {"channel": "D1", "ts": "1.1"}
    a.state.save()
    b = SlackAdapter(FakeRelay(), tmp_path, REPO, tmp_path, bot_token="xoxb-x", app_token="xapp-x",
                     console_url="http://x/", web=FakeWeb())
    assert b.state.run("r") == {"channel": "D1", "ts": "1.1"}
