import pytest

from myorch.ui.app import render_md
from myorch.ui.service import _safe_rel, check_run_id


def test_markdown_never_emits_raw_html_or_script_links():
    out = render_md("<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n\n[a](javascript:alert(1)) [b](https://ok.example)")
    assert "<script" not in out and "<img" not in out
    assert 'href="#"' in out and 'href="https://ok.example"' in out


@pytest.mark.parametrize("path", ["../x", "docs/../../etc", "", "/", "docs//a.md"])
def test_paths_stay_inside_the_workspace(path):
    with pytest.raises(ValueError):
        _safe_rel(path)


def test_relative_paths_are_kept():
    assert _safe_rel("/docs/review/round-1/summary.md") == "docs/review/round-1/summary.md"


@pytest.mark.parametrize("run_id", ["../runs", "a/b", "", "x y"])
def test_run_ids_are_plain_names(run_id):
    with pytest.raises(ValueError):
        check_run_id(run_id)


def test_valid_run_ids():
    assert check_run_id("development-blog24-issue24") == "development-blog24-issue24"
    assert check_run_id("review.r1.x2") == "review.r1.x2"


def test_images_of_a_run_load_from_its_workspace():
    from myorch.ui.app import run_md

    text = ("![shot](attachments/a.png) ![up](../../../x.png) ![svg](attachments/b.svg) [js](javascript:alert(1))\n\n"
            "- a.png → docs/external/attachments/301-a.png\n- `repo/ui/logo.JPG`\n- docs/notes.md\n")
    out = run_md(text, "ck-development-1", "docs/external/ticket.md")
    raw = "/api/runs/ck-development-1/raw?path="
    assert f'src="{raw}docs/external/attachments/a.png"' in out
    assert f'<a href="{raw}docs/external/attachments/301-a.png" target="_blank" rel="noopener">docs/external/attachments/301-a.png</a>' in out
    assert f'href="{raw}repo/ui/logo.JPG"' in out
    assert 'src="#"' in out and out.count(raw) == 3  # outside the workspace and SVG are not served
    assert 'href="#"' in out and "javascript" not in out.split('href="#"')[0][-20:]
    assert f'"{raw}docs/notes.md"' not in out  # only images are linked


def test_the_console_serves_only_images_of_bounded_size(tmp_path):
    import asyncio

    from myorch.ui.service import MAX_IMAGE_BYTES, Service

    class Relay:
        client = store = None

        async def read_bytes(self, run_id, rel, max_bytes):
            return {"docs/a.png": b"\x89PNG", "docs/big.jpg": b"x" * (max_bytes + 1)}.get(rel)

    svc = Service(Relay(), tmp_path, tmp_path, "jakobhe", log_dir=tmp_path / "log")
    assert asyncio.run(svc.read_image("r1", "docs/a.png")) == (b"\x89PNG", "image/png")
    assert asyncio.run(svc.read_image("r1", "docs/none.png")) is None
    for path in ("docs/big.jpg", "docs/x.svg", "docs/a.md", "../a.png"):
        with pytest.raises(ValueError):
            asyncio.run(svc.read_image("r1", path))
    assert MAX_IMAGE_BYTES >= 5_000_000  # fetch-ticket saves attachments up to 5 MB


def test_the_console_creates_a_project_and_reports_a_failed_sync_without_undoing_it(tmp_path):
    import asyncio

    import yaml

    from myorch.ui.service import Service

    kb = tmp_path / "kb"
    (kb / "mine").mkdir(parents=True)
    (kb / "mine" / "project.yaml").write_text(yaml.safe_dump({
        "repo": "me/mine", "identity": {"account": "me", "git_name": "Me", "git_email": "me@x"}, "image": "img"}))

    class Store:
        down = False

        def project_sync(self, name, local_dir, version):
            if self.down:
                raise RuntimeError("Docker is not running")

    class Relay:
        client = None
        store = Store()

    relay = Relay()
    svc = Service(relay, tmp_path, kb, "jakobhe", log_dir=tmp_path / "log")
    r = asyncio.run(svc.create_project({"repo": " me/app ", "name": "", "template": "", "base": "", "sync": True}))
    assert r["name"] == "app" and r["version"].startswith("sha256:") and not any("project sync" in s for s in r["next"])
    relay.store.down = True
    r = asyncio.run(svc.create_project({"repo": "me/other", "sync": True}))
    assert r["sync_error"] == "Docker is not running" and (kb / "other" / "project.yaml").is_file()
    assert any("project sync other" in s for s in r["next"])
    with pytest.raises(ValueError, match="already exists"):
        asyncio.run(svc.create_project({"repo": "me/app"}))


