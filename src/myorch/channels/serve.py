"""`myorch serve`: one local process for the channel relay and its platform adapters.

    /channel/...   the relay over HTTP and SSE (channels/http.py), for the web adapter
    /              the console (src/myorch/ui/), the web adapter's page
    Slack          channels/slack.py, when SLACK_BOT_TOKEN and SLACK_APP_TOKEN are set (Socket Mode)

It replaces `myorch ui` and `python -m myorch.channels`. The watchdog (myorch/watchdog.py) runs here, once per
process; the console only shows its alerts. Run one `myorch serve` at a time. Design:
docs/notes/2026-11-02-channel-layer.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Mount

from myorch.channels.http import create_channel_app
from myorch.channels.model import load_config
from myorch.channels.relay import Relay
from myorch.channels.slack import SlackAdapter, tokens_from_env
from myorch.watchdog import Watchdog, WatchdogConfig
from myorch.storage.events import EventLog

log = logging.getLogger("myorch.serve")


def create_serve_app(*, client, store, config: Path, kb_root: Path, data_root: Path, port: int) -> Starlette:
    from myorch.ui.app import create_app as create_console_app

    cfg = load_config(config / "channels.yaml")
    events = EventLog(data_root / "events.db")
    relay = Relay(client, store, events, config, kb_root, cfg)
    watchdog = Watchdog(relay, store, data_root / "watchdog.json", WatchdogConfig(**cfg.watchdog))
    console = create_console_app(client=client, store=store, config=config, kb_root=kb_root,
                                 data_root=data_root, port=port, relay=relay, watchdog=watchdog)

    slack = None
    tokens = tokens_from_env()
    if tokens:  # the Slack adapter (channels/slack.py), only with both tokens (deploy/secrets/slack.env)
        slack = SlackAdapter(relay, data_root, config, kb_root, bot_token=tokens[0], app_token=tokens[1],
                             console_url=os.environ.get("MYORCH_CONSOLE_URL", f"http://127.0.0.1:{port}/"))

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        tasks = [asyncio.create_task(watchdog.loop())]
        if slack is not None:
            tasks.append(asyncio.create_task(slack.run()))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()

    return Starlette(routes=[Mount("/channel", app=create_channel_app(relay, port)), Mount("/", app=console)],
                     lifespan=lifespan)