def _png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def test_image_signatures_decide_what_may_be_attached():
    from myorch.ui.service import sniff_image

    assert sniff_image(_png()) == "png" and sniff_image(b"\xff\xd8\xff\xe0abc") == "jpg"
    assert sniff_image(b"GIF89a....") == "gif" and sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert sniff_image(b"<svg xmlns='http://www.w3.org/2000/svg'/>") is None and sniff_image(b"") is None


def test_a_reply_carries_attached_images_to_the_agent_and_the_page_can_find_them_again(tmp_path):
    import asyncio
    import re

    from myorch.ui.service import ATTACH_MARK, MAX_ATTACH, Service

    class Relay:
        client = store = None
        sent: list = []
        files = ["a" * 16 + ".png"]
        waiting = [{"step": "develop", "kind": "question", "text": "Which?"}]

        async def write_attachment(self, run_id, data, ext):
            return f"docs/attachments/{'b' * 16}.{ext}"

        async def list_files(self, run_id, rel, depth):
            return self.files

        async def reply(self, run_id, step, text, *, via, account):
            self.sent.append((step, text))

    relay = Relay()
    svc = Service(relay, tmp_path, tmp_path, "jakobhe", log_dir=tmp_path / "log")

    async def detail(rid):
        return {"waiting": relay.waiting, "tree": []}

    svc.run_detail = detail
    svc.log = lambda *a, **k: None
    ok = f"docs/attachments/{'a' * 16}.png"

    assert asyncio.run(svc.attach("r1", _png())) == {"path": f"docs/attachments/{'b' * 16}.png", "bytes": len(_png())}
    for data, error in [(b"", "empty"), (b"GIF89a" + b"x" * 5_000_001, "larger"), (b"<html>", "Only PNG")]:
        with pytest.raises(ValueError, match=error):
            asyncio.run(svc.attach("r1", data))

    asyncio.run(svc.reply("r1", "develop", "Use this one", [ok]))
    step, text = relay.sent[-1]
    assert step == "develop" and text.startswith("Use this one\n\n" + ATTACH_MARK) and f"- /sandbox/workspace/{ok}" in text
    asyncio.run(svc.reply("r1", "develop", "  ", [ok]))
    assert relay.sent[-1][1].startswith("(Images only.)")
    asyncio.run(svc.reply("r1", "develop", "no images"))
    assert relay.sent[-1][1] == "no images"
    # what app.js splitAttachments looks for: the mark, then one "- /sandbox/workspace/<path>" per image
    assert re.search(rf"\n\n\[myorch: attached images\]\n[^\n]*\n(- /sandbox/workspace/docs/attachments/[0-9a-f]{{16}}\.png\n?)+$", text)

    n = len(relay.sent)
    for images, error in [(["../../etc/passwd"], "uploaded to this run"), ([ok, ok], "uploaded to this run"),
                          ([f"docs/attachments/{'c' * 16}.png"], "not in the run"),
                          ([f"docs/attachments/{i:016x}.png" for i in range(MAX_ATTACH + 1)], "up to")]:
        with pytest.raises(ValueError, match=error):
            asyncio.run(svc.reply("r1", "develop", "x", images))
    relay.waiting = [{"step": "review-loop", "kind": "loop", "text": "Decide"}]
    with pytest.raises(ValueError, match="loop"):
        asyncio.run(svc.reply("r1", "review-loop", "x", [ok]))
    assert len(relay.sent) == n  # none of the refused ones reached the agent


def test_a_run_is_started_with_its_images_named_in_the_request(tmp_path):
    import asyncio

    from myorch.ui.service import ATTACH_MARK, Service

    class Relay:
        client = store = None
        got: dict = {}

        async def start(self, flow, project, inputs, message, **kw):
            self.got = {"message": message, **kw}
            return "development-1"

    relay = Relay()
    svc = Service(relay, tmp_path, tmp_path, "jakobhe", log_dir=tmp_path / "log")
    logged = []
    svc.log = lambda run, e: logged.append(e)
    asyncio.run(svc.start("development", "sp", {}, "Fix the layout", images=[_png(), _png()]))
    paths = list(relay.got["attachments"])
    assert len(paths) == 2 and all(p.startswith("docs/attachments/") and p.endswith(".png") for p in paths)
    assert relay.got["message"].startswith("Fix the layout\n\n" + ATTACH_MARK) and all(f"/sandbox/workspace/{p}" in relay.got["message"] for p in paths)
    assert logged[-1]["text"] == relay.got["message"]  # the console shows the request with its images
    asyncio.run(svc.start("development", "sp", {}, "Plain"))
    assert relay.got["attachments"] == {} and relay.got["message"] == "Plain"
    asyncio.run(svc.start("development", "sp", {}, "", images=[_png()]))
    assert relay.got["message"].startswith("(Images only.)")
    with pytest.raises(ValueError, match="Only PNG"):
        asyncio.run(svc.start("development", "sp", {}, "x", images=[b"<svg/>"]))
    with pytest.raises(ValueError, match="at most"):
        asyncio.run(svc.start("development", "sp", {}, "x", images=[_png()] * 7))


def test_schedules_show_the_runs_they_started_and_can_be_paused_resumed_and_fired(tmp_path, monkeypatch):
    import asyncio

    from myorch.engines.temporal import schedules as sch
    from myorch.ui.service import Service

    calls = []

    async def list_schedules(client):
        return [{"name": "weekly", "when": "cron 0 9 * * 1 (Asia/Shanghai)", "paused": False, "flow": "development",
                 "project": "blog", "inputs": {}, "initiator": "jakobhe", "knowledge": None, "skip_if_running": True,
                 "next": ["2026-11-23T09:00+08:00"], "recent": []},
                {"name": "idle", "when": "every 1d", "paused": True, "flow": "sweep", "project": None, "inputs": {},
                 "initiator": "jakobhe", "knowledge": [], "skip_if_running": True, "next": [], "recent": []}]

    async def pause(client, name, paused):
        calls.append(("pause" if paused else "resume", name))

    async def trigger(client, name):
        calls.append(("trigger", name))

    monkeypatch.setattr(sch, "list_schedules", list_schedules)
    monkeypatch.setattr(sch, "pause", pause)
    monkeypatch.setattr(sch, "trigger", trigger)

    class Relay:
        client = store = None

        async def runs(self, limit, label=None):
            return [{"run_id": "development-2", "status": "RUNNING", "source": "schedule:weekly", "requested_at": "b"},
                    {"run_id": "development-1", "status": "COMPLETED", "source": "console", "requested_at": "a"},
                    {"run_id": "development-0", "status": "COMPLETED", "source": "schedule:weekly", "requested_at": "z"}]

    svc = Service(Relay(), tmp_path, tmp_path, "jakobhe", log_dir=tmp_path / "log")
    rows = {r["name"]: r for r in asyncio.run(svc.schedules())}
    assert [r["run_id"] for r in rows["weekly"]["runs"]] == ["development-2", "development-0"]  # newest first, only its own
    assert rows["idle"]["runs"] == []
    for op in ("pause", "resume", "trigger"):
        asyncio.run(svc.schedule_action("weekly", op))
    assert calls == [("pause", "weekly"), ("resume", "weekly"), ("trigger", "weekly")]
    with pytest.raises(ValueError, match="unknown schedule action"):
        asyncio.run(svc.schedule_action("weekly", "delete"))  # deleting stays on the command line


def test_when_a_schedule_fires_survives_pausing():
    from datetime import timedelta

    from temporalio.client import ScheduleCalendarSpec, ScheduleIntervalSpec, ScheduleSpec

    from myorch.engines.temporal.schedules import when_of

    assert when_of("cron 0 9 * * 1 (Asia/Shanghai)", ScheduleSpec()) == "cron 0 9 * * 1 (Asia/Shanghai)"
    assert when_of("Paused via Python SDK", ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(hours=6))])) == "every 6:00:00"
    assert when_of("Unpaused via Python SDK", ScheduleSpec(calendars=[ScheduleCalendarSpec()])).startswith("cron (")
